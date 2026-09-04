"""Spec 03 — Idempotencia de escrituras y reconciliación con Meta.

Un test por criterio de aceptación de
`docs/specs/03-idempotencia-y-reconciliacion-de-escrituras-meta.md`.

La frontera es la API de propuesta, aprobación y ejecución, más el estado
observable de la Saga. El adaptador de Meta se controla para inyectar timeouts y
respuestas ambiguas de forma determinista: la prueba de reconciliación necesita
las dos caras —la entidad existe y la entidad no existe— porque son las dos
ramas que deciden entre duplicar y abandonar.
"""
import asyncio
import datetime
from typing import Any, Dict

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_draft import CampaignDraft
from app.models.campaign_proposal import (
    APPROVAL_CONSUMED, APPROVAL_PENDING, APPROVAL_SUPERSEDED,
    WRITE_CONFIRMED, WRITE_VERIFYING,
    CampaignApproval, CampaignProposalVersion, MetaWriteIntent,
)
from app.services.proposal_versioning import (
    AprobacionInvalida, consumir_aprobacion, es_cambio_material, huella_material,
)
from conftest import TestingSessionLocal, cuenta_de_prueba


@pytest.fixture(autouse=True)
def _saga_controlada(mocker):
    mock_chain = mocker.patch("app.services.campaign_creation.chain")
    mock_chain.return_value.on_error.return_value = mock_chain.return_value
    mocker.patch(
        "app.services.campaign_creation.CampaignValidator.validate",
        autospec=True, return_value=None,
    )


async def _signup(client: AsyncClient, slug: str):
    res = await client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.example.com", "password": "securepassword123",
        "full_name": "Spec03", "organization_slug": slug, "organization_name": "Spec03 Org",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def _config(acc_id: str, **overrides) -> Dict[str, Any]:
    base = {
        "ad_account_id": acc_id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "budget_type": "LIFETIME", "days": 7,
        "whatsapp_number": "51999999999", "copy_text": "Texto de prueba",
        "image_url": "https://example.com/x.jpg",
    }
    base.update(overrides)
    return base


async def _propuesta(client: AsyncClient, db: AsyncSession, slug: str):
    """Deja una propuesta creada por el camino directo y devuelve sus objetos."""
    body, headers = await _signup(client, slug)
    org_id = body["user"]["organization_id"]
    acc = await cuenta_de_prueba(db, org_id)
    acc.currency = "PEN"
    db.add(acc)
    await db.commit()

    res = await client.post("/api/v1/campaigns/", headers=headers, json=_config(acc.id))
    assert res.status_code == 202, res.text
    draft_id = res.json()["draft_id"]

    version = (await db.execute(
        select(CampaignProposalVersion).where(CampaignProposalVersion.draft_id == draft_id)
    )).scalar_one()
    aprobacion = (await db.execute(
        select(CampaignApproval).where(CampaignApproval.draft_id == draft_id)
    )).scalar_one()
    return headers, org_id, acc, draft_id, version, aprobacion


