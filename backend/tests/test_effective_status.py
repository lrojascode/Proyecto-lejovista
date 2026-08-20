"""Estado efectivo de entrega (execution/01 §4, execution/04 §2).

La distinción que estos tests protegen: `status` dice cómo quedó CONFIGURADA la
campaña, `effective_status` dice si está ENTREGANDO. Filtrar por el primero da
una respuesta equivocada, y es el error que el documento de ITS marca
explícitamente.
"""
import datetime
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization


async def _signup(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.example.com", "password": "securepassword123",
        "full_name": "ES Tester", "organization_slug": slug, "organization_name": "ES Org",
    })
    body = res.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["organization_id"]


async def _seed(db: AsyncSession, org_id: str, campaigns):
    acc = AdAccount(
        organization_id=org_id, platform="META", external_account_id="act_es",
        name="ES Acc", status="ACTIVE",
    )
    db.add(acc)
    await db.flush()
    today = datetime.date.today()
    for camp_id, name, status, effective in campaigns:
        db.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org_id, campaign_id=camp_id,
            campaign_name=name, status=status, effective_status=effective,
            date=today, spend=10.0, conversions=1,
        ))
    await db.commit()
    return acc


@pytest.mark.asyncio
async def test_only_delivering_usa_effective_status_no_status(
    async_client: AsyncClient, db_session: AsyncSession
):
    """El caso del documento: una campaña ACTIVE cuyo conjunto está pausado NO
    está al aire, y filtrar por `status` la mostraría igual."""
    headers, org_id = await _signup(async_client, "es-filtro")
    await _seed(db_session, org_id, [
        ("c_viva", "Al aire", "ACTIVE", "ACTIVE"),
        ("c_fantasma", "Activa pero muerta", "ACTIVE", "ADSET_PAUSED"),
        ("c_pausada", "Pausada", "PAUSED", "PAUSED"),
    ])

    res = await async_client.get("/api/v1/campaigns/recent?only_delivering=true", headers=headers)
    assert res.status_code == 200
    names = {c["campaign_name"] for c in res.json()}
    assert names == {"Al aire"}

    # Filtrar por `status` habria devuelto tambien la fantasma: esa es la trampa.
    todas = await async_client.get("/api/v1/campaigns/recent", headers=headers)
    activas_por_status = {c["campaign_name"] for c in todas.json() if c["status"] == "ACTIVE"}
    assert "Activa pero muerta" in activas_por_status


@pytest.mark.asyncio
async def test_sin_filtro_devuelve_todo(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id = await _signup(async_client, "es-todas")
    await _seed(db_session, org_id, [
        ("c1", "Una", "ACTIVE", "ACTIVE"),
        ("c2", "Otra", "ACTIVE", "ADSET_PAUSED"),
    ])
    res = await async_client.get("/api/v1/campaigns/recent", headers=headers)
    assert len({c["campaign_name"] for c in res.json()}) == 2


@pytest.mark.asyncio
async def test_snapshots_viejos_sin_dato_no_desaparecen(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Los snapshots anteriores a la migración tienen effective_status nulo.
    Esconderlos dejaría al operador con una pantalla vacía y sin explicación —
    peor que mostrarlos."""
    headers, org_id = await _signup(async_client, "es-nulo")
    await _seed(db_session, org_id, [("c_vieja", "De antes", "ACTIVE", None)])

    res = await async_client.get("/api/v1/campaigns/recent?only_delivering=true", headers=headers)
    assert {c["campaign_name"] for c in res.json()} == {"De antes"}


@pytest.mark.asyncio
async def test_el_sync_simulado_produce_una_campana_activa_que_no_entrega():
    """En simulación tiene que existir el caso ACTIVE-pero-sin-entregar, o el
    filtro no se puede probar sin pegarle a Meta."""
    from app.services.meta_ads import sync as sync_module
    import inspect

    fuente = inspect.getsource(sync_module)
    assert '"effective_status": "ADSET_PAUSED"' in fuente
    assert '"fields": "id,name,status,effective_status' in fuente
