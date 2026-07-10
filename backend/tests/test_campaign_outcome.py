"""Fase 1 (C-1): outcome loop del aprendizaje.

Verifica el veredicto determinista (tabla de casos de `compute_verdict`) y el
flujo completo `evaluate_pending_outcomes`: resolver la campaña real vía el
draft, agregar snapshots sintéticos de la ventana y persistir WIN/LOSS/NEUTRAL/
NOT_ACTIVATED en `outcome_json`.
"""
import datetime
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_learning_log import CampaignLearningLog
from app.models.campaign_snapshot import CampaignSnapshot
from app.services.campaign_outcome import compute_verdict, evaluate_pending_outcomes


# ---------------------------------------------------------------------------
# compute_verdict: tabla de casos (puro, sin DB)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "spend,conversations,expected_cost,verdict",
    [
        # Costo esperado S/.5: WIN hasta 5*1.2=6, LOSS desde >10, NEUTRAL entre medio.
        (100.0, 20, 5.0, "WIN"),        # 5.00/conv — clavado en lo esperado
        (120.0, 20, 5.0, "WIN"),        # 6.00/conv — justo en el borde del +20%
        (140.0, 20, 5.0, "NEUTRAL"),    # 7.00/conv — caro pero no el doble
        (220.0, 20, 5.0, "LOSS"),       # 11.00/conv — más del doble
        (80.0, 0, 5.0, "LOSS"),         # gastó sin conversaciones
        (0.0, 0, 5.0, "NOT_ACTIVATED"), # ni gasto ni conversaciones
        (100.0, 20, None, "NEUTRAL"),   # sin expectativa (cuenta sin histórico)
    ],
)
def test_compute_verdict_table(spend, conversations, expected_cost, verdict):
    result = compute_verdict(
        spend=spend, conversations=conversations, expected_cost_per_conv=expected_cost
    )
    assert result["verdict"] == verdict
    assert result["reason"]  # siempre explica el porqué


# ---------------------------------------------------------------------------
# evaluate_pending_outcomes: flujo completo con DB
# ---------------------------------------------------------------------------

async def _make_account(db: AsyncSession, slug: str):
    org = Organization(slug=slug, name=f"Org {slug}", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id=f"act_{slug}", name=f"Acc {slug}", status="ACTIVE",
    )
    acc.set_access_token("mock_access_token")
    db.add(acc)
    await db.flush()
    return org, acc


async def _make_created_log(
    db: AsyncSession, org, acc, *,
    campaign_id: str | None = "camp_real_1",
    draft_status: str = "CREATED",
    days_ago: int = 10,
    suggested_budget: float = 100.0,   # → costo esperado S/.5 por conversación
    window_days: int = 7,
):
    payload = {"product_name": "Lentes Sol"}
    if campaign_id:
        payload["result"] = {"campaign_id": campaign_id}
    draft = CampaignDraft(
        organization_id=org.id, name="Lentes Sol",
        payload=payload, status=draft_status,
    )
    db.add(draft)
    await db.flush()

    log = CampaignLearningLog(
        organization_id=org.id, ad_account_id=acc.id, draft_id=draft.id,
        product="Lentes Sol",
        params_json={"suggested_budget": suggested_budget, "budget": 100.0},
        analysis_json={
            "summary": "Parto de la configuración ganadora.",
            "evaluation_window_days": window_days,
        },
        maturity_json={"mode": "exploration"},
        risk_level="LOW", status="CREATED",
        created_at=datetime.datetime.utcnow() - datetime.timedelta(days=days_ago),
    )
    db.add(log)
    await db.commit()
    return log


async def _add_snapshots(db: AsyncSession, org, acc, campaign_id, *, spend, conversations, days_ago=9, days=3):
    """Reparte spend/conversations en `days` snapshots diarios dentro de la ventana."""
    base = datetime.date.today() - datetime.timedelta(days=days_ago)
    for i in range(days):
        db.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id,
            campaign_id=campaign_id, campaign_name="Lentes Sol", status="ACTIVE",
            spend=spend / days, conversations=conversations // days if i else conversations - (conversations // days) * (days - 1),
            conversions=0, clicks=50, impressions=1000,
            date=base + datetime.timedelta(days=i),
        ))
    await db.commit()


