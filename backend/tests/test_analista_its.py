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


# --- Se tiene que poder saber POR QUE se descartó una respuesta ---

def test_cada_descarte_dice_su_motivo(caplog):
    """Antes el rechazo era silencioso: el cliente veía texto de plantilla y no
    había forma de distinguir "el analista no funciona" de "el analista funciona
    y esta respuesta puntual se descartó bien"."""
    import logging
    from app.services.campaign_chat.grounding import numbers_are_grounded

    facts = {"kpis": {"spend": 300.0, "resultados": 20}}

    with caplog.at_level(logging.WARNING, logger="lejovista.grounding"):
        assert numbers_are_grounded(facts, "Gastaste S/. 999.99") is False
    assert "999.99" in caplog.text and "no está en los datos" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="lejovista.grounding"):
        assert numbers_are_grounded(facts, "x" * 9999) is False
    assert "el tope es" in caplog.text


def test_una_cifra_calculada_se_descarta_y_se_ve():
    """300/20 = 15. El modelo lo dividió, no lo copió: la respuesta entera muere
    —correcto por AI-1— pero tiene que quedar registro."""
    from app.services.campaign_chat.grounding import numbers_are_grounded

    facts = {"kpis": {"spend": 300.0, "resultados": 20}}
    assert numbers_are_grounded(facts, "Te salieron a S/. 15.00 cada uno") is False


def test_el_tope_entra_una_respuesta_con_tabla():
    """El formato de cuatro pasos con una tabla de campañas no puede pasarse
    del tope y morir por largo."""
    from app.services.campaign_chat.grounding import MAX_ANALYSIS_CHARS

    # Una tabla de 5 campañas con nombre, id, gasto, resultados y costo ronda
    # los 900-1200 caracteres; más el resumen y la interpretación.
    assert MAX_ANALYSIS_CHARS >= 3500


# --- El analista tiene que RECIBIR datos, no solo permiso para hablar ---

def test_ve_muchas_mas_campanas_que_antes():
    """Eran 8, de 182 que puede tener una cuenta real. Con ocho filas no hay
    análisis posible: no se ve la cola, ni las que gastan sin resultados, ni un
    patrón."""
    from app.services.campaign_chat.facts import RANKING_TOP_K

    assert RANKING_TOP_K >= 50


def test_el_presupuesto_de_grounding_no_descarta_bloques():
    """`MAX_GROUNDING_VALUES` estaba en 220 y una cuenta de 182 campañas produce
    ~820 valores: al pasarse, `_safe_add_block` tiraba BLOQUES ENTEROS y el
    modelo recibía un puñado de cifras.

    No es una medida de seguridad: la guarda comprueba pertenencia a un set, y
    hacerlo contra 220 o contra 4000 cuesta lo mismo.
    """
    from app.services.campaign_chat.grounding import GroundingSet, MAX_GROUNDING_VALUES
    from app.services.campaign_chat.facts import RANKING_TOP_K

    camps = [
        {"campaign_id": f"120223365313040{i:03d}", "name": f"JMM{i:03d}-SAP-CP",
         "status": "ACTIVE", "effective_status": "ACTIVE", "spend": 100.0 + i,
         "resultados": 20 + i, "cost_per_conv": 3.4 + i / 100, "ctr": 1.7}
        for i in range(RANKING_TOP_K)
    ]
    facts = {"kpis_del_periodo": {"spend": 7417.19, "resultados": 2160}, "campaigns": camps}
    assert GroundingSet.from_data(facts).size() <= MAX_GROUNDING_VALUES


# --- Las comparaciones vienen hechas, porque el modelo no puede calcularlas ---

def _cuenta_de_ejemplo():
    return [
        {"name": "Ganadora", "spend": 1000.0, "conversations": 400, "cost_per_conv": 2.5},
        {"name": "Cara", "spend": 500.0, "conversations": 50, "cost_per_conv": 10.0},
        {"name": "Quema A", "spend": 800.0, "conversations": 0, "cost_per_conv": 800.0},
        {"name": "Quema B", "spend": 300.0, "conversations": 0, "cost_per_conv": 300.0},
    ]


def test_la_diferencia_entre_la_mejor_y_la_peor_viene_calculada():
    """"35% más caro" es una división. Por AI-1 el modelo no puede hacerla: si
    la escribe, su respuesta entera se descarta."""
    from app.services.campaign_chat.facts import _comparativas

    todas = _cuenta_de_ejemplo()
    comp = _comparativas([dict(c) for c in todas[:2]], todas)
    assert comp["la_mas_cara_cuesta_mas_pct"] == 300.0
    assert comp["campana_mas_barata"] == "Ganadora"


def test_se_sabe_donde_se_esta_yendo_la_plata():
    """"¿Dónde estoy perdiendo plata?" es uno de los botones sugeridos del chat,
    y hasta ahora no había con qué contestarlo: las campañas que gastan sin
    resultados quedan FUERA del ranking por costo."""
    from app.services.campaign_chat.facts import _comparativas

    todas = _cuenta_de_ejemplo()
    comp = _comparativas([dict(c) for c in todas[:2]], todas)
    assert comp["campanas_que_gastan_sin_resultados"] == 2
    assert comp["gasto_sin_resultados"] == 1100.0
    assert comp["las_que_mas_queman"][0]["name"] == "Quema A"


def test_cada_campana_trae_cuanto_pesa_en_el_gasto():
    from app.services.campaign_chat.facts import _comparativas

    todas = _cuenta_de_ejemplo()
    top = [dict(c) for c in todas[:2]]
    _comparativas(top, todas)
    assert top[0]["pct_del_gasto"] == 38.5


def test_sin_campanas_no_inventa_comparaciones():
    from app.services.campaign_chat.facts import _comparativas

    assert _comparativas([], []) == {}
