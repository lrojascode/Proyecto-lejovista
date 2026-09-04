"""Versionado de propuestas, autorización de un solo uso e idempotencia (spec 03).

Tres piezas que sostienen la garantía central: **una solicitud aprobada produce
como máximo una campaña real en Meta**, aunque haya reintentos, clics duplicados,
timeouts o respuestas inciertas.

1. `crear_version` — congela la configuración. Modificar algo material no edita
   la versión: crea la siguiente, e invalida la aprobación anterior.
2. `aprobar` / `consumir_aprobacion` — la autorización se liga a una versión y
   pasa a consumida en la MISMA operación que lanza la ejecución.
3. `registrar_intencion` / `confirmar` / `marcar_verificando` — el registro que
   precede a cada escritura real hacia Meta.
"""
import datetime
import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_proposal import (
    APPROVAL_CONSUMED,
    APPROVAL_PENDING,
    APPROVAL_SUPERSEDED,
    WRITE_CONFIRMED,
    WRITE_PENDING,
    WRITE_VERIFYING,
    CampaignApproval,
    CampaignProposalVersion,
    MetaWriteIntent,
)

logger = logging.getLogger("lejovista.proposal")


# Campos MATERIALES: los que describen qué campaña se va a crear y cuánto va a
# gastar. Cambiar cualquiera de estos invalida la aprobación anterior y obliga a
# recalcular presupuesto, riesgo y preflight.
#
# La lista es parte del CONTRATO, no un criterio de quien la lea: ampliarla es un
# cambio de comportamiento visible y se versiona con la spec. Lo que queda fuera
# —el nombre interno de la solicitud, las notas— se puede editar sin volver a
# pedir autorización, que es justamente lo que la hace utilizable.
CAMPOS_MATERIALES = (
    "ad_account_id",
    "page_id",
    "product_name",
    "budget",
    "budget_type",
    "days",
    "whatsapp_number",
    "objective",
    "image_url",
    "image_hash",
    "image_path",
    "video_id",
    "video_path",
    "thumbnail_url",
    "copy_text",
    "headline",
    "description",
    "excluded_placements",
    "best_age_range",
    "best_device",
)


class VersionInmutable(Exception):
    """Se intentó editar una versión ya creada."""


class AprobacionInvalida(Exception):
    """La autorización no existe, ya se consumió, o la invalidó un cambio.

    Lleva el motivo porque los tres casos se resuelven distinto: volver a
    aprobar, mirar el resultado de la primera ejecución, o revisar qué cambió.
    """

    def __init__(self, motivo: str, code: str):
        self.code = code
        super().__init__(motivo)

    def as_detail(self) -> dict:
        return {"code": self.code, "message": str(self)}


def huella_material(config: Dict[str, Any]) -> str:
    """SHA-256 de los campos materiales, en orden estable.

    Se serializa con `sort_keys` y separadores fijos para que dos diccionarios
    equivalentes den la misma huella independientemente del orden de inserción:
    si no, reordenar un JSON parecería un cambio material.
    """
    material = {k: config.get(k) for k in CAMPOS_MATERIALES}
    crudo = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(crudo.encode()).hexdigest()


def es_cambio_material(anterior: Dict[str, Any], nuevo: Dict[str, Any]) -> bool:
    return huella_material(anterior) != huella_material(nuevo)


async def crear_version(
    db: AsyncSession,
    *,
    draft_id: str,
    organization_id: str,
    ad_account_id: str,
    config: Dict[str, Any],
    risk_level: Optional[str] = None,
    gates: Optional[Dict[str, Any]] = None,
    created_by_user_id: Optional[str] = None,
    change_reason: Optional[str] = None,
) -> CampaignProposalVersion:
    """Congela `config` como la siguiente versión del Draft.

    Invalida toda autorización viva de versiones anteriores: es la mitad
    silenciosa de la historia 42 — sin esto, la aprobación de la versión 1
    seguiría siendo consumible después de crear la versión 2.
    """
    siguiente = (await db.execute(
        select(func.coalesce(func.max(CampaignProposalVersion.version_number), 0) + 1)
        .where(CampaignProposalVersion.draft_id == draft_id)
    )).scalar_one()

    await db.execute(
        update(CampaignApproval)
        .where(
            CampaignApproval.draft_id == draft_id,
            CampaignApproval.status == APPROVAL_PENDING,
        )
        .values(status=APPROVAL_SUPERSEDED, superseded_at=datetime.datetime.utcnow())
    )

    version = CampaignProposalVersion(
        draft_id=draft_id,
        organization_id=organization_id,
        ad_account_id=ad_account_id,
        version_number=siguiente,
        config_json=dict(config),
        material_fingerprint=huella_material(config),
        risk_level=risk_level,
        gates_json=gates or {},
        created_by_user_id=created_by_user_id,
        change_reason=change_reason,
    )
    db.add(version)
    await db.flush()
    return version


