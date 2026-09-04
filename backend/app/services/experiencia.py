"""Las dos experiencias del producto: cliente y agencia (spec 05).

Una sola plataforma y una sola fuente de datos, dos alturas distintas.

Lo que corrige esta separacion es un problema de frontera, no de estetica. Hasta
ahora el catalogo de modulos gobernaba la superficie de TODOS: apagar un modulo
para simplificarle la vista al cliente se lo apagaba tambien al operador de la
agencia, que necesita esa herramienta para prestar el servicio. Modulos y roles
respondian la misma pregunta desde dos sitios, y "ocultar" acababa usandose como
sustituto de "autorizar".

El contrato, en una linea: **el modulo acota la experiencia del CLIENTE; el rol
acota lo que puede hacer CUALQUIERA. Una peticion tiene que pasar las dos.**
"""
from typing import Any, Optional

# De que lado del producto entra una membresia.
ACCESO_CLIENTE = "CLIENT"
ACCESO_AGENCIA = "AGENCY"
ACCESOS_VALIDOS = (ACCESO_CLIENTE, ACCESO_AGENCIA)


def acceso_de(user: Any, org_id: Optional[str] = None) -> str:
    """De que lado entra este usuario en la Organizacion indicada.

    Ante la duda, `AGENCY`: perder una herramienta operativa se nota y se
    arregla; mostrarle al cliente una superficie de agencia por un default mal
    puesto es lo que esta separacion existe para evitar... y sin embargo el
    default seguro aqui es el contrario de lo que parece. Un usuario sin
    membresia resuelta no ve datos de ningun tenant (lo impide el aislamiento,
    no esto), asi que este valor solo decide QUE SUPERFICIE se le arma.
    """
    objetivo = org_id or getattr(user, "organization_id", None)
    if not objetivo:
        return ACCESO_AGENCIA
    for ou in (getattr(user, "organizations", None) or []):
        if ou.organization_id == objetivo:
            valor = (getattr(ou, "access_kind", None) or ACCESO_AGENCIA).upper()
            return valor if valor in ACCESOS_VALIDOS else ACCESO_AGENCIA
    return ACCESO_AGENCIA


def es_cliente(user: Any, org_id: Optional[str] = None) -> bool:
    return acceso_de(user, org_id) == ACCESO_CLIENTE


def es_agencia(user: Any, org_id: Optional[str] = None) -> bool:
    return acceso_de(user, org_id) == ACCESO_AGENCIA


# Campos que NUNCA salen hacia la experiencia del cliente. No son secretos
# tecnicos por casualidad: son las palancas de la agencia (proveedor y modelo de
# IA, claves, estado del write-back). El cliente ve QUE existe una configuracion
# y su estado, nunca su contenido.
CAMPOS_SOLO_AGENCIA = frozenset({
    "ai_model",
    "llm_provider",
    "llm_api_key_preview",
    "has_llm_api_key",
    # El techo de consumo de IA y su estado de aviso son palancas de la agencia:
    # el cliente no negocia su presupuesto de IA en la pantalla (spec 10, dep. 05).
    "ai_monthly_budget_usd",
    "ai_budget_alert_state",
    "campaign_rules_overrides",
    "ai_thresholds_overrides",
    "simulated",
    "simulated_reason",
    "writeback_block_reason",
    "grounding",
    "grounding_values",
    "prompt_version",
    "provider",
    "model",
})


def sanear_para_cliente(datos: dict) -> dict:
    """Vacia de una respuesta lo que pertenece a la altura de la agencia.

    Se aplica en el BORDE de salida y sobre el diccionario completo, no campo por
    campo en cada endpoint: una lista repartida por veinte sitios se desactualiza
    en el primero que alguien anada.

    **Se pone el valor a `None`, no se quita la clave.** Una respuesta cuya FORMA
    cambia segun quien pregunta obliga al frontend y a los tipos generados a
    tratar cada campo como opcional por un motivo que no es el suyo. Lo que
    importa —y lo que la spec pide— es que el VALOR no llegue al cliente; la
    forma estable es preferible. Ademas es explicito: sin esto, las claves
    reaparecian igualmente como `null` al revalidar contra el modelo de
    respuesta, y el saneado real habria sido un efecto colateral de FastAPI en
    vez de una decision.
    """
    if not isinstance(datos, dict):
        return datos
    return {
        k: (
            None if k in CAMPOS_SOLO_AGENCIA
            else (sanear_para_cliente(v) if isinstance(v, dict) else v)
        )
        for k, v in datos.items()
    }
