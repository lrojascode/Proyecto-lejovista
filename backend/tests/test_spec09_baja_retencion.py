"""Spec 09 — Baja de clientes, retención, privacidad y auditoría.

Un test por criterio de aceptación de
`docs/specs/09-baja-de-clientes-retencion-y-auditoria.md`.

Dos decisiones sobre cómo se prueba, del contrato de la spec:

* **Siempre con una Organización TESTIGO.** Una prueba de eliminación con una
  sola organización no distingue una eliminación dirigida de un borrado excesivo,
  y el borrado excesivo es el fallo caro.
* **El paso del tiempo se simula.** Los plazos de 30 y 90 días no se esperan.
"""
import datetime
import io

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.media_asset import ELIMINADO, MediaAsset
from app.models.meta_credential import MetaCredential
from app.models.organization import Organization
from app.models.snapshot_archive import CampaignSnapshotArchive
from app.models.user import User
from app.services.offboarding import (
    BAJA_COMPLETADA, BAJA_INICIADA, DIAS_DE_LOGS_DE_SEGURIDAD, DIAS_PARA_ELIMINAR,
    BajaInvalida, certificado_de_eliminacion, eliminar_datos, exportar_datos,
    iniciar_baja,
)
from conftest import cuenta_de_prueba


@pytest.fixture(autouse=True)
def _medios_en_tmp(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DRIVER", "local")


async def _signup(client: AsyncClient, slug: str, prefijo="admin"):
    res = await client.post("/api/v1/auth/signup", json={
        "email": f"{prefijo}@{slug}.example.com", "password": "securepassword123",
        "full_name": "Spec09", "organization_slug": slug, "organization_name": f"Org {slug}",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), "blue").save(buf, "PNG")
    return buf.getvalue()


async def _poblar(client: AsyncClient, db: AsyncSession, headers, org_id: str):
    """Deja la organización con datos en TODAS las superficies que la baja toca."""
    org = await db.get(Organization, org_id)
    org.llm_provider = "openai"
    org.set_llm_api_key("sk-clave-del-cliente-a-borrar")
    db.add(org)

    cred = MetaCredential(organization_id=org_id, status="ACTIVE")
    cred.set_access_token("EAAG-token-de-meta")
    db.add(cred)
    await db.flush()

    acc = await cuenta_de_prueba(db, org_id)
    acc.currency = "PEN"
    acc.meta_credential_id = cred.id
    db.add(acc)

    hoy = datetime.date.today()
    for i in range(5):
        db.add(CampaignSnapshot(
            ad_account_id=acc.id, organization_id=org_id,
            campaign_id=f"c{i}", campaign_name=f"Campaña {i}", status="ACTIVE",
            objective="OUTCOME_ENGAGEMENT", spend=100.0 + i, conversations=10,
            results=10, date=hoy - datetime.timedelta(days=i),
        ))
    # Y en el ARCHIVO frío, que es lo que el CASCADE no alcanza.
    for i in range(3):
        db.add(CampaignSnapshotArchive(
            original_id=f"o{i}", ad_account_id=acc.id, organization_id=org_id,
            campaign_id=f"vieja{i}", campaign_name=f"Vieja {i}", status="ACTIVE",
            objective="OUTCOME_ENGAGEMENT", spend=50.0, conversations=5,
            date=hoy - datetime.timedelta(days=500),
            created_at=datetime.datetime.utcnow(),
        ))
    await db.commit()

    subida = await client.post(
        "/api/v1/media", headers=headers,
        files={"file": ("foto.png", _png(), "image/png")},
    )
    assert subida.status_code == 201, subida.text
    return acc


# --------------------------------------------------------------------------
# Criterios 1-3 · la fase inmediata corta accesos, no borra
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c1_iniciar_la_baja_revoca_credenciales_y_borra_la_clave_de_ia(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c1-baja")
    org_id = body["user"]["organization_id"]
    await _poblar(async_client, db_session, headers, org_id)

    org = await db_session.get(Organization, org_id)
    await iniciar_baja(db_session, org, actor_id=body["user"]["id"], motivo="fin de contrato")
    await db_session.commit()

    # `cuenta_de_prueba` crea su propia credencial además de la del test, así
    # que hay más de una — y TODAS tienen que quedar revocadas: dejar una viva
    # es dejar el acceso vivo.
    credenciales = (await db_session.execute(
        select(MetaCredential).where(MetaCredential.organization_id == org_id)
    )).scalars().all()
    assert len(credenciales) >= 2
    for c in credenciales:
        await db_session.refresh(c)
        assert c.status == "REVOKED"

    await db_session.refresh(org)
    assert org.has_llm_api_key is False, "la clave del cliente se borra, no se marca"


@pytest.mark.asyncio
async def test_c2_y_c3_la_baja_bloquea_login_y_api(
    async_client: AsyncClient, db_session: AsyncSession
):
    """El estado bloquea las cinco superficies de una vez: las compuertas de la
    spec 01 comprueban `status != ACTIVE`."""
    body, headers = await _signup(async_client, "c2-bloqueo")
    org_id = body["user"]["organization_id"]

    org = await db_session.get(Organization, org_id)
    await iniciar_baja(db_session, org, actor_id=None, motivo="fin de contrato")
    await db_session.commit()

    # La API queda bloqueada. El código es 401 y no 403 porque la baja corta
    # además las SESIONES vivas (spec 01), y ese corte se comprueba antes que el
    # estado de la organización: el token deja de valer, no llega a mirarse a qué
    # organización pertenece. Las dos cosas bloquean; esta bloquea antes.
    res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert res.status_code == 401

    # El login sí llega a la comprobación de organización, y dice por qué.
    res = await async_client.post("/api/v1/auth/login", json={
        "email": "admin@c2-bloqueo.example.com", "password": "securepassword123",
    })
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "ORGANIZATION_SUSPENDED"


@pytest.mark.asyncio
async def test_c3b_la_baja_no_borra_nada_todavia(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Separar las dos fases es el punto: juntarlas obligaría a elegir entre
    cortar tarde o borrar sin dar tiempo a exportar."""
    body, headers = await _signup(async_client, "c3b-sin-borrar")
    org_id = body["user"]["organization_id"]
    await _poblar(async_client, db_session, headers, org_id)

    org = await db_session.get(Organization, org_id)
    await iniciar_baja(db_session, org, actor_id=None, motivo="fin de contrato")
    await db_session.commit()

    snapshots = (await db_session.execute(
        select(func.count()).select_from(CampaignSnapshot)
        .where(CampaignSnapshot.organization_id == org_id)
    )).scalar_one()
    assert snapshots == 5, "los datos siguen ahí durante la ventana de exportación"


# --------------------------------------------------------------------------
# Criterios 4-5 · exportación
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c4_la_exportacion_entrega_los_datos_comerciales(
    async_client: AsyncClient, db_session: AsyncSession
):
    body, headers = await _signup(async_client, "c4-exportar")
    org_id = body["user"]["organization_id"]
    await _poblar(async_client, db_session, headers, org_id)

    org = await db_session.get(Organization, org_id)
    datos = await exportar_datos(db_session, org)

    assert datos["organizacion"]["identificador"] == "c4-exportar"
    assert datos["totales"]["cuentas"] == 1
    # Lo caliente Y el archivo: el cliente se lleva su histórico entero.
    assert datos["totales"]["snapshots"] == 8
    assert all("fecha" in s and "gasto" in s for s in datos["historico_de_campanas"])


@pytest.mark.asyncio
async def test_c5_la_exportacion_no_lleva_secretos_ni_datos_de_otros(
    async_client: AsyncClient, db_session: AsyncSession
):
    body_a, headers_a = await _signup(async_client, "c5-mio")
    org_a = body_a["user"]["organization_id"]
    await _poblar(async_client, db_session, headers_a, org_a)

    body_b, headers_b = await _signup(async_client, "c5-ajeno")
    org_b = body_b["user"]["organization_id"]
    await _poblar(async_client, db_session, headers_b, org_b)

    org = await db_session.get(Organization, org_a)
    datos = await exportar_datos(db_session, org)
    texto = str(datos)

    assert "sk-clave" not in texto
    assert "EAAG-token" not in texto
    assert "c5-ajeno" not in texto
    assert all(s["campana"].startswith(("Campaña", "Vieja")) for s in datos["historico_de_campanas"])


# --------------------------------------------------------------------------
# Criterios 6-8 · la eliminación alcanza todo, incluido el archivo frío
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c6_c7_c8_la_eliminacion_alcanza_lo_caliente_el_archivo_y_los_medios(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Las tablas de archivo se diseñaron SIN claves foráneas para sobrevivir al
    borrado de un tenant. Omitirlas convertiría la promesa en falsa."""
    from app.services.media_storage import obtener_almacenamiento

    body, headers = await _signup(async_client, "c6-eliminar")
    org_id = body["user"]["organization_id"]
    await _poblar(async_client, db_session, headers, org_id)

    medio = (await db_session.execute(
        select(MediaAsset).where(MediaAsset.organization_id == org_id)
    )).scalar_one()
    clave = medio.storage_key
    assert await obtener_almacenamiento().existe(clave)

    org = await db_session.get(Organization, org_id)
    await iniciar_baja(db_session, org, actor_id=None, motivo="fin de contrato")
    await db_session.commit()

    futuro = datetime.datetime.utcnow() + datetime.timedelta(days=DIAS_PARA_ELIMINAR + 1)
    await eliminar_datos(db_session, org, ahora=futuro)
    await db_session.commit()

    calientes = (await db_session.execute(
        select(func.count()).select_from(CampaignSnapshot)
        .where(CampaignSnapshot.organization_id == org_id)
    )).scalar_one()
    assert calientes == 0

    archivadas = (await db_session.execute(
        select(func.count()).select_from(CampaignSnapshotArchive)
        .where(CampaignSnapshotArchive.organization_id == org_id)
    )).scalar_one()
    assert archivadas == 0, "el archivo frío es donde vive el histórico completo"

    await db_session.refresh(medio)
    assert medio.status == ELIMINADO
    assert not await obtener_almacenamiento().existe(clave), (
        "una fila borrada con el archivo todavía en disco no es una eliminación"
    )


@pytest.mark.asyncio
async def test_c15_la_baja_de_una_organizacion_no_toca_a_otra(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Con una sola organización, este test no distingue una eliminación dirigida
    de un borrado excesivo — y el borrado excesivo es el fallo caro."""
    body_a, headers_a = await _signup(async_client, "c15-baja")
    org_a = body_a["user"]["organization_id"]
    await _poblar(async_client, db_session, headers_a, org_a)

    body_b, headers_b = await _signup(async_client, "c15-testigo")
    org_b = body_b["user"]["organization_id"]
    await _poblar(async_client, db_session, headers_b, org_b)

    org = await db_session.get(Organization, org_a)
    await iniciar_baja(db_session, org, actor_id=None, motivo="fin de contrato")
    await db_session.commit()
    futuro = datetime.datetime.utcnow() + datetime.timedelta(days=DIAS_PARA_ELIMINAR + 1)
    await eliminar_datos(db_session, org, ahora=futuro)
    await db_session.commit()

    # La testigo queda intacta, en las tres superficies.
    assert (await db_session.execute(
        select(func.count()).select_from(CampaignSnapshot)
        .where(CampaignSnapshot.organization_id == org_b)
    )).scalar_one() == 5
    assert (await db_session.execute(
        select(func.count()).select_from(CampaignSnapshotArchive)
        .where(CampaignSnapshotArchive.organization_id == org_b)
    )).scalar_one() == 3
    medio_b = (await db_session.execute(
        select(MediaAsset).where(MediaAsset.organization_id == org_b)
    )).scalar_one()
    assert medio_b.status != ELIMINADO

    credenciales_b = (await db_session.execute(
        select(MetaCredential).where(MetaCredential.organization_id == org_b)
    )).scalars().all()
    assert all(c.status == "ACTIVE" for c in credenciales_b)


@pytest.mark.asyncio
async def test_el_plazo_se_respeta_salvo_forzado_explicito(
    async_client: AsyncClient, db_session: AsyncSession
):
    """El plazo existe para que el cliente pueda exportar. Saltárselo en silencio
    convierte la promesa en un adorno."""
    body, headers = await _signup(async_client, "plazo")
    org_id = body["user"]["organization_id"]
    await _poblar(async_client, db_session, headers, org_id)

    org = await db_session.get(Organization, org_id)
    await iniciar_baja(db_session, org, actor_id=None, motivo="fin de contrato")
    await db_session.commit()

    with pytest.raises(BajaInvalida) as exc:
        await eliminar_datos(db_session, org)
    assert exc.value.code == "OFFBOARDING_TOO_EARLY"

    # Con confirmación explícita, sí.
    await eliminar_datos(db_session, org, forzar=True)
    await db_session.commit()
    assert (org.offboarding_state or {})["fase"] == BAJA_COMPLETADA


# --------------------------------------------------------------------------
# Criterios 9-10, 13 · el certificado dice qué se conserva y por cuánto
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c9_y_c13_el_certificado_enumera_lo_eliminado_y_lo_conservado(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Un certificado que solo diga "listo" no cierra la relación con confianza:
    lo que da confianza es el detalle de lo que NO se borró y por qué."""
    body, headers = await _signup(async_client, "c9-certificado")
    org_id = body["user"]["organization_id"]
    await _poblar(async_client, db_session, headers, org_id)

    org = await db_session.get(Organization, org_id)
    await iniciar_baja(db_session, org, actor_id=None, motivo="fin de contrato")
    await eliminar_datos(db_session, org, forzar=True)
    await db_session.commit()

    cert = certificado_de_eliminacion(org)
    assert cert["identificador"] == "c9-certificado"
    assert cert["datos_eliminados_en"]

    assert "archivo frío" in cert["eliminado"]["historico_de_campanas"]
    assert "revocadas" in cert["eliminado"]["credenciales_de_meta"]

    # Y lo que NO se elimina, dicho sin rodeos.
    conserva = cert["se_conserva"]
    assert str(DIAS_DE_LOGS_DE_SEGURIDAD) in conserva["registros_de_seguridad"]
    assert "rotación" in conserva["copias_de_respaldo"], (
        "prometer que los respaldos se reescriben sería falso"
    )


@pytest.mark.asyncio
async def test_c10_la_organizacion_sobrevive_vacia_como_constancia(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Un DELETE sobre `organizations` cascadea a media base y deja la baja sin
    rastro auditable, que es lo contrario de lo que un certificado necesita."""
    body, headers = await _signup(async_client, "c10-constancia")
    org_id = body["user"]["organization_id"]
    await _poblar(async_client, db_session, headers, org_id)

    org = await db_session.get(Organization, org_id)
    await iniciar_baja(db_session, org, actor_id=None, motivo="fin de contrato")
    await eliminar_datos(db_session, org, forzar=True)
    await db_session.commit()

    sigue = await db_session.get(Organization, org_id)
    assert sigue is not None
    assert sigue.offboarding_state["fase"] == BAJA_COMPLETADA
    assert sigue.offboarding_state["iniciada_en"]
    assert sigue.has_llm_api_key is False


# --------------------------------------------------------------------------
# Criterios 11-12 · saneado de registros
# --------------------------------------------------------------------------

def test_c11_los_registros_no_contienen_credenciales_ni_datos_personales():
    """Confiar en que cada punto de llamada recuerde no registrar un secreto
    garantiza que alguna vez se olvide, y el que se olvide será el que importa."""
    from app.utils.log_sanitizer import sanear

    casos = [
        ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijk", "eyJhbGci"),
        ("clave sk-proj-1234567890abcdefghij", "sk-proj-1234567890"),
        ("token de meta EAAGm0PX4ZCpsBA1234567890abcdef", "EAAGm0PX4"),
        ("GET /media/x/contenido?token=1788.firmasecreta", "firmasecreta"),
        ("Cookie: refresh_token=eyJhbGciOi.abc.def", "eyJhbGciOi.abc.def"),
    ]
    for entrada, secreto in casos:
        saneado = sanear(entrada)
        assert secreto not in saneado, f"se escapó: {entrada}"

    # El teléfono deja el prefijo del país y los dos últimos dígitos: lo mínimo
    # para reconocer de cuál se habla sin poder marcarlo.
    saneado = sanear("whatsapp 51987654321")
    assert "51987654321" not in saneado
    assert "51" in saneado and "21" in saneado


def test_c12_los_registros_conservan_lo_que_hace_falta_para_investigar():
    """Un log que no sirve para investigar es tan malo como uno que filtra."""
    from app.utils.log_sanitizer import sanear

    mensaje = (
        "Sync de la cuenta 991bfff3-821b-4e99-af48-fb3e87140f9a falló con "
        "código 190 tras 2400ms (org 7fcd37fd-0a1d-47ac-988d-4ac87786ade8)"
    )
    saneado = sanear(mensaje)
    assert "991bfff3-821b-4e99-af48-fb3e87140f9a" in saneado
    assert "190" in saneado
    assert "2400ms" in saneado


# --------------------------------------------------------------------------
# Criterio 14 · la auditoría reconstruye el recorrido completo
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c14_la_auditoria_reconstruye_quien_pidio_con_que_y_que_resulto(
    async_client: AsyncClient, db_session: AsyncSession, mocker
):
    mock_chain = mocker.patch("app.services.campaign_creation.chain")
    mock_chain.return_value.on_error.return_value = mock_chain.return_value
    mocker.patch("app.services.campaign_creation.CampaignValidator.validate",
                 autospec=True, return_value=None)

    body, headers = await _signup(async_client, "c14-auditoria")
    org_id = body["user"]["organization_id"]
    acc = await _poblar(async_client, db_session, headers, org_id)
    org = await db_session.get(Organization, org_id)
    org.set_limite_mensual("100000.00")
    db_session.add(org)
    await db_session.commit()

    creada = await async_client.post("/api/v1/campaigns/", headers=headers, json={
        "ad_account_id": acc.id, "page_id": "p1", "product_name": "Curso",
        "budget": 200.0, "days": 7, "whatsapp_number": "51999999999",
        "copy_text": "Texto", "image_url": "https://example.com/x.jpg",
    })
    assert creada.status_code == 202, creada.text
    draft_id = creada.json()["draft_id"]

    # Un medio asociado a esa solicitud.
    medio = (await db_session.execute(
        select(MediaAsset).where(MediaAsset.organization_id == org_id)
    )).scalar_one()
    await db_session.execute(
        update(MediaAsset).where(MediaAsset.id == medio.id).values(draft_id=draft_id)
    )
    await db_session.commit()

    res = await async_client.get(f"/api/v1/proposals/drafts/{draft_id}/audit", headers=headers)
    assert res.status_code == 200, res.text
    auditoria = res.json()

    assert auditoria["organizacion_id"] == org_id
    assert auditoria["solicitada_por"]["email"] == "admin@c14-auditoria.example.com"
    assert auditoria["solicitada_en"]
    assert auditoria["aprobada_por"]["email"] == "admin@c14-auditoria.example.com"
    assert auditoria["aprobada_en"]
    assert len(auditoria["medios"]) == 1
    assert auditoria["medios"][0]["kind"] == "IMAGE"
    assert auditoria["evidencia"] is not None, "la evidencia con la que se propuso"
    assert auditoria["approved_version_id"]
    assert len(auditoria["versions"]) == 1