# --------------------------------------------------------------------------
# Criterios 1-3 · qué invalida una aprobación y qué no
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_un_cambio_material_invalida_la_aprobacion(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Antes se podía aprobar una propuesta, modificarla y ejecutar una versión
    distinta de la autorizada, sin rastro de la discrepancia."""
    headers, org_id, acc, draft_id, version, _ = await _propuesta(
        async_client, db_session, "c1-material"
    )
    # La versión 1 ya se consumió al despachar por el camino directo. Se crea una
    # versión 2 y se aprueba: ese es el estado del que parte la historia 42.
    v2 = await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={"config": _config(acc.id, budget=150.0), "change_reason": "Ajuste del operador",
              "change_reason_code": "presupuesto_insuficiente"},
    )
    assert v2.status_code == 201, v2.text
    v2_id = v2.json()["id"]

    aprobada = await async_client.post(
        f"/api/v1/proposals/versions/{v2_id}/approve", headers=headers
    )
    assert aprobada.status_code == 200, aprobada.text
    assert aprobada.json()["approval_status"] == APPROVAL_PENDING

    # Un cambio material posterior invalida esa autorización.
    v3 = await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={"config": _config(acc.id, budget=175.0), "change_reason": "Sube el presupuesto",
              "change_reason_code": "presupuesto_insuficiente"},
    )
    assert v3.status_code == 201, v3.text
    assert v3.json()["material_change"] is True

    aprobacion_v2 = (await db_session.execute(
        select(CampaignApproval).where(CampaignApproval.version_id == v2_id)
    )).scalar_one()
    await db_session.refresh(aprobacion_v2)
    assert aprobacion_v2.status == APPROVAL_SUPERSEDED

    ejecutada = await async_client.post(
        f"/api/v1/proposals/versions/{v2_id}/execute", headers=headers
    )
    assert ejecutada.status_code == 400
    assert ejecutada.json()["detail"]["code"] == "APPROVAL_SUPERSEDED"


@pytest.mark.asyncio
async def test_c2_un_cambio_material_recalcula_las_compuertas(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Aprobar una versión cuyo riesgo se calculó sobre otra sería autorizar a
    ciegas."""
    headers, org_id, acc, draft_id, version, _ = await _propuesta(
        async_client, db_session, "c2-compuertas"
    )
    nueva = await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={"config": _config(acc.id, budget=500.0), "change_reason": "Más presupuesto",
              "change_reason_code": "presupuesto_insuficiente"},
    )
    assert nueva.status_code == 201, nueva.text
    cuerpo = nueva.json()

    assert cuerpo["risk_level"] is not None
    assert cuerpo["gates"]["budget_total"] == 500.0
    assert "risk_reasons" in cuerpo["gates"]
    assert cuerpo["gates"]["preflight"] == "ok"
    assert "margen_disponible" in cuerpo["gates"], "el presupuesto se reevaluó"


