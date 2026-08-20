"""¿Qué sabe el chat, y hasta cuándo? (execution/04 §3)

El chat contestaba *"tengo acceso a los últimos 7 días"* a una pregunta sobre
la base de datos y *"en los últimos 36 días"* en el turno siguiente. Los dos
números eran correctos **dentro de su camino** y ninguno describía la cobertura
real: `available_days` se calculaba para acotar la ventana y se tiraba, así que
el único hecho sobre tiempo que el modelo veía era la ventana de la pregunta.

Este módulo produce esa cobertura, entera y pre-calculada en Python (regla
AI-1: ninguna cifra de acá puede salir de un LLM).

Todo filtra **siempre** por `organization_id` **y** `ad_account_id`. Es crítico:
las tablas `_archive` no tienen FK por diseño —para sobrevivir al borrado de un
tenant— así que ahí el aislamiento no lo garantiza la base, lo garantiza esta
query.
"""
import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ad_account import AdAccount
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.snapshot_archive import CampaignSnapshotArchive
from app.utils.formatting import money

logger = logging.getLogger("lejovista.coverage")

# Cuántos meses incompletos y cuántos huecos se reportan como mucho. El tope no
# es estético: `MAX_GROUNDING_VALUES` es 220 y el recorrido extrae también los
# dígitos de los strings, así que una fecha "2025-04-12" aporta tres entradas.
# Una lista larga de huecos desplazaría del conjunto a las cifras por las que
# realmente preguntaron.
MAX_REPORTED_GAPS = 3


@dataclass
class Coverage:
    """Cobertura de datos de una cuenta. Todo ya calculado."""
    primer_dia: Optional[datetime.date] = None
    ultimo_dia: Optional[datetime.date] = None
    dias_con_datos: int = 0
    dias_sin_datos: int = 0
    detalle_diario_desde: Optional[datetime.date] = None
    primer_mes: Optional[str] = None
    ultimo_mes: Optional[str] = None
    meses_con_datos: int = 0
    gasto_total_historico: float = 0.0
    ultima_sincronizacion: Optional[datetime.datetime] = None
    backfill_completo: bool = False
    meses_incompletos: List[str] = field(default_factory=list)

    @property
    def tiene_datos(self) -> bool:
        return self.dias_con_datos > 0 or self.meses_con_datos > 0

    @property
    def antiguedad_dias(self) -> int:
        """Días entre el primer dato y hoy. Se mide contra el primer dato
        MENSUAL cuando existe, que llega más atrás que el diario."""
        primero = self._primer_dato()
        if not primero:
            return 0
        return (datetime.date.today() - primero).days

    def _primer_dato(self) -> Optional[datetime.date]:
        candidatos = []
        if self.primer_dia:
            candidatos.append(self.primer_dia)
        if self.primer_mes:
            year, month = (int(p) for p in self.primer_mes.split("-"))
            candidatos.append(datetime.date(year, month, 1))
        return min(candidatos) if candidatos else None

    @property
    def antiguedad_texto(self) -> str:
        """"2 años y 3 meses". Se arma acá y no en el prompt: si el modelo
        dijera "hace más de 2 años" y ese 2 no estuviera en los facts, la
        guarda de grounding descartaría la respuesta entera."""
        dias = self.antiguedad_dias
        if dias <= 0:
            return "sin datos"
        meses_totales = max(1, round(dias / 30.44))
        anios, meses = divmod(meses_totales, 12)
        if anios and meses:
            return f"{anios} {'año' if anios == 1 else 'años'} y {meses} {'mes' if meses == 1 else 'meses'}"
        if anios:
            return f"{anios} {'año' if anios == 1 else 'años'}"
        return f"{meses} {'mes' if meses == 1 else 'meses'}"

    def as_facts(self) -> Dict[str, Any]:
        """Forma compacta para el chat. Solo lo que sirve para responder."""
        out: Dict[str, Any] = {
            "dias_con_datos": self.dias_con_datos,
            "antiguedad_de_la_cuenta": self.antiguedad_texto,
        }
        if self.primer_dia:
            out["primer_dia_con_datos"] = self.primer_dia.isoformat()
        if self.ultimo_dia:
            out["ultimo_dia_con_datos"] = self.ultimo_dia.isoformat()
        if self.detalle_diario_desde:
            out["detalle_diario_desde"] = self.detalle_diario_desde.isoformat()
        if self.primer_mes:
            out["primer_mes"] = self.primer_mes
        if self.ultimo_mes:
            out["ultimo_mes"] = self.ultimo_mes
        if self.gasto_total_historico:
            out["gasto_total_historico"] = round(self.gasto_total_historico, 2)
        if self.ultima_sincronizacion:
            out["ultima_sincronizacion"] = self.ultima_sincronizacion.date().isoformat()
        if self.dias_sin_datos:
            out["dias_sin_datos_en_el_rango"] = self.dias_sin_datos
        if self.meses_incompletos:
            out["meses_incompletos"] = self.meses_incompletos[:MAX_REPORTED_GAPS]
        out["historial_completo"] = self.backfill_completo
        return out

    def resumen(self) -> Dict[str, Any]:
        """Tres campos, ~6 entradas de grounding. Va en TODOS los turnos.

        La cobertura ya se calcula en cada turno para acotar la ventana, así que
        esto no cuesta una query extra — y hace que el modelo tenga siempre
        delante la diferencia entre "analicé 7 días" y "tengo 890".
        """
        return {
            "dias_con_datos_en_total": self.dias_con_datos,
            "primer_dia_con_datos": self.primer_dia.isoformat() if self.primer_dia else None,
            "ultimo_dia_con_datos": self.ultimo_dia.isoformat() if self.ultimo_dia else None,
        }


