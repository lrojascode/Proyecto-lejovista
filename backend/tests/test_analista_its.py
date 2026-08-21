"""El chat responde como el analista que describen los documentos de ITS.

`PROMPT-CONSULTAS-ITS.md` §8 define el formato: resumen → datos → qué significa
→ sugerencia. El prompt anterior decía "2 a 5 frases" y el resultado se leía
como un bot, no como un analista.

Estos tests fijan el COMPORTAMIENTO, no las constantes de ITS: cualquier cifra,
ID de cuenta o nombre de gestor de ese documento que termine hardcodeado en el
producto es un bug, no una feature.
"""
import inspect

import pytest

from app.services.ai import llm
from app.services.ai.llm import MODELS_BY_PROVIDER, pick_model
from app.services.campaign_chat.grounding import OPAQUE_KEYS


def _prompt_del_analista() -> str:
    """El system prompt de `compose_chat_answer`, tal como se envía."""
    fuente = inspect.getsource(llm.OpenRouterClient.compose_chat_answer)
    inicio = fuente.index("system_prompt = (")
    fin = fuente.index("user_prompt = (")
    return fuente[inicio:fin]


# --- El formato del §8 ---

def test_el_prompt_pide_los_cuatro_pasos_en_orden():
    """§8: resumen → datos → qué significa → sugerencia.

    Se buscan los pasos NUMERADOS y no las palabras sueltas: "los datos"
    aparece antes en la regla AI-1 ("números que aparezcan tal cual en los
    datos"), que es otra cosa.
    """
    prompt = _prompt_del_analista()
    pasos = ["1. RESUMEN", "2. LOS DATOS", "3. QUÉ SIGNIFICA", "4. SUGERENCIA"]
    posiciones = []
    for paso in pasos:
        assert paso in prompt, f"falta el paso '{paso}' del formato"
        posiciones.append(prompt.index(paso))
    assert posiciones == sorted(posiciones), "los pasos están fuera de orden"


def test_ya_no_hay_tope_de_frases():
    """"2 a 5 frases" es lo que hacía que no cumpliera el formato: no entran
    cuatro pasos con tabla en cinco frases."""
    prompt = _prompt_del_analista().lower()
    assert "2 a 5 frases" not in prompt
    assert "tabla" in prompt


def test_el_prompt_exige_exactitud():
    """Nombres completos, id y fechas completas — §8 'Exactitud'."""
    prompt = _prompt_del_analista().lower()
    assert "completos" in prompt
    assert "id de la campaña" in prompt or "id de la campana" in prompt
    assert "18 de agosto de 2026" in prompt   # el ejemplo de formato de fecha
    assert "18/08" in prompt                  # y el contraejemplo


def test_el_prompt_distingue_status_de_effective_status():
    prompt = _prompt_del_analista()
    assert "effective_status" in prompt
    assert "ENTREGANDO" in prompt


def test_el_prompt_explica_los_terminos_tecnicos():
    prompt = _prompt_del_analista().lower()
    assert "entre paréntesis" in prompt
    assert "ctr" in prompt


def test_el_prompt_prohibe_las_sugerencias_genericas():
    """§10: toda sugerencia apoyada en una cifra, citando la campaña exacta."""
    prompt = _prompt_del_analista().lower()
    assert "genéric" in prompt
    assert "campaña exacta" in prompt


def test_el_prompt_mantiene_la_regla_ai_1():
    """Lo que cambió es CÓMO se redacta, no de dónde salen los números."""
    prompt = _prompt_del_analista()
    assert "PROHIBIDO calcular" in prompt
    assert "TAL CUAL en los datos" in prompt


def test_el_prompt_dice_que_no_hay_ventas_ni_roas():
    prompt = _prompt_del_analista().lower()
    for termino in ("ventas", "roas", "matrícul"):
        assert termino in prompt, termino


# --- Las constantes de ITS NO pueden estar en el producto ---

@pytest.mark.parametrize("constante", [
    "act_992953052009562",     # la cuenta de ITS
    "301,714.20",              # su gasto histórico
    "Jairo Moreno",            # su gestor
    "its.institute",
    "106686772334869",         # su page_id
])
def test_el_producto_no_hardcodea_datos_de_its(constante):
    """El documento describe UNA cuenta. Se portan sus comportamientos, jamás
    sus cifras: este producto es multi-tenant."""
    assert constante not in _prompt_del_analista()


# --- El id de campaña se puede citar sin romper el grounding ---

def test_el_id_de_campana_es_opaco():
    """Un id de Meta son 15-17 dígitos. Si contara como valor disponible,
    respaldaría cualquier cifra que el modelo inventara."""
    assert "campaign_id" in OPAQUE_KEYS


# --- El selector de modelo tiene que servir para algo ---

def test_se_usa_el_modelo_que_eligio_la_organizacion():
    """Antes era `candidates[0]` en cinco llamadas: elegir un modelo en
    Configuración no cambiaba nada."""
    class Org:
        ai_model = "gpt-5.6-sol"
    assert pick_model(Org(), "openai") == "gpt-5.6-sol"


def test_un_modelo_de_otro_proveedor_cae_al_del_proveedor_activo():
    """Guardar un modelo de OpenAI y que las llamadas salgan por OpenRouter da
    un 404 que no dice nada útil."""
    class Org:
        ai_model = "gpt-5.6-sol"
    elegido = pick_model(Org(), "openrouter")
    assert elegido in {m["value"] for m in MODELS_BY_PROVIDER["openrouter"]}


def test_sin_modelo_configurado_usa_el_primero_del_proveedor():
    class Org:
        ai_model = None
    assert pick_model(Org(), "openai") == MODELS_BY_PROVIDER["openai"][0]["value"]
