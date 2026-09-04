"""Guarda estricta de respaldo numérico (grounding) para análisis del chat (AI-1).

Puro — sin BD, sin HTTP.

Compara valores estructurados (float) y clasificados por unidad (MONEY, PERCENT, COUNT, ANY),
eliminando colisiones de dígitos (ej. "27.00" vs "2700") y munición de IDs/timestamps opacos.
"""
from dataclasses import dataclass, is_dataclass, asdict
from enum import Enum
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("lejovista.grounding")

# Cuantos valores distintos puede respaldar una respuesta. NO es una medida de
# seguridad: la guarda verifica pertenencia a un set de Python, y comprobar
# contra 220 o contra 4000 cuesta lo mismo.
#
# Estaba en 220, y ESA era la razon de que el analista no analizara. Una cuenta
# con 182 campanas produce ~820 valores; al pasarse del tope, `_safe_add_block`
# descartaba BLOQUES ENTEROS -- desgloses, alertas, estructura -- y el modelo
# recibia un puñado de cifras con las que no se puede decir nada interesante.
# El sintoma se leia como "el chat suena a bot" y la causa era inanicion, no la
# guarda.
#
# El costo de subirlo es de tokens de entrada, y es despreciable: ~3.000 tokens
# mas por consulta, menos de un milesimo de dolar con el modelo por defecto.
MAX_GROUNDING_VALUES = 4000
# Tope de la respuesta del analista. Subio de 2000 cuando el prompt paso a
# pedir el formato de cuatro pasos (resumen -> tabla -> que significa ->
# sugerencia): una tabla de cinco campanas con sus ids ya se comia los 2000, y
# la respuesta se descartaba ENTERA por larga, cayendo al texto de plantilla.
# Sigue habiendo tope: sin el, un modelo en bucle podria devolver kilobytes.
MAX_ANALYSIS_CHARS = 4000

OPAQUE_KEYS = frozenset({
    "campaign_id",
    "external_account_id",
    "ad_account_id",
    "organization_id",
    "page_id",
    "external_page_id",
    "actor_id",
    "id",
    "created_at",
    "updated_at",
    "meta_created_time",
    "token",
    "image_hash",
    "hash",
})

_URL_RE = re.compile(r"https?://\S+")

# A partir de cuántos dígitos una corrida se considera un identificador y no una
# cantidad. Los ids de Meta tienen 15-18; ninguna cifra de negocio de esta
# cuenta se acerca (el gasto histórico completo tiene 6 dígitos más decimales).
_MIN_DIGITOS_IDENTIFICADOR = 12

# Regex para extraer tokens numéricos con prefijo de moneda o sufijo de unidad
_EXTRACT_TOKEN_RE = re.compile(
    r"(?P<prefix>S/\.?|\$|USD|PEN)?\s*(?P<num>\d[\d.,]*)(?:\s*(?P<suffix>%|por ciento|soles|dias|días))?",
    re.IGNORECASE
)


class GroundingUnit(str, Enum):
    MONEY = "MONEY"
    PERCENT = "PERCENT"
    COUNT = "COUNT"
    ANY = "ANY"


@dataclass(frozen=True)
class GroundingEntry:
    value: float
    unit: GroundingUnit


