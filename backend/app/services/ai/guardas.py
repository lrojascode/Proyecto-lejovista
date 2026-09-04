"""Guardas de periodo, cuenta y promesa de éxito (spec 10, criterios 2, 3 y 5).

Puro: sin BD, sin HTTP. Complementa `campaign_chat/grounding.py`, que valida las
CIFRAS. Estas tres guardas cubren lo que una validación numérica no puede ver.

**Por qué el grounding numérico no alcanza.** La guarda de cifras comprueba que
cada número de la respuesta exista en los datos autorizados. Un mes se escribe
con letras: "en julio el CPL fue S/ 12.40" pasa entera la validación numérica si
12.40 está en los datos de **agosto**. Lo mismo con las cuentas: el nombre de
otra cuenta publicitaria es texto, no un número, y una respuesta que mezcla dos
cuentas de la misma Organización tiene todas sus cifras respaldadas y aun así es
falsa.

La spec lo dice directamente: cambiar la ventana o mezclar AdAccount es una
invención tanto como cambiar un número.

**Qué NO hace `sin_promesa_de_exito`.** Reconoce las formas explícitas de
prometer rendimiento a partir de la apariencia. No es un clasificador semántico y
no lo pretende: una insinuación suficientemente indirecta se le escapa. Lo que sí
garantiza por completo es el lado determinista —ningún texto que escribe el
código promete éxito visual— y sobre la salida del modelo actúa como red, junto
con la instrucción del prompt. Decir que es hermética sería mentir sobre una
guarda de seguridad, que es la peor cosa sobre la que mentir.
"""
import datetime
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("lejovista.ai.guardas")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_MES_RE = re.compile(
    r"\b(" + "|".join(MESES) + r")\b(?:\s+de\s+(\d{4}))?", re.IGNORECASE
)
# "los últimos 30 días", "en los ultimos 7 dias"
_VENTANA_RE = re.compile(r"(?:ultim[oa]s|pasad[oa]s)\s+(\d{1,4})\s*d[ií]as", re.IGNORECASE)
# Identificador de cuenta publicitaria de Meta.
_ACT_RE = re.compile(r"\bact[_\s]?(\d{6,})\b", re.IGNORECASE)


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    ).lower()


@dataclass(frozen=True)
class PeriodoAutorizado:
    """La ventana que el producto consultó de verdad.

    `dias` es la longitud declarada al usuario ("los últimos 7 días"). Se guarda
    aparte de `desde`/`hasta` porque es la cifra que el modelo puede repetir mal,
    y compararla contra la diferencia de fechas introduciría discrepancias de un
    día por cómo se cuente el borde.
    """

    desde: datetime.date
    hasta: datetime.date
    dias: Optional[int] = None

    def contiene_mes(self, mes: int, anio: Optional[int]) -> bool:
        """¿El par (mes, año) cae dentro de la ventana?

        Sin año, se acepta el mes si aparece en la ventana en cualquiera de los
        años que la ventana toca. Es deliberadamente permisivo: exigir el año a
        un texto conversacional produciría falsos positivos constantes, y el
        riesgo que importa —nombrar un mes que no se consultó— queda cubierto
        igual.
        """
        cursor = self.desde.replace(day=1)
        fin = self.hasta.replace(day=1)
        while cursor <= fin:
            if cursor.month == mes and (anio is None or cursor.year == anio):
                return True
            cursor = (cursor.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)
        return False


def periodo_incoherente(
    autorizado: Optional[PeriodoAutorizado], respuesta: str
) -> Optional[str]:
    """Devuelve la mención de periodo que NO corresponde, o `None` si todo cuadra.

    Se devuelve la mención concreta y no un booleano porque es lo primero que
    hace falta al investigar un falso positivo: sin ella hay que releer la
    respuesta entera adivinando cuál de los periodos molestó.
    """
    if autorizado is None or not (respuesta or "").strip():
        return None

    texto = _sin_tildes(respuesta)

    for m in _MES_RE.finditer(texto):
        mes = MESES.get(m.group(1).lower())
        anio = int(m.group(2)) if m.group(2) else None
        if mes and not autorizado.contiene_mes(mes, anio):
            return m.group(0)

    if autorizado.dias:
        for m in _VENTANA_RE.finditer(texto):
            try:
                declarados = int(m.group(1))
            except ValueError:
                continue
            if declarados != autorizado.dias:
                return m.group(0)

    return None


@dataclass(frozen=True)
class CuentaAutorizada:
    """La AdAccount activa y las OTRAS cuentas de la misma Organización.

    Las otras hacen falta explícitamente. Sin ellas la guarda solo podría
    comprobar que la cuenta activa aparece, que no es lo mismo: el riesgo es que
    la respuesta hable de una cuenta HERMANA —cuyos datos el modelo pudo ver en
    otro turno o en el contexto— y eso solo se detecta sabiendo cómo se llaman
    las demás.
    """

    external_id: Optional[str] = None
    nombre: Optional[str] = None
    otras_ids: Tuple[str, ...] = ()
    otros_nombres: Tuple[str, ...] = ()


