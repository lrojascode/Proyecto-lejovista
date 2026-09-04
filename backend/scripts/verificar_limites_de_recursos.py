"""Comprueba que ningun contenedor puede quedarse con el VPS entero.

Spec 14, criterios 17 y 18. El servidor tiene 4 GB y 2 vCPU compartidos entre
PostgreSQL, Redis, la API, tres workers, el beat y el frontend. Un worker sin
techo de memoria no se degrada: se lleva por delante a la base de datos, porque
el que elige a quien matar cuando falta memoria es el nucleo, y elige por
tamano, no por importancia.

El compose de desarrollo ya declaraba limites. El de produccion no declaraba
ninguno, que es justo al reves de donde hacen falta.

Comprueba tres cosas:

  1. Todo servicio declara `mem_limit` y `cpus`.
  2. La suma de la memoria en regimen permanente entra en el presupuesto.
     `migrate` no cuenta: corre hasta terminar y sale antes de que arranque el
     backend.
  3. No hay ningun servicio de preproduccion en el compose de produccion.

    python backend/scripts/verificar_limites_de_recursos.py
"""
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]

COMPOSES = [
    ("docs/infra/coolify/lejovista.compose.yml", 3200),
    ("docker-compose.yml", 4096),
]

# Servicios que terminan y salen: no ocupan memoria en regimen permanente.
EFIMEROS = {"migrate"}

# Un entorno de preproduccion en el VPS productivo compite por los mismos 4 GB
# con lo que atiende a los clientes. La spec maestra lo descarta y la 14 lo
# convierte en algo comprobable.
INDICIOS_DE_STAGING = ("staging", "preprod", "preproduccion", "pre-prod", "sandbox")


def _a_mib(valor) -> int:
    texto = str(valor).strip().lower()
    if texto.endswith("g"):
        return int(float(texto[:-1]) * 1024)
    if texto.endswith("m"):
        return int(float(texto[:-1]))
    raise ValueError(f"no se entiende el limite de memoria {valor!r}")


def main() -> int:
    problemas: list[str] = []

    for ruta, presupuesto in COMPOSES:
        archivo = RAIZ / ruta
        if not archivo.exists():
            problemas.append(f"{ruta}: no existe")
            continue
        servicios = (yaml.safe_load(archivo.read_text()) or {}).get("services", {})
        print(f"\n{ruta} (presupuesto: {presupuesto} MiB)")

        permanente = 0
        for nombre, definicion in servicios.items():
            mem = definicion.get("mem_limit")
            cpu = definicion.get("cpus")
            if mem is None or cpu is None:
                problemas.append(
                    f"{ruta}: el servicio '{nombre}' no declara "
                    f"{'mem_limit' if mem is None else 'cpus'}"
                )
                print(f"  [MAL] {nombre}: mem_limit={mem} cpus={cpu}")
                continue
            marca = "efimero" if nombre in EFIMEROS else "permanente"
            if nombre not in EFIMEROS:
                permanente += _a_mib(mem)
            print(f"  [OK ] {nombre:24} {str(mem):>6}  {cpu} vCPU  ({marca})")

            if any(i in nombre.lower() for i in INDICIOS_DE_STAGING):
                problemas.append(
                    f"{ruta}: el servicio '{nombre}' parece un entorno de "
                    f"preproduccion, y no debe correr en el VPS productivo"
                )

        print(f"  Memoria en regimen permanente: {permanente} MiB")
        if permanente > presupuesto:
            problemas.append(
                f"{ruta}: los limites suman {permanente} MiB en regimen permanente "
                f"y el presupuesto es {presupuesto} MiB"
            )

    if problemas:
        print("\nLos limites de recursos no cierran:\n")
        for p in problemas:
            print(f"  - {p}")
        print("\nLa aritmetica y el porque de cada numero: docs/infra/operacion/dimensionado.md")
        return 1

    print("\nTodos los servicios tienen techo y la suma entra en el presupuesto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
