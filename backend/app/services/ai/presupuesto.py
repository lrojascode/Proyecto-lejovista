"""Presupuesto mensual de consumo de IA por Organización (spec 10, 13–15 y 17).

La clave del LLM la pone el cliente. Hasta ahora el sistema medía su gasto con
mucho detalle —tokens de entrada, de salida, de razonamiento, cacheados, costo
estimado por funcionalidad— y **no lo detenía nunca**. Un bucle de reintentos o
un uso abusivo se le factura a él, y la primera noticia llega en el resumen del
proveedor a fin de mes.

Contrato, deliberadamente paralelo al de la compuerta de presupuesto publicitario
de `02`, porque son el mismo tipo de decisión:

* **Ámbito**: la Organización.
* **Ventana**: el mes calendario en la zona horaria del negocio. En Perú (UTC-5)
  las últimas cinco horas de cada día ya son el día siguiente en UTC; calcular el
  mes en UTC dejaba el consumo de fin de mes fuera de la ventana.
* **Aritmética**: `Decimal`. Esta suma decide si se llama al modelo.
* **Moneda**: USD, que es en la que facturan los tres proveedores. No se
  convierte a la moneda de la cuenta publicitaria: mezclarlas obligaría a un tipo
  de cambio, y un tipo de cambio en una compuerta es una fuente de error que no
  hace falta introducir.

Los tres umbrales no son lo mismo. 70 % y 90 % **avisan**; 100 % **bloquea**. Y
el bloqueo es de las llamadas al modelo, no del producto: todo lo que el sistema
sabe responder de forma determinista —métricas, estados, tablas, el clasificador
de campañas— sigue funcionando igual (criterio 15). El cliente pierde el
redactor, no el tablero.
"""
import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_token_usage import AITokenUsage
from app.models.organization import Organization
from app.services.budget_gate import hoy_de_negocio
from app.services.money import to_money

logger = logging.getLogger("lejovista.ai.presupuesto")

CERO = Decimal("0.000000")

# Los umbrales de aviso, en orden. El 100 no es un aviso más: es el bloqueo.
UMBRAL_AVISO = (Decimal("0.70"), Decimal("0.90"))
UMBRAL_BLOQUEO = Decimal("1.00")


class PresupuestoDeIAAgotado(Exception):
    """No queda margen de IA este mes para esta Organización.

    Lleva las cifras encima. Un bloqueo que no dice cuánto se consumió ni cuál
    era el techo deja al operador adivinando si el límite está mal puesto o si
    de verdad hubo un uso anómalo.
    """

    code = "AI_BUDGET_EXHAUSTED"

    def __init__(self, *, limite: Decimal, consumo: Decimal, slug: str = "?"):
        self.limite = limite
        self.consumo = consumo
        self.slug = slug
        super().__init__(
            f"La organización '{slug}' agotó su presupuesto mensual de IA: "
            f"consumió USD {consumo} de un techo de USD {limite}."
        )

    def as_detail(self) -> dict:
        return {
            "code": self.code,
            "message": (
                "Se alcanzó el límite mensual de consumo de inteligencia "
                "artificial de esta cuenta. Los datos, las métricas y los "
                "informes siguen disponibles; lo que se pausa hasta el mes que "
                "viene son las respuestas redactadas por el modelo."
            ),
            "limite_usd": str(self.limite),
            "consumido_usd": str(self.consumo),
        }


