"""Spec 14 — CI, despliegue reproducible y operación en un VPS limitado.

La frontera es el **pipeline y el drill de despliegue**, y eso no lo sustituye
ninguna prueba unitaria: construir, publicar, desplegar por identificador, pasar
el smoke test y volver atrás ocurren fuera del proceso de pytest. Su evidencia
vive fechada en `docs/infra/operacion/despliegues/`.

Lo que sí es automatizable, y es lo que hay acá, son las comprobaciones que
tienen que fallar cuando alguien las desalinea. Se prueban en los dos sentidos:
que aceptan el árbol real y que rechazan la divergencia inyectada. Un verificador
que solo se prueba contra un árbol correcto no prueba nada — el día que deje de
detectar, seguirá dando verde.

Cubre:

* que las versiones y el gestor de paquetes de CI, del stack local y de la
  imagen son los mismos
* que ningún contenedor puede quedarse con el VPS entero, y que no hay
  preproducción en el compose de producción
* que ninguna migración deja a la imagen anterior sin poder volver
* que el sistema en marcha dice qué commit corre y si sus migraciones están
  aplicadas
* que la suite puede correr contra el motor de producción, y que los servicios
  que abren su propia sesión hablan con la base de test
* que el mecanismo de sesiones falla cerrado contra un Redis real que se cae
* que el smoke test no da por bueno un despliegue que no pudo comprobar
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "backend" / "scripts"
DESPLIEGUE = RAIZ / "scripts" / "despliegue"


def _cargar(nombre: str):
    """Importa un script de verificación como módulo, para poder redirigir su RAIZ."""
    ruta = SCRIPTS / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(f"_spec14_{nombre}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _arbol(destino: Path, archivos: dict) -> Path:
    for ruta, contenido in archivos.items():
        completo = destino / ruta
        completo.parent.mkdir(parents=True, exist_ok=True)
        completo.write_text(contenido)
    return destino


# ── Criterios 1, 5, 6 y 15 · paridad entre CI, local y producción ───────────

def test_c1_c5_el_arbol_real_declara_las_mismas_versiones_en_todas_partes():
    """CI, el compose local, el de producción y las imágenes coinciden."""
    assert _cargar("verificar_paridad").main() == 0


def test_c1_c5_una_version_distinta_en_la_imagen_falla_la_construccion(tmp_path, capsys):
    """El caso real: la imagen construía con un Node distinto del que CI valida.

    Nadie lo notó durante un mes porque nada lo miraba.
    """
    modulo = _cargar("verificar_paridad")
    fuente = modulo.RAIZ
    archivos = {}
    for ruta in (modulo.CI, modulo.COMPOSE_LOCAL, modulo.COMPOSE_PROD,
                 modulo.DOCKERFILE_BACK, modulo.DOCKERFILE_FRONT,
                 "frontend/.nvmrc", "frontend/package.json", "frontend/pnpm-lock.yaml"):
        archivos[ruta] = (fuente / ruta).read_text()

    # La imagen del frontend se va a node 20 mientras CI sigue en 22.
    archivos[modulo.DOCKERFILE_FRONT] = archivos[modulo.DOCKERFILE_FRONT].replace(
        "ARG NODE_VERSION=22", "ARG NODE_VERSION=20"
    )
    modulo.RAIZ = _arbol(tmp_path, archivos)

    assert modulo.main() == 1
    assert "Node" in capsys.readouterr().out


def test_c6_un_segundo_lockfile_falla_la_construccion(tmp_path):
    """Dos lockfiles son dos árboles de dependencias.

    Cuál se instala depende de quién construya, que es exactamente lo que pasó:
    CI resolvía con pnpm y la imagen con npm sobre un lockfile de un mes antes.
    """
    modulo = _cargar("verificar_paridad")
    fuente = modulo.RAIZ
    archivos = {
        ruta: (fuente / ruta).read_text()
        for ruta in (modulo.CI, modulo.COMPOSE_LOCAL, modulo.COMPOSE_PROD,
                     modulo.DOCKERFILE_BACK, modulo.DOCKERFILE_FRONT,
                     "frontend/.nvmrc", "frontend/package.json", "frontend/pnpm-lock.yaml")
    }
    archivos["frontend/package-lock.json"] = '{"lockfileVersion": 3}'
    modulo.RAIZ = _arbol(tmp_path, archivos)

    assert modulo.main() == 1


def test_c6_el_lockfile_del_frontend_es_uno_solo_y_es_el_de_pnpm():
    assert (RAIZ / "frontend" / "pnpm-lock.yaml").exists()
    assert not (RAIZ / "frontend" / "package-lock.json").exists()
    manifiesto = json.loads((RAIZ / "frontend" / "package.json").read_text())
    assert manifiesto["packageManager"].startswith("pnpm@")


# ── Criterios 17 y 18 · límites de recursos y ausencia de preproducción ─────

def test_c17_todos_los_servicios_tienen_techo_y_la_suma_entra():
    assert _cargar("verificar_limites_de_recursos").main() == 0


def test_c17_un_worker_sin_techo_de_memoria_falla_la_construccion(tmp_path, capsys):
    """Un worker sin límite no se degrada: se lleva por delante a la base."""
    modulo = _cargar("verificar_limites_de_recursos")
    compose = (modulo.RAIZ / "docs/infra/coolify/lejovista.compose.yml").read_text()
    sin_techo = compose.replace("    mem_limit: 384m\n    cpus: 0.75\n", "", 1)
    modulo.RAIZ = _arbol(tmp_path, {"docs/infra/coolify/lejovista.compose.yml": sin_techo})
    modulo.COMPOSES = [("docs/infra/coolify/lejovista.compose.yml", 3200)]

    assert modulo.main() == 1
    assert "no declara" in capsys.readouterr().out


def test_c18_un_entorno_de_preproduccion_en_produccion_falla_la_construccion(tmp_path, capsys):
    """El VPS productivo no aloja staging, y eso se comprueba, no se promete."""
    modulo = _cargar("verificar_limites_de_recursos")
    compose = (modulo.RAIZ / "docs/infra/coolify/lejovista.compose.yml").read_text()
    con_staging = compose.replace(
        "  frontend:\n", "  frontend-staging:\n    image: x\n    mem_limit: 8m\n    cpus: 0.1\n  frontend:\n", 1
    )
    modulo.RAIZ = _arbol(tmp_path, {"docs/infra/coolify/lejovista.compose.yml": con_staging})
    modulo.COMPOSES = [("docs/infra/coolify/lejovista.compose.yml", 3200)]

    assert modulo.main() == 1
    assert "preproduccion" in capsys.readouterr().out


# ── Criterio 10 · migraciones que no impiden volver a la imagen anterior ────

_MIGRACION = '''"""x

