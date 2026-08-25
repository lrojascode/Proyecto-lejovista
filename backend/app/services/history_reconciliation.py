"""¿Qué histórico falta bajar, y cuánto va a tardar?

Reemplaza el guard anterior —*"¿ya corrí un backfill alguna vez?"*, un
`SyncJob BACKFILL COMPLETED`— que era frágil: un backfill cortado a la mitad
quedaba marcado como hecho para siempre y la cuenta se quedaba sin historia sin
que nadie se enterara.

El criterio nuevo es **reconciliación mensual**:

    faltante_pct(mes) = (gasto que Meta reporta − gasto que tenemos) / gasto de Meta

`AdAccountSpendMonthly` guarda lo que Meta dice que se gastó cada mes (bajado
con `date_preset=maximum`, una fila por mes). Los snapshots diarios —los
calientes **y los archivados**— son lo que tenemos. La diferencia es,
literalmente, cuánto histórico falta medido en plata.

Eso resuelve tres cosas de una:

* **Idempotencia.** Sobre una cuenta completa cuesta dos queries agregadas y
  CERO llamadas a Meta, así que se puede correr todos los días sin costo.
* **Sin falsos positivos.** Un mes sin campañas no genera filas de snapshot
  *ni* gasto mensual, así que no se reporta como hueco. Un mes con gasto y sin
  snapshots sí.
* **Auto-reparación.** Si se pierden los datos de un mes, la próxima corrida lo
  detecta y lo vuelve a pedir — solo ese mes.

Todo se calcula en Python sobre SQL agregado: ninguna cifra de acá sale de un
LLM (regla AI-1).
"""
import datetime
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.models.api_call_log import APICallLog
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.snapshot_archive import CampaignSnapshotArchive

logger = logging.getLogger("lejovista.history")

# Por debajo de esto un mes se considera cubierto. No se exige exactitud: Meta
# reajusta cifras durante días después del cierre y la atribución cambia, así
# que perseguir el 100% haría que el backfill no terminara nunca.
COVERED_THRESHOLD_PCT = 5.0

# Un mes con muy poco gasto no vale una llamada a Meta aunque falte entero: el
# ruido de redondeo domina y el dato no cambia ninguna respuesta.
MIN_MONTH_SPEND = 1.0

# Fallback de segundos por request cuando la cuenta todavía no tiene historia
# de llamadas propia. En cuanto haya APICallLog se usa el ritmo de la cuenta.
DEFAULT_SECONDS_PER_REQUEST = 1.6

# Muestreo para derivar el ritmo real desde APICallLog.
_RATE_SAMPLE_SIZE = 200
_MIN_RATE_SAMPLES = 5
# Un hueco mayor a esto no es "la llamada tardó": son dos corridas distintas de
# sync separadas en el tiempo.
_MAX_SAME_RUN_GAP_S = 60.0

# Cuántas llamadas cuesta un mes: tres niveles (campaña, conjunto, anuncio) por
# unas dos páginas cada uno en una cuenta mediana. Calibrado contra las
# referencias del plan —24 meses ≈ 100-170 requests— y a propósito del lado
# conservador: un estimado que se queda corto se siente como que se colgó.
REQUESTS_PER_MONTH_CHUNK = 6


@dataclass
class MonthGap:
    """Un mes que Meta dice que tuvo gasto y nosotros no tenemos completo."""
    year_month: str
    meta_spend: float
    local_spend: float

    @property
    def missing_pct(self) -> float:
        if self.meta_spend <= 0:
            return 0.0
        return max(0.0, (self.meta_spend - self.local_spend) / self.meta_spend * 100.0)


@dataclass
class HistoryStatus:
    """Estado del histórico de una cuenta, todo pre-calculado."""
    months_total: int = 0
    months_covered: int = 0
    gaps: List[MonthGap] = field(default_factory=list)
    first_month: Optional[str] = None
    last_month: Optional[str] = None
    seconds_per_request: float = DEFAULT_SECONDS_PER_REQUEST

    @property
    def months_missing(self) -> int:
        return len(self.gaps)

    @property
    def is_complete(self) -> bool:
        return not self.gaps

    @property
    def estimated_requests(self) -> int:
        return self.months_missing * REQUESTS_PER_MONTH_CHUNK

    @property
    def estimated_seconds(self) -> int:
        return int(round(self.estimated_requests * self.seconds_per_request))

    def estimate_text(self) -> str:
        """Frase para el frontend. Redondea hacia arriba y en unidades gruesas:
        un estimado que se pasa por poco molesta menos que uno falsamente
        preciso."""
        if self.is_complete:
            return "Tu historial ya está completo."
        secs = self.estimated_seconds
        if secs < 90:
            duracion = "menos de un minuto"
        elif secs < 3600:
            duracion = f"alrededor de {max(1, round(secs / 60))} minutos"
        else:
            horas = secs / 3600
            duracion = f"alrededor de {horas:.1f} horas".replace(".0 ", " ")
        meses = self.months_missing
        return (
            f"Estamos trayendo todo el historial de tu cuenta: "
            f"{meses} {'mes' if meses == 1 else 'meses'} de datos, {duracion}. "
            "Podés seguir usando la app; te avisamos cuando termine."
        )