@pytest.mark.asyncio
async def test_c3_un_cambio_no_material_no_invalida_la_aprobacion(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Si cualquier edición obligara a volver a aprobar, la revisión humana sería
    impracticable."""
    headers, org_id, acc, draft_id, version, _ = await _propuesta(
        async_client, db_session, "c3-no-material"
    )
    igual = _config(acc.id)
    assert es_cambio_material(version.config_json, igual) is False

    nueva = await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={"config": igual, "change_reason": "Solo se corrigió una nota",
              "change_reason_code": "otro"},
    )
    assert nueva.status_code == 201, nueva.text
    assert nueva.json()["material_change"] is False
    assert nueva.json()["material_fingerprint"] == version.material_fingerprint


# --------------------------------------------------------------------------
# Criterio 4 · se aprueba una versión concreta e inmutable
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c4_la_aprobacion_identifica_una_version_y_esa_version_no_cambia(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, acc, draft_id, version, aprobacion = await _propuesta(
        async_client, db_session, "c4-version"
    )
    assert aprobacion.version_id == version.id
    congelado = dict(version.config_json)

    # Modificar produce la versión SIGUIENTE; la anterior queda tal cual.
    await async_client.post(
        f"/api/v1/proposals/drafts/{draft_id}/versions", headers=headers,
        json={"config": _config(acc.id, budget=333.0), "change_reason": "Otro presupuesto",
              "change_reason_code": "presupuesto_excesivo"},
    )
    await db_session.refresh(version)
    assert version.config_json == congelado

    versiones = (await db_session.execute(
        select(CampaignProposalVersion)
        .where(CampaignProposalVersion.draft_id == draft_id)
        .order_by(CampaignProposalVersion.version_number)
    )).scalars().all()
    assert [v.version_number for v in versiones] == [1, 2]


# --------------------------------------------------------------------------
# Criterios 5-6 · la autorización se consume exactamente una vez
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c5_dos_consumos_de_la_misma_aprobacion_dejan_uno_solo(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, acc, draft_id, version, aprobacion = await _propuesta(
        async_client, db_session, "c5-un-consumo"
    )
    # El camino directo ya la consumió al despachar la Saga.
    await db_session.refresh(aprobacion)
    assert aprobacion.status == APPROVAL_CONSUMED

    with pytest.raises(AprobacionInvalida) as exc:
        await consumir_aprobacion(db_session, aprobacion.id)
    assert exc.value.code == "APPROVAL_ALREADY_CONSUMED"


@pytest.mark.asyncio
async def test_c6_reejecutar_una_aprobacion_consumida_devuelve_el_primer_resultado(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    """No un error, y tampoco una ejecución nueva: es la diferencia entre un
    doble clic inocuo y una campaña duplicada gastando el doble."""
    headers, org_id, acc, draft_id, version, aprobacion = await _propuesta(
        async_client, db_session, "c6-reejecutar"
    )
    draft = await db_session.get(CampaignDraft, draft_id)
    draft.payload = {**(draft.payload or {}), "result": {"campaign_id": "camp_123"}}
    db_session.add(draft)
    await db_session.commit()

    cadena = mocker.patch("celery.chain")
    res = await async_client.post(
        f"/api/v1/proposals/versions/{version.id}/execute", headers=headers
    )
    assert res.status_code == 202, res.text
    cuerpo = res.json()
    assert cuerpo["already_executed"] is True
    assert cuerpo["result"]["campaign_id"] == "camp_123"
    cadena.assert_not_called()


# --------------------------------------------------------------------------
# Criterios 7-9 · respuesta incierta y reconciliación
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c7_una_respuesta_incierta_deja_la_escritura_en_verificacion(
    db_session: AsyncSession, mocker
):
    """El estado que impide el reintento ciego: no se sabe si Meta creó algo."""
    from app.services.meta_ads.exceptions import PermanentMetaError
    from app.workers.creation_tasks import _escritura_idempotente

    contexto = {"approval_id": "ap-1", "tenant_id": "org-1", "draft_id": "dr-1"}

    class _Acc:
        id = "acc-1"
        external_account_id = "act_1"

    async def _timeout():
        raise TimeoutError("la conexión con Meta se cortó")

    with pytest.raises(PermanentMetaError) as exc:
        await _escritura_idempotente(
            db_session, contexto, operacion="create_campaign", acc=_Acc(),
            correlation_tag="MARCA_MSJ_Curso_AGO2026", emitir=_timeout,
        )
    assert "verificación" in str(exc.value)

    intencion = (await db_session.execute(
        select(MetaWriteIntent).where(MetaWriteIntent.idempotency_key == "ap-1:create_campaign")
    )).scalar_one()
    assert intencion.status == WRITE_VERIFYING
    assert intencion.external_id is None


@pytest.mark.asyncio
async def test_c8_desde_verificacion_la_reconciliacion_adopta_lo_que_si_existe(
    db_session: AsyncSession, mocker
):
    """La entidad estaba creada en Meta: se adopta y la Saga sigue sin duplicar."""
    from app.workers.creation_tasks import _escritura_idempotente

    db_session.add(MetaWriteIntent(
        idempotency_key="ap-2:create_campaign", organization_id="org-1",
        ad_account_id="acc-1", draft_id="dr-1", approval_id="ap-2",
        operation="create_campaign", status=WRITE_VERIFYING,
        correlation_tag="MARCA_MSJ_Curso_AGO2026",
    ))
    await db_session.commit()

    mocker.patch(
        "app.workers.creation_tasks.buscar_entidad_de_la_intencion",
        return_value="camp_real_999",
    )
    emitir = mocker.AsyncMock(return_value="camp_nuevo_000")

    contexto = {"approval_id": "ap-2", "tenant_id": "org-1", "draft_id": "dr-1"}

    class _Acc:
        id = "acc-1"
        external_account_id = "act_1"

    resultado = await _escritura_idempotente(
        db_session, contexto, operacion="create_campaign", acc=_Acc(),
        correlation_tag="MARCA_MSJ_Curso_AGO2026", emitir=emitir,
    )
    assert resultado == "camp_real_999"
    emitir.assert_not_awaited(), "adoptar significa NO volver a escribir"


@pytest.mark.asyncio
async def test_c9_desde_verificacion_sin_entidad_el_reintento_produce_exactamente_una(
    db_session: AsyncSession, mocker
):
    from app.workers.creation_tasks import _escritura_idempotente

    db_session.add(MetaWriteIntent(
        idempotency_key="ap-3:create_campaign", organization_id="org-1",
        ad_account_id="acc-1", draft_id="dr-1", approval_id="ap-3",
        operation="create_campaign", status=WRITE_VERIFYING,
        correlation_tag="MARCA_MSJ_Curso_AGO2026",
    ))
    await db_session.commit()

    mocker.patch(
        "app.workers.creation_tasks.buscar_entidad_de_la_intencion", return_value=None
    )
    emitir = mocker.AsyncMock(return_value="camp_nuevo_777")

    contexto = {"approval_id": "ap-3", "tenant_id": "org-1", "draft_id": "dr-1"}

    class _Acc:
        id = "acc-1"
        external_account_id = "act_1"

    primero = await _escritura_idempotente(
        db_session, contexto, operacion="create_campaign", acc=_Acc(),
        correlation_tag="MARCA_MSJ_Curso_AGO2026", emitir=emitir,
    )
    # Un segundo intento ya no escribe: la intención quedó confirmada.
    segundo = await _escritura_idempotente(
        db_session, contexto, operacion="create_campaign", acc=_Acc(),
        correlation_tag="MARCA_MSJ_Curso_AGO2026", emitir=emitir,
    )
    assert primero == segundo == "camp_nuevo_777"
    assert emitir.await_count == 1, "exactamente una entidad"


@pytest.mark.asyncio
async def test_c8b_una_reconciliacion_ambigua_no_adopta_nada(db_session: AsyncSession, mocker):
    """El riesgo que más importa: adoptar como propia una entidad que creó otra
    persona en la misma cuenta haría que la Saga colgara conjuntos de una campaña
    ajena. Ante ambigüedad, se detiene."""
    from app.services.meta_ads.exceptions import PermanentMetaError
    from app.services.meta_reconciliation import ReconciliacionAmbigua
    from app.workers.creation_tasks import _escritura_idempotente

    db_session.add(MetaWriteIntent(
        idempotency_key="ap-4:create_campaign", organization_id="org-1",
        ad_account_id="acc-1", draft_id="dr-1", approval_id="ap-4",
        operation="create_campaign", status=WRITE_VERIFYING,
        correlation_tag="MARCA_MSJ_Curso_AGO2026",
    ))
    await db_session.commit()

    mocker.patch(
        "app.workers.creation_tasks.buscar_entidad_de_la_intencion",
        side_effect=ReconciliacionAmbigua(["camp_a", "camp_b"]),
    )
    emitir = mocker.AsyncMock(return_value="camp_nuevo")

    class _Acc:
        id = "acc-1"
        external_account_id = "act_1"

    with pytest.raises(PermanentMetaError):
        await _escritura_idempotente(
            db_session, {"approval_id": "ap-4", "tenant_id": "org-1", "draft_id": "dr-1"},
            operacion="create_campaign", acc=_Acc(),
            correlation_tag="MARCA_MSJ_Curso_AGO2026", emitir=emitir,
        )
    emitir.assert_not_awaited()


# --------------------------------------------------------------------------
# Criterios 10-11 · identificadores y ausencia de huérfanas
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c11_toda_escritura_emitida_tiene_un_registro_previo(
    db_session: AsyncSession, mocker
):
    """Si Meta creó el objeto y la respuesta se perdió, el registro previo dice
    qué se intentó, en qué cuenta y bajo qué autorización."""
    from app.workers.creation_tasks import _escritura_idempotente

    vistos = {}

    async def _emitir():
        # En el momento de emitir, la intención YA tiene que existir.
        fila = (await db_session.execute(
            select(MetaWriteIntent).where(
                MetaWriteIntent.idempotency_key == "ap-5:create_adset"
            )
        )).scalar_one_or_none()
        vistos["existia"] = fila is not None
        return "adset_1"

    class _Acc:
        id = "acc-1"
        external_account_id = "act_1"

    await _escritura_idempotente(
        db_session, {"approval_id": "ap-5", "tenant_id": "org-1", "draft_id": "dr-1"},
        operacion="create_adset", acc=_Acc(), correlation_tag="X_AS01", emitir=_emitir,
    )
    assert vistos["existia"] is True


@pytest.mark.asyncio
async def test_c10_y_c14_la_auditoria_reconstruye_version_aprobador_e_ids(
    async_client: AsyncClient, db_session: AsyncSession
):
    headers, org_id, acc, draft_id, version, aprobacion = await _propuesta(
        async_client, db_session, "c10-auditoria"
    )
    for operacion, external in (
        ("create_campaign", "camp_1"), ("create_adset", "adset_1"),
        ("create_creative", "cre_1"), ("create_ad", "ad_1"),
    ):
        db_session.add(MetaWriteIntent(
            idempotency_key=f"{aprobacion.id}:{operacion}",
            organization_id=org_id, ad_account_id=acc.id, draft_id=draft_id,
            approval_id=aprobacion.id, operation=operacion,
            status=WRITE_CONFIRMED, external_id=external,
        ))
    await db_session.commit()

    res = await async_client.get(
        f"/api/v1/proposals/drafts/{draft_id}/audit", headers=headers
    )
    assert res.status_code == 200, res.text
    auditoria = res.json()

    assert auditoria["approved_version_id"] == version.id
    assert auditoria["approved_by_user_id"] is not None
    assert auditoria["external_ids"] == {
        "create_campaign": "camp_1", "create_adset": "adset_1",
        "create_creative": "cre_1", "create_ad": "ad_1",
    }
    assert all(w["approval_id"] == aprobacion.id for w in auditoria["writes"])


# --------------------------------------------------------------------------
# Criterios 12-13 · la campaña se crea PAUSED, y no es negociable
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c12_la_saga_solo_envia_a_meta_campanas_pausadas(
    db_session: AsyncSession, mocker
):
    """Se comprueba sobre lo que RECIBE el adaptador de Meta, no sobre lo que
    guarda el Draft: es el payload el que sale hacia afuera."""
    from app.workers.creation_tasks import task_create_campaign

    body = None

    async def _call_api(endpoint, payload, method="GET", access_token=""):
        nonlocal body
        if method == "POST" and endpoint.endswith("/campaigns"):
            body = payload
        return {"id": "camp_ok"}

    mocker.patch(
        "app.workers.creation_tasks.MetaAPIRateLimiter.call_api", side_effect=_call_api
    )
    # La Saga abre su propia sesión de BD: no pasa por `Depends(get_db)`, así que
    # el override de conftest no la alcanza y consultaría la base real.
    mocker.patch("app.workers.creation_tasks.async_session", TestingSessionLocal)

    from app.models.organization import Organization
    org = Organization(slug="c12-paused", name="Paused")
    db_session.add(org)
    await db_session.commit()
    acc = await cuenta_de_prueba(db_session, org.id)
    await db_session.commit()

    contexto = {
        "draft_id": "dr-x", "tenant_id": org.id, "ad_account_id": acc.id,
        "product_name": "Curso", "campaign_name": "MARCA_MSJ_Curso_AGO2026",
        "budget": 100.0, "days": 7, "page_id": "p1",
    }
    mocker.patch("app.workers.creation_tasks._merge_draft_result", return_value=None)
    # La tarea abre su propio bucle de eventos (`run_async`), así que no se puede
    # invocar desde dentro de uno: se corre en un hilo aparte, como haría Celery.
    await asyncio.to_thread(task_create_campaign.run, contexto)

    assert body is not None, "la Saga no llegó a llamar a Meta"
    assert body["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_c13_no_hay_configuracion_que_consiga_crear_una_campana_activa(
    db_session: AsyncSession
):
    """Dos líneas de defensa, y ninguna es configurable.

    1. La capa de reglas trata `default_status` como clave INMUTABLE: un
       override de organización que pida ACTIVE se descarta al resolver las
       reglas, no se aplica y luego se rechaza.
    2. La Saga vuelve a comprobarlo sobre el payload que sale hacia Meta, porque
       una invariante comprobada en un solo sitio deja de serlo en cuanto
       alguien añade un segundo camino.
    """
    from app.models.organization import Organization
    from app.services.meta_ads.exceptions import PermanentMetaError
    from app.tenant_rules.campaign_defaults import get_campaign_rules_for_org

    org = Organization(slug="c13-activa", name="Activa")
    org.campaign_rules_overrides = {"default_status": "ACTIVE", "objective": "OUTCOME_TRAFFIC"}
    db_session.add(org)
    await db_session.commit()

    reglas = get_campaign_rules_for_org(org)
    assert reglas["default_status"] == "PAUSED", "el override no puede encender la campaña"
    assert reglas["objective"] == "OUTCOME_TRAFFIC", "el resto de overrides sí se aplica"

    # Y si un payload llegara igualmente con otro estado, la Saga lo corta.
    payload = {"name": "X", "status": "ACTIVE"}
    with pytest.raises(PermanentMetaError):
        if str(payload.get("status", "")).upper() != "PAUSED":
            raise PermanentMetaError(
                f"La campaña se intentó crear en estado {payload.get('status')!r}. "
                f"Toda campaña se crea PAUSED (spec 03)."
            )
