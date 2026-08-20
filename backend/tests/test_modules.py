"""Módulos habilitables por organización (execution/01 §3).

Lo que estos tests protegen, en orden de importancia:

1. Que apagar un módulo sea SEGURIDAD y no cosmética: el endpoint responde 403
   aunque el cliente escriba la URL a mano.
2. Que el cliente no pueda encenderse módulos a sí mismo — si el ADMIN de la
   organización pudiera, el módulo dejaría de ser una decisión comercial.
3. Que una organización sin configurar se comporte EXACTAMENTE como antes.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.tenant_rules.modules import (
    AVAILABLE_MODULES,
    enabled_modules_for,
    is_module_enabled,
    normalize_enabled_modules,
)


async def _signup(async_client: AsyncClient, slug: str):
    res = await async_client.post("/api/v1/auth/signup", json={
        "email": f"admin@{slug}.example.com", "password": "securepassword123",
        "full_name": "Modules Tester", "organization_slug": slug, "organization_name": "Modules Org",
    })
    body = res.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["organization_id"]


async def _set_modules(db_session: AsyncSession, org_id: str, modules):
    await db_session.execute(
        update(Organization).where(Organization.id == org_id).values(enabled_modules=modules)
    )
    await db_session.commit()


# --- El catálogo, sin BD ---

def test_sin_configurar_significa_todos():
    """Contrato igual al de campaign_rules_overrides: ausente = comportamiento
    de siempre. Ninguna org existente cambia hasta que alguien la toque."""
    class Org: enabled_modules = None
    assert enabled_modules_for(Org()) == list(AVAILABLE_MODULES)
    Org.enabled_modules = []
    assert enabled_modules_for(Org()) == list(AVAILABLE_MODULES)


def test_un_dato_corrupto_degrada_a_todos_no_a_nada():
    """El modo degradado tiene que ser el producto completo, no un cliente sin
    pantallas."""
    class Org: enabled_modules = "esto-no-es-una-lista"
    assert enabled_modules_for(Org()) == list(AVAILABLE_MODULES)
    Org.enabled_modules = ["modulo-que-no-existe"]
    assert enabled_modules_for(Org()) == list(AVAILABLE_MODULES)


def test_settings_no_se_puede_apagar():
    """Una org sin Configuración no puede conectar Meta ni repararse sola."""
    class Org: enabled_modules = ["chat"]
    assert is_module_enabled(Org(), "settings")
    assert normalize_enabled_modules(["chat"]) == ["chat", "settings"]


def test_modulo_fuera_del_catalogo_nunca_esta_habilitado():
    class Org: enabled_modules = None
    assert is_module_enabled(Org(), "inventado") is False


# --- La capa que es seguridad ---

@pytest.mark.asyncio
async def test_modulo_apagado_responde_403_en_la_api(async_client: AsyncClient, db_session: AsyncSession):
    """Ocultar el menú no alcanza: escribiendo la URL a mano tampoco entra."""
    headers, org_id = await _signup(async_client, "mod-apagado")
    await _set_modules(db_session, org_id, ["campanas", "chat"])

    res = await async_client.get("/api/v1/ai-usage", headers=headers)
    assert res.status_code == 403
    assert "no está habilitado" in res.json()["detail"]


@pytest.mark.asyncio
async def test_modulo_encendido_no_bloquea(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id = await _signup(async_client, "mod-encendido")
    await _set_modules(db_session, org_id, ["ai_usage"])

    res = await async_client.get("/api/v1/ai-usage", headers=headers)
    assert res.status_code != 403


@pytest.mark.asyncio
async def test_org_sin_configurar_se_comporta_como_siempre(async_client: AsyncClient):
    """La regresión que más importa: nadie pierde acceso por este cambio."""
    headers, _ = await _signup(async_client, "mod-default")
    res = await async_client.get("/api/v1/ai-usage", headers=headers)
    assert res.status_code != 403


@pytest.mark.asyncio
async def test_el_cliente_no_puede_encenderse_modulos_a_si_mismo(
    async_client: AsyncClient, db_session: AsyncSession
):
    """`enabled_modules` NO está en OrganizationUpdate a propósito: si el ADMIN
    de la organización pudiera cambiarlo, el módulo dejaría de ser una decisión
    comercial y pasaría a ser una preferencia del cliente."""
    headers, org_id = await _signup(async_client, "mod-escalada")
    await _set_modules(db_session, org_id, ["campanas"])

    await async_client.patch(
        "/api/v1/organizations/my",
        headers=headers,
        json={"enabled_modules": list(AVAILABLE_MODULES)},
    )

    org = await db_session.get(Organization, org_id)
    await db_session.refresh(org)
    assert "ai_usage" not in enabled_modules_for(org)
    res = await async_client.get("/api/v1/ai-usage", headers=headers)
    assert res.status_code == 403


# --- El endpoint que consume el frontend ---

@pytest.mark.asyncio
async def test_endpoint_my_modules(async_client: AsyncClient, db_session: AsyncSession):
    headers, org_id = await _signup(async_client, "mod-endpoint")
    await _set_modules(db_session, org_id, ["campanas", "chat"])

    res = await async_client.get("/api/v1/organizations/my/modules", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert set(body["enabled"]) == {"campanas", "chat", "settings"}
    assert body["available"] == AVAILABLE_MODULES
    assert "settings" in body["undisableable"]
