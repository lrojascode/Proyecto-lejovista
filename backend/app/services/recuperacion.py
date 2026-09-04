"""Recuperación de campañas comparables y trazabilidad de la propuesta (spec 11).

La capa con base de datos. La aritmética vive en `comparabilidad.py` y
`evidencia.py`, que son puros; acá está la consulta y la persistencia.

**El aislamiento no es una preferencia de calidad, es la regla.** La
recuperación se restringe a la Organización y a la AdAccount activas. No hay
conjunto global ni "aprendizajes del rubro" derivados de otros clientes, y no
hay excepción para el caso en que el usuario tenga acceso a las dos cuentas.
ADR-11 deja de ser solo aislamiento de lectura y pasa a ser también aislamiento
de derivación de conocimiento: dos cuentas de la misma Organización tampoco se
mezclan, porque su histórico describe negocios distintos aunque la factura sea
la misma.

Se lee de las tablas calientes y del archivo (criterio 16). Que una campaña
archivada pueda seguir siendo evidencia es el punto: si no, el producto pierde
su propuesta de valor a medida que envejece.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.proposal_evidence import (
    DIAS_DE_ESPERA_TRAS_RECHAZO,
    MOTIVOS_DE_CAMBIO,
    MOTIVOS_LEGIBLES,
    MOTIVOS_QUE_EXIGEN_TEXTO,
    REGLA_ACEPTADA,
    REGLA_PROPUESTA,
    REGLA_RECHAZADA,
    REPETICIONES_PARA_PROPONER,
    CandidateRule,
    ProposalChange,
    ProposalEvidence,
)
from app.services import evidencia as evidencia_mod
from app.services.baseline import calcular_baseline
from app.services.baseline_repository import VENTANA_BASELINE_DIAS, campanas_agregadas
from app.services.comparabilidad import conjunto_comparable

logger = logging.getLogger("lejovista.recuperacion")

# Cuántas campañas comparables se muestran como evidencia. El conjunto entero
# se usa para calcular baseline y confianza; lo que se EXHIBE se acota, porque
# una lista de ochenta campañas no se lee y equivale a no mostrar ninguna.
MAX_CAMPANAS_EXHIBIDAS = 12


class MotivoRequerido(ValueError):
    """Se intentó modificar una propuesta sin decir por qué (criterio 11)."""

    code = "CHANGE_REASON_REQUIRED"

    def as_detail(self) -> dict:
        return {"code": self.code, "message": str(self)}


async def recuperar_comparables(
    db: AsyncSession,
    ad_account: AdAccount,
    referencia: Dict[str, Any],
    *,
    ventana_dias: int = VENTANA_BASELINE_DIAS,
    hasta: Optional[datetime.date] = None,
    limite_exhibido: int = MAX_CAMPANAS_EXHIBIDAS,
) -> evidencia_mod.Evidencia:
    """El conjunto comparable de ESTA cuenta, con su confianza.

    `referencia` describe la campaña que se propone: objetivo, evento de
    conversión, gasto previsto y fecha. Es lo que se compara contra el
    histórico.

    La consulta filtra por `ad_account_id`, que es lo que sostiene los criterios
    2 y 3. No se pasa `organization_id` además porque la AdAccount ya pertenece
    a una sola Organización; filtrar por las dos daría una falsa sensación de
    doble cerrojo donde solo hay uno, y el cerrojo real es que este parámetro
    venga de la cuenta activa y no del cliente.
    """
    hasta = hasta or datetime.date.today()
    desde = hasta - datetime.timedelta(days=ventana_dias)

    candidatas = await campanas_agregadas(
        db, ad_account.id, desde=desde, hasta=hasta,
    )

    incluidas, excluidas = conjunto_comparable(referencia, candidatas)

    afirmaciones = (
        _afirmaciones_del_conjunto(incluidas)
        if incluidas else evidencia_mod.afirmaciones_exploratorias()
    )

    return evidencia_mod.construir(
        incluidas[:limite_exhibido] if limite_exhibido else incluidas,
        excluidas,
        ventana_dias=ventana_dias,
        afirmaciones=afirmaciones,
    )


def _afirmaciones_del_conjunto(
    incluidas: Sequence[Tuple[Dict[str, Any], Any]],
) -> Tuple[evidencia_mod.Afirmacion, ...]:
    """Lo que se puede decir del conjunto, con su registro marcado.

    Deliberadamente escueto y deliberadamente sin causalidad. "Las campañas de
    verano costaron menos" es una asociación; "el verano abarata las campañas"
    sería causalidad, y para afirmarla haría falta una prueba controlada que
    este alcance no construye.
    """
    if not incluidas:
        return evidencia_mod.afirmaciones_exploratorias()

    costos = []
    for campana, _ in incluidas:
        resultados = int(campana.get("conversaciones") or 0) + int(campana.get("leads") or 0)
        gasto = float(campana.get("gasto") or 0)
        if resultados > 0 and gasto > 0:
            costos.append(gasto / resultados)

    afirmaciones = [
        evidencia_mod.hecho(
            f"Se encontraron {len(incluidas)} campaña(s) anterior(es) de esta cuenta "
            "con el mismo objetivo y el mismo tipo de resultado."
        )
    ]

    if costos:
        base = calcular_baseline([
            {
                "gasto": float(c.get("gasto") or 0),
                "conversaciones": int(c.get("conversaciones") or 0),
                "leads": int(c.get("leads") or 0),
                "dias": int(c.get("dias") or 0),
            }
            for c, _ in incluidas
        ])
        if base.hay_baseline:
            afirmaciones.append(evidencia_mod.hecho(
                f"La mediana de costo por resultado de ese conjunto es "
                f"{base.mediana:.2f}."
            ))
        if len(costos) >= 2:
            afirmaciones.append(evidencia_mod.asociacion(
                f"Los costos de esas campañas fueron entre {min(costos):.2f} y "
                f"{max(costos):.2f} por resultado."
            ))

    mejor = incluidas[0][0]
    if mejor.get("nombre"):
        afirmaciones.append(evidencia_mod.asociacion(
            f"La campaña histórica más parecida a esta propuesta es "
            f"'{mejor['nombre']}'."
        ))

    return tuple(afirmaciones)


async def persistir_evidencia(
    db: AsyncSession,
    *,
    organization_id: str,
    ad_account_id: Optional[str],
    evidencia: evidencia_mod.Evidencia,
    baseline_congelado: Dict[str, Any],
    draft_id: Optional[str] = None,
    version_id: Optional[str] = None,
    reglas_version: Optional[str] = None,
    prompt_version: Optional[str] = None,
    modelo: Optional[str] = None,
    proveedor: Optional[str] = None,
) -> ProposalEvidence:
    """Fija la trazabilidad en el momento de PROPONER (criterio 10).

    Al proponer y no al crear: una propuesta rechazada tiene que dejar rastro, y
    las rechazadas son justamente las que conviene revisar cuando se quiere
    saber por qué el sistema recomienda mal.
    """
    fila = ProposalEvidence(
        organization_id=organization_id,
        ad_account_id=ad_account_id,
        draft_id=draft_id,
        version_id=version_id,
        campanas_fuente=[dict(c) for c in evidencia.campanas],
        baseline_json=baseline_congelado,
        confianza_json=evidencia.confianza.as_dict(),
        afirmaciones_json=[a.as_dict() for a in evidencia.afirmaciones],
        excluidas_json=evidencia.as_dict()["excluidas_por_motivo"],
        ventana_dias=evidencia.ventana_dias,
        campanas_comparables=evidencia.confianza.campanas,
        nivel_confianza=evidencia.confianza.nivel,
        exploratoria=1 if evidencia.exploratoria else 0,
        reglas_version=reglas_version,
        prompt_version=prompt_version,
        modelo=modelo,
        proveedor=proveedor,
    )
    db.add(fila)
    await db.flush()
    return fila


# --- Cambio humano ----------------------------------------------------------

def validar_motivo(motivo: Optional[str], detalle: Optional[str]) -> Tuple[str, Optional[str]]:
    """El motivo es obligatorio y tiene que estar en el conjunto (criterio 11).

    `otro` exige texto. Sin esa válvula, quien no encuentre su motivo elegirá el
    más parecido y el histórico quedará contaminado con correcciones mal
    clasificadas — que es peor que no tener el dato, porque parece que se tiene.
    """
    limpio = (motivo or "").strip()
    if not limpio:
        raise MotivoRequerido(
            "Para guardar un cambio en la propuesta hay que decir por qué se "
            "cambia. Sin el motivo, la corrección no enseña nada."
        )
    if limpio not in MOTIVOS_DE_CAMBIO:
        raise MotivoRequerido(
            f"'{limpio}' no es un motivo válido. Los válidos son: "
            f"{', '.join(MOTIVOS_DE_CAMBIO)}."
        )
    texto = (detalle or "").strip() or None
    if limpio in MOTIVOS_QUE_EXIGEN_TEXTO and not texto:
        raise MotivoRequerido(
            "Al elegir 'otro motivo' hay que escribir cuál: es lo que evita que "
            "el resto de motivos se llene de correcciones mal clasificadas."
        )
    return limpio, texto


def diff_de_configuracion(
    anterior: Dict[str, Any], nueva: Dict[str, Any], campos: Sequence[str]
) -> Dict[str, Dict[str, Any]]:
    """Campo → {antes, despues}, solo para lo que realmente cambió."""
    diff: Dict[str, Dict[str, Any]] = {}
    for campo in campos:
        antes, despues = anterior.get(campo), nueva.get(campo)
        if antes != despues:
            diff[campo] = {"antes": antes, "despues": despues}
    return diff


async def registrar_cambio_humano(
    db: AsyncSession,
    *,
    organization_id: str,
    ad_account_id: Optional[str],
    motivo: str,
    motivo_detalle: Optional[str] = None,
    cambios: Optional[Dict[str, Any]] = None,
    draft_id: Optional[str] = None,
    version_anterior_id: Optional[str] = None,
    version_nueva_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> ProposalChange:
    """Deja constancia de una corrección humana. Valida el motivo primero."""
    motivo, detalle = validar_motivo(motivo, motivo_detalle)

    fila = ProposalChange(
        organization_id=organization_id,
        ad_account_id=ad_account_id,
        draft_id=draft_id,
        version_anterior_id=version_anterior_id,
        version_nueva_id=version_nueva_id,
        motivo=motivo,
        motivo_detalle=detalle,
        cambios_json=cambios or {},
        actor_user_id=actor_user_id,
        actor_email=actor_email,
    )
    db.add(fila)
    await db.flush()
    return fila


# --- Reglas candidatas ------------------------------------------------------

async def detectar_reglas_candidatas(
    db: AsyncSession,
    organization_id: str,
    *,
    ad_account_id: Optional[str] = None,
    momento: Optional[datetime.datetime] = None,
) -> List[CandidateRule]:
    """Propone como regla los motivos de corrección que se repiten (criterio 14).

    **Propone, no aplica.** La regla no entra en vigor hasta que una persona la
    acepta. Convertir correcciones en política automáticamente sobreajusta a la
    última opinión y hace el comportamiento del sistema impredecible para quien
    lo opera.

    Un motivo rechazado no se vuelve a proponer durante
    `DIAS_DE_ESPERA_TRAS_RECHAZO`. Reproponerlo al día siguiente lo convierte en
    ruido y garantiza que se dejen de leer también las buenas.
    """
    ahora = momento or datetime.datetime.utcnow()

    filtro = [ProposalChange.organization_id == organization_id]
    if ad_account_id:
        filtro.append(ProposalChange.ad_account_id == ad_account_id)

    conteos = (await db.execute(
        select(ProposalChange.motivo, func.count().label("veces"))
        .where(*filtro)
        .group_by(ProposalChange.motivo)
        .having(func.count() >= REPETICIONES_PARA_PROPONER)
    )).all()

    if not conteos:
        return []

    existentes = {
        (r.motivo, r.status, r.resuelta_en): r
        for r in (await db.execute(
            select(CandidateRule).where(CandidateRule.organization_id == organization_id)
        )).scalars().all()
    }
    por_motivo: Dict[str, List[CandidateRule]] = {}
    for regla in existentes.values():
        por_motivo.setdefault(regla.motivo, []).append(regla)

    nuevas: List[CandidateRule] = []
    for motivo, veces in conteos:
        previas = por_motivo.get(motivo, [])

        # Ya hay una propuesta abierta: no se duplica.
        if any(r.status == REGLA_PROPUESTA for r in previas):
            continue
        # Ya se aceptó: la regla existe, no hay nada que proponer.
        if any(r.status == REGLA_ACEPTADA for r in previas):
            continue
        # Se rechazó hace poco: se respeta el plazo.
        rechazos = [r for r in previas if r.status == REGLA_RECHAZADA and r.resuelta_en]
        if rechazos:
            ultimo = max(r.resuelta_en for r in rechazos)
            if (ahora - ultimo).days < DIAS_DE_ESPERA_TRAS_RECHAZO:
                logger.info(
                    "El motivo '%s' se repite pero su regla se rechazó hace %d día(s); "
                    "no se vuelve a proponer todavía.",
                    motivo, (ahora - ultimo).days,
                )
                continue

        ocurrencias = (await db.execute(
            select(ProposalChange)
            .where(*filtro, ProposalChange.motivo == motivo)
            .order_by(desc(ProposalChange.created_at))
            .limit(20)
        )).scalars().all()

        regla = CandidateRule(
            organization_id=organization_id,
            ad_account_id=ad_account_id,
            motivo=motivo,
            propuesta=(
                f"«{MOTIVOS_LEGIBLES.get(motivo, motivo)}» se corrigió a mano "
                f"{veces} veces en esta cuenta. Conviene revisar si el sistema "
                "debería proponerlo así de entrada."
            ),
            ocurrencias_json=[
                {
                    "change_id": o.id,
                    "draft_id": o.draft_id,
                    "detalle": o.motivo_detalle,
                    "cambios": o.cambios_json,
                    "actor": o.actor_email,
                    "fecha": o.created_at.isoformat() if o.created_at else None,
                }
                for o in ocurrencias
            ],
            ocurrencias=int(veces),
        )
        db.add(regla)
        nuevas.append(regla)

    if nuevas:
        await db.flush()
    return nuevas


async def resolver_regla_candidata(
    db: AsyncSession,
    regla: CandidateRule,
    *,
    aceptar: bool,
    actor_user_id: Optional[str] = None,
    nota: Optional[str] = None,
    momento: Optional[datetime.datetime] = None,
) -> CandidateRule:
    """Acepta o rechaza una regla candidata. Las dos dejan constancia.

    El rechazo se registra tanto como la aceptación: sin él no se puede
    respetar el plazo de espera, y la propuesta volvería en el siguiente ciclo.
    """
    if regla.status != REGLA_PROPUESTA:
        raise ValueError(
            f"La regla candidata ya está {regla.status}; no se puede volver a resolver."
        )
    regla.status = REGLA_ACEPTADA if aceptar else REGLA_RECHAZADA
    regla.resuelta_por_user_id = actor_user_id
    regla.resuelta_en = momento or datetime.datetime.utcnow()
    regla.nota_de_resolucion = (nota or "").strip() or None
    await db.flush()
    return regla
