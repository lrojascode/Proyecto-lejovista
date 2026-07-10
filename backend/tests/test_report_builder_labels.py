"""C-2: la clasificación de una campaña debe ser la MISMA en /reporte
(ReportBuilder) y en la narrativa de la IA (llm.py) — antes cada uno tenía su
propio umbral hardcodeado y podían discrepar para la misma campaña.
"""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.services.report_builder import ReportBuilder
from app.services.ai.llm import OpenRouterClient


async def _make_account(db: AsyncSession, slug="rb-labels-org"):
    org = Organization(slug=slug, name="RB Labels Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(
        organization_id=org.id, platform="META",
        external_account_id="act_1", name="RB Labels Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    return org, acc


@pytest.mark.asyncio
async def test_report_builder_uses_uppercase_relative_labels(init_db, db_session: AsyncSession):
    org, acc = await _make_account(db_session)
    today = datetime.date.today()
    # Dos campañas: una barata (relativa al target_cpl real de la cuenta) y
    # una muy cara sin conversiones.
    db_session.add_all([
        CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id="cheap",
            campaign_name="Barata", status="ACTIVE", budget=50.0, spend=20.0,
            conversions=10, impressions=2000, clicks=40, date=today,
        ),
        CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id="pricey",
            campaign_name="Cara sin conversión", status="ACTIVE", budget=50.0, spend=80.0,
            conversions=0, impressions=2000, clicks=10, date=today,
        ),
    ])
    await db_session.commit()

    report = await ReportBuilder(db_session).build(acc, window_days=7)
    by_id = {c["campaign_id"]: c for c in report["campaigns"]}

    assert by_id["cheap"]["label"] in ("GANADORA", "BUENA")
    assert by_id["pricey"]["label"] == "PAUSAR"
    assert report["kpis"]["target_cpl"] > 0
    assert "budget" in by_id["cheap"]


@pytest.mark.asyncio
async def test_mock_narrative_agrees_with_report_builder_classification():
    """Mismos números, mismo target_cpl -> la campaña cae en el mismo balde
    (ganadoras/alertas/fallidas) en la narrativa mock que en /reporte."""
    org = Organization(slug="mock-agree-org", name="Mock Agree Org")
    acc = AdAccount(organization_id="o", platform="META", external_account_id="1", name="Acc")

    from app.services.ai.thresholds import classify_campaign

    target_cpl = 5.0
    raw_data = {
        "c1": {"name": "Barata", "spend": 15.0, "conversations": 10, "conversions": 10, "ctr": 2.0},
        "c2": {"name": "Cara", "spend": 100.0, "conversations": 2, "conversions": 2, "ctr": 0.5},
        "c3": {"name": "Sin conversión", "spend": 30.0, "conversations": 0, "conversions": 0, "ctr": 0.2},
    }
    brief_context = {"target_cpl": target_cpl}

    narrative = OpenRouterClient()._generate_mock_narrative(org, acc, raw_data, brief_context)

    # classify_campaign es la fuente de verdad para dónde debería caer cada una.
    expected = {
        name: classify_campaign(d["spend"] / d["conversations"] if d["conversations"] else d["spend"], d["conversations"], target_cpl)
        for name, d in ((v["name"], v) for v in raw_data.values())
    }
    assert expected["Barata"] in ("GANADORA", "BUENA")
    assert expected["Cara"] == "CARA"
    assert expected["Sin conversión"] == "PAUSAR"

    # La narrativa mock sigue el contrato de 5 secciones y agrupa igual.
    assert "## Ganadoras" in narrative and "Barata" in narrative.split("## Ganadoras")[1].split("## En alerta")[0]
    assert "Cara" in narrative.split("## Fallidas")[1].split("## Prioridades")[0]
    assert "Sin conversión" in narrative.split("## Fallidas")[1].split("## Prioridades")[0]
    for section in ("## Resumen", "## Ganadoras", "## En alerta", "## Fallidas", "## Prioridades de la semana"):
        assert section in narrative
