"""Q2: aprendizaje longitudinal por sincronización.

Verifica las piezas deterministas de `account_learning` (KPIs, mejor segmento,
top/bottom, observaciones y veredicto de tendencia) como tabla de casos puros, y
el flujo con DB `record_sync_learning` → `get_latest_snapshot` →
`recent_trajectory`.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_learning_snapshot import AccountLearningSnapshot
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.account_learning import (
    _kpis_from_report, _best_segment, _segmentation_winners, _top_bottom,
    _build_observations, _verdict,
    record_sync_learning, get_latest_snapshot, recent_trajectory,
)


def _report(spend, conversations, campaigns=None, segmentation=None):
    return {
        "kpis": {"total_spend": spend, "total_conversations": conversations},
        "campaigns": campaigns or [],
        "segmentation": segmentation or {},
    }


# ---------------------------------------------------------------------------
# _kpis_from_report (puro)
# ---------------------------------------------------------------------------

def test_kpis_from_report_with_conversations():
    assert _kpis_from_report(_report(100.0, 40)) == {
        "spend": 100.0, "conversations": 40, "cost_per_conv": 2.5,
    }


def test_kpis_from_report_zero_conversations_falls_back_to_spend():
    k = _kpis_from_report(_report(50.0, 0))
    assert k["conversations"] == 0
    assert k["cost_per_conv"] == 50.0


# ---------------------------------------------------------------------------
# _best_segment / _segmentation_winners (puro)
# ---------------------------------------------------------------------------

def test_best_segment_picks_lowest_cost_ignoring_zero_conv():
    bucket = {
        "facebook": {"spend": 100.0, "conversions": 10},   # 10.0/conv
        "instagram": {"spend": 30.0, "conversions": 10},   # 3.0/conv ← mejor
        "audiencia": {"spend": 20.0, "conversions": 0},    # sin conv → se ignora
    }
    best = _best_segment(bucket)
    assert best["name"] == "instagram"
    assert best["cost_per_conv"] == 3.0


def test_best_segment_none_without_conversions():
    assert _best_segment({"x": {"spend": 10.0, "conversions": 0}}) is None
    assert _best_segment({}) is None


def test_segmentation_winners_skips_groups_without_data():
    seg = {
        "placements": {"feed": {"spend": 20.0, "conversions": 10}},
        "device": {"mobile": {"spend": 15.0, "conversions": 5}},
        "age": {},
    }
    out = _segmentation_winners(_report(0, 0, segmentation=seg))
    assert out["placement"]["name"] == "feed"
    assert out["device"]["name"] == "mobile"
    assert "age" not in out


# ---------------------------------------------------------------------------
# _top_bottom (puro)
# ---------------------------------------------------------------------------

def test_top_bottom_separates_winners_and_worst():
    campaigns = [
        {"name": "A", "cost_per_conv": 1.0, "conversations": 10},
        {"name": "B", "cost_per_conv": 2.0, "conversations": 5},
        {"name": "Z", "cost_per_conv": 50.0, "conversations": 0},  # perdedora
    ]
    top, bottom = _top_bottom(_report(0, 0, campaigns=campaigns))
    assert [c["name"] for c in top] == ["A", "B"]   # solo con conversaciones
    assert bottom[-1]["name"] == "Z"                # la peor (última de la lista)


# ---------------------------------------------------------------------------
# _verdict (puro) — tabla de casos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("conv,delta_cost,prev,expected", [
    (0, None, None, "NO_DATA"),          # sin conversaciones
    (10, None, None, "BASELINE"),        # primera medición
    (10, -15.0, object(), "IMPROVING"),  # costo baja ≥10%
    (10, 15.0, object(), "DEGRADING"),   # costo sube ≥10%
    (10, 3.0, object(), "STABLE"),       # movimiento menor
    (10, None, object(), "STABLE"),      # sin delta con qué comparar
])
def test_verdict_table(conv, delta_cost, prev, expected):
    kpis = {"spend": 100.0, "conversations": conv, "cost_per_conv": 2.5}
    assert _verdict(kpis, {"cost_per_conv": delta_cost}, prev) == expected


# ---------------------------------------------------------------------------
# _build_observations (puro)
# ---------------------------------------------------------------------------

def _snap(kpis, seg=None):
    return AccountLearningSnapshot(kpis_json=kpis, segmentation_json=seg or {})


def test_observations_no_conversations():
    obs = _build_observations(
        {"spend": 10.0, "conversations": 0, "cost_per_conv": 10.0}, {}, {}, None
    )
    assert len(obs) == 1
    assert "no registró conversaciones" in obs[0]


def test_observations_baseline_first_measure():
    kpis = {"spend": 100.0, "conversations": 40, "cost_per_conv": 2.5}
    obs = _build_observations(kpis, {}, {}, None)
    assert any("referencia" in o.lower() for o in obs)


def test_observations_cost_down_and_consistent_segment():
    prev = _snap(
        {"cost_per_conv": 5.0, "conversations": 30},
        {"device": {"name": "mobile"}},
    )
    kpis = {"spend": 100.0, "conversations": 40, "cost_per_conv": 2.5}
    deltas = {"cost_per_conv": -50.0, "conversations": 33.0}
    seg = {"device": {"name": "mobile"}}
    joined = " ".join(_build_observations(kpis, deltas, seg, prev))
    assert "bajó" in joined                 # costo/conversación bajó
    assert "más" in joined                  # más conversaciones
    assert "mobile" in joined               # segmento eficiente que se mantiene


def test_observations_flat_cost_reads_as_stable():
    prev = _snap({"cost_per_conv": 2.5, "conversations": 40})
    kpis = {"spend": 100.0, "conversations": 40, "cost_per_conv": 2.5}
    joined = " ".join(_build_observations(kpis, {"cost_per_conv": 0.0, "conversations": 0.0}, {}, prev))
    assert "prácticamente igual" in joined


# ---------------------------------------------------------------------------
# Flujo con DB: record_sync_learning → get_latest_snapshot → recent_trajectory
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


@pytest.mark.asyncio
async def test_record_and_trajectory_flow(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session, "learn-org")

    # 1ª sincronización: sin previo → BASELINE.
    report1 = _report(
        100.0, 40,
        campaigns=[{"name": "A", "cost_per_conv": 2.5, "conversations": 40, "spend": 100.0}],
        segmentation={"device": {"mobile": {"spend": 50.0, "conversions": 25}}},
    )
    assert await get_latest_snapshot(db_session, acc.id) is None
    snap1 = await record_sync_learning(db_session, acc, report1, None)
    await db_session.commit()
    assert snap1.verdict == "BASELINE"
    assert snap1.kpis_json["cost_per_conv"] == 2.5
    assert snap1.segmentation_json["device"]["name"] == "mobile"

    # 2ª sincronización: costo/conversación baja 40% → IMPROVING.
    report2 = _report(
        60.0, 40,
        campaigns=[{"name": "A", "cost_per_conv": 1.5, "conversations": 40, "spend": 60.0}],
        segmentation={"device": {"mobile": {"spend": 30.0, "conversions": 25}}},
    )
    prev = await get_latest_snapshot(db_session, acc.id)
    assert prev.id == snap1.id
    snap2 = await record_sync_learning(db_session, acc, report2, prev)
    await db_session.commit()
    assert snap2.verdict == "IMPROVING"
    assert snap2.deltas_json["cost_per_conv"] is not None

    # Trayectoria: más reciente primero, compacta y serializable.
    traj = await recent_trajectory(db_session, acc.id, limit=5)
    assert [t["verdict"] for t in traj] == ["IMPROVING", "BASELINE"]
    assert set(traj[0].keys()) == {"created_at", "verdict", "kpis", "deltas", "observations"}