# Un nombre de cuenta demasiado corto o demasiado genérico produce falsos
# positivos por coincidencia casual dentro de una palabra cualquiera.
_MIN_NOMBRE_COMPARABLE = 5


def cuenta_incoherente(
    autorizada: Optional[CuentaAutorizada], respuesta: str
) -> Optional[str]:
    """Devuelve la referencia a otra cuenta que aparece en la respuesta, o `None`."""
    if autorizada is None or not (respuesta or "").strip():
        return None

    texto = _sin_tildes(respuesta)

    activa = re.sub(r"\D", "", autorizada.external_id or "")
    for m in _ACT_RE.finditer(texto):
        if m.group(1) != activa:
            return m.group(0)

    for otro in autorizada.otras_ids:
        digitos = re.sub(r"\D", "", otro or "")
        if digitos and digitos != activa and digitos in texto:
            return otro

    propio = _sin_tildes(autorizada.nombre or "")
    for nombre in autorizada.otros_nombres:
        limpio = _sin_tildes(nombre or "").strip()
        if len(limpio) < _MIN_NOMBRE_COMPARABLE:
            continue
        # Un nombre que es subcadena del de la cuenta activa ("Kibo" dentro de
        # "Kibo Perú") no distingue nada: nombrarlo no prueba que la respuesta
        # se haya ido a otra cuenta.
        if limpio in propio:
            continue
        if re.search(rf"\b{re.escape(limpio)}\b", texto):
            return nombre

    return None


# Promesas de rendimiento a partir de la apariencia (criterio 5, historia 17).
#
# Cada patrón exige las DOS mitades: el sujeto visual y la promesa. Solo el
# sujeto ("esta imagen") no dice nada, y solo la promesa ("va a funcionar") puede
# estar hablando legítimamente de una campaña con datos detrás.
_SUJETO_VISUAL = r"(?:est[ae]|esa|la|tu|su)\s+(?:imagen|foto|fotografia|video|creatividad|pieza|diseno|anuncio|arte|miniatura)"
_PROMESA = (
    r"(?:va\s+a\s+(?:funcionar|rendir|convertir|andar|volar|explotar)"
    r"|funcionara|rendira|convertira|tendra\s+exito"
    r"|garantiza|asegura|te\s+aseguro|te\s+garantizo"
    r"|sera\s+(?:un\s+)?(?:exito|ganadora|exitosa)"
    r"|seguro\s+(?:que\s+)?(?:funciona|convierte|rinde))"
)

_PROMESAS_RE = [
    re.compile(rf"{_SUJETO_VISUAL}\s+(?:\w+\s+){{0,3}}?{_PROMESA}", re.IGNORECASE),
    re.compile(rf"{_PROMESA}\s+(?:\w+\s+){{0,3}}?{_SUJETO_VISUAL}", re.IGNORECASE),
    # "con esta imagen vas a conseguir más leads" — la promesa recae en el
    # usuario, pero el sujeto sigue siendo la apariencia.
    re.compile(
        rf"con\s+{_SUJETO_VISUAL}\s+(?:\w+\s+){{0,4}}?"
        r"(?:vas\s+a\s+(?:conseguir|lograr|tener|duplicar|aumentar)|conseguiras|lograras)",
        re.IGNORECASE,
    ),
]


def promesa_de_exito(texto: str) -> Optional[str]:
    """Devuelve la frase que promete éxito por la apariencia, o `None`.

    El sistema puede decir que una creatividad *se parece* a otra que rindió
    bien —eso es un hecho medido— y puede recomendar probarla. Lo que no puede es
    afirmar que va a funcionar: nadie lo sabe antes de gastar, y decirlo empuja
    al cliente a poner dinero sobre una intuición presentada como pronóstico.
    """
    plano = _sin_tildes(texto or "")
    for patron in _PROMESAS_RE:
        m = patron.search(plano)
        if m:
            return m.group(0).strip()
    return None


def fuera_de_catalogo(
    valor: object, catalogo: Iterable[object], *, campo: str = "valor"
) -> Optional[str]:
    """Comprueba pertenencia a un catálogo cerrado (criterio 4).

    Devuelve el mensaje del descarte o `None`. No corrige, no busca el valor más
    parecido y no normaliza más allá de recortar espacios: "lo que no encaja se
    descarta; no se corrige ni se reinterpreta". Adivinar cuál quiso decir el
    modelo es introducir una elección que nadie tomó.
    """
    admisibles = {str(c).strip() for c in catalogo}
    actual = str(valor).strip() if valor is not None else ""
    if actual in admisibles:
        return None
    return (
        f"{campo}={actual!r} no pertenece al catálogo cerrado "
        f"({', '.join(sorted(admisibles)) or 'vacío'})"
    )