@pytest.mark.asyncio
async def test_win_outcome_persisted(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "win-org")
    log = await _make_created_log(db_session, org, acc)
    # 20 conversaciones con S/.100 → S/.5/conv = exactamente lo esperado → WIN.
    await _add_snapshots(db_session, org, acc, "camp_real_1", spend=100.0, conversations=20)

    log_id = log.id
    counts = await evaluate_pending_outcomes(db_session)
    assert counts["evaluated"] == 1

    db_session.expire_all()
    fresh = (await db_session.execute(
        select(CampaignLearningLog).where(CampaignLearningLog.id == log_id)
    )).scalar_one()
    assert fresh.status == "EVALUATED"
    assert fresh.outcome_recorded_at is not None
    out = fresh.outcome_json
    assert out["verdict"] == "WIN"
    assert out["cost_per_conv"] == 5.0
    assert out["expected_cost_per_conv"] == 5.0
    assert out["spend"] == 100.0
    assert out["conversations"] == 20
    assert out["campaign_id"] == "camp_real_1"
    assert out["hypothesis"] == "Parto de la configuración ganadora."


@pytest.mark.asyncio
async def test_loss_when_spend_without_conversations(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "loss-org")
    log = await _make_created_log(db_session, org, acc)
    await _add_snapshots(db_session, org, acc, "camp_real_1", spend=90.0, conversations=0)

    log_id = log.id
    await evaluate_pending_outcomes(db_session)
    db_session.expire_all()
    fresh = await db_session.get(CampaignLearningLog, log_id)
    assert fresh.outcome_json["verdict"] == "LOSS"
    assert fresh.outcome_json["cost_per_conv"] is None


@pytest.mark.asyncio
async def test_neutral_when_cost_above_expected_but_not_double(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "neutral-org")
    log = await _make_created_log(db_session, org, acc)
    # S/.140 / 20 conv = S/.7/conv: sobre el esperado (5*1.2=6) pero bajo el doble (10).
    await _add_snapshots(db_session, org, acc, "camp_real_1", spend=140.0, conversations=20)

    log_id = log.id
    await evaluate_pending_outcomes(db_session)
    db_session.expire_all()
    fresh = await db_session.get(CampaignLearningLog, log_id)
    assert fresh.outcome_json["verdict"] == "NEUTRAL"


@pytest.mark.asyncio
async def test_not_activated_when_saga_failed(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "failed-org")
    log = await _make_created_log(
        db_session, org, acc, campaign_id=None, draft_status="FAILED"
    )

    log_id = log.id
    await evaluate_pending_outcomes(db_session)
    db_session.expire_all()
    fresh = await db_session.get(CampaignLearningLog, log_id)
    assert fresh.outcome_json["verdict"] == "NOT_ACTIVATED"
    assert fresh.status == "EVALUATED"


@pytest.mark.asyncio
async def test_not_activated_when_no_snapshots(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "ghost-org")
    log = await _make_created_log(db_session, org, acc)  # draft OK pero sin snapshots

    log_id = log.id
    await evaluate_pending_outcomes(db_session)
    db_session.expire_all()
    fresh = await db_session.get(CampaignLearningLog, log_id)
    assert fresh.outcome_json["verdict"] == "NOT_ACTIVATED"


@pytest.mark.asyncio
async def test_window_not_elapsed_stays_pending(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "young-org")
    log = await _make_created_log(db_session, org, acc, days_ago=2, window_days=7)
    await _add_snapshots(db_session, org, acc, "camp_real_1", spend=100.0, conversations=20, days_ago=1, days=1)

    log_id = log.id
    counts = await evaluate_pending_outcomes(db_session)
    assert counts == {"evaluated": 0, "waiting": 1}

    db_session.expire_all()
    fresh = await db_session.get(CampaignLearningLog, log_id)
    assert fresh.status == "CREATED"
    assert fresh.outcome_json is None
