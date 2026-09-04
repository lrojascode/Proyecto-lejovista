"""Spec 08 — Meta Graph/Marketing API v26 y gestión de deprecaciones.

Un test por criterio de aceptación de
`docs/specs/08-meta-api-v26-y-gestion-de-deprecaciones.md`.

La coherencia entre archivos se prueba **como comprobación de construcción**: es
el único lugar donde una divergencia se detecta antes de llegar a producción.
Alinear la versión una vez no arregla nada —la próxima migración vuelve a
desalinearla—; lo que lo arregla es que la construcción falle.
"""
import datetime
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization import Organization
from app.services.meta_ads.version import (
    CABECERA_VERSION_ATENDIDA, DIAS_DE_AVISO_ANTICIPADO,
    EXPIRACION_VERSION_SOPORTADA, VERSION_SOPORTADA, VERSIONES_ADMITIDAS,
    dias_hasta_la_expiracion, es_version_valida, expiracion_proxima,
    registrar_version_atendida, version_para,
)

RAIZ = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Criterios 1-4 · una sola versión, declarada en todas partes
# --------------------------------------------------------------------------

def test_c1_la_version_soportada_es_v26():
    assert VERSION_SOPORTADA == "v26.0"


def test_c1b_el_default_de_configuracion_coincide_con_la_soportada():
    """Producción usaba v26 mientras el default decía v25: cualquier despliegue
    que no inyectara la variable volvía a v25 en silencio."""
    from app.config import Settings

    assert Settings.model_fields["META_API_VERSION"].default == VERSION_SOPORTADA


