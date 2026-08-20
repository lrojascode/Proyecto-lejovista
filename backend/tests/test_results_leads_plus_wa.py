"""`resultados = leads + conversaciones de WhatsApp` (execution/04 §1).

El documento de ITS es tajante: nunca dividir el gasto de una campaña de
WhatsApp entre `leads`. Medida mal da S/101.78 por lead; medida bien, S/3.38 por
conversación. El producto colapsaba las dos métricas con
`conversations or conversions` — ELEGÍA una en vez de sumar — así que en una
cuenta que corre los dos tipos el número final no era "resultados", era "lo que
haya".
"""
import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.kpi_period import get_kpis_with_deltas
from app.services.meta_ads.sync import MetaAdsSyncService
from app.services.campaign_chat.questions import _answer_leads
from app.services.campaign_chat.period import Period


_WA = "onsite_conversion.messaging_conversation_started_7d"


def test_el_conteo_crudo_no_usa_el_proxy():
    """`_extract_conversions_conversations` rellena `conversations` con los
    leads cuando no hay mensajería. Sumar sobre eso contaría cada lead dos
    veces — por eso `_count_results` va sobre los valores crudos."""
    solo_form = [{"action_type": "lead", "value": 10}]
    # El proxy dice 10 conversaciones que no existen...
    _, proxy = MetaAdsSyncService._extract_conversions_conversations(solo_form)
    assert proxy == 10
    # ...pero los resultados reales son 10, no 20.
    assert MetaAdsSyncService._count_results(solo_form) == 10


def test_una_cuenta_mixta_suma_las_dos():
    mixta = [{"action_type": "lead", "value": 10}, {"action_type": _WA, "value": 25}]
    assert MetaAdsSyncService._count_results(mixta) == 35
    assert MetaAdsSyncService._extract_raw_results(mixta) == (10, 25)
    # Lo que hacía antes: elegir uno. 25 o 10, nunca 35.
    _, elegido = MetaAdsSyncService._extract_conversions_conversations(mixta)
    assert elegido == 25


def test_solo_whatsapp():
    solo_wa = [{"action_type": _WA, "value": 25}]
    assert MetaAdsSyncService._count_results(solo_wa) == 25
    assert MetaAdsSyncService._extract_raw_results(solo_wa) == (0, 25)


async def _seed(db: AsyncSession, slug: str, filas):
    org = Organization(slug=slug, name="Res Org", status="ACTIVE")
    db.add(org)
    await db.flush()
    acc = AdAccount(organization_id=org.id, platform="META",
                    external_account_id="act_res", name="Res Acc", status="ACTIVE")
    db.add(acc)
    await db.flush()
    # Ayer y hacia atras: get_window_kpis usa `date < today`, asi que un
    # snapshot de hoy queda fuera de la ventana.
    ayer = datetime.date.today() - datetime.timedelta(days=1)
    for i, (conversions, conversations, results) in enumerate(filas):
        db.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org.id, campaign_id=f"c{i}",
            campaign_name=f"C{i}", status="ACTIVE", date=ayer - datetime.timedelta(days=i),
            spend=100.0, conversions=conversions, conversations=conversations, results=results,
        ))
    await db.commit()
    return org, acc


@pytest.mark.asyncio
async def test_los_kpis_suman_en_vez_de_elegir(db_session: AsyncSession):
    org, acc = await _seed(db_session, "res-mixta", [(10, 25, 35)])
    kpis = await get_kpis_with_deltas(db_session, acc.id, window_days=7)
    actual = kpis["current"]
    assert actual["results"] == 35
    assert actual["leads"] == 10
    assert actual["whatsapp"] == 25
    # El costo por resultado se calcula sobre la SUMA, no sobre una de las dos.
    assert actual["cost_per_conv"] == pytest.approx(100.0 / 35, abs=0.01)


@pytest.mark.asyncio
async def test_los_datos_viejos_sin_la_columna_no_doble_cuentan(db_session: AsyncSession):
    """Los snapshots anteriores a la migración tienen `results` en None y su
    `conversations` trae el proxy. Ahí se conserva el comportamiento anterior:
    preferible a inventar una suma que contaría cada lead dos veces."""
    org, acc = await _seed(db_session, "res-viejos", [(10, 10, None)])
    kpis = await get_kpis_with_deltas(db_session, acc.id, window_days=7)
    assert kpis["current"]["results"] == 10   # no 20


def test_el_chat_nombra_cada_cosa_por_lo_que_es():
    """Antes decía "conversaciones por WhatsApp" para TODO, incluso en una
    cuenta que solo corre formularios."""
    per = Period(30, "los últimos 30 días")

    mixta = _answer_leads(per, {"current": {
        "results": 35, "leads": 10, "whatsapp": 25, "cost_per_conv": 8.5}, "deltas_pct": {}}, None)
    assert "35 resultados" in mixta
    assert "10 por formulario" in mixta
    assert "25 conversaciones de WhatsApp" in mixta

    form = _answer_leads(per, {"current": {
        "results": 10, "leads": 10, "whatsapp": 0, "cost_per_conv": 12.0}, "deltas_pct": {}}, None)
    assert "10 leads por formulario" in form
    assert "WhatsApp" not in form

    wa = _answer_leads(per, {"current": {
        "results": 25, "leads": 0, "whatsapp": 25, "cost_per_conv": 3.38}, "deltas_pct": {}}, None)
    assert "25 conversaciones por WhatsApp" in wa