async def get_account_coverage(
    db: AsyncSession, organization_id: str, ad_account: AdAccount
) -> Coverage:
    """Cobertura real de una cuenta, uniendo lo caliente, el archivo y lo mensual."""
    cov = Coverage()
    acc_id = ad_account.id

    # --- Diario caliente. COUNT(DISTINCT date) y no (max - min): el cálculo
    # anterior asumía continuidad, así que un rango con huecos reportaba días
    # que no tenía.
    hot = (await db.execute(
        select(
            func.min(CampaignSnapshot.date),
            func.max(CampaignSnapshot.date),
            func.count(func.distinct(CampaignSnapshot.date)),
        ).where(
            CampaignSnapshot.organization_id == organization_id,
            CampaignSnapshot.ad_account_id == acc_id,
        )
    )).one()

    # --- Diario frío (tablas _archive). Sin FK por diseño: el filtro por
    # organización es la ÚNICA garantía de aislamiento acá.
    cold = (await db.execute(
        select(
            func.min(CampaignSnapshotArchive.date),
            func.max(CampaignSnapshotArchive.date),
            func.count(func.distinct(CampaignSnapshotArchive.date)),
        ).where(
            CampaignSnapshotArchive.organization_id == organization_id,
            CampaignSnapshotArchive.ad_account_id == acc_id,
        )
    )).one()

    dias = [d for d in (hot[0], hot[1], cold[0], cold[1]) if d]
    if dias:
        cov.primer_dia = min(dias)
        cov.ultimo_dia = max(dias)
    # Los dos rangos no se solapan por construcción —el archivador mueve las
    # filas, no las copia— así que sumar los distintos de cada lado es correcto.
    cov.dias_con_datos = int(hot[2] or 0) + int(cold[2] or 0)
    if hot[0]:
        cov.detalle_diario_desde = hot[0]

    if cov.primer_dia and cov.ultimo_dia:
        span = (cov.ultimo_dia - cov.primer_dia).days + 1
        cov.dias_sin_datos = max(0, span - cov.dias_con_datos)

    # --- Mensual: llega más atrás que todo lo demás y es la verdad de
    # referencia. Es lo que permite responder "cuántos años lleva activa".
    monthly = (await db.execute(
        select(
            func.min(AdAccountSpendMonthly.year_month),
            func.max(AdAccountSpendMonthly.year_month),
            func.count(AdAccountSpendMonthly.id),
            func.sum(AdAccountSpendMonthly.spend),
        ).where(
            AdAccountSpendMonthly.organization_id == organization_id,
            AdAccountSpendMonthly.ad_account_id == acc_id,
        )
    )).one()
    cov.primer_mes, cov.ultimo_mes = monthly[0], monthly[1]
    cov.meses_con_datos = int(monthly[2] or 0)
    cov.gasto_total_historico = float(monthly[3] or 0.0)

    cov.ultima_sincronizacion = ad_account.last_synced_at

    # --- Qué falta, según la misma reconciliación que usa el backfill.
    from app.services.history_reconciliation import get_history_status
    status = await get_history_status(db, organization_id, acc_id)
    cov.backfill_completo = status.is_complete and status.months_total > 0
    cov.meses_incompletos = [g.year_month for g in status.gaps[:MAX_REPORTED_GAPS]]

    return cov


