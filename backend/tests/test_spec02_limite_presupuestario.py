"""Spec 02 — Límite presupuestario mensual y dinero exacto.

Un test por criterio de aceptación de
`docs/specs/02-limite-presupuestario-mensual-y-dinero-exacto.md`.

La frontera es la API de creación, de aprobación y de modificación de
presupuesto: la compuerta se prueba por lo que devuelven esas superficies, no
llamando a la función que calcula el margen.

El valor de los criterios 7 y 8 está en el PAR: uno solo no distingue "la
compuerta usa aritmética exacta" de "la compuerta rechaza de más".
"""
import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.ai_action import AIAction
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.budget_gate import (
    BudgetLimitExceeded, MixedCurrencies, assert_cabe_en_el_mes, estado_del_mes,
)
from app.services.budget_gate import hoy_de_negocio
from app.services.money import to_money
from conftest import cuenta_de_prueba


@pytest.fixture(autouse=True)
def _sin_efectos_de_creacion(mocker):
    """La Saga y el validador quedan fuera: acá se prueba la compuerta, no la
    creación real."""
    mock_chain = mocker.patch("app.services.campaign_creation.chain")
    mock_chain.return_value.on_error.return_value = mock_chain.return_value
    mocker.patch(
        "app.services.campaign_creation.CampaignValidator.validate",
        autospec=True, return_value=None,
    )


async def _signup(client: AsyncClient, slug: str):
    res = await client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.example.com", "password": "securepassword123",
        "full_name": "Spec02", "organization_slug": slug, "organization_name": "Spec02 Org",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


async def _org_con_limite(db: AsyncSession, org_id: str, limite: str, moneda: str = "PEN"):
    org = await db.get(Organization, org_id)
    org.set_limite_mensual(limite)
    db.add(org)
    acc = await cuenta_de_prueba(db, org_id)
    acc.currency = moneda
    db.add(acc)
    await db.commit()
    return acc


async def _gasto_real(db: AsyncSession, org_id: str, acc_id: str, monto: float, cuando=None):
    db.add(CampaignSnapshot(
        ad_account_id=acc_id, organization_id=org_id,
        campaign_id="c1", campaign_name="Campaña", status="ACTIVE",
        spend=monto, date=(cuando or hoy_de_negocio()),
    ))
    await db.commit()


def _peticion(acc_id: str, budget: float, budget_type: str = "LIFETIME", days: int = 7):
    return {
        "ad_account_id": acc_id, "page_id": "p1", "product_name": "Curso",
        "budget": budget, "budget_type": budget_type, "days": days,
        "whatsapp_number": "51999999999", "copy_text": "Texto de prueba",
    }


# --------------------------------------------------------------------------
# Criterios 1-2 · la compuerta permite lo que cabe y rechaza lo que no
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_crear_dentro_del_margen_se_permite(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c1-cabe")
    acc = await _org_con_limite(db_session, body["user"]["organization_id"], "1000.00")

    res = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 300.0)
    )
    assert res.status_code == 202, res.text


