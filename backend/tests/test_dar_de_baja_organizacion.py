"""Baja de una organización residual.

Un script que borra tenants tiene que ser más difícil de usar mal que de usar
bien: `organizations` cascadea a media base, así que un DELETE sobre la
equivocada es irreversible y silencioso.
"""
import importlib.util
import pathlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User

_RUTA = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "dar_de_baja_organizacion.py"
_spec = importlib.util.spec_from_file_location("dar_de_baja_organizacion", _RUTA)
baja = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baja)


async def _org(db: AsyncSession, slug: str, con_cuenta: bool = False):
    org = Organization(slug=slug, name=slug, status="ACTIVE")
    db.add(org)
    await db.flush()
    user = User(email=f"{slug}@example.com", hashed_password="x", full_name="U")
    db.add(user)
    await db.flush()
    db.add(OrganizationUser(user_id=user.id, organization_id=org.id, role="ADMIN"))
    if con_cuenta:
        db.add(AdAccount(
            organization_id=org.id, platform="META",
            external_account_id=f"act_{slug}", name="Acc", status="ACTIVE",
        ))
    await db.commit()
    return org, user


@pytest.mark.asyncio
async def test_se_niega_a_tocar_una_organizacion_con_datos(
    db_session: AsyncSession, capsys
):
    """Si tiene cuentas, no es residual: darla de baja es una decisión de
    negocio, no una limpieza."""
    org, _ = await _org(db_session, "conreal", con_cuenta=True)
    assert await baja.dar_de_baja(db_session, "conreal", aplicar=True) == 1
    assert "NO SE TOCA" in capsys.readouterr().out

    await db_session.refresh(org)
    assert org.status == "ACTIVE"


@pytest.mark.asyncio
async def test_sin_aplicar_no_escribe_nada(
    db_session: AsyncSession, capsys
):
    org, _ = await _org(db_session, "residual1")
    assert await baja.dar_de_baja(db_session, "residual1", aplicar=False) == 0
    assert "RESIDUAL" in capsys.readouterr().out

    await db_session.refresh(org)
    assert org.status == "ACTIVE"
    miembros = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org.id)
    )).scalars().all()
    assert len(miembros) == 1


@pytest.mark.asyncio
async def test_con_aplicar_la_marca_inactive_y_le_quita_los_miembros(
    db_session: AsyncSession, capsys
):
    org, _ = await _org(db_session, "residual2")
    assert await baja.dar_de_baja(db_session, "residual2", aplicar=True) == 0
    assert "LISTO" in capsys.readouterr().out

    await db_session.refresh(org)
    assert org.status == "INACTIVE"
    miembros = (await db_session.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org.id)
    )).scalars().all()
    assert miembros == []
    # La fila se conserva: el CASCADE de organizations se lleva media base.
    assert (await db_session.get(Organization, org.id)) is not None


@pytest.mark.asyncio
async def test_un_slug_inexistente_no_es_un_crash(
    db_session: AsyncSession, capsys
):
    assert await baja.dar_de_baja(db_session, "no-existe", aplicar=True) == 1
    assert "No existe" in capsys.readouterr().out