async def days_of_history(db: AsyncSession, organization_id: str, ad_account_id: str) -> int:
    """Días con datos, para acotar la ventana de una pregunta.

    Reemplaza a `kpi_period.days_of_history`, que tenía dos defectos: asumía
    continuidad (un rango con huecos reportaba días que no tenía) y **no
    filtraba por organización** — era la única query del módulo sin scope de
    tenant.
    """
    hot = (await db.execute(
        select(func.count(func.distinct(CampaignSnapshot.date))).where(
            CampaignSnapshot.organization_id == organization_id,
            CampaignSnapshot.ad_account_id == ad_account_id,
        )
    )).scalar() or 0
    cold = (await db.execute(
        select(func.count(func.distinct(CampaignSnapshotArchive.date))).where(
            CampaignSnapshotArchive.organization_id == organization_id,
            CampaignSnapshotArchive.ad_account_id == ad_account_id,
        )
    )).scalar() or 0
    return int(hot) + int(cold)


def _fecha_larga(iso: Optional[str]) -> Optional[str]:
    """`2026-08-18` -> `18 de agosto de 2026`.

    El documento de ITS pide fechas completas, no `18/08` ni "la semana pasada".
    Se arma en codigo y no en el prompt: cualquier fecha que el modelo redacte
    por su cuenta trae digitos que el grounding no reconoce, y descarta la
    respuesta entera.
    """
    if not iso:
        return None
    try:
        d = datetime.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return f"{d.day} de {_MESES[d.month - 1]} de {d.year}"


_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def narrate_coverage(facts: Dict[str, Any]) -> str:
    """Texto canonico de COVERAGE.

    Tiene que ser bueno SOLO: el grounding va a rechazar seguido la version del
    LLM, porque cualquier frase como "hace mas de 2 anos" muere si ese 2 no esta
    en los facts. Por eso hasta la antiguedad viene ya redactada desde
    `Coverage.antiguedad_texto`.
    """
    cov = facts.get("cobertura") or {}
    if not cov or not cov.get("dias_con_datos"):
        return (
            "Todavía no tengo datos sincronizados de esta cuenta. "
            "En cuanto termine la primera sincronización te puedo responder sobre el historial."
        )

    partes = []
    primero = _fecha_larga(cov.get("primer_dia_con_datos"))
    ultimo = _fecha_larga(cov.get("ultimo_dia_con_datos"))
    if primero and ultimo:
        partes.append(f"Tengo datos desde el {primero} hasta el {ultimo}")
    elif ultimo:
        partes.append(f"Tengo datos hasta el {ultimo}")
    partes.append(f"son {cov['dias_con_datos']} días con información")

    texto = ", ".join(partes) + "."

    antiguedad = cov.get("antiguedad_de_la_cuenta")
    if antiguedad and antiguedad != "sin datos":
        texto += f" La cuenta lleva {antiguedad} de actividad registrada"
        if cov.get("gasto_total_historico"):
            texto += f", con {money(cov['gasto_total_historico'])} invertidos en total"
        texto += "."

    sync = _fecha_larga(cov.get("ultima_sincronizacion"))
    if sync:
        texto += f" La última sincronización fue el {sync}."

    if cov.get("dias_sin_datos_en_el_rango"):
        texto += f" Hay {cov['dias_sin_datos_en_el_rango']} días sin datos dentro de ese rango."

    if not cov.get("historial_completo"):
        meses = cov.get("meses_incompletos") or []
        if meses:
            texto += f" Todavía estoy trayendo historial; me faltan datos de {', '.join(meses)}."
        else:
            texto += " Todavía estoy terminando de traer el historial completo."

    return texto