@dataclass(frozen=True)
class EstadoDeConsumo:
    """Foto del consumo de IA del mes. Fuente única para pantalla y compuerta.

    Si la pantalla y el backend lo calcularan por separado, una podría mostrar
    margen mientras el otro bloquea.
    """

    limite: Optional[Decimal]      # None = sin techo configurado
    consumo: Decimal
    margen: Optional[Decimal]
    desde: datetime.date
    proporcion: Optional[Decimal]  # consumo / límite; None si no hay techo
    moneda: str = "USD"

    @property
    def sin_techo(self) -> bool:
        return self.limite is None

    @property
    def agotado(self) -> bool:
        return self.proporcion is not None and self.proporcion >= UMBRAL_BLOQUEO

    @property
    def umbral_alcanzado(self) -> Optional[int]:
        """El umbral más alto que ya se cruzó: 100, 90, 70 o `None`."""
        if self.proporcion is None:
            return None
        if self.proporcion >= UMBRAL_BLOQUEO:
            return 100
        if self.proporcion >= Decimal("0.90"):
            return 90
        if self.proporcion >= Decimal("0.70"):
            return 70
        return None

    def as_dict(self) -> dict:
        return {
            "moneda": self.moneda,
            "limite_usd": None if self.limite is None else str(self.limite),
            "consumido_usd": str(self.consumo),
            "margen_usd": None if self.margen is None else str(self.margen),
            "proporcion": None if self.proporcion is None else float(self.proporcion),
            "umbral_alcanzado": self.umbral_alcanzado,
            "agotado": self.agotado,
            "sin_techo": self.sin_techo,
            "desde": self.desde.isoformat(),
        }


def _inicio_de_mes(momento: Optional[datetime.datetime] = None) -> datetime.date:
    return hoy_de_negocio(momento).replace(day=1)


async def consumo_del_mes(
    db: AsyncSession, org_id: str, *, momento: Optional[datetime.datetime] = None
) -> Decimal:
    """Costo estimado de IA acumulado en el mes en curso, en USD.

    Se suma la columna EXACTA. Las filas anteriores a esta spec no la tienen y
    aportan cero a esta suma: el histórico en coma flotante no se convierte
    retroactivamente porque no se puede recuperar la precisión que ya se perdió,
    y arrastrarla a la compuerta contaminaría la decisión. En la práctica el
    efecto es que el primer mes arranca contando desde el despliegue, lo cual es
    correcto: nadie acordó un techo antes de que existiera.
    """
    desde = _inicio_de_mes(momento)
    total = (await db.execute(
        select(func.sum(cast(AITokenUsage.estimated_cost_usd_exact, Numeric(14, 6)))).where(
            AITokenUsage.organization_id == org_id,
            AITokenUsage.created_at >= datetime.datetime(desde.year, desde.month, desde.day),
        )
    )).scalar()
    return Decimal(total) if total is not None else CERO


async def estado_del_mes(
    db: AsyncSession,
    org_id: str,
    *,
    momento: Optional[datetime.datetime] = None,
    org: Optional[Organization] = None,
) -> EstadoDeConsumo:
    """Techo, consumo y margen de IA de la Organización para el mes en curso."""
    if org is None:
        org = await db.get(Organization, org_id)

    desde = _inicio_de_mes(momento)
    consumo = await consumo_del_mes(db, org_id, momento=momento)

    bruto = getattr(org, "ai_monthly_budget_usd", None) if org is not None else None
    if bruto is None:
        return EstadoDeConsumo(
            limite=None, consumo=consumo, margen=None, desde=desde, proporcion=None,
        )

    limite = Decimal(bruto)
    if limite <= 0:
        # Un techo de cero es "sin IA", no "sin techo". Se distingue a
        # propósito: son configuraciones distintas y confundirlas dejaría a una
        # organización con la IA encendida cuando alguien la apagó poniendo 0.
        return EstadoDeConsumo(
            limite=limite, consumo=consumo, margen=CERO, desde=desde,
            proporcion=Decimal("1.00"),
        )

    return EstadoDeConsumo(
        limite=limite,
        consumo=consumo,
        margen=max(CERO, limite - consumo),
        desde=desde,
        proporcion=consumo / limite,
    )


