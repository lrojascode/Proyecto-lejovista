"""Version de la Graph/Marketing API de Meta: fuente unica de verdad (spec 08).

Produccion usaba v26 mientras el valor predeterminado del codigo, el fichero de
ejemplo de entorno, la plantilla de despliegue y seis documentos decian v25.
Cualquier despliegue que no inyectara la variable correcta —o cualquier entorno
nuevo levantado desde la plantilla— volvia a v25 **en silencio**. Meta retira
cada version a los ~2 anos, y cuando eso pasa fallan TODAS las llamadas de TODOS
los clientes a la vez.

Tres mecanismos, y ninguno sustituye a los otros:

1. **Una fuente de verdad replicada mecanicamente.** `VERSION_SOPORTADA` es el
   valor; el resto de sitios donde aparece se comprueban contra el en CI. Sin esa
   comprobacion el problema reaparece en la siguiente migracion.
2. **La version que Meta declara haber ATENDIDO** se lee de la respuesta y se
   contrasta. Es la unica forma de detectar que una llamada esta cayendo a una
   version distinta de la pedida.
3. **Una version candidata por Organizacion**, para migrar de forma progresiva
   con rollback sin desplegar.
"""
import datetime
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("lejovista.meta_version")

# LA fuente de verdad. Todo lo demas se deriva o se verifica contra esto.
VERSION_SOPORTADA = "v26.0"

# Cuando Meta retira esta version. Se vigila con antelacion para poder planificar
# una migracion, no para reaccionar a un corte.
# https://developers.facebook.com/docs/graph-api/changelog
EXPIRACION_VERSION_SOPORTADA = datetime.date(2028, 1, 1)

# Con cuanta antelacion se empieza a avisar.
DIAS_DE_AVISO_ANTICIPADO = 120

# La version anterior sigue siendo un valor ADMITIDO mientras dure una migracion:
# volver atras tiene que ser cambiar una variable, no construir y publicar una
# imagen.
VERSIONES_ADMITIDAS = (VERSION_SOPORTADA, "v25.0", "v27.0")

# Cabecera con la que Meta declara que version atendio realmente.
CABECERA_VERSION_ATENDIDA = "facebook-api-version"

_FORMATO = re.compile(r"^v\d+\.\d+$")


def es_version_valida(version: str) -> bool:
    return bool(_FORMATO.match(version or ""))


def dias_hasta_la_expiracion(hoy: Optional[datetime.date] = None) -> int:
    return (EXPIRACION_VERSION_SOPORTADA - (hoy or datetime.date.today())).days


def expiracion_proxima(hoy: Optional[datetime.date] = None) -> bool:
    return dias_hasta_la_expiracion(hoy) <= DIAS_DE_AVISO_ANTICIPADO


def version_para(organizacion: Any = None) -> str:
    """La version que debe usar esta Organizacion.

    Una version candidata se activa por Organizacion, no globalmente: la cuenta
    piloto ejercita la nueva mientras el resto sigue en la vigente. Conviven DOS
    versiones a la vez y ninguna mas — mantener tres multiplica los caminos sin
    aportar seguridad.

    Un valor invalido o no admitido cae a la soportada y lo registra: quedarse
    sin version es peor que ignorar una configuracion equivocada.
    """
    from app.config import settings

    candidata = (getattr(organizacion, "meta_api_version_candidata", None) or "").strip()
    if candidata:
        if candidata in VERSIONES_ADMITIDAS:
            return candidata
        logger.warning(
            "La organización %s pide la versión candidata %r, que no está en "
            "VERSIONES_ADMITIDAS. Se usa %s.",
            getattr(organizacion, "slug", "?"), candidata, VERSION_SOPORTADA,
        )

    configurada = (settings.META_API_VERSION or "").strip()
    if configurada and es_version_valida(configurada):
        return configurada
    logger.warning(
        "META_API_VERSION=%r no es una versión válida; se usa %s.",
        configurada, VERSION_SOPORTADA,
    )
    return VERSION_SOPORTADA


def registrar_version_atendida(headers: Any, version_pedida: str) -> Optional[str]:
    """Lee la version que Meta dice haber atendido y avisa si no coincide.

    Meta declara en cada respuesta con que version sirvio. Una divergencia
    sostenida significa que las llamadas estan cayendo a otra version, y eso solo
    se nota cuando un campo desaparece — con el cliente ya afectado.

    Devuelve la version atendida, o `None` si la respuesta no la trae.
    """
    try:
        atendida = headers.get(CABECERA_VERSION_ATENDIDA)
    except Exception:
        return None
    if not atendida:
        return None
    if atendida != version_pedida:
        logger.warning(
            "DIVERGENCIA de versión de Meta: se pidió %s y respondió %s. "
            "Si se sostiene, las llamadas están cayendo a una versión distinta "
            "de la configurada.",
            version_pedida, atendida,
        )
    return atendida
