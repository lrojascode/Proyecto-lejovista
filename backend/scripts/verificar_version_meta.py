"""Comprueba que TODOS los sitios declaran la misma version de la API de Meta.

Corre en CI. Es la pieza que impide que el problema reaparezca: producción usaba
v26 mientras el valor predeterminado del código, el fichero de ejemplo de
entorno, la plantilla de despliegue y seis documentos decían v25. Cualquier
despliegue que no inyectara la variable volvía a v25 en silencio.

Alinearlo una vez no arregla nada por sí solo — la próxima migración vuelve a
desalinearlo. Lo que lo arregla es que la construcción falle cuando se desalinea.

    python backend/scripts/verificar_version_meta.py
"""
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "backend"))

from app.services.meta_ads.version import VERSION_SOPORTADA  # noqa: E402

# Cada entrada: (archivo, patron que captura la version declarada).
# Solo sitios que DECLARAN el valor por defecto. Los comentarios que citan una
# versión histórica ("la Marketing API v25 documenta…") no se tocan: describen
# cuándo se aprendió algo, no qué versión se usa.
DECLARACIONES = [
    ("backend/app/config.py", r'META_API_VERSION:\s*str\s*=\s*Field\(default="(v\d+\.\d+)"\)'),
    (".env.example", r"^META_API_VERSION=(v\d+\.\d+)$"),
    ("docs/infra/coolify/lejovista.compose.yml", r"META_API_VERSION:\s*\$\{META_API_VERSION:-(v\d+\.\d+)\}"),
    ("docs/README.md", r"Meta Graph/Marketing API\*\* `(v\d+\.\d+)`"),
    ("docs/10-configuracion.md", r"\|\s*`META_API_VERSION`\s*\|\s*`(v\d+\.\d+)`"),
    ("docs/01-arquitectura-general.md", r'META\["Meta Graph API (v\d+\.\d+)"\]'),
    ("docs/11-servicios-externos.md", r"`META_API_VERSION`, default `(v\d+\.\d+)`"),
]


def main() -> int:
    print(f"Versión soportada (fuente de verdad): {VERSION_SOPORTADA}\n")
    problemas = []

    for ruta, patron in DECLARACIONES:
        archivo = RAIZ / ruta
        if not archivo.exists():
            problemas.append(f"{ruta}: no existe")
            continue
        encontrado = re.search(patron, archivo.read_text(), re.M)
        if not encontrado:
            problemas.append(
                f"{ruta}: no se encontró la declaración de versión "
                f"(¿cambió el formato del archivo?)"
            )
            continue
        declarada = encontrado.group(1)
        marca = "OK " if declarada == VERSION_SOPORTADA else "MAL"
        print(f"  [{marca}] {ruta}: {declarada}")
        if declarada != VERSION_SOPORTADA:
            problemas.append(
                f"{ruta}: declara {declarada} y la versión soportada es {VERSION_SOPORTADA}"
            )

    if problemas:
        print("\nLa versión de Meta NO está alineada:\n")
        for p in problemas:
            print(f"  - {p}")
        print(
            "\nCambia app/services/meta_ads/version.py::VERSION_SOPORTADA y "
            "alinea los demás sitios."
        )
        return 1

    print("\nTodos los sitios declaran la misma versión.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