class GroundingSet:
    def __init__(self):
        self._entries: List[GroundingEntry] = []
        # Los identificadores (ids de campaña, de anuncio, de página) NO entran
        # al set numérico: `OPAQUE_KEYS` los saltea a propósito, para que un id
        # de 18 dígitos no pueda respaldar por casualidad una cantidad.
        #
        # Pero el modelo tiene motivos legítimos para nombrarlos —una tabla de
        # campañas con su id— y ahí la guarda descartaba la respuesta entera. En
        # producción se vieron tres descartes seguidos por ids reales de Meta.
        #
        # Se guardan aparte y como CADENA: un id se valida por coincidencia
        # exacta, no por comparación de floats. `float("120207124204200569")`
        # pierde precisión, así que ni siquiera se compararía bien contra sí
        # mismo.
        self._identificadores: Set[str] = set()

    def add_identificador(self, valor: Any) -> None:
        texto = str(valor).strip()
        if texto.isdigit() and len(texto) >= _MIN_DIGITOS_IDENTIFICADOR:
            self._identificadores.add(texto)

    def contains_identificador(self, texto: str) -> bool:
        return texto.strip() in self._identificadores

    def add(self, value: float, unit: GroundingUnit = GroundingUnit.ANY) -> None:
        val_rounded = round(float(value), 4)
        entry = GroundingEntry(value=val_rounded, unit=unit)
        if entry not in self._entries:
            if len(self._entries) >= MAX_GROUNDING_VALUES:
                logger.warning(
                    "GroundingSet alcanzo el limite MAX_GROUNDING_VALUES (%d); descartando entrada %s",
                    MAX_GROUNDING_VALUES, entry
                )
                return
            self._entries.append(entry)
        if val_rounded < 0:
            abs_entry = GroundingEntry(value=abs(val_rounded), unit=unit)
            if abs_entry not in self._entries:
                if len(self._entries) >= MAX_GROUNDING_VALUES:
                    logger.warning(
                        "GroundingSet alcanzo el limite MAX_GROUNDING_VALUES (%d); descartando abs_entry %s",
                        MAX_GROUNDING_VALUES, abs_entry
                    )
                    return
                self._entries.append(abs_entry)

    def size(self) -> int:
        return len(self._entries)

    def contains(self, val: float, required_unit: GroundingUnit = GroundingUnit.ANY, epsilon: float = 0.001) -> bool:
        for entry in self._entries:
            # Compatibilidad de unidades
            if required_unit == GroundingUnit.MONEY and entry.unit not in (GroundingUnit.MONEY, GroundingUnit.ANY):
                continue
            if required_unit == GroundingUnit.PERCENT and entry.unit not in (GroundingUnit.PERCENT, GroundingUnit.ANY):
                continue
            if required_unit == GroundingUnit.COUNT and entry.unit not in (GroundingUnit.COUNT, GroundingUnit.ANY):
                continue

            # Comparación de valor flotante
            if abs(entry.value - val) <= epsilon:
                return True
        return False

    @classmethod
    def from_data(cls, data: Any) -> "GroundingSet":
        gset = cls()
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                # String plano: extraer números sueltos
                for m in re.finditer(r"\d[\d.,]*", data):
                    cand = _parse_float_candidates(m.group(0))
                    for c in cand:
                        gset.add(c, GroundingUnit.ANY)
                return gset

        cls._traverse_and_collect(data, gset)
        return gset

    @classmethod
    def _traverse_and_collect(cls, node: Any, gset: "GroundingSet", path: Tuple[str, ...] = ()) -> None:
        if is_dataclass(node):
            node = asdict(node)

        if isinstance(node, dict):
            for k, v in node.items():
                if k in OPAQUE_KEYS:
                    # Fuera del set numérico, pero anotado como identificador:
                    # el modelo puede nombrarlo si de verdad está en los datos.
                    if isinstance(v, (str, int)) and not isinstance(v, bool):
                        gset.add_identificador(v)
                    continue
                cls._traverse_and_collect(v, gset, path=path + (k.lower(),))
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                cls._traverse_and_collect(item, gset, path=path)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            unit = cls._classify_path_unit(path)
            gset.add(float(node), unit)
        elif isinstance(node, str):
            # Extraer números de strings descriptivos (ej. "los últimos 30 días" -> 30)
            for m in re.finditer(r"\d[\d.,]*", node):
                for c in _parse_float_candidates(m.group(0)):
                    gset.add(c, GroundingUnit.ANY)

    @classmethod
    def _classify_path_unit(cls, path: Tuple[str, ...]) -> GroundingUnit:
        if not path:
            return GroundingUnit.ANY

        leaf = path[-1].lower()

        # 1. Sufijos explícitos tienen máxima prioridad
        if leaf.endswith(("_pct", "_percent", "_tasa")):
            return GroundingUnit.PERCENT
        if leaf.endswith(("_soles", "_pen", "_usd", "_money")):
            return GroundingUnit.MONEY
        if leaf.endswith(("_cant", "_count", "_dias", "_days")):
            return GroundingUnit.COUNT

        # 2. Si algún ancestro o la clave misma es de porcentaje (deltas_pct, variacion, pct, etc.)
        for key in path:
            k = key.lower()
            if any(p in k for p in ("deltas_pct", "variacion", "delta_pct", "pct", "percent", "tasa", "porcentaje")):
                return GroundingUnit.PERCENT

        # 3. Clasificación por clave inmediata: PERCENT antes que MONEY
        if any(p in leaf for p in ("ctr", "rate", "share")):
            return GroundingUnit.PERCENT

        # 4. Clasificación por dinero
        if any(m in leaf for m in ("spend", "budget", "cpl", "cost", "amount", "cap", "inversion", "gasto", "precio")):
            return GroundingUnit.MONEY

        # 5. Clasificación por conteo
        if any(c in leaf for c in ("impressions", "clicks", "reach", "conversions", "conversations", "count", "days", "dias", "frequency", "leads", "frecuencia", "alcance")):
            return GroundingUnit.COUNT

        return GroundingUnit.ANY

    @staticmethod
    def _classify_key_unit(key: str) -> GroundingUnit:
        return GroundingSet._classify_path_unit((key.lower(),))


