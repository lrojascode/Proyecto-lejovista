#!/usr/bin/env python3
"""Ejecuta el conjunto de evaluación de la IA (spec 10, criterio 18).

Requisito de PUBLICACIÓN, no de integración continua: corre cuando cambia un
prompt, una regla de guarda o un modelo, y compara el candidato contra la línea
base antes de publicar. Sin esto, cambiar un prompt es una apuesta — y el sistema
ya perdió esa apuesta una vez, con un tope mal calibrado que dejó al analista
contestando plantillas durante días mientras todos los tests seguían en verde.

Qué mide, y qué NO:

* Mide que la salida no pueda introducir hechos: cifras, periodo, cuenta y
  opciones fuera de catálogo.
* Mide que no prometa éxito por la apariencia.
* Mide el costo por respuesta contra la línea base.
* **No** mide si el texto es agradable ni si el consejo es bueno. Eso no se
  puede medir automáticamente sin engañarse, y no es el riesgo que esta spec
  ataja.

Uso:

    .venv/bin/python scripts/evaluar_cambio_ia.py --etiqueta "prompt analista v2"
    .venv/bin/python scripts/evaluar_cambio_ia.py --fijar-linea-base

Sale con código distinto de cero si alguna dimensión se degrada por encima del
umbral. Eso es lo que lo convierte en una compuerta y no en un informe.
"""
import argparse
import asyncio
import datetime
import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

CASOS = RAIZ / "evaluacion" / "casos"
RESULTADOS = RAIZ / "evaluacion" / "resultados"
LINEA_BASE = RESULTADOS / "linea-base.json"

# Umbrales de publicación. Ver evaluacion/README.md.
COSTO_TOLERADO = 0.15  # +15 % sin aprobación explícita


def cargar_casos():
    casos = []
    for archivo in sorted(CASOS.glob("*.json")):
        for caso in json.loads(archivo.read_text(encoding="utf-8")):
            caso["_archivo"] = archivo.name
            casos.append(caso)
    return casos


async def _evaluar_uno(caso):
    """Devuelve (paso, detalle). `paso` es si el sistema hizo lo que se espera."""
    from app.services.ai import contrato, guardas, validacion

    espera_descarte = caso["espera"] == "descartar"

    # Casos de contrato: no hay texto, hay una salida estructurada.
    if "contrato" in caso:
        decl = caso["contrato"]
        c = contrato.ContratoDeSalida(
            nombre=caso["id"],
            campos=(contrato.Campo(
                nombre=decl["campo"], catalogo=tuple(decl["catalogo"]),
            ),),
        )
        motivos = c.motivos(caso.get("salida") or {})
        descartado = bool(motivos)
        return descartado == espera_descarte, "; ".join(motivos) or "cumple"

    periodo = None
    if caso.get("periodo"):
        p = caso["periodo"]
        periodo = guardas.PeriodoAutorizado(
            desde=datetime.date.fromisoformat(p["desde"]),
            hasta=datetime.date.fromisoformat(p["hasta"]),
            dias=p.get("dias"),
        )

    cuenta = None
    if caso.get("cuenta"):
        c = caso["cuenta"]
        cuenta = guardas.CuentaAutorizada(
            external_id=c.get("external_id"),
            nombre=c.get("nombre"),
            otras_ids=tuple(c.get("otras_ids") or ()),
            otros_nombres=tuple(c.get("otros_nombres") or ()),
        )

    veredicto = await validacion.validar(
        hechos=caso.get("hechos") or {},
        respuesta=caso.get("respuesta") or "",
        contexto=validacion.Contexto(
            feature=f"evaluacion:{caso['id']}", periodo=periodo, cuenta=cuenta,
        ),
    )
    descartado = not veredicto.aceptada
    detalle = veredicto.motivo or "aceptada"

    if not descartado == espera_descarte:
        return False, detalle

    # Cuando el caso declara QUÉ motivo esperaba, se comprueba también. Un
    # descarte por el motivo equivocado pasa la cuenta y esconde que una guarda
    # dejó de funcionar mientras otra la tapaba.
    esperado = caso.get("motivo")
    if espera_descarte and esperado and veredicto.motivo != esperado:
        return False, f"descartó por '{veredicto.motivo}', se esperaba '{esperado}'"

    return True, detalle


