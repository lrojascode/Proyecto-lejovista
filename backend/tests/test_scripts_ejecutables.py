"""Que los scripts de `scripts/` se puedan EJECUTAR, no solo importar.

`python scripts/x.py` pone `scripts/` en `sys.path`, no el directorio actual, así
que `from app...` falla con `ModuleNotFoundError` aunque estés parado en la raíz
del backend. Los tests no lo veían porque pytest arma el path por su cuenta y
los scripts se cargaban con `importlib` desde una ruta: o sea que estaban
probados de una forma que nadie usa.

Se corren como subproceso y desde OTRO directorio, que es exactamente como se
usan en producción (terminal del contenedor, `WORKDIR /app`).
"""
import pathlib
import subprocess
import sys

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
_EJECUTABLES = sorted(p.name for p in _SCRIPTS.glob("*.py"))


@pytest.mark.parametrize("nombre", _EJECUTABLES)
def test_el_script_arranca_sin_romperse_al_importar(nombre, tmp_path):
    """No se comprueba que haga su trabajo —para eso hacen falta datos— sino que
    llegue a ejecutarse. `ModuleNotFoundError: app` es el fallo que se persigue."""
    res = subprocess.run(
        [sys.executable, str(_SCRIPTS / nombre)],
        cwd=tmp_path,  # desde otro directorio, como en el contenedor
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": "/usr/bin:/bin",
            "SECRET_KEY": "test-secret-key-solo-para-tests-1234567890",
            "ENCRYPTION_KEY": "sHEU494awg7HtPuuIPg989QiYv0fUh0_kdmnGVa31N8=",
            "MOCK_SERVICES": "true",
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/vacia.db",
        },
    )
    salida = res.stdout + res.stderr
    assert "ModuleNotFoundError" not in salida, (
        f"{nombre} no encuentra sus propios imports al ejecutarse:\n{salida[-800:]}"
    )
    assert "No module named 'app'" not in salida
