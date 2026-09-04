"""Compuerta de presupuesto mensual por Organización (spec 02).

El límite existía como campo (`Organization.max_budget`) y se mostraba en la
interfaz, pero **ningún camino de ejecución lo consultaba**: no había
comprobación al crear una campaña, al aprobar una propuesta ni al subir un
presupuesto. La promesa financiera que veía el cliente no correspondía a ninguna
garantía del sistema.

Contrato:

* **Ámbito**: la Organización. Un cliente puede tener varias AdAccount;
  aplicarlo por cuenta permitiría multiplicar el techo abriendo cuentas.
* **Ventana**: el mes calendario. Es la unidad en la que ya se consolida el
  gasto por cuenta y mes, y la que el cliente entiende.
* **Consumo**: gasto real ya sincronizado **más** el presupuesto comprometido
  por campañas creadas en el mes que todavía no acumularon gasto medido. Sumar
  solo el gasto real permitiría crear en un mismo día veinte campañas que
  ninguna gastó todavía.
* **Aritmética**: `Decimal` de punta a punta (ver `app/services/money.py`).
  Float no autoriza gasto.
* **Moneda**: la de la cuenta. Una Organización con cuentas en monedas distintas
  obtiene un rechazo explícito, no una suma de cifras incomparables.
"""
import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ad_account import AdAccount
from app.models.campaign_draft import CampaignDraft
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.services.money import money_or_zero, to_money

logger = logging.getLogger("lejovista.budget_gate")

CERO = Decimal("0.00")

# Estados de Draft cuyo presupuesto está comprometido: la campaña se creó o se
# está creando, y su gasto todavía no aparece en los snapshots.
ESTADOS_COMPROMETIDOS = ("PENDING_CREATION", "CREATED")


class BudgetLimitExceeded(Exception):
    """El monto solicitado no cabe en el margen del mes.

    Lleva las cifras encima —límite, consumo y margen— porque un rechazo
    financiero que no dice cuánto falta obliga a adivinar.
    """

    def __init__(self, *, limite: Decimal, consumo: Decimal, margen: Decimal,
                 solicitado: Decimal, moneda: str = ""):
        self.limite = limite
        self.consumo = consumo
        self.margen = margen
        self.solicitado = solicitado
        self.moneda = moneda
        super().__init__(
            f"El presupuesto solicitado ({solicitado}) supera el margen disponible "
            f"del mes ({margen}). Límite mensual: {limite}; consumido: {consumo}."
        )

    def as_detail(self) -> dict:
        return {
            "code": "MONTHLY_BUDGET_EXCEEDED",
            "message": (
                f"Esta campaña supera el límite mensual de presupuesto de la "
                f"organización. Disponible este mes: {self.margen}; "
                f"solicitado: {self.solicitado}."
            ),
            "limite_mensual": str(self.limite),
            "consumido": str(self.consumo),
            "margen_disponible": str(self.margen),
            "solicitado": str(self.solicitado),
            "moneda": self.moneda,
        }


class MixedCurrencies(Exception):
    """La Organización tiene cuentas en monedas distintas.

    Se rechaza en vez de sumar: mientras el alcance sea Perú no se implementa
    conversión, y sumar montos en monedas distintas produciría un número que no
    significa nada pero que autorizaría gasto igual.
    """

    def __init__(self, monedas):
        self.monedas = sorted(monedas)
        super().__init__(
            f"La organización tiene cuentas en monedas distintas ({', '.join(self.monedas)}); "
            f"el límite mensual no se puede evaluar sin conversión."
        )

    def as_detail(self) -> dict:
        return {
            "code": "MIXED_ACCOUNT_CURRENCIES",
            "message": (
                "Esta organización tiene cuentas publicitarias en monedas "
                "distintas, así que el límite mensual no se puede evaluar. "
                "Contacta con la agencia."
            ),
            "monedas": self.monedas,
        }


