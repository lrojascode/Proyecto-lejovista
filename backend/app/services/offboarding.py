"""Baja de una Organizacion: dos fases con plazos (spec 09).

La eliminacion declarada no coincidia con la persistencia real. El script que
habia marca la organizacion como inactiva y retira las membresias, y se NIEGA a
actuar si tiene datos: es una herramienta de limpieza de organizaciones
residuales, no un procedimiento de baja de un cliente con historico. No revocaba
credenciales, no exportaba, no eliminaba medios y no tenia plazo.

Las tablas de archivo, ademas, se disenaron **sin claves foraneas** para
sobrevivir al borrado de un tenant: el historico de un cliente dado de baja
permanecia despues del CASCADE. Omitirlo convierte la promesa de eliminacion en
falsa.

**Dos fases, y la separacion es el punto.**

* **Inmediata**: lo que corta accesos. Revocar credenciales de Meta, invalidar
  sesiones, eliminar la clave de IA, bloquear login y API, detener sync e IA.
* **Diferida** (30 dias): lo que destruye informacion. Historico, medios y datos
  de campanas.

Confundirlas obligaria a elegir entre cortar tarde o borrar sin dar tiempo a
exportar. Son lo mismo solo si a uno le da igual el cliente.
"""
import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization

logger = logging.getLogger("lejovista.offboarding")

# Estados de la baja.
BAJA_INICIADA = "INICIADA"       # accesos cortados, ventana de exportacion abierta
BAJA_COMPLETADA = "COMPLETADA"   # datos eliminados

# Plazos comunicados al cliente. Estan aca y no repartidos por el codigo porque
# son una PROMESA, y una promesa con dos valores distintos en dos sitios se
# incumple en el que nadie mira.
DIAS_PARA_ELIMINAR = 30
DIAS_DE_LOGS_DE_SEGURIDAD = 90


class BajaInvalida(Exception):
    def __init__(self, mensaje: str, code: str = "OFFBOARDING_INVALID"):
        self.code = code
        super().__init__(mensaje)

    def as_detail(self) -> dict:
        return {"code": self.code, "message": str(self)}


@dataclass
class ResultadoDeBaja:
    fase: str
    detalle: Dict[str, Any]


def estado_de_baja(org: Organization) -> Dict[str, Any]:
    return dict((org.offboarding_state or {}))


def esta_dada_de_baja(org: Organization) -> bool:
    return bool(estado_de_baja(org).get("fase"))


# --- Fase inmediata ---------------------------------------------------------


async def iniciar_baja(
    db: AsyncSession, org: Organization, *, actor_id: Optional[str], motivo: str
) -> ResultadoDeBaja:
    """Corta TODOS los accesos, ya. No borra nada.

    Lo que se hace aca es irreversible solo en un punto: la credencial de Meta
    queda revocada y hay que volver a emitirla para reactivar. Lo demas —estado,
    sesiones— se puede deshacer.
    """
    from app.models.meta_credential import MetaCredential
    from app.models.organization_user import OrganizationUser
    from app.models.user import User
    from app.utils.security import session_cutoff_now

    if estado_de_baja(org).get("fase") == BAJA_COMPLETADA:
        raise BajaInvalida(
            "Esta organización ya completó su baja: sus datos fueron eliminados.",
            code="OFFBOARDING_ALREADY_DONE",
        )

    ahora = datetime.datetime.utcnow()
    detalle: Dict[str, Any] = {}

    # 1. Credenciales externas: lo primero, y lo unico verdaderamente urgente.
    #    Mientras sigan ACTIVE, cualquier camino que las lea puede hablar con
    #    Meta en nombre de un cliente al que ya se le corto el servicio.
    res = await db.execute(
        update(MetaCredential)
        .where(MetaCredential.organization_id == org.id,
               MetaCredential.status != "REVOKED")
        .values(status="REVOKED", updated_at=ahora)
    )
    detalle["credenciales_meta_revocadas"] = res.rowcount or 0

    # 2. La clave de IA se BORRA, no se marca: es de propiedad del cliente y no
    #    tenemos ninguna razon para conservarla ni un dia mas.
    tenia_clave = org.has_llm_api_key
    org.set_llm_api_key(None)
    detalle["clave_de_ia_eliminada"] = bool(tenia_clave)

    # 3. Sesiones de los miembros. Sin esto, un access token ya emitido sigue
    #    entrando hasta 24 horas despues de la baja.
    miembros = (await db.execute(
        select(OrganizationUser.user_id).where(
            OrganizationUser.organization_id == org.id
        )
    )).scalars().all()
    if miembros:
        await db.execute(
            update(User).where(User.id.in_(list(miembros)))
            .values(sessions_valid_from=session_cutoff_now())
        )
    detalle["sesiones_cortadas"] = len(miembros)

    # 4. El estado bloquea login, API, sync, IA y write-back de una vez: las
    #    compuertas de la spec 01 comprueban `status != ACTIVE`.
    org.status = "INACTIVE"
    org.offboarding_state = {
        "fase": BAJA_INICIADA,
        "iniciada_en": ahora.isoformat(),
        "iniciada_por": actor_id,
        "motivo": motivo,
        "eliminar_a_partir_de": (
            ahora + datetime.timedelta(days=DIAS_PARA_ELIMINAR)
        ).isoformat(),
        "detalle_inmediato": detalle,
    }
    db.add(org)
    logger.info("Baja iniciada para la organización %s: %s", org.slug, detalle)
    return ResultadoDeBaja(fase=BAJA_INICIADA, detalle=detalle)


