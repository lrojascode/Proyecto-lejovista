"""Comprueba que ninguna migracion rompe la imagen anterior sin decirlo.

Spec 14, criterios 10 y 11. El rollback de esta aplicacion es volver a la imagen
anterior. Eso funciona mientras el esquema que dejo la migracion nueva siga
sirviendo al codigo viejo: se anade, se escribe en paralelo, se conmuta la
lectura, y solo en un despliegue POSTERIOR se retira lo antiguo.

Una migracion que borra una columna en el mismo despliegue que el codigo que
dejo de usarla convierte el rollback en una restauracion desde respaldo, que es
un procedimiento de horas. Y no avisa: la migracion aplica limpia, el despliegue
queda verde, y el problema aparece el dia que hay que volver atras.

Solo se mira `upgrade()`. Lo que hace `downgrade()` es, por definicion,
destructivo, y ahi es correcto.

Una contraccion planificada —la segunda mitad de un expand-and-contract, cuando
el codigo que usaba lo viejo ya no esta desplegado— se declara en el propio
archivo:

    # CONTRACCION-APROBADA: la columna quedo sin lectores en la revision abc123,
    # desplegada el 2026-08-01. El procedimiento esta en
    # docs/infra/operacion/migraciones.md.

No es una excepcion para saltarse la regla: es la constancia de que alguien
comprobo que el codigo anterior ya no la necesita, con fecha y revision.

    python backend/scripts/verificar_migraciones_compatibles.py [--desde <rev>]
"""
import ast
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
VERSIONES = RAIZ / "backend" / "alembic" / "versions"
MARCA = "CONTRACCION-APROBADA"

# Cada operacion destructiva, con lo que le hace al codigo que ya esta corriendo.
DESTRUCTIVAS = {
    "drop_column": "la imagen anterior sigue leyendo esa columna",
    "drop_table": "la imagen anterior sigue leyendo esa tabla",
    "rename_table": "un renombrado es un borrado y un alta para el codigo viejo",
    "drop_index": "puede dejar consultas de la imagen anterior sin indice",
    "drop_constraint": "puede quitar una garantia que la imagen anterior asume",
}

def _hallazgos(arbol: ast.AST) -> list:
    """Las operaciones destructivas que hace `upgrade()`, con su linea."""
    upgrade = next(
        (n for n in ast.walk(arbol)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "upgrade"),
        None,
    )
    if upgrade is None:
        return []

    # Una restriccion o un indice que se borran y se vuelven a crear en el mismo
    # `upgrade()` no son una perdida: son un reemplazo. Es como se cambia el
    # `ondelete` de una clave foranea, y marcarlo seria ensenar a ignorar al
    # verificador. Se comparan las expresiones del nombre, no su valor, para que
    # tambien valga cuando el nombre sale de una variable de un bucle.
    recreados = {
        ast.dump(n.args[0])
        for n in ast.walk(upgrade)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("create_foreign_key", "create_index", "create_unique_constraint",
                            "create_check_constraint", "create_primary_key")
        and n.args
    }

    encontrados = []
    for nodo in ast.walk(upgrade):
        if not isinstance(nodo, ast.Call) or not isinstance(nodo.func, ast.Attribute):
            continue
        nombre = nodo.func.attr
        if nombre in ("drop_constraint", "drop_index") and nodo.args and ast.dump(nodo.args[0]) in recreados:
            continue
        if nombre in DESTRUCTIVAS:
            encontrados.append((nodo.lineno, nombre, DESTRUCTIVAS[nombre]))
        elif nombre == "alter_column":
            for kw in nodo.keywords:
                # NOT NULL sobre una columna que la imagen anterior deja vacia
                # hace fallar sus INSERT, no los del codigo nuevo.
                if kw.arg == "nullable" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                    encontrados.append(
                        (nodo.lineno, "alter_column nullable=False",
                         "la imagen anterior puede insertar filas sin ese valor")
                    )
                if kw.arg == "new_column_name":
                    encontrados.append(
                        (nodo.lineno, "alter_column new_column_name",
                         "la imagen anterior sigue escribiendo el nombre viejo")
                    )
    return encontrados


def main() -> int:
    problemas: list[str] = []
    revisadas = 0

    # Se revisa la cadena entera, no solo lo nuevo: las dos contracciones que ya
    # estaban aplicadas quedaron declaradas en su propio archivo en vez de
    # exentas en una lista aparte, que nadie vuelve a leer.
    for archivo in sorted(VERSIONES.glob("*.py")):
        revisadas += 1
        texto = archivo.read_text()
        hallazgos = _hallazgos(ast.parse(texto))
        if not hallazgos:
            print(f"  [OK ] {archivo.name}")
            continue
        if MARCA in texto:
            print(f"  [OK ] {archivo.name}: contraccion declarada ({len(hallazgos)} operaciones)")
            continue
        print(f"  [MAL] {archivo.name}")
        for linea, operacion, consecuencia in hallazgos:
            problemas.append(f"{archivo.name}:{linea}: {operacion} — {consecuencia}")

    print(f"\nRevisadas {revisadas} migraciones.")

    if problemas:
        print("\nEstas migraciones dejan a la imagen anterior sin poder volver:\n")
        for p in problemas:
            print(f"  - {p}")
        print(
            f"\nO se parten en expandir ahora / contraer despues, o se declaran con "
            f"un comentario `{MARCA}: <por que el codigo viejo ya no lo necesita>`.\n"
            f"El procedimiento: docs/infra/operacion/migraciones.md"
        )
        return 1

    print("Toda migracion nueva deja funcionando a la imagen anterior.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
