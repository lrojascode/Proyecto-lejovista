"""Baseline de una AdAccount a partir de sus Snapshot (spec 06).

Separado de `app/services/baseline.py` a proposito: alli vive la aritmetica pura
—mediana, madurez, estados— y aca la consulta. La aritmetica se prueba con
datasets y sin base; la consulta se prueba con base y sin aritmetica.

Lee de las tablas CALIENTES y del ARCHIVO. Que el archivo cuente es el punto:
una campana cuyo periodo ya se archivo tiene que poder seguir siendo evidencia,
o el producto pierde su propuesta de valor a medida que envejece.
"""
import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_snapshot import CampaignSnapshot
from app.models.snapshot_archive import CampaignSnapshotArchive
from app.services.baseline import (
    Baseline, UmbralesDeMadurez, calcular_baseline, get_madurez_for_org,
)

logger = logging.getLogger("lejovista.baseline_repo")

# Ventana por defecto del conjunto comparable. Un ano cubre la estacionalidad
# completa sin arrastrar condiciones de mercado que ya no existen.
VENTANA_BASELINE_DIAS = 365


async def campanas_agregadas(
    db: AsyncSession,
    ad_account_id: str,
    *,
    desde: Optional[datetime.date] = None,
    hasta: Optional[datetime.date] = None,
    objetivo: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Una fila por campana, con su gasto, conversaciones y dias con actividad.

    Recorre lo caliente y el archivo y suma por `campaign_id`: una campana que
    empezo antes del corte de archivado tiene filas en los dos lados, y contarla
    solo en uno la haria parecer mas chica de lo que fue.
    """
    hasta = hasta or datetime.date.today()
    desde = desde or (hasta - datetime.timedelta(days=VENTANA_BASELINE_DIAS))

    acumulado: Dict[str, Dict[str, Any]] = {}

    for modelo in (CampaignSnapshot, CampaignSnapshotArchive):
        q = (
            select(
                modelo.campaign_id,
                func.max(modelo.campaign_name),
                func.max(modelo.objective),
                func.sum(modelo.spend),
                func.sum(modelo.conversations),
                func.sum(modelo.conversions),
                func.count(func.distinct(modelo.date)),
                # El periodo real de actividad y el evento que contaba. Los
                # necesita la comparabilidad de `11`: sin el periodo no se puede
                # saber si dos campañas comparten temporada, y sin el evento dos
                # campañas del mismo objetivo pero distinto resultado se mezclan
                # en la misma mediana.
                func.min(modelo.date),
                func.max(modelo.date),
                func.max(modelo.optimization_goal),
            )
            .where(
                modelo.ad_account_id == ad_account_id,
                modelo.date >= desde,
                modelo.date <= hasta,
            )
            .group_by(modelo.campaign_id)
        )
        if objetivo:
            q = q.where(modelo.objective == objetivo)

        for cid, nombre, obj, gasto, conv, leads, dias, d_min, d_max, goal in (await db.execute(q)).all():
            fila = acumulado.setdefault(cid, {
                "campaign_id": cid, "nombre": nombre, "objetivo": obj,
                "gasto": 0.0, "conversaciones": 0, "leads": 0, "dias": 0,
                "desde": None, "hasta": None, "optimization_goal": None,
            })
            fila["nombre"] = fila["nombre"] or nombre
            fila["objetivo"] = fila["objetivo"] or obj
            fila["optimization_goal"] = fila["optimization_goal"] or goal
            fila["gasto"] += float(gasto or 0)
            fila["conversaciones"] += int(conv or 0)
            fila["leads"] += int(leads or 0)
            fila["dias"] += int(dias or 0)
            # Los extremos se combinan entre lo caliente y el archivo: una
            # campaña que empezó antes del corte tiene filas en los dos lados, y
            # quedarse con los de uno la haría parecer más corta de lo que fue.
            if d_min and (fila["desde"] is None or d_min < fila["desde"]):
                fila["desde"] = d_min
            if d_max and (fila["hasta"] is None or d_max > fila["hasta"]):
                fila["hasta"] = d_max

    # `fecha` es el punto medio del periodo de actividad: es lo que compara la
    # dimensión de temporada. Una campaña de tres meses no "ocurre" en su primer
    # día ni en el último.
    for fila in acumulado.values():
        if fila["desde"] and fila["hasta"]:
            fila["fecha"] = fila["desde"] + (fila["hasta"] - fila["desde"]) / 2
        else:
            fila["fecha"] = fila["desde"] or fila["hasta"]

    return list(acumulado.values())


async def baseline_de_la_cuenta(
    db: AsyncSession,
    ad_account_id: str,
    *,
    organization: Any = None,
    objetivo: Optional[str] = None,
    etiqueta: Optional[str] = None,
    desde: Optional[datetime.date] = None,
    hasta: Optional[datetime.date] = None,
) -> Baseline:
    """Baseline vigente de la cuenta.

    Este valor EVOLUCIONA: cambia con la temporada, con las variantes y con cada
    campana nueva que madura. El baseline CONGELADO de una campana ya creada vive
    en su version de propuesta y no se toca — son dos cosas distintas que
    conviven, y confundirlas destruye la posibilidad de saber si el sistema
    acerto.
    """
    filas = await campanas_agregadas(
        db, ad_account_id, desde=desde, hasta=hasta, objetivo=objetivo
    )
    return calcular_baseline(
        filas,
        umbrales=get_madurez_for_org(organization),
        etiqueta=etiqueta or (objetivo or "global"),
    )