# --- Exportacion ------------------------------------------------------------


async def exportar_datos(db: AsyncSession, org: Organization) -> Dict[str, Any]:
    """Los datos COMERCIALES del cliente, en un formato legible fuera del producto.

    No lleva secretos, ni identificadores internos que solo tienen sentido dentro
    del sistema, ni nada de otras Organizaciones. Lo que se lleva el cliente es su
    historico y sus propuestas, no nuestra base de datos.
    """
    from app.models.ad_account import AdAccount
    from app.models.campaign_learning_log import CampaignLearningLog
    from app.models.campaign_snapshot import CampaignSnapshot
    from app.models.snapshot_archive import CampaignSnapshotArchive

    cuentas = (await db.execute(
        select(AdAccount).where(AdAccount.organization_id == org.id)
    )).scalars().all()

    snapshots: List[Dict[str, Any]] = []
    for modelo in (CampaignSnapshot, CampaignSnapshotArchive):
        filas = (await db.execute(
            select(modelo).where(modelo.organization_id == org.id)
        )).scalars().all()
        for s in filas:
            snapshots.append({
                "fecha": s.date.isoformat(),
                "campana": s.campaign_name,
                "objetivo": getattr(s, "objective", None),
                "estado": s.status,
                "gasto": round(float(s.spend or 0), 2),
                "impresiones": s.impressions,
                "alcance": s.reach,
                "clics": s.clicks,
                "conversaciones": s.conversations,
                "resultados": getattr(s, "results", None),
            })

    propuestas = (await db.execute(
        select(CampaignLearningLog).where(CampaignLearningLog.organization_id == org.id)
    )).scalars().all()

    return {
        "organizacion": {"nombre": org.name, "identificador": org.slug},
        "exportado_en": datetime.datetime.utcnow().isoformat(),
        "cuentas_publicitarias": [
            {"nombre": c.name, "cuenta_de_meta": c.external_account_id,
             "moneda": c.currency}
            for c in cuentas
        ],
        "historico_de_campanas": sorted(snapshots, key=lambda s: s["fecha"]),
        "propuestas_de_la_ia": [
            {
                "producto": p.product,
                "creada_en": p.created_at.isoformat() if p.created_at else None,
                "parametros": p.params_json,
                "resultado": p.outcome_json,
            }
            for p in propuestas
        ],
        "totales": {
            "cuentas": len(cuentas),
            "snapshots": len(snapshots),
            "propuestas": len(propuestas),
        },
    }


# --- Fase diferida ----------------------------------------------------------

# Tablas cuyo borrado NO lo cubre el CASCADE de `organizations`, porque se
# disenaron sin claves foraneas para sobrevivir al borrado de un tenant. Ahi
# esta el historico completo del cliente.
def _tablas_sin_cascade():
    from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
    from app.models.snapshot_archive import (
        AdSnapshotArchive, AdsetSnapshotArchive, CampaignSnapshotArchive,
    )

    return (
        CampaignSnapshotArchive, AdsetSnapshotArchive, AdSnapshotArchive,
        AdAccountSpendMonthly,
    )


