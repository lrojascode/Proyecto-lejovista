"""Presupuesto diario vs total (F2).

El AdSet mandaba SIEMPRE `lifetime_budget`, pero la mayoría de anunciantes
trabaja con presupuesto diario. La sutileza está en que todo lo que razona
sobre presupuesto —el nivel de riesgo, la calibración R1 del suggester y el
costo esperado del outcome loop (`suggested_budget / 20`)— asume un TOTAL de
campaña. Por eso `CampaignCreateRequest.total_budget()` normaliza una sola vez
y esos tres no tienen que saber en qué modo pidió el usuario el dinero.
"""
import pytest

from app.schemas.campaign_schemas import CampaignCreateRequest


def _request(**overrides):
    base = dict(
        ad_account_id="acc_1", page_id="page_1", product_name="Producto",
        budget=100.0, days=7, whatsapp_number="51999888777",
        image_hash="h1", copy_text="Copy",
    )
    base.update(overrides)
    return CampaignCreateRequest(**base)


def test_lifetime_is_the_default_and_unchanged():
    """No-regresión: quien no manda budget_type se comporta como siempre."""
    req = _request(budget=140.0)
    assert req.budget_type == "LIFETIME"
    assert req.total_budget() == 140.0


def test_daily_budget_normalizes_to_total():
    req = _request(budget=20.0, days=7, budget_type="DAILY")
    assert req.total_budget() == 140.0


def test_daily_total_rounds_to_cents():
    req = _request(budget=3.333, days=3, budget_type="DAILY")
    assert req.total_budget() == 10.0


def test_invalid_budget_type_is_rejected():
    with pytest.raises(Exception):
        _request(budget_type="WEEKLY")
