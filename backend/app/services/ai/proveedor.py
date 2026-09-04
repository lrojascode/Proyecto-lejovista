"""Proveedor y modelo EXPLÍCITOS por Organización (spec 10, criterios 8–10).

Deroga el mecanismo de selección de ADR-04. La portabilidad entre proveedores
compatibles se conserva —sigue siendo el motivo por el que existe un cliente
unificado— pero la elección deja de derivarse de qué credencial esté presente.

Qué hacía el mecanismo anterior, en orden:

1. si la Organización tenía credencial propia, deducía el proveedor **por la
   forma de la clave** (`sk-or-` → OpenRouter, `sk-` → OpenAI, `AIza` → Gemini);
2. si no, tomaba la primera credencial global que encontrara, en el orden
   OpenAI → Gemini → OpenRouter.

Las dos ramas son cambios silenciosos. En la primera, rotar la clave de un
cliente por una de otro proveedor cambiaba proveedor, endpoint y modelo sin que
nadie lo decidiera. En la segunda —la más cara— añadir `OPENAI_API_KEY` al
entorno del servidor movía a **todas** las organizaciones sin clave propia de
OpenRouter a OpenAI: otro modelo, otro precio, otro comportamiento, en el
siguiente reinicio y sin registro.

La regla ahora es una sola: **el proveedor es el que la Organización declara**.
La credencial se busca únicamente para ESE proveedor —primero la de la
Organización, si no la global— y si no está, la llamada falla de forma visible.
Nunca se usa la credencial de otro proveedor, ni siquiera cuando es la única
disponible.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_config_change import AIConfigChange
from app.models.organization import Organization

logger = logging.getLogger("lejovista.ai.proveedor")

PROVEEDORES = ("openai", "gemini_direct", "openrouter")

ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "gemini_direct": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


class ConfiguracionDeIAInvalida(Exception):
    """La Organización no puede llamar al modelo, y se dice por qué.

    Es un fallo VISIBLE a propósito. El comportamiento anterior ante lo mismo era
    resolver a otro proveedor o devolver texto de plantilla indefinidamente, con
    la pantalla de Configuración mostrando todo en orden. Eso costó un día entero
    de buscar el problema en el lugar equivocado.
    """

    code = "AI_CONFIG_INVALID"

    def __init__(self, mensaje: str, *, slug: str = "?"):
        self.slug = slug
        super().__init__(mensaje)

    def as_detail(self) -> dict:
        return {"code": self.code, "message": str(self)}


class ProveedorNoConfigurado(ConfiguracionDeIAInvalida):
    code = "AI_PROVIDER_NOT_SET"


class CredencialAusente(ConfiguracionDeIAInvalida):
    code = "AI_CREDENTIAL_MISSING"


@dataclass(frozen=True)
class ConfiguracionDeIA:
    provider: str
    model: str
    api_key: str
    base_url: str

    @property
    def is_gemini_direct(self) -> bool:
        return self.provider == "gemini_direct"

    @property
    def modelo_para_el_endpoint(self) -> str:
        """El nombre del modelo tal como lo espera el endpoint activo.

        Gemini directo no entiende el prefijo `google/` que usa OpenRouter para
        el mismo modelo. Es la única traducción, y vive acá para que no se
        repita en cada método del cliente — que era donde se olvidaba.
        """
        if self.is_gemini_direct and self.model.startswith("google/"):
            return self.model.replace("google/", "", 1)
        return self.model


def _credencial_global(provider: str) -> Optional[str]:
    """La credencial de entorno DE ESE proveedor. No hay cascada.

    Que la función tome el proveedor como argumento en vez de devolver "la
    primera que haya" es toda la diferencia entre esta spec y la anterior.
    """
    clave = {
        "openai": settings.OPENAI_API_KEY,
        "gemini_direct": settings.GEMINI_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }.get(provider) or ""
    return clave.strip() or None


def resolver(org: Optional[Organization]) -> ConfiguracionDeIA:
    """Proveedor, modelo y credencial efectivos de la Organización.

    Levanta en vez de adivinar. Cada excepción nombra qué falta y dónde
    cargarlo: un error que no dice eso obliga a leer el código para
    interpretarlo, y quien lo lee en producción es quien menos tiempo tiene.
    """
    slug = getattr(org, "slug", "?")

    provider = (getattr(org, "llm_provider", None) or "").strip()
    if not provider:
        raise ProveedorNoConfigurado(
            f"La organización '{slug}' no tiene proveedor de IA declarado. "
            "Elegí uno en Configuración: ya no se deduce de qué credencial esté "
            "cargada.",
            slug=slug,
        )
    if provider not in PROVEEDORES:
        raise ProveedorNoConfigurado(
            f"La organización '{slug}' declara el proveedor '{provider}', que no "
            f"existe. Los válidos son: {', '.join(PROVEEDORES)}.",
            slug=slug,
        )

    api_key = (org.get_llm_api_key() if org is not None else None) or _credencial_global(provider)
    if not api_key:
        raise CredencialAusente(
            f"La organización '{slug}' usa el proveedor '{provider}' y no hay "
            f"credencial para ese proveedor —ni propia ni global—. No se usa la "
            f"credencial de otro proveedor: cargá la de '{provider}' en "
            f"Configuración.",
            slug=slug,
        )

    modelo = (getattr(org, "ai_model", None) or "").strip() or settings.DEFAULT_AI_MODEL
    return ConfiguracionDeIA(
        provider=provider,
        model=modelo,
        api_key=api_key,
        base_url=ENDPOINTS[provider],
    )


def resolver_o_none(org: Optional[Organization]) -> Optional[ConfiguracionDeIA]:
    """Como `resolver`, pero devuelve `None` en vez de levantar.

    Para los caminos que ya sabían convivir con "no hay LLM" y caen a una
    respuesta determinista. El fallo se registra igual: silencioso para el
    usuario no significa silencioso para el operador.
    """
    try:
        return resolver(org)
    except ConfiguracionDeIAInvalida as e:
        logger.error("IA no disponible para '%s': %s", e.slug, e)
        return None


async def registrar_cambio(
    db: AsyncSession,
    org: Organization,
    *,
    campo: str,
    valor_anterior: Optional[str],
    valor_nuevo: Optional[str],
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    origen: str = "ui",
    motivo: Optional[str] = None,
) -> Optional[AIConfigChange]:
    """Deja constancia de un cambio de configuración de IA (criterio 10).

    Un cambio que no altera nada no se registra: llenar la bitácora de filas
    donde el valor anterior y el nuevo coinciden hace más difícil encontrar el
    cambio que sí importó.

    La credencial se registra como el HECHO de que cambió, nunca su valor.
    """
    anterior = None if valor_anterior is None else str(valor_anterior)
    nuevo = None if valor_nuevo is None else str(valor_nuevo)
    if anterior == nuevo:
        return None

    if campo == "llm_api_key":
        anterior = "(credencial anterior)" if anterior else None
        nuevo = "(credencial nueva)" if nuevo else None

    fila = AIConfigChange(
        organization_id=org.id,
        campo=campo,
        valor_anterior=anterior,
        valor_nuevo=nuevo,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        origen=origen,
        motivo=motivo,
    )
    db.add(fila)
    logger.info(
        "IA: '%s' cambió %s de %r a %r (actor=%s, origen=%s).",
        org.slug, campo, anterior, nuevo, actor_email or actor_user_id or "sistema", origen,
    )
    return fila


def proveedor_inicial() -> Optional[str]:
    """El proveedor con el que nace una Organización nueva.

    Fijarlo al crearla, y no resolverlo en cada llamada, es lo que hace la
    diferencia: la elección queda escrita en el dato el día del alta y deja de
    depender de qué credencial haya en el entorno en el momento de cada llamada.
    Un default de configuración escrito una vez no es un cambio silencioso; una
    cascada evaluada en cada arranque sí lo era.
    """
    elegido = (settings.DEFAULT_AI_PROVIDER or "").strip()
    if not elegido:
        return None
    if elegido not in PROVEEDORES:
        logger.error(
            "DEFAULT_AI_PROVIDER=%r no es un proveedor válido (%s). Las "
            "organizaciones nuevas nacen sin proveedor declarado.",
            elegido, ", ".join(PROVEEDORES),
        )
        return None
    return elegido
