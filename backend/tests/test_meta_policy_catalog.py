"""Tests para el catálogo de políticas de Meta Ads y su validador (Fase 6.7 / Roadmap 10)."""
import pytest
from app.services.meta_policy_rules.validator import MetaPolicyValidator, default_policy_validator

default_policy_validator._load_rules()


def test_validator_health_unrealistic_claims():
    res = default_policy_validator.validate(
        copy_text="Pierde 10kg en 3 días con nuestro batido milagroso.",
        headline="Cura garantizada",
    )
    assert res.is_compliant is False
    assert len(res.blocking_violations) >= 1
    categories = [v.category for v in res.blocking_violations]
    assert any("Salud" in c for c in categories)


def test_validator_discrimination_special_categories():
    res = default_policy_validator.validate(
        copy_text="Buscamos ejecutivos de ventas. Oferta solo para mujeres solteras.",
    )
    assert res.is_compliant is False
    categories = [v.category for v in res.blocking_violations]
    assert any("Discriminación" in c or "Empleo" in c for c in categories)


def test_validator_personal_attributes():
    res = default_policy_validator.validate(
        copy_text="¿Estás endeudado y no sabes cómo pagar tus tarjetas?",
    )
    assert res.is_compliant is False
    rule_ids = [v.rule_id for v in res.blocking_violations]
    assert "PERSONAL_ATTRIBUTES" in rule_ids


def test_validator_financial_misleading():
    res = default_policy_validator.validate(
        copy_text="Gana dinero sin trabajar desde tu celular. Préstamo garantizado sin evaluación.",
    )
    assert res.is_compliant is False
    rule_ids = [v.rule_id for v in res.blocking_violations]
    assert "FINANCIAL_MISLEADING_OPPORTUNITIES" in rule_ids


def test_validator_compliant_copy():
    res = default_policy_validator.validate(
        copy_text="Descubre nuestra nueva colección de calzado deportivo con envíos a todo el Perú.",
        headline="Calidad y Comodidad",
        description="Pide por WhatsApp",
    )
    assert res.is_compliant is True
    assert len(res.blocking_violations) == 0


def test_validator_warnings():
    res = default_policy_validator.validate(
        copy_text="Resultados 100% garantizados con nuestro servicio de asesoría contable.",
    )
    assert res.is_compliant is True  # Warning no bloquea
    assert len(res.warnings) >= 1
    assert res.warnings[0].severity == "WARNING"


@pytest.mark.asyncio
async def test_preflight_integration_with_policy_check(mocker):
    from app.services.campaign_preflight import CampaignPreflight
    from app.models.ad_account import AdAccount

    fake_acc = mocker.MagicMock(spec=AdAccount)
    fake_acc.id = "acc_test_1"
    fake_acc.name = "Mi Marca"
    fake_acc.external_account_id = "act_123456"
    fake_acc.get_access_token.return_value = "token_123"

    from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES

    rules = dict(TENANT_CAMPAIGN_RULES)

    preflight = CampaignPreflight(fake_acc, rules)
    mocker.patch.object(preflight, "_check", return_value={"step": "campaign", "ok": True})
    mocker.patch.object(preflight, "_find_parent_campaign", return_value=("camp_parent_1", "Camp Padre"))

    # Copy infractor
    result = await preflight.run(
        product_name="Batido",
        page_id="page_123",
        whatsapp_number="+51999888777",
        budget=100.0,
        days=7,
        copy_text="Baja 10 kilos en 3 días sin esfuerzo con este remedio milagroso",
    )

    assert result["ok"] is False
    policy_checks = [c for c in result["checks"] if c.get("step") == "meta_policy"]
    assert len(policy_checks) == 1
    assert policy_checks[0]["ok"] is False
    assert "Infracción de Política" in policy_checks[0]["error"]["title"]