@dataclass(frozen=True)
class EstadoPresupuesto:
    """Lo que consume y lo que queda de una Organización este mes."""

    limite: Decimal
    gasto_real: Decimal
    comprometido: Decimal
    moneda: str
    periodo: str  # "YYYY-MM"

    @property
    def consumo(self) -> Decimal:
        return self.gasto_real + self.comprometido

    @property
    def margen(self) -> Decimal:
        restante = self.limite - self.consumo
        return restante if restante > CERO else CERO

    def cabe(self, monto: Decimal) -> bool:
        """`<=` y no `<`: un monto que agota exactamente el margen cabe."""
        return self.consumo + monto <= self.limite


def hoy_de_negocio(momento: Optional[datetime.datetime] = None) -> datetime.date:
    """La fecha de HOY en el calendario en el que Meta reporta las fechas.

    `datetime.utcnow().date()` no sirve: en Perú (UTC-5) las últimas cinco horas
    de cada día ya son el día siguiente en UTC. Aplicado al inicio de mes, eso
    dejaba el gasto de los últimos días de mes fuera de la ventana y el margen
    se veía más grande de lo que era — la compuerta autorizaba de más justo
    cuando el límite estaba por agotarse.
    """
    if momento is not None:
        return momento.date()
    try:
        return datetime.datetime.now(ZoneInfo(settings.BUSINESS_TIMEZONE)).date()
    except Exception:
        logger.warning(
            "BUSINESS_TIMEZONE=%r no es una zona válida; se usa UTC para la "
            "ventana mensual.", settings.BUSINESS_TIMEZONE,
        )
        return datetime.datetime.utcnow().date()


def _inicio_de_mes(momento: Optional[datetime.datetime] = None) -> datetime.date:
    return hoy_de_negocio(momento).replace(day=1)


async def _moneda_de_la_organizacion(db: AsyncSession, org_id: str) -> str:
    """La moneda común de las cuentas de la Organización.

    Levanta `MixedCurrencies` si hay más de una. Las cuentas sin moneda conocida
    —todavía no sincronizadas— no cuentan: no aportan gasto ni pueden recibir una
    campaña.
    """
    monedas = {
        m for m in (await db.execute(
            select(AdAccount.currency).where(
                AdAccount.organization_id == org_id,
                AdAccount.status != "ARCHIVED",
            )
        )).scalars().all()
        if m
    }
    if len(monedas) > 1:
        raise MixedCurrencies(monedas)
    return next(iter(monedas), "")


