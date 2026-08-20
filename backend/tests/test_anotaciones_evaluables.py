"""Las anotaciones de tipo tienen que poder evaluarse en Python 3.11.

Existe por un fallo real de CI: `_preflight_warnings` se anotó con `Dict` sin
importarlo, la suite local pasó las 814 pruebas y el CI reventó al importar el
módulo, antes de correr un solo test.

La causa es una diferencia de versión. Desde Python 3.14 las anotaciones se
evalúan de forma diferida (PEP 649), así que un nombre inexistente en una
anotación no se nota. En 3.11 —la versión del CI y de la imagen de producción—
se evalúan al definir la función y lanzan `NameError` en el import.

Este test no depende de la versión del intérprete: recorre el AST y comprueba
que todo nombre de `typing` usado en una anotación esté efectivamente
disponible en ese módulo. Vale la pena que sea barato, porque protege contra un
fallo que rompe el arranque entero de la app, no un caso de borde.
"""
import ast
import builtins
import pathlib

import pytest

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Nombres de `typing` que aparecen en anotaciones de este proyecto. La lista es
# explícita para que el test falle por lo que tiene que fallar y no por
# cualquier símbolo desconocido.
_TYPING_NAMES = frozenset({
    "Any", "Awaitable", "Callable", "Coroutine", "Dict", "FrozenSet", "Iterable",
    "List", "Literal", "Mapping", "Optional", "Sequence", "Set", "Tuple", "Type",
    "Union",
})


def _nombres_disponibles(arbol: ast.AST) -> set:
    """Lo que ese módulo tiene a mano: imports, defs y asignaciones de nivel
    superior, más los builtins."""
    disponibles = set(dir(builtins))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom):
            for alias in nodo.names:
                disponibles.add(alias.asname or alias.name)
        elif isinstance(nodo, ast.Import):
            for alias in nodo.names:
                disponibles.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            disponibles.add(nodo.name)
        elif isinstance(nodo, ast.Assign):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name):
                    disponibles.add(destino.id)
    return disponibles


def _anotaciones(arbol: ast.AST):
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if nodo.returns:
                yield nodo.returns
            args = nodo.args
            for arg in (*args.args, *args.posonlyargs, *args.kwonlyargs,
                        args.vararg, args.kwarg):
                if arg is not None and arg.annotation:
                    yield arg.annotation
        elif isinstance(nodo, ast.AnnAssign) and nodo.annotation:
            yield nodo.annotation


def _modulos():
    return sorted(_APP.rglob("*.py"))


def test_hay_modulos_que_revisar():
    """Si el barrido no encuentra nada, el test de abajo pasaría vacío."""
    assert len(_modulos()) > 50


@pytest.mark.parametrize("ruta", _modulos(), ids=lambda p: str(p.name))
def test_las_anotaciones_solo_usan_nombres_disponibles(ruta: pathlib.Path):
    arbol = ast.parse(ruta.read_text())
    disponibles = _nombres_disponibles(arbol)

    faltantes = [
        (sub.id, sub.lineno)
        for anotacion in _anotaciones(arbol)
        for sub in ast.walk(anotacion)
        if isinstance(sub, ast.Name)
        and sub.id in _TYPING_NAMES
        and sub.id not in disponibles
    ]
    assert not faltantes, (
        f"{ruta.relative_to(_APP.parent)} usa en anotaciones nombres que no importa: "
        + ", ".join(f"'{n}' (línea {l})" for n, l in faltantes)
        + ". En Python 3.11 esto lanza NameError al importar el módulo."
    )