async def assert_hay_presupuesto(
    db: AsyncSession,
    org: Organization,
    *,
    momento: Optional[datetime.datetime] = None,
) -> EstadoDeConsumo:
    """Compuerta: levanta `PresupuestoDeIAAgotado` si no queda margen.

    Se llama ANTES de la llamada al modelo. Comprobarlo después serviría para el
    informe y no para el bolsillo del cliente.

    Una organización sin techo configurado pasa. Es el estado de todo cliente
    anterior a esta spec, y bloquear retroactivamente a quien nunca acordó un
    límite le cortaría el producto de un despliegue al otro.
    """
    estado = await estado_del_mes(db, org.id, momento=momento, org=org)
    if estado.agotado:
        raise PresupuestoDeIAAgotado(
            limite=estado.limite, consumo=estado.consumo, slug=org.slug,
        )
    return estado


def _marca_del_mes(desde: datetime.date, umbral: int) -> str:
    return f"{desde.year:04d}-{desde.month:02d}:{umbral}"


def aviso_pendiente(org: Organization, estado: EstadoDeConsumo) -> Optional[int]:
    """El umbral del que HAY que avisar ahora, o `None`.

    Devuelve un umbral solo la primera vez que se cruza en el mes. Sin esta
    comprobación el aviso se emitiría en cada llamada al modelo a partir del
    70 %, que en un día de uso normal son cientos de correos y el efecto
    práctico es que la agencia deja de leerlos.

    El reinicio mensual (criterio 17) sale gratis de la marca: al cambiar el mes
    cambia el prefijo, ninguna marca coincide y los tres umbrales vuelven a estar
    disponibles.
    """
    umbral = estado.umbral_alcanzado
    if umbral is None:
        return None
    if (org.ai_budget_alert_state or "") == _marca_del_mes(estado.desde, umbral):
        return None
    return umbral


def marcar_avisado(org: Organization, estado: EstadoDeConsumo, umbral: int) -> None:
    org.ai_budget_alert_state = _marca_del_mes(estado.desde, umbral)


def texto_del_aviso(org: Organization, estado: EstadoDeConsumo, umbral: int) -> tuple[str, str]:
    """Asunto y cuerpo del aviso. Devuelto en vez de enviado a propósito.

    Quien envía decide el canal; este módulo no debería saber si el aviso sale
    por correo, por el panel o por los dos.
    """
    nombre = org.display_name or org.name or org.slug
    if umbral >= 100:
        asunto = f"[LejoVista] {nombre} agotó su presupuesto de IA del mes"
        cuerpo = (
            f"La cuenta {nombre} alcanzó el 100 % de su presupuesto mensual de "
            f"inteligencia artificial (USD {estado.consumo} de USD {estado.limite}).\n\n"
            "Las llamadas al modelo quedan bloqueadas hasta el primer día del mes "
            "que viene. El tablero, las métricas y los informes deterministas "
            "siguen funcionando con normalidad.\n\n"
            "Si el consumo no se corresponde con el uso esperado, conviene revisar "
            "el desglose por funcionalidad antes de ampliar el techo: un pico "
            "concentrado en una sola funcionalidad suele ser un bucle, no demanda."
        )
    else:
        asunto = f"[LejoVista] {nombre} lleva {umbral} % de su presupuesto de IA"
        cuerpo = (
            f"La cuenta {nombre} consumió USD {estado.consumo} de un techo mensual "
            f"de USD {estado.limite} ({umbral} %).\n\n"
            f"Quedan USD {estado.margen} hasta el bloqueo. Al llegar al 100 % se "
            "pausan las respuestas redactadas por el modelo; el resto del producto "
            "no se ve afectado."
        )
    return asunto, cuerpo


def limite_desde_entrada(valor) -> Optional[Decimal]:
    """Interpreta el techo que llega de la interfaz.

    Vacío o `None` significa sin techo. Se pasa por `to_money` con seis
    decimales de destino para que un valor con residuo binario no se convierta
    en un techo distinto del que la agencia escribió.
    """
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    monto = to_money(valor, field="presupuesto de IA")
    if monto < 0:
        raise ValueError("El presupuesto de IA no puede ser negativo.")
    return monto
