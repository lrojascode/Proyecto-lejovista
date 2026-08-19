"""Tests para la guarda estricta de respaldo numérico (grounding) (Fase 5.1)."""
import json
import pytest

from app.services.campaign_chat.grounding import (
    GroundingSet, GroundingUnit, numbers_are_grounded, grounded_or_canonical,
    MAX_GROUNDING_VALUES
)


def test_collision_digit_string_bug_is_fixed():
    """
    BUG AI-1 histórico:
    "S/. 27.00" -> "2700" colisionaba con 2700 impresiones ("2700").
    Con float + tipado por unidad, S/. 27.00 (val=27.0, MONEY) debe ser rechazado
    si en los datos solo hay impresiones: 2700 (val=2700.0, COUNT).
    """
    facts = {
        "campaigns": [
            {"name": "Camp1", "impressions": 2700, "spend": 150.0}
        ]
    }
    # En el sistema anterior esto pasaba porque "2700" == "2700"
    assert numbers_are_grounded(facts, "Tu costo por lead fue de S/. 27.00.") is False


def test_opaque_keys_do_not_leak_numbers():
    """
    IDs y timestamps opacos (campaign_id, external_account_id, created_at) no deben
    aportar valores numéricos a la lista blanca.
    """
    facts = {
        "campaign_id": "12020593847291048",
        "external_account_id": "act_999888777",
        "created_at": "2026-08-18T21:46:00",
        "spend": 500.0,
    }
    gset = GroundingSet.from_data(facts)
    # 500 debe estar
    assert gset.contains(500.0, GroundingUnit.MONEY) is True
    # 12020593847291048 o 999888777 no deben estar
    assert gset.contains(999888777.0) is False
    assert gset.contains(2026.0) is False


def test_grounding_set_size_invariant():
    """
    Invariante: ningún paquete de facts puede superar MAX_GROUNDING_VALUES.
    """
    large_facts = {
        "kpis": {"spend": 1000.0, "reach": 5000, "clicks": 200},
        "campaigns": [
            {"name": f"Camp {i}", "spend": float(i * 10), "conversations": i}
            for i in range(100)
        ]
    }
    gset = GroundingSet.from_data(large_facts)
    assert gset.size() <= MAX_GROUNDING_VALUES


def test_float_formatting_es_and_en_accepted():
    facts = {"spend": 1085.47, "conversations": 402}
    # Formato inglés
    assert numbers_are_grounded(facts, "Invertiste S/. 1,085.47 en total.") is True
    # Formato español
    assert numbers_are_grounded(facts, "Invertiste S/. 1.085,47 en total.") is True
    # Sin separador
    assert numbers_are_grounded(facts, "Invertiste S/. 1085.47 en total.") is True


def test_unit_mismatch_rejections():
    facts = {
        "dias_analizados": 12,
        "clicks": 34,
        "spend": 100.0,
    }
    # S/. 12 (MONEY) no puede ser respaldado por dias: 12 (COUNT)
    assert numbers_are_grounded(facts, "El costo por lead fue S/. 12.00.") is False
    # 34% (PERCENT) no puede ser respaldado por clicks: 34 (COUNT)
    assert numbers_are_grounded(facts, "El CTR aumentó en 34%.") is False
    # Cifra correcta respaldada
    assert numbers_are_grounded(facts, "Analizamos 12 días y gastaste S/. 100.00.") is True
