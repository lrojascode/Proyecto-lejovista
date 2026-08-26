"""Dos descartes reales de producción que tiraban análisis correctos.

Los dos salieron de los logs del 26-ago-2026, en la misma conversación:

    WARNING [lejovista.grounding] Respuesta del analista descartada:
      el número '120207124204200569' (ANY) no está en los datos.
    WARNING [lejovista.grounding] Respuesta del analista descartada:
      contiene un enlace.

En los dos casos el cliente vio una plantilla y no se enteró de nada.
"""
from app.services.campaign_chat.grounding import (
    GroundingSet,
    grounded_or_canonical,
    limpiar_enlaces,
    numbers_are_grounded,
)

# Un id real de los logs.
_ID = "120207124204200569"


def test_un_id_de_meta_que_esta_en_los_datos_pasa():
    """`OPAQUE_KEYS` deja los ids fuera del set numérico a propósito —para que un
    id de 18 dígitos no respalde por casualidad una cantidad— pero el modelo
    tiene motivos legítimos para nombrarlos."""
    datos = {"campanas": [{"campaign_id": _ID, "name": "SAP FI", "gasto": 120.0}]}
    assert numbers_are_grounded(datos, f"La campaña SAP FI (id {_ID}) gastó 120.")


def test_un_id_inventado_sigue_sin_pasar():
    datos = {"campanas": [{"campaign_id": _ID, "name": "SAP FI", "gasto": 120.0}]}
    assert not numbers_are_grounded(datos, "La campaña (id 999999999999999999) gastó 120.")


def test_un_id_no_puede_respaldar_una_cantidad():
    """Lo que `OPAQUE_KEYS` protege: que el gasto no se valide contra un id."""
    datos = {"campanas": [{"campaign_id": _ID, "name": "SAP FI", "gasto": 120.0}]}
    assert not numbers_are_grounded(datos, f"Gastaste S/. {_ID}.")


def test_una_cifra_normal_no_se_confunde_con_un_id():
    """El corte son 12 dígitos: ninguna cifra de negocio se acerca."""
    datos = {"gasto": 303380.55}
    assert numbers_are_grounded(datos, "Gastaste S/. 303380.55.")
    assert not numbers_are_grounded(datos, "Gastaste S/. 303381.55.")


def test_el_enlace_se_quita_y_el_analisis_se_conserva():
    """Descartar la respuesta entera por un link es tirar cifras ya verificadas."""
    texto = "Tu mejor campaña gastó 120. Ver en https://adsmanager.facebook.com/x"
    limpio = limpiar_enlaces(texto)
    assert "http" not in limpio
    assert "Tu mejor campaña gastó 120." in limpio


def test_un_markdown_con_enlace_no_deja_parentesis_vacios():
    assert "()" not in limpiar_enlaces("Mirá [acá](https://x.com/y) el detalle.")


def test_grounded_or_canonical_limpia_antes_de_validar():
    datos = {"gasto": 120.0}
    salida = grounded_or_canonical(
        datos, "TEXTO CANONICO", "Gastaste 120. Ver https://adsmanager.facebook.com/x"
    )
    assert salida != "TEXTO CANONICO", "se descartó por el enlace en vez de limpiarlo"
    assert "http" not in salida
    assert "120" in salida


def test_el_set_no_mete_los_ids_entre_los_numeros():
    gset = GroundingSet.from_data({"campanas": [{"campaign_id": _ID, "gasto": 5.0}]})
    assert gset.contains(5.0)
    assert not gset.contains(float(_ID))
    assert gset.contains_identificador(_ID)