async def ejecutar(etiqueta: str):
    # El descarte tiene que estar ACTIVO para evaluar: en modo de registro todo
    # se acepta y el conjunto entero pasaría midiendo nada.
    from app.config import settings
    settings.GROUNDING_MODO_REGISTRO = False

    # El registro de descartes se apaga durante la evaluación. Escribe en la
    # base de la aplicación, y esta herramienta corre desde el portátil de quien
    # publica, sin acceso a ella: cada caso hostil produciría un intento de
    # conexión fallido y un traceback que tapa el informe. Lo que se está
    # midiendo son las guardas, no el registro — que tiene sus propias pruebas.
    from app.services.ai import descartes

    async def _no_registrar(**_kw):
        return None

    descartes.registrar = _no_registrar

    casos = cargar_casos()
    resultados, fallos = [], []

    for caso in casos:
        try:
            paso, detalle = await _evaluar_uno(caso)
        except Exception as e:  # un caso roto no puede tumbar la corrida entera
            paso, detalle = False, f"error al evaluar: {e}"
        resultados.append({
            "id": caso["id"],
            "dimension": caso["dimension"],
            "espera": caso["espera"],
            "paso": paso,
            "detalle": detalle,
        })
        if not paso:
            fallos.append(resultados[-1])

    def tasa(dim):
        de_la_dim = [r for r in resultados if r["dimension"] == dim]
        if not de_la_dim:
            return None
        return round(sum(1 for r in de_la_dim if r["paso"]) / len(de_la_dim), 4)

    informe = {
        "etiqueta": etiqueta,
        "momento": datetime.datetime.now().isoformat(timespec="seconds"),
        "casos": len(casos),
        "grounding": tasa("grounding"),
        "seguridad": tasa("seguridad"),
        # El costo se mide sobre tráfico real, no sobre el corpus: acá no se
        # llama al modelo. Se deja declarado como None en vez de inventar un 0,
        # que es lo mismo que hace el sistema con un modelo sin tarifa.
        "costo_relativo": None,
        "resultados": resultados,
    }
    return informe, fallos


def comparar(informe, base):
    """Devuelve la lista de degradaciones. Vacía significa publicable."""
    problemas = []

    if (informe.get("seguridad") or 0) < 1.0:
        problemas.append(
            f"seguridad {informe['seguridad']}: el umbral es 1.0 y cualquier "
            "fallo bloquea la publicación"
        )

    if base:
        for dim in ("grounding", "seguridad"):
            actual, previo = informe.get(dim), base.get(dim)
            if actual is not None and previo is not None and actual < previo:
                problemas.append(f"{dim} bajó de {previo} a {actual}")

        actual_costo, previo_costo = informe.get("costo_relativo"), base.get("costo_relativo")
        if actual_costo and previo_costo and previo_costo > 0:
            variacion = (actual_costo - previo_costo) / previo_costo
            if variacion > COSTO_TOLERADO:
                problemas.append(
                    f"el costo subió {variacion:.1%}, por encima del "
                    f"{COSTO_TOLERADO:.0%} tolerado sin aprobación"
                )

    return problemas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--etiqueta", default="sin-etiqueta",
                        help="qué cambio se está evaluando")
    parser.add_argument("--fijar-linea-base", action="store_true",
                        help="guardar esta corrida como la línea base")
    args = parser.parse_args()

    informe, fallos = asyncio.run(ejecutar(args.etiqueta))

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    base = json.loads(LINEA_BASE.read_text()) if LINEA_BASE.exists() else None

    print(f"Casos: {informe['casos']}")
    print(f"  grounding: {informe['grounding']}")
    print(f"  seguridad: {informe['seguridad']}")
    if base:
        print(f"  (línea base: grounding {base.get('grounding')}, "
              f"seguridad {base.get('seguridad')})")

    for fallo in fallos:
        print(f"  FALLA {fallo['id']} ({fallo['dimension']}, "
              f"espera {fallo['espera']}): {fallo['detalle']}")

    fecha = datetime.date.today().isoformat()
    slug = "".join(c if c.isalnum() else "-" for c in args.etiqueta).strip("-").lower()
    destino = RESULTADOS / f"{fecha}-{slug}.json"
    destino.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n")
    print(f"Resultado en {destino.relative_to(RAIZ)}")

    if args.fijar_linea_base:
        LINEA_BASE.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n")
        print(f"Línea base fijada en {LINEA_BASE.relative_to(RAIZ)}")
        return 0

    problemas = comparar(informe, base)
    if problemas:
        print("\nNO PUBLICABLE:")
        for p in problemas:
            print(f"  - {p}")
        return 1

    print("\nPublicable: ninguna dimensión se degrada respecto de la línea base.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
