"""Preflight: valida los payloads de la Saga contra Meta con
`execution_options=['validate_only']`, sin crear nada.

Sustituye el ciclo de "descubrir una regla de Meta por intento real, dejando una
campaña huérfana cada vez" (sesión del 2026-07-17).
"""
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.campaign_preflight import CampaignPreflight
from app.tenant_rules.campaign_defaults import TENANT_CAMPAIGN_RULES


async def _make_account(db: AsyncSession, slug="preflight-org"):
    org = Organization(slug=slug, name="Preflight Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_pf", name="Preflight Acc", status="ACTIVE",
    )
    acc.set_access_token("tok")
    db.add(acc)
    await db.flush()
    return org, acc


class _FakeLimiter:
    """Simula Meta: registra las llamadas y devuelve lo que se le indique por endpoint."""

    def __init__(self, responses=None, campaigns=None):
        self.responses = responses or {}
        self.campaigns = campaigns if campaigns is not None else [
            {"id": "camp_existente", "name": "Campaña Previa", "objective": "OUTCOME_LEADS"},
        ]
        self.calls = []

    async def call_api(self, endpoint, params, method="GET", access_token=""):
        self.calls.append({"endpoint": endpoint, "params": params, "method": method})
        if endpoint.endswith("/campaigns") and method == "GET":
            return {"data": self.campaigns}
        for key, res in self.responses.items():
            if endpoint.endswith(key):
                return res
        return {}  # éxito vacío = validación OK


def _run(acc, limiter, **overrides):
    kwargs = dict(
        product_name="Producto X", page_id="page-1", whatsapp_number="51926325027",
        budget=70.0, days=7, copy_text="Copy", image_hash="hash1",
        best_age_range="45-54", best_device=None, excluded_placements=[],
    )
    kwargs.update(overrides)
    return CampaignPreflight(acc, TENANT_CAMPAIGN_RULES, limiter=limiter).run(**kwargs)


@pytest.mark.asyncio
async def test_preflight_sends_validate_only_and_creates_nothing(db_session: AsyncSession):
    org, acc = await _make_account(db_session)
    limiter = _FakeLimiter()

    result = await _run(acc, limiter)

    assert result["ok"] is True
    # Los 3 POST (campaña, adset, creativo) llevan validate_only.
    posts = [c for c in limiter.calls if c["method"] == "POST"]
    assert len(posts) == 3
    for p in posts:
        assert json.loads(p["params"]["execution_options"]) == ["validate_only"]


@pytest.mark.asyncio
async def test_preflight_reports_meta_error_with_readable_message(db_session: AsyncSession):
    """El error se devuelve legible (error_user_msg), no como JSON crudo —
    que es como le llegaba al usuario hasta ahora."""
    org, acc = await _make_account(db_session, slug="preflight-org-err")
    limiter = _FakeLimiter(responses={"/adsets": {"error": {
        "message": "Invalid parameter", "code": 100, "error_subcode": 1885272,
        "error_user_title": "Presupuesto demasiado bajo",
        "error_user_msg": "El presupuesto del conjunto de anuncios debe ser de más de S/23,94.",
    }}})

    result = await _run(acc, limiter, budget=10.0)

    assert result["ok"] is False
    adset = next(c for c in result["checks"] if c["step"] == "adset")
    assert adset["ok"] is False
    assert adset["error"]["title"] == "Presupuesto demasiado bajo"
    assert "S/23,94" in adset["error"]["message"]
    assert adset["error"]["subcode"] == 1885272