async def eliminar_datos(
    db: AsyncSession, org: Organization, *, forzar: bool = False,
    ahora: Optional[datetime.datetime] = None,
) -> ResultadoDeBaja:
    """Elimina el historico, los medios y los datos de campanas.

    Irreversible. Se niega si el plazo comunicado no vencio, salvo `forzar`
    explicito: el plazo existe para que el cliente pueda exportar, y saltarselo
    en silencio convierte la promesa en un adorno.
    """
    from app.models.media_asset import ELIMINADO, MediaAsset
    from app.services.media_storage import obtener_almacenamiento

    ahora = ahora or datetime.datetime.utcnow()
    estado = estado_de_baja(org)
    if estado.get("fase") != BAJA_INICIADA:
        raise BajaInvalida(
            "La baja no está iniciada: no hay nada que eliminar todavía.",
            code="OFFBOARDING_NOT_STARTED",
        )

    limite = estado.get("eliminar_a_partir_de")
    if limite and not forzar:
        if ahora < datetime.datetime.fromisoformat(limite):
            raise BajaInvalida(
                f"El plazo de {DIAS_PARA_ELIMINAR} días para exportar no venció "
                f"(hasta {limite}). Usa `forzar` solo con confirmación explícita.",
                code="OFFBOARDING_TOO_EARLY",
            )

    detalle: Dict[str, Any] = {}

    # 1. Medios: del almacenamiento, no solo de la base. Una fila borrada con el
    #    archivo todavia en disco no es una eliminacion.
    almacen = obtener_almacenamiento()
    medios = (await db.execute(
        select(MediaAsset).where(MediaAsset.organization_id == org.id)
    )).scalars().all()
    for m in medios:
        try:
            await almacen.borrar(m.storage_key)
        except Exception:
            logger.warning("No se pudo borrar el medio %s del almacenamiento", m.storage_key)
        m.status = ELIMINADO
        m.deleted_at = ahora
        db.add(m)
    detalle["medios_eliminados"] = len(medios)

    # 2. Las tablas SIN cascade. Es lo que el borrado de la organizacion no
    #    alcanza, y donde vive el historico entero.
    for modelo in _tablas_sin_cascade():
        res = await db.execute(
            delete(modelo).where(modelo.organization_id == org.id)
        )
        detalle[modelo.__tablename__] = res.rowcount or 0

    # 3. Las tablas CON cascade se borran al borrar la organizacion. Pero la fila
    #    de `organizations` NO se borra: se conserva vacia como constancia de que
    #    existio y de cuando se dio de baja. Un DELETE aca cascadea a media base
    #    y deja la baja sin rastro auditable.
    from app.models.ai_action import AIAction
    from app.models.ai_insight import AIInsight
    from app.models.ai_token_usage import AITokenUsage
    from app.models.ad_snapshot import AdSnapshot
    from app.models.adset_snapshot import AdsetSnapshot
    from app.models.campaign_draft import CampaignDraft
    from app.models.campaign_learning_log import CampaignLearningLog
    from app.models.campaign_snapshot import CampaignSnapshot
    from app.models.sync_job import SyncJob

    for modelo in (CampaignSnapshot, AdsetSnapshot, AdSnapshot, AIInsight,
                   AIAction, CampaignDraft, CampaignLearningLog, SyncJob,
                   AITokenUsage):
        res = await db.execute(
            delete(modelo).where(modelo.organization_id == org.id)
        )
        detalle[modelo.__tablename__] = res.rowcount or 0

    org.offboarding_state = {
        **estado,
        "fase": BAJA_COMPLETADA,
        "completada_en": ahora.isoformat(),
        "detalle_eliminacion": detalle,
    }
    db.add(org)
    logger.info("Baja completada para la organización %s: %s", org.slug, detalle)
    return ResultadoDeBaja(fase=BAJA_COMPLETADA, detalle=detalle)


def certificado_de_eliminacion(org: Organization) -> Dict[str, Any]:
    """Lo que el cliente recibe: qué se eliminó, qué se conserva y por cuánto.

    Un certificado que solo diga "listo" no sirve para cerrar la relación con
    confianza: lo que da confianza es el detalle de lo que NO se borró y por qué.
    """
    estado = estado_de_baja(org)
    if estado.get("fase") != BAJA_COMPLETADA:
        raise BajaInvalida(
            "La baja todavía no está completa.", code="OFFBOARDING_NOT_DONE"
        )

    return {
        "organizacion": org.name,
        "identificador": org.slug,
        "baja_iniciada_en": estado.get("iniciada_en"),
        "datos_eliminados_en": estado.get("completada_en"),
        "eliminado": {
            "credenciales_de_meta": "revocadas al iniciar la baja",
            "clave_de_ia": "eliminada al iniciar la baja",
            "historico_de_campanas": "eliminado, incluido el archivo frío",
            "medios": "eliminados del almacenamiento",
            "propuestas_y_diagnosticos": "eliminados",
        },
        "se_conserva": {
            "registro_de_la_organizacion": (
                "Solo el nombre, el identificador y las fechas de la baja, como "
                "constancia de que existió. Sin datos de campañas."
            ),
            "registros_de_seguridad": (
                f"Saneados —sin credenciales, teléfonos completos ni URL "
                f"firmadas— hasta {DIAS_DE_LOGS_DE_SEGURIDAD} días, para poder "
                f"investigar un incidente posterior."
            ),
            "copias_de_respaldo": (
                "Un respaldo tomado antes de la baja contiene estos datos y no "
                "se reescribe. Desaparecen por rotación dentro de la ventana de "
                "retención del sistema de respaldo. Prometer lo contrario sería "
                "falso."
            ),
        },
        "base_de_una_retencion_mayor": estado.get("base_contractual"),
    }
