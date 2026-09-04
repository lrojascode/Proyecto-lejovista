"""Alta asistida de una Organizacion (spec 05).

El alta era manual y desordenada: la credencial de Meta, la clave de IA y los
numeros de WhatsApp se cargaban por separado, sin nada que confirmara que la
Organizacion estaba lista antes de invitar al cliente. El precedente conocido es
una clave de IA guardada en la organizacion equivocada, con el analista
contestando con plantillas durante dias sin que nada pareciera roto.

Es una **secuencia con estado**, no un formulario. Cada paso se valida contra el
sistema externo REAL —no se comprueba que el campo este lleno, se comprueba que
funcione— y la invitacion solo se puede emitir cuando todos estan resueltos.

Un paso puede marcarse OMITIDO con un motivo. Queda registrado y visible: es
distinto de "validado", y hacerlo explicito evita la tentacion de dejar el hueco
en silencio.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization

logger = logging.getLogger("lejovista.onboarding")

PASO_META = "meta"
PASO_IA = "ia"
PASO_WHATSAPP = "whatsapp"

# Los tres que tienen que estar resueltos antes de invitar. El producto vende
# campañas de mensajeria a WhatsApp: sin cuenta de Meta no hay datos, sin clave
# de IA el analista contesta con plantillas, y sin numero no hay a donde mandar
# las conversaciones.
PASOS_REQUERIDOS = (PASO_META, PASO_IA, PASO_WHATSAPP)

ESTADO_VALIDADO = "validado"
ESTADO_OMITIDO = "omitido"


class PasoNoValidado(Exception):
    """Un paso del alta fallo contra el sistema externo."""

    def __init__(self, paso: str, motivo: str):
        self.paso = paso
        super().__init__(motivo)

    def as_detail(self) -> dict:
        return {"code": "ONBOARDING_STEP_FAILED", "paso": self.paso, "message": str(self)}


class AltaIncompleta(Exception):
    """Faltan pasos por resolver: no se puede invitar todavia."""

    def __init__(self, faltantes: List[str]):
        self.faltantes = faltantes
        super().__init__(
            "El alta no esta completa. Faltan: " + ", ".join(faltantes)
        )

    def as_detail(self) -> dict:
        return {
            "code": "ONBOARDING_INCOMPLETE",
            "message": (
                "No se puede invitar al cliente todavía: faltan pasos del alta "
                "por validar (" + ", ".join(self.faltantes) + "). Un cliente que "
                "entra a una organización a medio configurar ve un producto que "
                "no funciona y no sabe por qué."
            ),
            "faltantes": self.faltantes,
        }


def pasos_faltantes(org: Organization) -> List[str]:
    estado = org.onboarding_state or {}
    return [
        paso for paso in PASOS_REQUERIDOS
        if (estado.get(paso) or {}).get("estado") not in (ESTADO_VALIDADO, ESTADO_OMITIDO)
    ]


def alta_completa(org: Organization) -> bool:
    return not pasos_faltantes(org)


def registrar_paso(
    org: Organization, paso: str, *, estado: str, detalle: str, actor_id: Optional[str]
) -> None:
    """Anota el resultado de un paso. Con actor y momento: sin eso no es auditable."""
    actual = dict(org.onboarding_state or {})
    actual[paso] = {
        "estado": estado,
        "detalle": detalle,
        "actor": actor_id,
        "en": datetime.datetime.utcnow().isoformat(),
    }
    org.onboarding_state = actual


async def validar_meta(db: AsyncSession, org: Organization) -> str:
    """La credencial de Meta lista cuentas de verdad.

    No alcanza con que el token este guardado: un token invalido se guarda igual
    de bien que uno bueno, y la diferencia solo aparece en el primer sync — con
    el cliente ya dentro.
    """
    from app.models.ad_account import AdAccount
    from app.models.meta_credential import MetaCredential

    cred = (await db.execute(
        select(MetaCredential).where(
            MetaCredential.organization_id == org.id,
            MetaCredential.status == "ACTIVE",
        )
    )).scalars().first()
    if cred is None:
        raise PasoNoValidado(PASO_META, "La organización no tiene ninguna credencial de Meta activa.")
    if not cred.get_access_token():
        raise PasoNoValidado(PASO_META, "La credencial de Meta no se puede descifrar con la ENCRYPTION_KEY actual.")

    cuentas = (await db.execute(
        select(AdAccount).where(
            AdAccount.organization_id == org.id,
            AdAccount.status != "ARCHIVED",
        )
    )).scalars().all()
    if not cuentas:
        raise PasoNoValidado(PASO_META, "No hay ninguna cuenta publicitaria conectada.")

    sin_moneda = [c.name for c in cuentas if not c.currency]
    if sin_moneda:
        # La moneda gobierna la compuerta de presupuesto mensual (spec 02): sin
        # ella el límite no se puede evaluar.
        raise PasoNoValidado(
            PASO_META,
            f"Estas cuentas no tienen moneda conocida (falta sincronizarlas): {', '.join(sin_moneda)}.",
        )
    return f"{len(cuentas)} cuenta(s) conectada(s) en {cuentas[0].currency}"


async def validar_ia(db: AsyncSession, org: Organization) -> str:
    """La clave de IA existe, es de esta Organizacion y se puede descifrar."""
    if not org.has_llm_api_key:
        raise PasoNoValidado(PASO_IA, "La organización no tiene clave de IA configurada.")
    clave = org.get_llm_api_key()
    if not clave:
        raise PasoNoValidado(PASO_IA, "La clave de IA no se puede descifrar con la ENCRYPTION_KEY actual.")
    if not org.llm_provider:
        # Sin proveedor declarado, el sistema tendría que adivinarlo por qué
        # clave está presente — que es exactamente lo que la spec 10 prohíbe.
        raise PasoNoValidado(
            PASO_IA,
            "Falta declarar el proveedor de IA. Sin él, el proveedor se elegiría solo y podría cambiar sin que nadie lo decida.",
        )

    # El modelo tiene que ser elegible HOY en el catálogo del proveedor activo
    # (spec 10, criterios 11 y 12). Dar por completo un alta con un modelo
    # retirado deja al cliente con la IA muerta desde el primer día y sin
    # ninguna señal: la clave está, el proveedor está, y todas las llamadas
    # fallan con un 404 que no dice cuál es el problema.
    from app.services.ai import catalogo

    try:
        await catalogo.assert_elegible(db, org.llm_provider, org.ai_model)
    except catalogo.ModeloNoElegible as e:
        raise PasoNoValidado(
            PASO_IA,
            f"El modelo '{org.ai_model}' no se puede usar con el proveedor "
            f"'{org.llm_provider}': {e.motivo}. Elegí otro en Configuración.",
        ) from e

    return f"proveedor {org.llm_provider}, modelo {org.ai_model}"


async def validar_whatsapp(db: AsyncSession, org: Organization) -> str:
    """Hay al menos un numero de WhatsApp, y alcanza a alguna cuenta."""
    from app.models.whatsapp_number import WhatsAppNumber
    from app.models.whatsapp_number_ad_account import WhatsAppNumberAdAccount

    numeros = (await db.execute(
        select(WhatsAppNumber).where(WhatsAppNumber.organization_id == org.id)
    )).scalars().all()
    if not numeros:
        raise PasoNoValidado(PASO_WHATSAPP, "La organización no tiene ningún número de WhatsApp.")

    enlazados = (await db.execute(
        select(WhatsAppNumberAdAccount.whatsapp_number_id).where(
            WhatsAppNumberAdAccount.organization_id == org.id
        )
    )).scalars().all()
    if not enlazados:
        # Un número que no alcanza a ninguna cuenta no aparece en el desplegable
        # de creación: existe y no sirve.
        raise PasoNoValidado(
            PASO_WHATSAPP,
            "Hay números cargados pero ninguno está enlazado a una cuenta publicitaria.",
        )
    return f"{len(numeros)} número(s), {len(set(enlazados))} enlazado(s) a cuentas"


VALIDADORES = {
    PASO_META: validar_meta,
    PASO_IA: validar_ia,
    PASO_WHATSAPP: validar_whatsapp,
}