@pytest.mark.asyncio
async def test_preflight_validates_the_real_saga_payload(db_session: AsyncSession):
    """Debe validar lo que la Saga enviaría de verdad (build_targeting +
    CreativeBuilder), no un payload escrito a mano para la ocasión."""
    org, acc = await _make_account(db_session, slug="preflight-org-payload")
    limiter = _FakeLimiter()

    await _run(acc, limiter)

    adset = next(c for c in limiter.calls if c["endpoint"].endswith("/adsets"))
    targeting = json.loads(adset["params"]["targeting"])
    # Sin segmentar por edad (el default desde execution/05 §3): publico amplio
    # y Advantage+ ENCENDIDO, con el valor explicito que Meta exige (1870227).
    # Antes, con el rango ganador 45-54, la app se veia obligada a apagarlo
    # porque Meta no admite Advantage+ con age_min > 25 (1870188) -- o sea que
    # segmentar por edad desactivaba justo lo que conviene usar.
    assert targeting["age_min"] == 25
    assert targeting["targeting_automation"] == {"advantage_audience": 1}
    # promoted_object solo con page_id (CTWA).
    assert json.loads(adset["params"]["promoted_object"]) == {"page_id": "page-1"}

    creative = next(c for c in limiter.calls if c["endpoint"].endswith("/adcreatives"))
    spec = json.loads(creative["params"]["object_story_spec"])
    assert spec["link_data"]["link"] == "https://api.whatsapp.com/send?phone=51926325027"

    campaign = next(c for c in limiter.calls if c["endpoint"].endswith("/campaigns") and c["method"] == "POST")
    assert campaign["params"]["is_adset_budget_sharing_enabled"] == "false"
    assert campaign["params"]["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_preflight_skips_adset_without_parent_campaign(db_session: AsyncSession):
    """Sin campañas en la cuenta no hay padre con el que validar el adset: se
    informa como omitido, no como éxito."""
    org, acc = await _make_account(db_session, slug="preflight-org-nocamp")
    limiter = _FakeLimiter(campaigns=[])

    result = await _run(acc, limiter)

    adset = next(c for c in result["checks"] if c["step"] == "adset")
    assert adset["ok"] is None
    assert "padre" in adset["skipped"]


@pytest.mark.asyncio
async def test_preflight_skips_creative_without_image(db_session: AsyncSession):
    org, acc = await _make_account(db_session, slug="preflight-org-noimg")
    limiter = _FakeLimiter()

    result = await _run(acc, limiter, image_hash=None)

    creative = next(c for c in result["checks"] if c["step"] == "creative")
    assert creative["ok"] is None
    assert "image_hash" in creative["skipped"]


@pytest.mark.asyncio
async def test_preflight_prefers_parent_with_same_objective(db_session: AsyncSession):
    """Un padre con otro objetivo daría errores que no son de nuestro payload."""
    org, acc = await _make_account(db_session, slug="preflight-org-parent")
    limiter = _FakeLimiter(campaigns=[
        {"id": "camp_traffic", "name": "Otra", "objective": "OUTCOME_TRAFFIC"},
        {"id": "camp_leads", "name": "La buena", "objective": "OUTCOME_LEADS"},
    ])

    result = await _run(acc, limiter)

    adset_call = next(c for c in limiter.calls if c["endpoint"].endswith("/adsets"))
    assert adset_call["params"]["campaign_id"] == "camp_leads"
    adset = next(c for c in result["checks"] if c["step"] == "adset")
    assert adset["parent_campaign"]["id"] == "camp_leads"


@pytest.mark.asyncio
async def test_preflight_does_not_fake_ok_when_writes_are_simulated(db_session: AsyncSession):
    """Con el write-back apagado Meta no valida nada: no se debe reportar OK."""
    org, acc = await _make_account(db_session, slug="preflight-org-sim")
    limiter = _FakeLimiter(responses={
        "/campaigns": {"mock": True}, "/adsets": {"mock": True}, "/adcreatives": {"mock": True},
    })

    result = await _run(acc, limiter)

    api_checks = [c for c in result["checks"] if c["step"] in ("campaign", "adset", "creative")]
    assert len(api_checks) == 3
    for check in api_checks:
        assert check["ok"] is not True
        assert "simulad" in (check.get("skipped") or "")