def test_c2_y_c3_todos_los_sitios_declaran_la_misma_version():
    """Esta es la comprobación que impide que el problema reaparezca. Corre
    también en CI."""
    res = subprocess.run(
        [sys.executable, str(RAIZ / "backend" / "scripts" / "verificar_version_meta.py")],
        capture_output=True, text=True, cwd=str(RAIZ / "backend"),
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_c4_un_entorno_sin_variables_usa_la_version_soportada():
    """Un entorno nuevo levantado desde la plantilla no puede nacer con la
    versión equivocada."""
    assert version_para(None) == VERSION_SOPORTADA


# --------------------------------------------------------------------------
# Criterios 5-6 · la versión que Meta atiende de verdad
# --------------------------------------------------------------------------

def test_c5_se_registra_la_version_que_meta_declara_haber_atendido():
    atendida = registrar_version_atendida({CABECERA_VERSION_ATENDIDA: "v26.0"}, "v26.0")
    assert atendida == "v26.0"


def test_c6_una_divergencia_se_detecta_y_se_avisa(caplog):
    """Una llamada que cae a otra versión solo se nota cuando un campo
    desaparece — con el cliente ya afectado."""
    import logging

    with caplog.at_level(logging.WARNING):
        atendida = registrar_version_atendida(
            {CABECERA_VERSION_ATENDIDA: "v25.0"}, "v26.0"
        )
    assert atendida == "v25.0"
    assert "DIVERGENCIA" in caplog.text
    assert "v25.0" in caplog.text and "v26.0" in caplog.text


def test_c6b_sin_cabecera_no_se_inventa_una_divergencia(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        assert registrar_version_atendida({}, "v26.0") is None
    assert "DIVERGENCIA" not in caplog.text


# --------------------------------------------------------------------------
# Criterio 7 · la expiración se vigila con antelación
# --------------------------------------------------------------------------

def test_c7_la_expiracion_esta_registrada_y_se_avisa_con_antelacion():
    """Meta retira cada versión a los ~2 años, y entonces fallan TODAS las
    llamadas de TODOS los clientes a la vez."""
    assert isinstance(EXPIRACION_VERSION_SOPORTADA, datetime.date)
    assert dias_hasta_la_expiracion() > 0, "la versión soportada ya expiró"

    # Lejos de la fecha no avisa; cerca, sí.
    lejos = EXPIRACION_VERSION_SOPORTADA - datetime.timedelta(days=DIAS_DE_AVISO_ANTICIPADO + 30)
    cerca = EXPIRACION_VERSION_SOPORTADA - datetime.timedelta(days=DIAS_DE_AVISO_ANTICIPADO - 1)
    assert expiracion_proxima(lejos) is False
    assert expiracion_proxima(cerca) is True


# --------------------------------------------------------------------------
# Criterio 8 · el inventario existe y es consultable
# --------------------------------------------------------------------------

def test_c8_el_inventario_existe_y_cubre_lo_que_se_usa():
    """Sin inventario, evaluar una deprecación obliga a leer el código entero."""
    inventario = (RAIZ / "docs" / "11-inventario-meta.md").read_text()

    for endpoint in ("/campaigns", "/adsets", "/ads", "/adcreatives",
                     "/adimages", "/advideos", "/insights", "debug_token"):
        assert endpoint in inventario, f"falta {endpoint} en el inventario"

    for permiso in ("ads_read", "ads_management", "business_management"):
        assert permiso in inventario, f"falta el permiso {permiso}"

    # Y la versión que declara es la soportada.
    assert VERSION_SOPORTADA in inventario


# --------------------------------------------------------------------------
# Criterios 10-12 · versión candidata por Organización
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_c10_una_version_candidata_se_activa_solo_en_una_organizacion(
    db_session: AsyncSession
):
    piloto = Organization(slug="c10-piloto", name="Piloto")
    piloto.meta_api_version_candidata = "v27.0"
    otra = Organization(slug="c10-otra", name="Otra")
    db_session.add_all([piloto, otra])
    await db_session.commit()

    assert version_para(piloto) == "v27.0"
    assert version_para(otra) == VERSION_SOPORTADA


@pytest.mark.asyncio
async def test_c12_vaciar_la_candidata_devuelve_a_la_vigente_sin_desplegar(
    db_session: AsyncSession
):
    """El rollback es cambiar un campo, no construir y publicar una imagen."""
    org = Organization(slug="c12-rollback", name="Rollback")
    org.meta_api_version_candidata = "v27.0"
    db_session.add(org)
    await db_session.commit()
    assert version_para(org) == "v27.0"

    await db_session.execute(
        update(Organization).where(Organization.id == org.id)
        .values(meta_api_version_candidata=None)
    )
    await db_session.commit()
    await db_session.refresh(org)
    assert version_para(org) == VERSION_SOPORTADA


def test_una_candidata_no_admitida_cae_a_la_soportada(caplog):
    """Quedarse sin versión es peor que ignorar una configuración equivocada,
    pero ignorarla en silencio también: se registra."""
    import logging

    class Org:
        slug = "inventada"
        meta_api_version_candidata = "v99.0"

    with caplog.at_level(logging.WARNING):
        assert version_para(Org()) == VERSION_SOPORTADA
    assert "VERSIONES_ADMITIDAS" in caplog.text


def test_la_version_anterior_sigue_admitida_durante_una_migracion():
    """Conviven DOS versiones: la vigente y una candidata. Mantener tres
    multiplica los caminos sin aportar seguridad."""
    assert VERSION_SOPORTADA in VERSIONES_ADMITIDAS
    assert "v25.0" in VERSIONES_ADMITIDAS, "el rollback necesita la anterior"


def test_el_formato_de_version_se_valida():
    assert es_version_valida("v26.0") is True
    assert es_version_valida("26.0") is False
    assert es_version_valida("") is False
    assert es_version_valida("latest") is False


# --------------------------------------------------------------------------
# Criterio 13 · el procedimiento de migración está escrito
# --------------------------------------------------------------------------

def test_c13_el_procedimiento_de_migracion_esta_documentado():
    inventario = (RAIZ / "docs" / "11-inventario-meta.md").read_text().lower()
    assert "migrar a una versión nueva" in inventario
    assert "cuenta piloto" in inventario
    assert "rollback" in inventario
    # Y nombra las dos cosas que hay que ejercitar: los campos retirados se
    # manifiestan en el sync, los cambios de contrato solo al escribir.
    assert "sync completo" in inventario
    assert "creación pausada" in inventario