@pytest.mark.asyncio
async def test_c2_crear_por_encima_del_margen_se_rechaza_con_las_cifras(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Antes: el límite existía como campo, se mostraba, y ningún camino de
    ejecución lo consultaba."""
    body, headers = await _signup(async_client, "c2-nocabe")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1000.00")
    await _gasto_real(db_session, org_id, acc.id, 800.0)

    res = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 500.0)
    )
    assert res.status_code == 400, res.text
    detalle = res.json()["detail"]
    assert detalle["code"] == "MONTHLY_BUDGET_EXCEEDED"
    assert detalle["limite_mensual"] == "1000.00"
    assert detalle["consumido"] == "800.00"
    assert detalle["margen_disponible"] == "200.00"
    assert detalle["solicitado"] == "500.00"


# --------------------------------------------------------------------------
# Criterios 3-5 · aprobación y escalado
# --------------------------------------------------------------------------

async def _accion(db: AsyncSession, org_id: str, acc_id: str, params: dict,
                  tipo: str = "MODIFY_BUDGET") -> AIAction:
    accion = AIAction(
        organization_id=org_id, ad_account_id=acc_id, action_type=tipo,
        status="PROPOSED", target_campaign_id="camp-1", target_adset_id="adset-1",
        parameters=params, risk_level="LOW", title="Prueba",
        description="Acción de prueba de la spec 02",
    )
    db.add(accion)
    await db.commit()
    return accion


@pytest.mark.asyncio
async def test_c3_aprobar_por_encima_del_margen_se_rechaza(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """La propuesta pudo generarse cuando había margen; aprobarla es la
    superficie que compromete el gasto."""
    mocker.patch("app.api.v1.actions.execute_action_task")
    body, headers = await _signup(async_client, "c3-aprobar")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1000.00")
    await _gasto_real(db_session, org_id, acc.id, 950.0)

    # Incremento de 100 contra un margen de 50. Se cuenta el INCREMENTO y no el
    # monto nuevo: lo anterior ya está contado en el gasto real, y contarlo
    # entero haría que subir de 100 a 110 consumiera 110 de margen.
    accion = await _accion(db_session, org_id, acc.id,
                           {"old_budget": 100.0, "new_budget": 200.0})

    res = await async_client.post(
        f"/api/v1/actions/{accion.id}/approve", headers=headers,
        json={"approve": True},
    )
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "MONTHLY_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_c3b_aprobar_un_incremento_que_cabe_se_permite(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """El par del criterio 3: se cuenta el incremento, no el monto nuevo."""
    mocker.patch("app.api.v1.actions.execute_action_task")
    body, headers = await _signup(async_client, "c3b-incremento")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1000.00")
    await _gasto_real(db_session, org_id, acc.id, 950.0)

    accion = await _accion(db_session, org_id, acc.id,
                           {"old_budget": 100.0, "new_budget": 140.0})
    res = await async_client.post(
        f"/api/v1/actions/{accion.id}/approve", headers=headers, json={"approve": True}
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_c4_escalar_por_encima_del_margen_se_rechaza(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    mocker.patch("app.api.v1.actions.execute_action_task")
    body, headers = await _signup(async_client, "c4-escalar")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "500.00")
    await _gasto_real(db_session, org_id, acc.id, 490.0)

    accion = await _accion(db_session, org_id, acc.id,
                           {"old_budget": 100.0, "new_budget": 150.0})
    res = await async_client.post(
        f"/api/v1/actions/{accion.id}/approve", headers=headers, json={"approve": True}
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_c5_bajar_un_presupuesto_nunca_se_rechaza_por_limite(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """Con el margen agotado, poder BAJAR presupuesto es justo lo que hace
    falta: rechazarlo dejaría al operador sin forma de contener el gasto."""
    mocker.patch("app.api.v1.actions.execute_action_task")
    body, headers = await _signup(async_client, "c5-bajar")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "100.00")
    await _gasto_real(db_session, org_id, acc.id, 999.0)

    accion = await _accion(db_session, org_id, acc.id,
                           {"old_budget": 300.0, "new_budget": 100.0})
    res = await async_client.post(
        f"/api/v1/actions/{accion.id}/approve", headers=headers, json={"approve": True}
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "APPROVED"


# --------------------------------------------------------------------------
# Criterio 6 · el compromiso pendiente cuenta
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c6_las_campanas_creadas_sin_gasto_medido_consumen_margen(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Sumar solo el gasto real permitiría crear en un mismo día veinte
    campañas: ninguna habría gastado todavía."""
    body, headers = await _signup(async_client, "c6-comprometido")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1000.00")

    primera = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 700.0)
    )
    assert primera.status_code == 202, primera.text

    estado = await estado_del_mes(db_session, org_id)
    assert estado.gasto_real == Decimal("0.00")
    assert estado.comprometido == Decimal("700.00")

    segunda = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 400.0)
    )
    assert segunda.status_code == 400, "sin contar el compromiso, esta pasaría"


# --------------------------------------------------------------------------
# Criterios 7-8 · el par decimal
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c7_un_centimo_por_encima_del_margen_se_rechaza(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c7-un-centimo")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "100.00")
    await _gasto_real(db_session, org_id, acc.id, 70.30)

    res = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 29.71)
    )
    assert res.status_code == 400, "29.71 se pasa por un céntimo"


def test_c7b_la_aritmetica_de_la_compuerta_es_exacta_donde_la_flotante_no():
    """La propiedad que sostiene los criterios 7 y 8, probada donde vive.

    Conviene ser preciso sobre lo que aporta el `Decimal`, para no vender una
    garantía que no da. Con montos de dos decimales y las magnitudes que el
    esquema admite (`NUMERIC(14,2)`, hasta ~1e12), una comparación suelta en
    coma flotante y una exacta **coinciden**: el residuo binario está muchos
    órdenes por debajo de un céntimo.

    Lo que el `Decimal` sí elimina son las dos formas en que ese residuo llega a
    importar:

    1. **Igualdad exacta en el borde.** `0.1 + 0.2 == 0.3` es falso en binario.
       Toda comparación de la compuerta es de la forma "consumo + monto <=
       límite", y en el borde exacto —el caso 8, donde el monto agota el margen
       justo— esa diferencia decide.
    2. **Acumulación.** Sumar cientos de snapshots en coma flotante desplaza el
       total; la suma exacta no. Ver
       `tests/test_spec02_concurrencia_postgres.py`, donde se comprueba con el
       motor que sí tiene NUMERIC real.
    """
    from app.services.money import to_money

    assert 0.1 + 0.2 != 0.3, "el residuo binario existe"
    assert to_money("0.1") + to_money("0.2") == to_money("0.3")

    # Acumulación: mil veces un céntimo son diez soles, exactos.
    total = sum((to_money("0.01") for _ in range(1000)), to_money("0"))
    assert total == to_money("10.00")
    acumulado_float = 0.0
    for _ in range(1000):
        acumulado_float += 0.01
    assert acumulado_float != 10.00, "la suma en coma flotante se desvía"


