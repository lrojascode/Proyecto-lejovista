"""El vocabulario interno no cruza al cliente (spec 12, criterio 16).

`Draft`, `Saga`, `AIAction`, `grounding` y `write-back` son nombres de piezas
del sistema. Para el equipo son precisos; para un dueño de negocio son ruido que
además sugiere que algo técnico falló. Se traducen en el borde.

**Traducir, no ocultar.** Cada término interno tiene un equivalente que dice lo
mismo en el idioma del cliente: un `Draft` es "tu solicitud", una `Saga` es "la
creación de tu campaña". Borrarlos sin más dejaría frases mutiladas.

**Se comprueba sobre las respuestas de un recorrido completo, no sobre las
plantillas.** Revisar plantillas encuentra lo que alguien escribió a mano y se
pierde lo que llega desde un mensaje de error, desde una excepción o desde el
modelo. El test del criterio recorre la conversación entera y mira lo que sale.
"""
import re
from typing import Dict, Iterable, List, Tuple

# Términos internos y con qué se dicen de cara al cliente.
#
# El orden importa: los más largos primero, porque "write-back" contiene "back"
# y una sustitución parcial dejaría un texto peor que el original.
TRADUCCIONES: Tuple[Tuple[str, str], ...] = (
    ("write-back", "los cambios que se envían a Meta"),
    ("writeback", "los cambios que se envían a Meta"),
    ("grounding", "la comprobación de que las cifras salen de tus datos"),
    ("AIAction", "acción sugerida"),
    ("AI Action", "acción sugerida"),
    ("Saga", "la creación de tu campaña"),
    ("Draft", "tu solicitud"),
)

# Lo que NUNCA debe aparecer en la superficie del cliente. Es la lista que
# comprueba el test del criterio 16.
TERMINOS_INTERNOS: Tuple[str, ...] = (
    "draft", "saga", "aiaction", "ai action", "grounding", "write-back", "writeback",
)

# Palabras que CONTIENEN un término interno sin serlo. Sin esta lista, "drafteo"
# o una campaña que el cliente llamó "Saga de Verano" dispararían el guardián, y
# un guardián que da falsos positivos se acaba desactivando.
_EXCEPCIONES = re.compile(r"\b(?:drafts?men|saga[s]?\s+de\b)", re.IGNORECASE)


def _patron(termino: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(termino)}\b", re.IGNORECASE)


# El artículo que precede al término se sustituye JUNTO con él. Reemplazar solo
# la palabra produce "El tu solicitud falló": una traducción que deja el texto
# peor que el original no la usaría nadie, y el guardián acabaría desactivado.
#
# El reemplazo ya trae su propio artículo, así que se traga el que hubiera.
_ARTICULO = r"(?:\b(?:el|la|los|las|un|una|unos|unas)\s+)?"


def _patron_con_articulo(termino: str) -> re.Pattern:
    return re.compile(rf"{_ARTICULO}\b{re.escape(termino)}\b", re.IGNORECASE)


# Cada reemplazo se escribe CON su artículo, para que la frase resultante se
# lea. El primero de la frase pierde la mayúscula, y eso se corrige aparte.
_CON_ARTICULO = {
    "write-back": "los cambios que se envían a Meta",
    "writeback": "los cambios que se envían a Meta",
    "grounding": "la comprobación de que las cifras salen de tus datos",
    "AIAction": "la acción sugerida",
    "AI Action": "la acción sugerida",
    "Saga": "la creación de tu campaña",
    "Draft": "tu solicitud",
}


def traducir(texto: str) -> str:
    """Sustituye los términos internos por su equivalente para el cliente.

    Es una RED, no el mecanismo principal: los textos de la superficie se
    escriben ya en el idioma del cliente. Esto atrapa lo que llega desde un
    mensaje de error, una excepción o el modelo, que es justo donde nadie
    relee.
    """
    salida = texto or ""
    for termino, reemplazo in _CON_ARTICULO.items():
        salida = _patron_con_articulo(termino).sub(reemplazo, salida)

    # Mayúscula inicial si la frase empezaba con una: la sustitución se la come
    # cuando el término iba al principio.
    if texto and texto[:1].isupper() and salida[:1].islower():
        salida = salida[:1].upper() + salida[1:]
    return salida


def terminos_internos_en(texto: str) -> List[str]:
    """Los términos internos presentes, para que un test pueda nombrarlos.

    Devolver cuáles y no un booleano es lo que convierte un fallo en algo
    accionable: "hay vocabulario interno" obliga a buscarlo a ojo en una
    conversación de treinta mensajes.
    """
    plano = texto or ""
    sin_excepciones = _EXCEPCIONES.sub("", plano)
    return [t for t in TERMINOS_INTERNOS if _patron(t).search(sin_excepciones)]


def recolectar_textos(mensajes: Iterable[Dict]) -> List[str]:
    """Todo el texto visible de una conversación, para auditarlo de una vez.

    Recorre también las tarjetas: el vocabulario interno se cuela con más
    facilidad en una etiqueta de campo o en el título de una tarjeta que en un
    mensaje redactado, justamente porque nadie los relee.
    """
    textos: List[str] = []

    def _visitar(valor):
        if isinstance(valor, str):
            textos.append(valor)
        elif isinstance(valor, dict):
            for clave, hijo in valor.items():
                # Las claves técnicas del protocolo no son texto visible: un
                # campo llamado `draft_id` es la API, no algo que el cliente lea.
                if clave.endswith("_id") or clave in ("type", "role", "key", "action"):
                    continue
                _visitar(hijo)
        elif isinstance(valor, (list, tuple)):
            for hijo in valor:
                _visitar(hijo)

    for mensaje in mensajes or []:
        _visitar(mensaje)
    return textos
