"""De dónde vino cada respuesta: modelo, código o simulación (spec 10, criterio 6).

La spec maestra es tajante: **en producción no se presenta contenido simulado
como análisis real, sin excepción.** El sistema tenía dos modos de respuesta no
provenientes del modelo y los mezclaba bajo el mismo nombre.

Los tres orígenes, que hasta ahora eran dos:

* `modelo` — lo redactó el LLM en una llamada real, y pasó las guardas.
* `determinista` — lo escribió el código a partir de datos reales. Es una
  respuesta legítima y correcta: las cifras salen de la base igual que las
  otras. Lo único que le falta es la redacción del modelo. **Sigue permitida en
  producción, identificada como tal.**
* `simulado` — texto de relleno con datos de ejemplo, para desarrollo y pruebas.
  **Prohibido en producción**, sin excepción.

La distinción importa porque los dos últimos se llamaban "fallback" y volvían por
el mismo camino. `_generate_mock_narrative` produce prosa plausible con las
cifras reales dentro; `_generate_mock_copy` produce texto de ejemplo. Devueltos
sin distinguir, el cliente no tenía forma de saber cuál estaba leyendo, y el
producto tampoco.

`assert_no_simulado` es la comprobación que convierte la regla en garantía. Se
llama en el borde de salida, y en producción levanta en vez de devolver: un
`logger.error` aquí sería otra vez una prohibición que el sistema documenta y no
cumple.
"""
import logging
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger("lejovista.ai.origen")

ORIGEN_MODELO = "modelo"
ORIGEN_DETERMINISTA = "determinista"
ORIGEN_SIMULADO = "simulado"

ORIGENES = (ORIGEN_MODELO, ORIGEN_DETERMINISTA, ORIGEN_SIMULADO)

# Lo que se le muestra al cliente. Solo el determinista se anuncia: que el
# modelo haya redactado es lo esperado y no hace falta decirlo en cada mensaje.
ETIQUETAS = {
    ORIGEN_DETERMINISTA: "Respuesta generada por el sistema a partir de tus datos, sin redacción del asistente.",
    ORIGEN_SIMULADO: "CONTENIDO SIMULADO — datos de ejemplo, no de tu cuenta.",
}


class ContenidoSimuladoEnProduccion(RuntimeError):
    """Se intentó devolver contenido simulado con el sistema en producción.

    Es un fallo del sistema, no del usuario: significa que un camino de
    simulación quedó alcanzable donde no debía. Se levanta para que aparezca en
    los errores y se corrija, en vez de llegar al cliente disfrazado de análisis.
    """


def en_produccion() -> bool:
    """`MOCK_SERVICES=false` es la definición operativa de producción acá.

    Es la misma variable que decide si las lecturas a Meta son reales, así que
    no puede haber un entorno que lea datos reales de Meta y a la vez se
    considere autorizado a devolver análisis simulado.
    """
    return not settings.MOCK_SERVICES


def assert_no_simulado(origen: str, *, feature: str) -> None:
    """Impide que contenido simulado salga en producción."""
    if origen == ORIGEN_SIMULADO and en_produccion():
        raise ContenidoSimuladoEnProduccion(
            f"'{feature}' intentó devolver contenido simulado con MOCK_SERVICES=false. "
            "En producción una respuesta que no viene del modelo tiene que ser "
            "determinista (calculada desde los datos del cliente) o un error "
            "explícito, nunca texto de ejemplo."
        )


def origen_de_respaldo(feature: str) -> str:
    """Qué origen corresponde cuando el modelo no está disponible.

    En desarrollo, simulado: es el punto de tener un modo simulado. En
    producción, determinista: el llamador tiene que producir su texto a partir
    de datos reales, y si no puede, decir que no puede.
    """
    return ORIGEN_SIMULADO if not en_produccion() else ORIGEN_DETERMINISTA


def marcar(payload: Dict[str, Any], origen: str, *, motivo: Optional[str] = None) -> Dict[str, Any]:
    """Añade el origen a una respuesta que va hacia el cliente.

    Estructural y no un prefijo en el texto: la interfaz decide cómo mostrarlo, y
    un texto con una advertencia incrustada se rompe al copiarlo, al truncarlo o
    al reenviarlo por otro canal.
    """
    if origen not in ORIGENES:
        raise ValueError(f"Origen desconocido: {origen!r}")
    assert_no_simulado(origen, feature=str(payload.get("type") or "respuesta"))

    payload = dict(payload)
    payload["origen"] = origen
    etiqueta = ETIQUETAS.get(origen)
    if etiqueta:
        payload["origen_label"] = etiqueta
    if motivo:
        payload["origen_motivo"] = motivo
    return payload
