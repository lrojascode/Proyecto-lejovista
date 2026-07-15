"""A-1: CREATE_CAMPAIGN se ejecuta por el mismo camino real de creación (la
Saga de creation_tasks) en vez del flujo inline duplicado que existía antes
(`_execute_campaign_creation`, con el bug del image_hash mock que nunca
llegaba a Meta). El chain de Celery no debe intentar hablar con un broker
real en tests — se mockea igual que en test_campaign_creation_endpoint.py.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.models.meta_page import MetaPage
from app.models.meta_creative import MetaCreative
from app.models.campaign_draft import CampaignDraft
from app.services.ai.executor import AIExecutor


@pytest.fixture(autouse=True)
def _mock_chain(mocker):
    mock_chain_factory = mocker.patch("app.services.ai.executor.chain")
    mock_chain_instance = mock_chain_factory.return_value
    mock_chain_instance.on_error.return_value = mock_chain_instance
    return mock_chain_factory, mock_chain_instance


async def _make_account(db: AsyncSession, slug="exec-cc-org"):
    org = Organization(slug=slug, name="Exec CC Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="Exec Acc", status="ACTIVE",
    )
    acc.set_access_token("mock_access_token")
    db.add(acc)
    await db.flush()
    return org, acc


def _create_action(org, acc, **param_overrides):
    params = {
        "campaign_name": "EXEC_MSJ_ProductoX_JUL2026",
        "product_name": "Producto X",
        "objective": "OUTCOME_LEADS",
        "daily_budget": 100.0,
        "duration_days": 7,
        "excluded_placements": ["instagram_positions"],
        "best_age_range": "25-34",
        "best_device": "mobile",
        "creative": {
            "headline": "Envío gratis hoy",
            "body": "Aprovecha la promoción de esta semana.",
            "description": "Escríbenos por WhatsApp.",
            "welcome_message": "¡Hola! Cuéntanos qué buscas.",
        },
    }
    params.update(param_overrides)
    return AIAction(
        organization_id=org.id, ad_account_id=acc.id, action_type="CREATE_CAMPAIGN",
        risk_level="HIGH", title="Crear nueva campaña optimizada", description="test",
        platform="META", parameters=params, planned_api_calls=[], status="APPROVED",
    )


@pytest.mark.asyncio
async def test_approved_create_campaign_dispatches_saga_and_stays_executing(init_db, db_session: AsyncSession, _mock_chain):
    org, acc = await _make_account(db_session)
    page = MetaPage(
        organization_id=org.id, external_page_id="page-1", name="Mi Página",
        whatsapp_number="51999888777", status="ACTIVE",
    )
    creative = MetaCreative(ad_account_id=acc.id, organization_id=org.id, image_hash="hash_real_1")
    db_session.add_all([page, creative])
    action = _create_action(org, acc)
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "EXECUTING"

    _, mock_chain_instance = _mock_chain
    mock_chain_instance.apply_async.assert_called_once()

    drafts = (await db_session.execute(
        __import__("sqlalchemy").select(CampaignDraft).where(CampaignDraft.organization_id == org.id)
    )).scalars().all()
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.payload["page_id"] == "page-1"
    assert draft.payload["whatsapp_number"] == "51999888777"
    assert draft.payload["image_hash"] == "hash_real_1"
    assert "Envío gratis hoy" in draft.payload["copy_text"]
    assert draft.payload["action_id"] == action.id


@pytest.mark.asyncio
async def test_needs_input_when_no_active_page_with_whatsapp(init_db, db_session: AsyncSession, _mock_chain):
    org, acc = await _make_account(db_session, "exec-no-page")
    db_session.add(MetaCreative(ad_account_id=acc.id, organization_id=org.id, image_hash="h1"))
    action = _create_action(org, acc)
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "NEEDS_INPUT"
    assert result.execution_result["needs_input"] is True
    _, mock_chain_instance = _mock_chain
    mock_chain_instance.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_needs_input_when_multiple_pages_with_whatsapp(init_db, db_session: AsyncSession, _mock_chain):
    org, acc = await _make_account(db_session, "exec-multi-page")
    db_session.add_all([
        MetaPage(organization_id=org.id, external_page_id="page-1", name="P1", whatsapp_number="51999888777", status="ACTIVE"),
        MetaPage(organization_id=org.id, external_page_id="page-2", name="P2", whatsapp_number="51999888778", status="ACTIVE"),
        MetaCreative(ad_account_id=acc.id, organization_id=org.id, image_hash="h1"),
    ])
    action = _create_action(org, acc)
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "NEEDS_INPUT"
    _, mock_chain_instance = _mock_chain
    mock_chain_instance.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_needs_input_when_no_creative_available(init_db, db_session: AsyncSession, _mock_chain):
    org, acc = await _make_account(db_session, "exec-no-creative")
    db_session.add(MetaPage(
        organization_id=org.id, external_page_id="page-1", name="P1",
        whatsapp_number="51999888777", status="ACTIVE",
    ))
    action = _create_action(org, acc)
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "NEEDS_INPUT"
    assert "creativo" in result.execution_result["error"]
    _, mock_chain_instance = _mock_chain
    mock_chain_instance.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_modify_action_unaffected_by_create_campaign_change(init_db, db_session: AsyncSession, _mock_chain):
    """Las acciones de modificación (no CREATE_CAMPAIGN) siguen su camino de
    siempre (MetaCampaignModifier), sin pasar por el dispatcher de la Saga."""
    org, acc = await _make_account(db_session, "exec-modify")
    action = AIAction(
        organization_id=org.id, ad_account_id=acc.id, action_type="MODIFY_BUDGET",
        risk_level="MEDIUM", title="Reducir presupuesto", description="test",
        target_campaign_id="camp_1", target_adset_id="adset_1", platform="META",
        parameters={"old_budget": 50.0, "new_budget": 35.0},
        planned_api_calls=[], status="APPROVED",
    )
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "COMPLETED"
    _, mock_chain_instance = _mock_chain
    mock_chain_instance.apply_async.assert_not_called()


# --- Página predeterminada por cuenta (Settings → Páginas y WhatsApp): con
# 2+ páginas en la org, antes TODA acción CREATE_CAMPAIGN caía en NEEDS_INPUT;
# ahora la default de la cuenta resuelve la ambigüedad. ---

@pytest.mark.asyncio
async def test_default_page_resolves_ambiguity_with_multiple_pages(init_db, db_session: AsyncSession, _mock_chain):
    org, acc = await _make_account(db_session, "exec-default-page")
    p1 = MetaPage(organization_id=org.id, external_page_id="page-1", name="P1",
                  whatsapp_number="51999888777", status="ACTIVE")
    p2 = MetaPage(organization_id=org.id, external_page_id="page-2", name="P2",
                  whatsapp_number="51999888778", status="ACTIVE")
    db_session.add_all([p1, p2, MetaCreative(ad_account_id=acc.id, organization_id=org.id, image_hash="h1")])
    await db_session.flush()
    acc.default_page_id = p2.id
    action = _create_action(org, acc)
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "EXECUTING"
    drafts = (await db_session.execute(
        __import__("sqlalchemy").select(CampaignDraft).where(CampaignDraft.organization_id == org.id)
    )).scalars().all()
    assert len(drafts) == 1
    assert drafts[0].payload["page_id"] == "page-2"
    assert drafts[0].payload["whatsapp_number"] == "51999888778"


@pytest.mark.asyncio
async def test_default_page_without_whatsapp_needs_input(init_db, db_session: AsyncSession, _mock_chain):
    """Si el usuario eligió una default sin WhatsApp, se pide completar ese dato
    explícitamente en vez de caer en silencio a otra página que sí tenga."""
    org, acc = await _make_account(db_session, "exec-default-no-wa")
    p1 = MetaPage(organization_id=org.id, external_page_id="page-1", name="Con WA",
                  whatsapp_number="51999888777", status="ACTIVE")
    p2 = MetaPage(organization_id=org.id, external_page_id="page-2", name="Sin WA", status="ACTIVE")
    db_session.add_all([p1, p2, MetaCreative(ad_account_id=acc.id, organization_id=org.id, image_hash="h1")])
    await db_session.flush()
    acc.default_page_id = p2.id
    action = _create_action(org, acc)
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "NEEDS_INPUT"
    assert "WhatsApp" in result.execution_result["error"]
    _, mock_chain_instance = _mock_chain
    mock_chain_instance.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_without_default_page_falls_back_to_single_page_rule(init_db, db_session: AsyncSession, _mock_chain):
    """Sin default configurada, la regla original sigue vigente: exactamente 1
    página activa con WhatsApp resuelve sola."""
    org, acc = await _make_account(db_session, "exec-fallback-rule")
    db_session.add_all([
        MetaPage(organization_id=org.id, external_page_id="page-1", name="Única",
                 whatsapp_number="51999888777", status="ACTIVE"),
        MetaCreative(ad_account_id=acc.id, organization_id=org.id, image_hash="h1"),
    ])
    action = _create_action(org, acc)
    db_session.add(action)
    await db_session.commit()

    result = await AIExecutor(db_session).execute_action(acc, action)

    assert result.status == "EXECUTING"
    drafts = (await db_session.execute(
        __import__("sqlalchemy").select(CampaignDraft).where(CampaignDraft.organization_id == org.id)
    )).scalars().all()
    assert drafts[0].payload["page_id"] == "page-1"