async def _seconds_per_request(db: AsyncSession, ad_account_id: str) -> float:
    """Segundos por llamada medidos EN ESTA CUENTA, no una constante inventada.

    `APICallLog` no guarda duración, solo `timestamp`, así que el ritmo real se
    deriva del hueco entre llamadas consecutivas. Los huecos grandes se
    descartan: separan corridas distintas de sync, no llamadas seguidas, y
    promediarlos daría un estimado de horas.

    Con menos de un puñado de intervalos utilizables se usa el default — con
    dos llamadas no se puede estimar nada estable.
    """
    rows = (await db.execute(
        select(APICallLog.timestamp)
        .where(APICallLog.ad_account_id == ad_account_id)
        .order_by(APICallLog.timestamp.desc())
        .limit(_RATE_SAMPLE_SIZE)
    )).scalars().all()
    if len(rows) < _MIN_RATE_SAMPLES + 1:
        return DEFAULT_SECONDS_PER_REQUEST

    deltas = []
    for newer, older in zip(rows, rows[1:]):
        gap = (newer - older).total_seconds()
        if 0 < gap <= _MAX_SAME_RUN_GAP_S:
            deltas.append(gap)
    if len(deltas) < _MIN_RATE_SAMPLES:
        return DEFAULT_SECONDS_PER_REQUEST

    # Mediana y no promedio: un solo backoff de rate limit de 60s desplazaría
    # el promedio lo suficiente como para triplicar el estimado.
    deltas.sort()
    mid = len(deltas) // 2
    median = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2
    return max(0.2, median)


async def _local_spend_by_month(
    db: AsyncSession, organization_id: str, ad_account_id: str
) -> dict:
    """Gasto diario que YA tenemos, por mes, sumando lo caliente y el archivo.

    El archivo no es opcional. `snapshot_archiver` mueve a
    `campaign_snapshots_archive` todo lo anterior a `SNAPSHOT_RETENTION_DAYS`
    (400 días) y lo BORRA de la tabla caliente. Mirando solo la caliente, todo
    mes de más de 400 días aparece como hueco para siempre: el backfill lo baja
    cada día y el archivador lo vuelve a mover cada domingo. En una cuenta con
    dos años y medio de historia eso son ~15 meses en bucle infinito, quemando
    la cuota de Meta que después le falta a los meses que sí faltan de verdad.

    `data_coverage.get_account_coverage` ya unía las dos fuentes; esto no era
    una decisión distinta, era la misma pregunta contestada a medias.

    Se agrupa en SQL: traer 400 días de filas para sumarlas en Python sería el
    mismo resultado y mucha más memoria. Las tablas `_archive` no tienen FK por
    diseño —sobreviven al borrado de un tenant— así que ahí el filtro por
    organización es la ÚNICA garantía de aislamiento.
    """
    totals: dict = {}
    for model in (CampaignSnapshot, CampaignSnapshotArchive):
        rows = (await db.execute(
            select(
                func.substr(func.cast(model.date, String), 1, 7).label("ym"),
                func.sum(model.spend),
            )
            .where(
                model.organization_id == organization_id,
                model.ad_account_id == ad_account_id,
            )
            .group_by("ym")
        )).all()
        for ym, total in rows:
            if ym:
                totals[ym] = totals.get(ym, 0.0) + float(total or 0.0)
    return totals


async def get_history_status(
    db: AsyncSession, organization_id: str, ad_account_id: str
) -> HistoryStatus:
    """Reconcilia el gasto mensual de Meta contra los snapshots diarios locales.

    Filtra SIEMPRE por organización además de por cuenta: es la regla de
    aislamiento del proyecto y acá no cuesta nada respetarla.
    """
    meta_rows = (await db.execute(
        select(AdAccountSpendMonthly.year_month, AdAccountSpendMonthly.spend)
        .where(
            AdAccountSpendMonthly.organization_id == organization_id,
            AdAccountSpendMonthly.ad_account_id == ad_account_id,
        )
        .order_by(AdAccountSpendMonthly.year_month)
    )).all()

    if not meta_rows:
        # Sin histórico mensual no hay contra qué comparar. No es "está
        # completo": es "todavía no sé", y decir que está completo sería mentir.
        return HistoryStatus(seconds_per_request=await _seconds_per_request(db, ad_account_id))

    local_by_month = await _local_spend_by_month(db, organization_id, ad_account_id)

    current_ym = datetime.date.today().strftime("%Y-%m")
    status = HistoryStatus(
        first_month=meta_rows[0][0],
        last_month=meta_rows[-1][0],
        seconds_per_request=await _seconds_per_request(db, ad_account_id),
    )

    for year_month, meta_spend in meta_rows:
        meta_spend = float(meta_spend or 0.0)
        # El mes en curso no se reconcilia: está a medio transcurrir y siempre
        # se vería incompleto, así que el backfill lo pediría todos los días.
        if year_month == current_ym:
            continue
        status.months_total += 1
        local_spend = local_by_month.get(year_month, 0.0)

        if meta_spend < MIN_MONTH_SPEND:
            status.months_covered += 1
            continue

        gap = MonthGap(year_month=year_month, meta_spend=meta_spend, local_spend=local_spend)
        if gap.missing_pct <= COVERED_THRESHOLD_PCT:
            status.months_covered += 1
        else:
            status.gaps.append(gap)

    # Del más reciente al más viejo: si el backfill se corta, lo que quedó
    # bajado es lo que más se consulta.
    status.gaps.sort(key=lambda g: g.year_month, reverse=True)
    return status


def month_range(year_month: str) -> tuple[datetime.date, datetime.date]:
    """Primer y último día de un `YYYY-MM`, para el `time_range` de Meta."""
    year, month = (int(p) for p in year_month.split("-"))
    first = datetime.date(year, month, 1)
    last = datetime.date(year + (month == 12), (month % 12) + 1, 1) - datetime.timedelta(days=1)
    return first, last