@pytest.mark.asyncio
async def test_c8_un_monto_que_agota_exactamente_el_margen_se_acepta(
    async_client: AsyncClient, db_session: AsyncSession
):
    """El par del criterio 7. Una compuerta que rechaza de más también está
    rota, solo que en la dirección que no se ve."""
    body, headers = await _signup(async_client, "c8-borde-exacto")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "100.00")
    await _gasto_real(db_session, org_id, acc.id, 70.30)

    res = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 29.70)
    )
    assert res.status_code == 202, f"29.70 cabe exactamente en el margen: {res.text}"


# --------------------------------------------------------------------------
# Criterio 9 · concurrencia
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c9_dos_creaciones_que_juntas_exceden_dejan_una_sola(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Cada una cabe; juntas no. El resultado observable es un solo compromiso
    aceptado."""
    body, headers = await _signup(async_client, "c9-concurrencia")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1000.00")

    primera = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 600.0)
    )
    segunda = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 600.0)
    )
    codigos = sorted([primera.status_code, segunda.status_code])
    assert codigos == [202, 400], f"{primera.text} / {segunda.text}"


# --------------------------------------------------------------------------
# Criterios 10-11 · la cifra que se muestra es la que decide
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c10_y_c11_el_cliente_ve_la_misma_cifra_que_decide(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c10-visible")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1500.00")
    await _gasto_real(db_session, org_id, acc.id, 425.75)

    res = await async_client.get("/api/v1/organizations/my/budget", headers=headers)
    assert res.status_code == 200, res.text
    visto = res.json()

    assert visto["limite_mensual"] == "1500.00"
    assert visto["consumido"] == "425.75"
    assert visto["margen_disponible"] == "1074.25"
    assert visto["moneda"] == "PEN"
    assert visto["periodo"] == hoy_de_negocio().strftime("%Y-%m")
    # Sin identificadores internos ni configuración de la agencia.
    assert "organization_id" not in visto and "ad_account_id" not in visto

    # La misma cifra decide: un monto igual al margen entra, uno mayor no.
    estado = await estado_del_mes(db_session, org_id)
    assert str(estado.margen) == visto["margen_disponible"]
    assert estado.cabe(to_money("1074.25")) is True
    assert estado.cabe(to_money("1074.26")) is False


# --------------------------------------------------------------------------
# Criterio 12 · rebajar el límite bloquea, pero no apaga nada
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c12_rebajar_el_limite_bloquea_y_no_toca_las_campanas_activas(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c12-rebaja")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1000.00")
    await _gasto_real(db_session, org_id, acc.id, 800.0)

    res = await async_client.patch(
        "/api/v1/organizations/my", headers=headers, json={"max_budget": 100.0}
    )
    assert res.status_code == 200, res.text

    bloqueada = await async_client.post(
        "/api/v1/campaigns/", headers=headers, json=_peticion(acc.id, 10.0)
    )
    assert bloqueada.status_code == 400

    # Apagar gasto en curso es una decisión humana, no un efecto colateral de
    # editar un número: las campañas activas siguen como estaban.
    snapshots = (await db_session.execute(
        select(CampaignSnapshot).where(CampaignSnapshot.organization_id == org_id)
    )).scalars().all()
    assert all(s.status == "ACTIVE" for s in snapshots)


# --------------------------------------------------------------------------
# Criterio 13 · el mes se reinicia
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c13_el_consumo_se_reinicia_al_cambiar_de_mes(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, _ = await _signup(async_client, "c13-mes")
    org_id = body["user"]["organization_id"]
    acc = await _org_con_limite(db_session, org_id, "1000.00")

    hoy = hoy_de_negocio()
    mes_pasado = (hoy.replace(day=1) - datetime.timedelta(days=1))
    await _gasto_real(db_session, org_id, acc.id, 950.0, cuando=mes_pasado)

    estado = await estado_del_mes(db_session, org_id)
    assert estado.gasto_real == Decimal("0.00"), "el gasto del mes anterior no cuenta"
    assert estado.margen == Decimal("1000.00")


# --------------------------------------------------------------------------
# Criterio 14 · monedas distintas se rechazan, no se suman
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c14_monedas_distintas_producen_un_rechazo_explicito(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c14-monedas")
    org_id = body["user"]["organization_id"]
    await _org_con_limite(db_session, org_id, "1000.00", moneda="PEN")

    otra = await cuenta_de_prueba(db_session, org_id, external_account_id="act_usd")
    otra.currency = "USD"
    db_session.add(otra)
    await db_session.commit()

    with pytest.raises(MixedCurrencies):
        await estado_del_mes(db_session, org_id)

    res = await async_client.get("/api/v1/organizations/my/budget", headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "MIXED_ACCOUNT_CURRENCIES"
    assert res.json()["detail"]["monedas"] == ["PEN", "USD"]