async def estado_del_mes(
    db: AsyncSession,
    org_id: str,
    *,
    momento: Optional[datetime.datetime] = None,
    bloquear: bool = False,
) -> EstadoPresupuesto:
    """Límite, consumo y margen de la Organización para el mes en curso.

    Fuente ÚNICA: la cifra que ve el cliente, la que ve la agencia y la que
    decide la compuerta salen de acá. Si cada consumidor la calculara por su
    cuenta, la pantalla podría decir una cosa y el backend rechazar por otra.

    `bloquear=True` toma un bloqueo de fila sobre la Organización que se
    mantiene hasta que la transacción confirme. Es lo que convierte la compuerta
    en algo más que una lectura: sin él, dos autorizaciones simultáneas leen el
    mismo consumo, las dos ven margen suficiente y las dos pasan — el límite
    mensual se puede exceder con solo pedir dos veces a la vez. Se usa solo
    cuando se va a AUTORIZAR; la consulta que alimenta la pantalla no bloquea a
    nadie.

    El bloqueo es por Organización, que es el ámbito del límite. En el motor
    embebido de los tests no existe y la cláusula se ignora en silencio: otra
    razón por la que esta carrera se prueba contra PostgreSQL.
    """
    q = select(Organization).where(Organization.id == org_id)
    if bloquear:
        q = q.with_for_update()
    org = (await db.execute(q)).scalar_one_or_none()
    if org is None:
        raise ValueError(f"Organización {org_id} no encontrada.")

    desde = _inicio_de_mes(momento)
    moneda = await _moneda_de_la_organizacion(db, org_id)

    # 1. Gasto REAL ya sincronizado, del mes en curso.
    # `cast` a NUMERIC antes de sumar: cuantiza cada fila a dos decimales y
    # suma en aritmética exacta, así el error no se acumula a lo largo de
    # cientos de snapshots. En PostgreSQL es exacto de punta a punta; el motor
    # embebido de los tests no tiene NUMERIC real y sigue sumando en coma
    # flotante — otra razón por la que estas pruebas pertenecen a PostgreSQL.
    gasto = money_or_zero((await db.execute(
        select(func.sum(cast(CampaignSnapshot.spend, Numeric(18, 2)))).where(
            CampaignSnapshot.organization_id == org_id,
            CampaignSnapshot.date >= desde,
        )
    )).scalar_one_or_none())

    # 2. Presupuesto COMPROMETIDO por campañas creadas este mes que todavía no
    # registran gasto medido. Sin esto, veinte campañas creadas el mismo día
    # pasarían todas: ninguna habría gastado aún.
    comprometido = CERO
    drafts = (await db.execute(
        select(CampaignDraft).where(
            CampaignDraft.organization_id == org_id,
            CampaignDraft.status.in_(ESTADOS_COMPROMETIDOS),
            CampaignDraft.created_at >= datetime.datetime.combine(desde, datetime.time.min),
        )
    )).scalars().all()
    for d in drafts:
        comprometido += _presupuesto_comprometido(d)

    return EstadoPresupuesto(
        limite=org.limite_mensual,
        gasto_real=gasto,
        comprometido=comprometido,
        moneda=moneda,
        periodo=desde.strftime("%Y-%m"),
    )


def _presupuesto_comprometido(draft: CampaignDraft) -> Decimal:
    """El total que compromete un Draft, normalizado a presupuesto de campaña.

    `budget` puede venir como monto diario; lo que compromete el mes es
    `budget * days`. Un Draft cuyo payload no permita calcularlo compromete cero
    y queda registrado: preferir el cero a inventar una cifra, y que se vea.
    """
    payload = draft.payload or {}
    # Las escrituras simuladas no comprometen dinero de nadie.
    if (payload.get("result") or {}).get("simulated"):
        return CERO
    try:
        bruto = to_money(payload.get("budget"), field="presupuesto del draft")
    except Exception:
        logger.warning(
            "Draft %s sin presupuesto legible en el payload; compromete 0 en la "
            "compuerta del mes.", draft.id,
        )
        return CERO
    if str(payload.get("budget_type", "LIFETIME")).upper() == "DAILY":
        dias = payload.get("days") or 1
        try:
            return to_money(bruto * Decimal(int(dias)))
        except Exception:
            return bruto
    return bruto


async def assert_cabe_en_el_mes(
    db: AsyncSession,
    org_id: str,
    monto,
    *,
    momento: Optional[datetime.datetime] = None,
) -> EstadoPresupuesto:
    """Compuerta. Levanta `BudgetLimitExceeded` si `monto` no cabe este mes.

    Se llama antes de crear una campaña, antes de aprobar una propuesta y antes
    de subir un presupuesto existente. Devuelve el estado para que el llamador
    pueda informarlo sin recalcularlo.
    """
    solicitado = to_money(monto, field="presupuesto solicitado")
    # `bloquear=True`: la comprobación y el compromiso que la sigue tienen que
    # ser una sola sección crítica por Organización.
    estado = await estado_del_mes(db, org_id, momento=momento, bloquear=True)
    if not estado.cabe(solicitado):
        raise BudgetLimitExceeded(
            limite=estado.limite,
            consumo=estado.consumo,
            margen=estado.margen,
            solicitado=solicitado,
            moneda=estado.moneda,
        )
    return estado