def _parse_float_candidates(raw: str) -> List[float]:
    """Interpreta números en notaciones español e inglés."""
    raw = raw.strip().rstrip(".,")
    if not raw:
        return []
    candidates = []
    
    # 1. Solo '.' y no ','
    if "." in raw and "," not in raw:
        try:
            candidates.append(float(raw))
        except ValueError:
            pass
        # Lectura de miles con punto (ej. 789.412 o 1.234.567)
        parts = raw.split(".")
        if len(parts) > 1 and 1 <= len(parts[0]) <= 3 and parts[0].isdigit() and all(len(p) == 3 and p.isdigit() for p in parts[1:]):
            try:
                val = float(raw.replace(".", ""))
                if val not in candidates:
                    candidates.append(val)
            except ValueError:
                pass
    # 2. Solo ',' y no '.'
    elif "," in raw and "." not in raw:
        try:
            candidates.append(float(raw.replace(",", ".")))
        except ValueError:
            pass
        # Lectura de miles con coma (ej. 789,412 o 1,234,567)
        parts = raw.split(",")
        if len(parts) > 1 and 1 <= len(parts[0]) <= 3 and parts[0].isdigit() and all(len(p) == 3 and p.isdigit() for p in parts[1:]):
            try:
                val = float(raw.replace(",", ""))
                if val not in candidates:
                    candidates.append(val)
            except ValueError:
                pass
    # 3. '.' y ',' presentes
    elif "." in raw and "," in raw:
        # Formato 1,085.47 o 1,234,567.89 (inglés)
        if raw.rfind(".") > raw.rfind(","):
            try:
                candidates.append(float(raw.replace(",", "")))
            except ValueError:
                pass
        # Formato 1.085,47 o 1.234.567,89 (español)
        else:
            try:
                candidates.append(float(raw.replace(".", "").replace(",", ".")))
            except ValueError:
                pass
    else:
        try:
            candidates.append(float(raw))
        except ValueError:
            pass

    return candidates


def motivo_de_rechazo(facts_data: Any, answer: str) -> Optional[Tuple[str, Optional[str]]]:
    """Como `numbers_are_grounded`, pero devuelve (motivo, detalle) o `None`.

    Existe porque "no pasó la guarda" no es un diagnóstico. Que el modelo
    invente un identificador y que calcule un promedio son fallos distintos:
    el primero suele ser el modelo citando de memoria y el segundo, un prompt
    que pide una comparación que no viene calculada en los datos. Se arreglan en
    sitios distintos, y con un motivo único la diferencia solo estaba en el log.

    Los motivos son los de `app/models/ai_discard.py`.
    """
    return _evaluar(facts_data, answer)


def numbers_are_grounded(facts_data: Any, answer: str) -> bool:
    """¿Todos los números de la respuesta están respaldados en `facts_data`?

    Cuando devuelve False el motor descarta la respuesta del analista y muestra
    el texto de plantilla. Eso antes pasaba **en silencio**: el cliente veía una
    respuesta robótica y no había forma de saber si el modelo había escrito mal,
    si se había pasado de largo o si ni siquiera se lo había llamado.

    Por eso cada rechazo se loguea con su motivo. Es la única manera de
    distinguir "el analista no funciona" de "el analista funciona y esta
    respuesta puntual se descartó bien".
    """
    return _evaluar(facts_data, answer) is None


