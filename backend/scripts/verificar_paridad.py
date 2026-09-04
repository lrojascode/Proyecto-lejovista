"""Comprueba que CI, el stack local y produccion declaran las MISMAS versiones.

Spec 14, criterios 1, 5 y 15. La paridad entre lo que CI valida y lo que
produccion ejecuta no se comprueba confiando en que coincidan: se comprueba
comparando las versiones declaradas en cada sitio y fallando la construccion
cuando divergen.

El caso que motiva el script ya habia pasado. La imagen del frontend se
construia con `npm ci` sobre un package-lock.json de julio mientras CI validaba
`pnpm install --frozen-lockfile` sobre un pnpm-lock.yaml de agosto, y ademas con
node 20 contra los node 22 de CI. Nadie lo noto porque nada lo miraba: el commit
que migro CI a pnpm anoto "package-lock.json queda en el repo pero ya no lo usa
nadie", y el Dockerfile si lo usaba. Un CI verde describia un artefacto que no
era el que se desplegaba.

    python backend/scripts/verificar_paridad.py
"""
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

CI = ".github/workflows/ci.yml"
COMPOSE_LOCAL = "docker-compose.yml"
COMPOSE_PROD = "docs/infra/coolify/lejovista.compose.yml"
DOCKERFILE_BACK = "backend/Dockerfile"
DOCKERFILE_FRONT = "frontend/Dockerfile"

# Cada componente: nombre y la lista de (archivo, patron) que lo declaran. El
# patron captura la version en el grupo 1. Todas las capturas de un componente
# tienen que dar el mismo valor.
COMPONENTES = {
    "PostgreSQL": [
        (CI, r"image:\s*postgres:(\d+)"),
        (COMPOSE_LOCAL, r"image:\s*postgres:(\d+)-alpine"),
        (COMPOSE_PROD, r"image:\s*postgres:(\d+)-alpine"),
    ],
    "Redis": [
        (CI, r"image:\s*redis:(\d+)"),
        (COMPOSE_LOCAL, r"image:\s*redis:(\d+)-alpine"),
        (COMPOSE_PROD, r"image:\s*redis:(\d+)-alpine"),
    ],
    "Python": [
        (CI, r'python-version:\s*"([\d.]+)"'),
        (DOCKERFILE_BACK, r"FROM python:([\d.]+)-slim"),
    ],
    "Node": [
        (CI, r'node-version:\s*"(\d+)"'),
        (DOCKERFILE_FRONT, r"ARG NODE_VERSION=(\d+)"),
        ("frontend/.nvmrc", r"^(\d+)"),
    ],
}


def _capturas(ruta: str, patron: str, problemas: list) -> set:
    archivo = RAIZ / ruta
    if not archivo.exists():
        problemas.append(f"{ruta}: no existe")
        return set()
    encontrado = set(re.findall(patron, archivo.read_text(), re.M))
    if not encontrado:
        problemas.append(f"{ruta}: no declara la version (patron: {patron})")
    return encontrado


def _versiones(problemas: list) -> None:
    for componente, declaraciones in COMPONENTES.items():
        valores = {}
        for ruta, patron in declaraciones:
            for v in _capturas(ruta, patron, problemas):
                valores.setdefault(v, []).append(ruta)
        if not valores:
            continue
        marca = "OK " if len(valores) == 1 else "MAL"
        detalle = ", ".join(f"{v} ({', '.join(rutas)})" for v, rutas in sorted(valores.items()))
        print(f"  [{marca}] {componente}: {detalle}")
        if len(valores) > 1:
            problemas.append(f"{componente}: versiones distintas — {detalle}")


def _gestor_de_paquetes(problemas: list) -> None:
    """Un solo gestor de paquetes en el frontend, y el mismo en CI y en la imagen."""
    pkg = RAIZ / "frontend/package.json"
    declarado = json.loads(pkg.read_text()).get("packageManager", "")
    if not declarado.startswith("pnpm@"):
        problemas.append(f"frontend/package.json: packageManager es {declarado!r}, se espera pnpm@…")
        return
    print(f"  [OK ] Gestor declarado: {declarado}")

    sobrante = RAIZ / "frontend/package-lock.json"
    if sobrante.exists():
        problemas.append(
            "frontend/package-lock.json existe: un segundo lockfile es un segundo "
            "arbol de dependencias, y el que se instale depende de quien construya"
        )
    else:
        print("  [OK ] No hay un segundo lockfile (package-lock.json)")

    if not (RAIZ / "frontend/pnpm-lock.yaml").exists():
        problemas.append("frontend/pnpm-lock.yaml: no existe, no hay dependencias bloqueadas")

    # Sin comentarios: el Dockerfile explica en prosa por que ya no usa `npm ci`,
    # y buscar la cadena a secas encontraba esa explicacion.
    dockerfile = "\n".join(
        l for l in (RAIZ / DOCKERFILE_FRONT).read_text().splitlines()
        if not l.lstrip().startswith("#")
    )
    if "npm ci" in dockerfile or "yarn install" in dockerfile:
        problemas.append(f"{DOCKERFILE_FRONT}: instala con npm/yarn y el proyecto usa pnpm")
    if "pnpm install --frozen-lockfile" not in dockerfile:
        problemas.append(
            f"{DOCKERFILE_FRONT}: no instala con `pnpm install --frozen-lockfile`, "
            "que es lo que hace fallar la construccion si el lockfile no corresponde"
        )
    ci = (RAIZ / CI).read_text()
    if "pnpm install --frozen-lockfile" not in ci:
        problemas.append(f"{CI}: no instala con `pnpm install --frozen-lockfile`")
    if "npm ci" not in dockerfile and "pnpm install --frozen-lockfile" in dockerfile:
        print("  [OK ] La imagen y CI instalan con el mismo gestor y el mismo lockfile")


def main() -> int:
    print("Paridad entre CI, el stack local y produccion:\n")
    problemas: list[str] = []
    _versiones(problemas)
    _gestor_de_paquetes(problemas)

    if problemas:
        print("\nLo que CI valida NO es lo que produccion ejecuta:\n")
        for p in problemas:
            print(f"  - {p}")
        return 1

    print("\nTodos los sitios declaran las mismas versiones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
