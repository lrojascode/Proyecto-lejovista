"""Disciplinas de creación de campañas (execution/05 §1-§5, §9).

Todo lo de acá se verifica en SIMULACIÓN. El write-back está encendido en
producción con la cuenta piloto en el allowlist: un test que escriba de verdad
gastaría dinero real.
"""
import pytest
from fastapi import HTTPException

from app.services.campaign_validator import _detectar_acortador, CampaignValidator
from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES


# --- §9: acortadores rompen la atribución ---

def test_detecta_los_acortadores_conocidos():
    assert _detectar_acortador("Escribinos a bit.ly/promo") == "bit.ly"
    assert _detectar_acortador("Mira https://tinyurl.com/xyz") == "tinyurl.com"
    assert _detectar_acortador("link: https://cutt.ly/abc") == "cutt.ly"


def test_no_marca_dominios_legitimos():
    """La lista es cerrada a propósito: detectar "cualquier dominio corto" daría
    falsos positivos con dominios reales de clientes."""
    assert _detectar_acortador("Visitanos en miempresa.com.pe") is None
    assert _detectar_acortador("Escribinos por WhatsApp") is None
    assert _detectar_acortador("habitat.ly no es un acortador") is None
    assert _detectar_acortador("") is None
    assert _detectar_acortador(None) is None


def test_el_validador_rechaza_y_explica_el_porque():
    """Un "no" sin motivo se lee como capricho del sistema y el usuario lo
    esquiva. El mensaje tiene que decir qué se rompe."""
    import types
    request = types.SimpleNamespace(
        image_url=None, image_hash="abc123", image_path=None, video_id=None,
        video_path=None, thumbnail_url=None, product_name="Curso SAP",
        copy_text="Inscribite en bit.ly/curso-sap", headline=None, description=None,
    )
    validador = CampaignValidator.__new__(CampaignValidator)
    with pytest.raises(HTTPException) as exc:
        validador._validate_business_rules(request)
    detalle = exc.value.detail
    assert "bit.ly" in detalle
    assert "atribución" in detalle
    assert "más caro" in detalle


def test_un_copy_limpio_pasa():
    import types
    request = types.SimpleNamespace(
        image_url=None, image_hash="abc123", image_path=None, video_id=None,
        video_path=None, thumbnail_url=None, product_name="Curso SAP",
        copy_text="Inscribite en cursos.miempresa.com.pe", headline=None, description=None,
    )
    validador = CampaignValidator.__new__(CampaignValidator)
    validador._validate_business_rules(request)   # no levanta


# --- §4: piso de presupuesto por aprendizaje ---

def _request(budget, budget_type, days):
    import types
    return types.SimpleNamespace(budget=budget, budget_type=budget_type, days=days)


def test_avisa_cuando_el_presupuesto_no_sale_de_aprendizaje():
    from app.api.v1.campaigns import _preflight_warnings
    avisos = _preflight_warnings(_request(10.0, "DAILY", 7), TENANT_CAMPAIGN_RULES)
    assert len(avisos) == 1
    assert avisos[0]["code"] == "presupuesto_bajo_umbral_aprendizaje"
    # Explica el POR QUÉ: es lo que convierte un número en una decisión.
    assert "aprendizaje" in avisos[0]["detail"]
    assert "50 eventos" in avisos[0]["detail"]
    # Y deja continuar.
    assert "Podés continuar" in avisos[0]["detail"]


def test_deriva_el_diario_de_un_presupuesto_total():
    from app.api.v1.campaigns import _preflight_warnings
    # 70 en 7 días son 10/día: por debajo del piso aunque el total suene alto.
    assert _preflight_warnings(_request(70.0, "LIFETIME", 7), TENANT_CAMPAIGN_RULES)
    assert not _preflight_warnings(_request(200.0, "LIFETIME", 7), TENANT_CAMPAIGN_RULES)


def test_no_avisa_por_encima_del_piso():
    from app.api.v1.campaigns import _preflight_warnings
    assert _preflight_warnings(_request(20.0, "DAILY", 7), TENANT_CAMPAIGN_RULES) == []


def test_el_piso_es_configurable_por_organizacion():
    from app.api.v1.campaigns import _preflight_warnings
    reglas = {**TENANT_CAMPAIGN_RULES, "min_daily_budget_for_learning": 0}
    assert _preflight_warnings(_request(1.0, "DAILY", 7), reglas) == []


# --- §6: pausar antes de editar ---

@pytest.mark.asyncio
async def test_pausa_antes_de_editar_y_devuelve_el_estado(db_session, mocker):
    """Editar un objeto que está entregando deja a Meta reoptimizando con la
    configuración a medio cambiar, y ese gasto sale de la cuenta del cliente."""

    from app.models.ad_account import AdAccount
    from app.models.ai_action import AIAction
    from app.models.organization import Organization
    from app.services.meta_ads.modifier import MetaCampaignModifier

    org = Organization(slug="pausa-org", name="Pausa Org", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(organization_id=org.id, platform="META", external_account_id="act_p",
                    name="Pausa Acc", status="ACTIVE")
    db_session.add(acc)
    await db_session.flush()
    action = AIAction(
        organization_id=org.id, ad_account_id=acc.id, action_type="MODIFY_BUDGET",
        target_campaign_id="camp_1", status="APPROVED", risk_level="LOW",
        title="Subir presupuesto", description="Prueba",
        parameters={"new_budget": 60.0, "old_budget": 50.0},
    )
    db_session.add(action)
    await db_session.commit()

    mock = mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"success": True},
    )
    await MetaCampaignModifier(db_session).modify(acc, action)

    payloads = [llamada[0][2] for llamada in mock.call_args_list]
    assert payloads[0] == {"status": "PAUSED"}
    assert any("daily_budget" in pl or "lifetime_budget" in pl for pl in payloads)
    assert payloads[-1] == {"status": "ACTIVE"}


@pytest.mark.asyncio
async def test_si_no_se_puede_pausar_no_se_edita(db_session, mocker):
    """Editar igual es exactamente lo que esta regla evita."""

    from app.models.ad_account import AdAccount
    from app.models.ai_action import AIAction
    from app.models.organization import Organization
    from app.services.meta_ads.modifier import MetaCampaignModifier

    org = Organization(slug="pausa-falla-org", name="Falla", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(organization_id=org.id, platform="META", external_account_id="act_pf",
                    name="Falla Acc", status="ACTIVE")
    db_session.add(acc)
    await db_session.flush()
    action = AIAction(
        organization_id=org.id, ad_account_id=acc.id, action_type="MODIFY_BUDGET",
        target_campaign_id="camp_1", status="APPROVED", risk_level="LOW",
        title="Subir presupuesto", description="Prueba",
        parameters={"new_budget": 60.0, "old_budget": 50.0},
    )
    db_session.add(action)
    await db_session.commit()

    mocker.patch(
        "app.services.meta_ads.rate_limiter.MetaAPIRateLimiter.call_api",
        autospec=True, return_value={"error": {"message": "no se pudo pausar"}},
    )
    with pytest.raises(Exception, match="No se pudo pausar"):
        await MetaCampaignModifier(db_session).modify(acc, action)
