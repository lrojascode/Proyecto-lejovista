"""Saneado de los registros, aplicado en la ESCRITURA (spec 07, y base de la 09).

Un filtro de `logging` que se instala en la raiz y por el que pasa TODO lo que
se registra, incluido lo que emiten librerias de terceros (httpx, uvicorn,
sqlalchemy). Es deliberado que sea central: confiar en que cada punto de llamada
recuerde no registrar un secreto garantiza que alguna vez se olvide, y el que se
olvide sera el que importa.

Lo que se borra son credenciales y datos personales. Lo que se CONSERVA son
identificadores internos, codigos de error, tiempos y metadatos minimos: sin eso
no se puede investigar un incidente, y un log que no sirve para investigar es
tan malo como uno que filtra.
"""
import logging
import re

REDACTADO = "[REDACTADO]"

# Cada patron captura el prefijo que identifica el dato y sustituye SOLO el
# valor: se conserva la forma del mensaje, que es lo que lo hace legible.
_PATRONES = (
    # URL firmadas de medios y tickets de WebSocket en query strings.
    (re.compile(r"([?&](?:token|ticket|signature|sig|access_token)=)[^\s&\"'#]+"), r"\1" + REDACTADO),
    # Cabeceras de autorizacion.
    (re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.I), r"\1" + REDACTADO),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{20,}"), r"\1" + REDACTADO),
    # JWT sueltos: tres bloques base64url separados por puntos.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"), REDACTADO),
    # Claves de proveedores de IA.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), REDACTADO),
    # Tokens de Meta.
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), REDACTADO),
    # Cookies de sesion.
    (re.compile(r"(refresh_token=)[^\s;,\"']+"), r"\1" + REDACTADO),
    # Telefonos completos: se deja el prefijo del pais y los ultimos dos digitos,
    # que es lo minimo para reconocer de cual se habla sin poder marcarlo.
    # El movil peruano son NUEVE digitos (9XXXXXXXX), asi que con prefijo son
    # once: un patron de siete dejaba pasar el numero entero.
    (re.compile(r"\b(\+?51)(\d{5,10})(\d{2})\b"), r"\1*****\3"),
    # Y sin prefijo de pais, tal como se escriben en el producto.
    (re.compile(r"\b(9)(\d{5,7})(\d{2})\b"), r"\1*****\3"),
)


def sanear(texto: str) -> str:
    for patron, reemplazo in _PATRONES:
        texto = patron.sub(reemplazo, texto)
    return texto


def _sanear_arg(valor):
    """Sanea un argumento de formateo, sea o no una cadena.

    Los argumentos NO son siempre cadenas: `httpx` registra la URL como objeto
    `URL` y solo se convierte a texto al formatear el mensaje — es decir, DESPUES
    de cualquier saneado que mire solo las cadenas. Por ahi se escapaba la URL
    firmada de un medio, entera, al log de peticiones.

    Un objeto solo se sustituye por su forma saneada cuando su texto CONTIENE
    algo que hay que borrar. Convertir todo a cadena romperia un `%d` con un
    entero, y no hay razon para tocar lo que no filtra nada.
    """
    if isinstance(valor, str):
        return sanear(valor)
    if isinstance(valor, (int, float, bool, type(None))):
        return valor
    try:
        texto = str(valor)
    except Exception:
        return valor
    saneado = sanear(texto)
    return saneado if saneado != texto else valor


def _sanear_record(record: logging.LogRecord) -> logging.LogRecord:
    """Reescribe el mensaje y los argumentos de un registro."""
    try:
        if isinstance(record.msg, str):
            record.msg = sanear(record.msg)
        elif record.msg is not None:
            record.msg = _sanear_arg(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _sanear_arg(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_sanear_arg(a) for a in record.args)
    except Exception:
        # Un fallo del saneador no puede tumbar el registro: se prefiere un
        # mensaje sin sanear a perder la traza de un incidente. Que el fallo sea
        # sistematico lo detecta la prueba, no produccion.
        pass
    return record


class FiltroDeSecretos(logging.Filter):
    """Version como filtro, para instalarla en un handler concreto.

    El mecanismo principal es la fabrica de registros (`instalar_saneador`); esto
    queda para handlers que se creen por su cuenta y no pasen por ella.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        _sanear_record(record)
        return True


_fabrica_original = None


def instalar_saneador() -> None:
    """Instala el saneado como FABRICA de registros.

    Un `logging.Filter` no sirve para esto: los filtros de un logger solo se
    aplican a lo que se emite POR ese logger, no a lo que sube propagado desde
    sus hijos. Un secreto registrado por `httpx` o `uvicorn` nunca pasaria por un
    filtro puesto en la raiz.

    La fabrica, en cambio, corre al CREAR cada `LogRecord`, sin importar quien lo
    emita ni por donde propague. Es el unico punto por el que pasa todo.
    """
    global _fabrica_original

    if _fabrica_original is not None:
        return  # ya instalado; instalarlo dos veces anidaria el saneado

    _fabrica_original = logging.getLogRecordFactory()

    def fabrica(*args, **kwargs):
        return _sanear_record(_fabrica_original(*args, **kwargs))

    logging.setLogRecordFactory(fabrica)
