"""El monitor de backfill por terminal.

El banner dice "3 de 29 meses" y nada más. Cuando eso tarda, la pregunta real
es si está avanzando o si murió — y un backfill lento y uno colgado se ven
igual desde afuera. Esto verifica que el script consulte lo que dice consultar.
"""
import datetime
import importlib.util
import pathlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.models.sync_job import SyncJob

_RUTA = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "estado_historial.py"
_spec = importlib.util.spec_from_file_location("estado_historial", _RUTA)
estado_historial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(estado_historial)


def test_las_duraciones_se_leen_en_criollo():
    assert estado_historial._duracion(45) == "45s"
    assert estado_historial._duracion(125) == "2m 5s"
    assert estado_historial._duracion(7325) == "2h 2m"
    assert estado_historial._duracion(-5) == "0s"


@pytest.mark.asyncio
async def test_el_informe_corre_contra_una_cuenta_de_verdad(db_session: AsyncSession, capsys):
    """Que las queries sean válidas contra el esquema real. Un script de
    diagnóstico que explota cuando lo necesitás no sirve de nada."""
    org = Organization(slug="hist", name="Hist", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(organization_id=org.id, platform="META",
                    external_account_id="act_hist", name="Hist Acc", status="ACTIVE")
    db_session.add(acc)
    await db_session.flush()

    db_session.add(SyncJob(
        ad_account_id=acc.id, organization_id=org.id, sync_type="BACKFILL",
        status="RUNNING", started_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=8),
        records_synced=1200,
        payload_json={"months_total": 29, "months_done": 3, "last_month": "2026-05"},
    ))
    db_session.add(CampaignSnapshot(
        ad_account_id=acc.id, organization_id=org.id, campaign_id="c1",
        campaign_name="X", status="ACTIVE", date=datetime.date.today(),
        spend=10.0, impressions=100, clicks=5, conversions=1, conversations=1, results=1,
    ))
    await db_session.commit()

    await estado_historial._informe(db_session, acc)

    salida = capsys.readouterr().out
    assert "act_hist" in salida
    assert "3 de 29" in salida, salida
    assert "RUNNING" in salida


@pytest.mark.asyncio
async def test_avisa_cuando_el_backfill_nunca_corrio(db_session: AsyncSession, capsys):
    org = Organization(slug="nunca", name="Nunca", status="ACTIVE")
    db_session.add(org)
    await db_session.flush()
    acc = AdAccount(organization_id=org.id, platform="META",
                    external_account_id="act_nunca", name="N", status="ACTIVE")
    db_session.add(acc)
    await db_session.commit()

    await estado_historial._informe(db_session, acc)
    assert "nunca corrió" in capsys.readouterr().out