Revision ID: {rev}
"""
from alembic import op
import sqlalchemy as sa

revision = "{rev}"


def upgrade() -> None:
{cuerpo}


def downgrade() -> None:
    op.drop_table("lo_que_sea")
'''


def _con_migracion(modulo, tmp_path, cuerpo: str, rev: str = "aaa111"):
    versiones = tmp_path / "backend" / "alembic" / "versions"
    versiones.mkdir(parents=True)
    (versiones / f"{rev}_x.py").write_text(_MIGRACION.format(rev=rev, cuerpo=cuerpo))
    modulo.VERSIONES = versiones
    return modulo


def test_c10_la_cadena_real_no_impide_volver_a_la_imagen_anterior():
    assert _cargar("verificar_migraciones_compatibles").main() == 0


def test_c10_una_migracion_aditiva_pasa(tmp_path):
    modulo = _con_migracion(
        _cargar("verificar_migraciones_compatibles"), tmp_path,
        '    op.add_column("campaigns", sa.Column("nueva", sa.String(), nullable=True))',
    )
    assert modulo.main() == 0


def test_c10_borrar_una_columna_sin_declararlo_falla_la_construccion(tmp_path, capsys):
    """El caso que convierte un rollback de minutos en una restauración de horas."""
    modulo = _con_migracion(
        _cargar("verificar_migraciones_compatibles"), tmp_path,
        '    op.drop_column("campaigns", "vieja")',
    )
    assert modulo.main() == 1
    salida = capsys.readouterr().out
    assert "drop_column" in salida
    assert "imagen anterior" in salida


def test_c10_un_not_null_nuevo_tambien_rompe_a_la_imagen_anterior(tmp_path):
    """La imagen vieja sigue insertando filas sin ese valor y empieza a fallar."""
    modulo = _con_migracion(
        _cargar("verificar_migraciones_compatibles"), tmp_path,
        '    op.alter_column("campaigns", "col", nullable=False)',
    )
    assert modulo.main() == 1


def test_c10_una_contraccion_declarada_pasa(tmp_path):
    """La segunda mitad de un expand-and-contract, con constancia de quién comprobó."""
    modulo = _con_migracion(
        _cargar("verificar_migraciones_compatibles"), tmp_path,
        '    # CONTRACCION-APROBADA: sin lectores desde la revision bbb222.\n'
        '    op.drop_column("campaigns", "vieja")',
    )
    assert modulo.main() == 0


def test_c10_reemplazar_una_clave_foranea_no_cuenta_como_destruccion(tmp_path):
    """Cambiar el `ondelete` borra y recrea la restricción. Marcarlo sería enseñar a ignorar."""
    modulo = _con_migracion(
        _cargar("verificar_migraciones_compatibles"), tmp_path,
        '    op.drop_constraint("fk_x", "t", type_="foreignkey")\n'
        '    op.create_foreign_key("fk_x", "t", "o", ["a"], ["id"], ondelete="SET NULL")',
    )
    assert modulo.main() == 0


def test_c10_lo_que_hace_downgrade_no_se_mira(tmp_path):
    """Ahí ser destructivo es lo correcto: es su trabajo."""
    modulo = _con_migracion(
        _cargar("verificar_migraciones_compatibles"), tmp_path,
        '    op.add_column("t", sa.Column("n", sa.String(), nullable=True))',
    )
    assert modulo.main() == 0


# ── Criterios 7 y 8 · qué commit corre, y si su esquema está aplicado ───────

@pytest.mark.asyncio
async def test_c8_el_sistema_en_marcha_dice_que_commit_corre(async_client: AsyncClient):
    res = await async_client.get("/version")
    assert res.status_code == 200
    cuerpo = res.json()
    # En desarrollo es "desconocido", que es la respuesta honesta cuando el
    # código viene de un volumen y no de un artefacto construido. Lo que no
    # puede es faltar: el runbook y el smoke test leen esta clave.
    assert "commit" in cuerpo
    assert cuerpo["migracion_en_codigo"], "la imagen tiene que saber su propia revisión head"


@pytest.mark.asyncio
async def test_c8_el_commit_sale_de_la_imagen_y_no_de_una_conjetura(async_client: AsyncClient, monkeypatch):
    """Horneado en la imagen: `/version` no lo calcula ni lo deduce del entorno."""
    from app.config import settings

    monkeypatch.setattr(settings, "GIT_SHA", "1234abcd")
    res = await async_client.get("/version")
    assert res.json()["commit"] == "1234abcd"


@pytest.mark.asyncio
async def test_c8_un_esquema_sin_migrar_no_se_reporta_como_al_dia(async_client: AsyncClient):
    """El fallo más caro de un despliegue: `migrate` no corrió y la app arrancó igual.

    La suite crea el esquema con `create_all`, así que no hay tabla
    `alembic_version` y la revisión aplicada es desconocida. Justo el estado que
    NO debe pasar por bueno.
    """
    cuerpo = (await async_client.get("/version")).json()
    assert cuerpo["migracion_aplicada"] is None
    assert cuerpo["migraciones_al_dia"] is False


# ── Criterio 2 · la suite corre contra el motor de producción ──────────────

def test_c2_la_suite_apunta_al_motor_que_pida_el_entorno():
    """Sin `TEST_DATABASE_URL` cae a SQLite; con ella, a PostgreSQL."""
    import conftest

    if os.environ.get("TEST_DATABASE_URL"):
        assert conftest.CORRIENDO_SOBRE_POSTGRES
        assert conftest.SQLALCHEMY_DATABASE_URL == os.environ["TEST_DATABASE_URL"]
    else:
        assert not conftest.CORRIENDO_SOBRE_POSTGRES


def test_c2_los_servicios_que_abren_su_propia_sesion_hablan_con_la_base_de_test():
    """Si no, apuntan a la base del `.env` y fallan cerrado en silencio.

    Es lo que hacía la comprobación de interruptores de la spec 13: apagaba la
    capacidad de IA porque no podía leer su estado, y el chat devolvía una
    plantilla. El test correspondiente pasaba o fallaba según otro test hubiera
    dejado el estado en la caché de 5 segundos.
    """
    import app.database
    import conftest

    assert app.database.async_session is conftest.TestingSessionLocal
    assert app.database.engine is conftest.engine


# ── Criterio 3 · fallo cerrado contra un Redis real que se cae ─────────────

REDIS_URL = os.environ.get("TEST_REDIS_URL")

requiere_redis = pytest.mark.skipif(
    not REDIS_URL,
    reason="Requiere un Redis real: fijar TEST_REDIS_URL. CI lo levanta (spec 14).",
)

# Se capturan ANTES de que el fixture autouse de conftest las sustituya: lo que
# se quiere probar es el mecanismo real contra un almacén real, no el doble.
from app.utils import security as _security  # noqa: E402

_ESTA_DISPONIBLE = _security.revocation_backend_available
_ESTA_REVOCADO = _security.is_revoked


@requiere_redis
@pytest.mark.asyncio
async def test_c3_con_redis_en_pie_el_mecanismo_de_revocacion_responde(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", REDIS_URL)
    assert await _ESTA_DISPONIBLE() is True
    assert await _ESTA_REVOCADO("un-jti-que-nadie-revoco") is False


@requiere_redis
@pytest.mark.asyncio
async def test_c3_con_redis_caido_la_sesion_no_se_da_por_valida(monkeypatch):
    """Fallo cerrado: no se puede afirmar que un token NO esté revocado.

    Es el comportamiento de la spec 01, y hasta ahora no había dónde probarlo
    con un almacén de verdad: el puerto muerto se parece, pero no distingue
    entre "no hay Redis configurado" y "el que hay dejó de responder".
    """
    from app.config import settings
    from app.utils.security import RevocationCheckUnavailable

    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:6399/0")
    assert await _ESTA_DISPONIBLE() is False
    with pytest.raises(RevocationCheckUnavailable):
        await _ESTA_REVOCADO("cualquier-jti")


# ── Criterio 13 · el smoke test no da por bueno lo que no pudo comprobar ───

def test_c13_el_smoke_test_falla_cuando_no_hay_nada_que_comprobar(tmp_path):
    """Un smoke test que pasa contra un host que no responde es peor que ninguno."""
    resultado = subprocess.run(
        [str(DESPLIEGUE / "smoke.sh"), "http://127.0.0.1:1"],
        capture_output=True, text=True,
        env={**os.environ, "SIN_EVIDENCIA": "1", "COMMIT_ESPERADO": "", 
             "SMOKE_EMAIL": "", "SMOKE_PASSWORD": ""},
    )
    assert resultado.returncode == 1
    assert "NO VERIFICADO" in resultado.stdout
    assert "/version no respondio" in resultado.stdout


def test_c13_el_smoke_test_recorre_los_cinco_pasos_que_exige_la_spec():
    """Autenticación, lectura de histórico y una llamada que toca base y Redis."""
    guion = (DESPLIEGUE / "smoke.sh").read_text()
    assert "/version" in guion
    assert "/api/v1/auth/login" in guion
    assert "/api/v1/campaigns/recent" in guion
    # `senales` publica la señal `dependencias`, que consulta base y Redis. Que
    # responda 200 no alcanza: responde igual con Redis caído.
    assert "/api/v1/operacion/senales" in guion
    assert "dependencias" in guion


# ── Criterios 11 y 12 · la puerta previa a una migración riesgosa ──────────

def test_c12_la_puerta_exige_un_respaldo_externo_COMPROBADO():
    """No que exista un respaldo: que la verificación de la spec 04 lo confirme."""
    guion = (DESPLIEGUE / "preflight-migracion.sh").read_text()
    assert "scripts/backup/verify.sh" in guion or "backup/verify.sh" in guion


def test_c11_la_puerta_exige_el_modo_de_solo_lectura_y_no_se_conforma_con_un_true_cualquiera():
    """El patrón laxo daba por activo el modo de solo lectura estando apagado.

    `"solo_lectura"[^}]*true` encontraba el `"encendida":true` de la primera
    capacidad. Es el peor sentido en el que puede equivocarse una puerta previa
    a una migración destructiva: deja pasar.
    """
    guion = (DESPLIEGUE / "preflight-migracion.sh").read_text()
    apagado = json.dumps({
        "solo_lectura": False,
        "solo_lectura_motivo": None,
        "capacidades": [{"capacidad": "creacion", "encendida": True}],
    })
    encendido = json.dumps({
        "solo_lectura": True,
        "solo_lectura_motivo": "ventana",
        "capacidades": [{"capacidad": "creacion", "encendida": False}],
    })

    patron = None
    for linea in guion.splitlines():
        if "grep -qE" in linea and "solo_lectura" in linea:
            patron = linea.split("grep -qE '", 1)[1].rsplit("'", 1)[0]
    assert patron, "la puerta ya no comprueba el modo de solo lectura"

    def coincide(cuerpo: str) -> bool:
        return subprocess.run(["grep", "-qE", patron], input=cuerpo,
                              text=True, capture_output=True).returncode == 0

    assert not coincide(apagado), "da por activo el modo de solo lectura estando apagado"
    assert coincide(encendido)