async def aprobar(
    db: AsyncSession, version: CampaignProposalVersion, *, user_id: Optional[str]
) -> CampaignApproval:
    """Concede una autorización sobre ESTA versión.

    Una por versión (restricción única): aprobar dos veces no puede producir dos
    autorizaciones consumibles.
    """
    existente = (await db.execute(
        select(CampaignApproval).where(CampaignApproval.version_id == version.id)
    )).scalar_one_or_none()
    if existente is not None:
        if existente.status == APPROVAL_PENDING:
            return existente
        raise AprobacionInvalida(
            "Esta versión ya tenía una autorización que no está vigente.",
            code="APPROVAL_NOT_PENDING",
        )

    aprobacion = CampaignApproval(
        version_id=version.id,
        draft_id=version.draft_id,
        organization_id=version.organization_id,
        approved_by_user_id=user_id,
    )
    db.add(aprobacion)
    await db.flush()
    return aprobacion


async def consumir_aprobacion(db: AsyncSession, approval_id: str) -> CampaignApproval:
    """Consume la autorización. Exactamente una vez.

    `UPDATE` condicional sobre el estado: la comprobación y la marca son una
    sola operación, así que dos ejecuciones simultáneas no pueden consumirla las
    dos. Que la segunda reciba un error y no una Saga es lo que impide la
    campaña duplicada.
    """
    res = await db.execute(
        update(CampaignApproval)
        .where(
            CampaignApproval.id == approval_id,
            CampaignApproval.status == APPROVAL_PENDING,
        )
        .values(status=APPROVAL_CONSUMED, consumed_at=datetime.datetime.utcnow())
    )
    if res.rowcount != 1:
        actual = await db.get(CampaignApproval, approval_id)
        if actual is None:
            raise AprobacionInvalida(
                "No existe esa autorización.", code="APPROVAL_NOT_FOUND"
            )
        if actual.status == APPROVAL_CONSUMED:
            raise AprobacionInvalida(
                "Esta autorización ya se usó para lanzar una creación.",
                code="APPROVAL_ALREADY_CONSUMED",
            )
        raise AprobacionInvalida(
            "Esta autorización quedó invalidada por un cambio posterior en la "
            "propuesta. Revisa la versión nueva y vuelve a aprobar.",
            code="APPROVAL_SUPERSEDED",
        )
    return await db.get(CampaignApproval, approval_id)


def clave_de_idempotencia(approval_id: str, operacion: str) -> str:
    """Clave lógica, estable y única, de una operación de escritura.

    Deriva de la AUTORIZACIÓN y no del Draft: es la autorización la que se
    consume una vez, y por tanto la que define qué conjunto de escrituras
    corresponde a esta ejecución.
    """
    return f"{approval_id}:{operacion}"


async def registrar_intencion(
    db: AsyncSession,
    *,
    approval_id: str,
    operacion: str,
    organization_id: str,
    ad_account_id: str,
    draft_id: Optional[str] = None,
    correlation_tag: Optional[str] = None,
) -> Tuple[MetaWriteIntent, bool]:
    """Registra la intención ANTES de emitir la escritura.

    Devuelve `(intencion, ya_confirmada)`. Cuando ya está confirmada, el llamador
    debe devolver el identificador que se guardó en vez de volver a escribir: eso
    es lo que convierte un reintento en una recuperación.
    """
    clave = clave_de_idempotencia(approval_id, operacion)
    existente = (await db.execute(
        select(MetaWriteIntent).where(MetaWriteIntent.idempotency_key == clave)
    )).scalar_one_or_none()

    if existente is not None:
        existente.attempts += 1
        if correlation_tag and not existente.correlation_tag:
            existente.correlation_tag = correlation_tag
        db.add(existente)
        await db.flush()
        return existente, existente.status == WRITE_CONFIRMED

    intencion = MetaWriteIntent(
        idempotency_key=clave,
        organization_id=organization_id,
        ad_account_id=ad_account_id,
        draft_id=draft_id,
        approval_id=approval_id,
        operation=operacion,
        status=WRITE_PENDING,
        correlation_tag=correlation_tag,
        attempts=1,
    )
    db.add(intencion)
    await db.flush()
    return intencion, False


async def confirmar_intencion(
    db: AsyncSession, intencion: MetaWriteIntent, external_id: str
) -> MetaWriteIntent:
    intencion.status = WRITE_CONFIRMED
    intencion.external_id = str(external_id)
    intencion.last_error = None
    db.add(intencion)
    await db.flush()
    return intencion


async def marcar_verificando(
    db: AsyncSession, intencion: MetaWriteIntent, error: str
) -> MetaWriteIntent:
    """Respuesta incierta: no se sabe si la escritura ocurrió.

    Es el estado que impide el reintento ciego. Salir de aquí exige reconciliar
    contra Meta, nunca repetir el POST y esperar suerte.
    """
    intencion.status = WRITE_VERIFYING
    intencion.last_error = (error or "")[:500]
    db.add(intencion)
    await db.flush()
    return intencion