def _evaluar(facts_data: Any, answer: str) -> Optional[Tuple[str, Optional[str]]]:
    """El motivo del rechazo, o `None` si la respuesta pasa.

    Es el cuerpo que antes vivía en `numbers_are_grounded`. Se separó para que
    el motivo llegue al registro de descartes en vez de quedarse en el log.
    """
    answer = (answer or "").strip()
    if not answer:
        logger.warning("Respuesta del analista descartada: vacía.")
        return ("vacia", None)
    if len(answer) > MAX_ANALYSIS_CHARS:
        logger.warning(
            "Respuesta del analista descartada: %d caracteres, el tope es %d. "
            "Si esto se repite, el formato pedido no entra en el tope.",
            len(answer), MAX_ANALYSIS_CHARS,
        )
        return ("demasiado_larga", str(len(answer)))
    if _URL_RE.search(answer):
        logger.warning("Respuesta del analista descartada: contiene un enlace.")
        return ("enlace", None)

    gset = GroundingSet.from_data(facts_data)

    # Buscar todos los números en la respuesta
    matches = list(_EXTRACT_TOKEN_RE.finditer(answer))
    for m in matches:
        raw_num = m.group("num")
        prefix = (m.group("prefix") or "").strip()
        suffix = (m.group("suffix") or "").strip().lower()

        req_unit = GroundingUnit.ANY
        if prefix or suffix in ("soles", "pen", "usd"):
            req_unit = GroundingUnit.MONEY
        elif suffix in ("%", "por ciento"):
            req_unit = GroundingUnit.PERCENT

        # Un identificador no es una cantidad: se valida por coincidencia exacta
        # contra los ids que vinieron en los datos, no convirtiéndolo a float
        # (que a 18 dígitos ya pierde precisión).
        #
        # `req_unit == ANY` es la condición que sostiene lo que `OPAQUE_KEYS`
        # protege: un id nunca viene con "S/." adelante ni "%" atrás, así que un
        # token con unidad se sigue validando como cantidad y no puede
        # respaldarse contra un id por casualidad.
        solo_digitos = raw_num.replace(".", "").replace(",", "")
        if (
            req_unit == GroundingUnit.ANY
            and solo_digitos.isdigit()
            and len(solo_digitos) >= _MIN_DIGITOS_IDENTIFICADOR
        ):
            if gset.contains_identificador(raw_num) or gset.contains_identificador(solo_digitos):
                continue
            logger.warning(
                "Respuesta del analista descartada: el identificador %r no está en los datos.",
                raw_num,
            )
            return ("identificador_ajeno", raw_num)

        candidates = _parse_float_candidates(raw_num)
        if not candidates:
            logger.warning(
                "Respuesta del analista descartada: no se pudo interpretar el número %r.",
                raw_num,
            )
            return ("cifra_sin_respaldo", raw_num)

        # Si ninguna interpretación numérica de este token tiene respaldo, rechazar
        if not any(gset.contains(c, required_unit=req_unit) for c in candidates):
            logger.warning(
                "Respuesta del analista descartada: el número %r (%s) no está en los datos. "
                "O el modelo lo calculó, o el dato no se le pasó en los facts.",
                raw_num, req_unit.name if hasattr(req_unit, "name") else req_unit,
            )
            return ("cifra_sin_respaldo", raw_num)

    return None


def limpiar_enlaces(texto: str) -> str:
    """Saca los enlaces del texto en vez de descartar la respuesta por tenerlos.

    La guarda rechaza cualquier respuesta con un `http://`, y con razón: un
    enlace que el modelo se inventa puede llevar a cualquier lado. Pero
    descartarla ENTERA por eso significa tirar un análisis correcto —con sus
    cifras ya verificadas— y darle al cliente una plantilla, sin que se entere.
    Pasó en producción.

    Quitar el enlace conserva lo que vale y elimina lo que preocupa.
    """
    limpio = _URL_RE.sub("", texto or "")
    # Los paréntesis vacíos que quedan al sacar el enlace de un markdown, y los
    # espacios dobles.
    limpio = re.sub(r"\(\s*\)|\[\s*\]", "", limpio)
    return re.sub(r"[ \t]{2,}", " ", limpio).strip()


def grounded_or_canonical(facts_data: Any, canonical: str, answer: str) -> str:
    """Devuelve la respuesta del modelo si cumple la guarda; si no, el texto canónico.

    El enlace se quita ANTES de validar: es lo único que se puede corregir sin
    tocar las cifras, y descartar por él sale más caro que limpiarlo.
    """
    limpio = limpiar_enlaces(answer)
    return limpio if numbers_are_grounded(facts_data, limpio) else canonical
