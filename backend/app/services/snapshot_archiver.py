"""D-3 — Archivado de snapshots viejos.

campaign_snapshots/adset_snapshots/ad_snapshots crecen a diario sin límite.
En vez de particionado nativo de Postgres (que exige migrar PKs/constraints
de tablas ya en producción, con downtime/riesgo), se archivan periódicamente
las filas más viejas que `SNAPSHOT_RETENTION_DAYS` a tablas "_archive" (sin
FKs, para que sobrevivan aunque se borre el tenant) y se borran de la tabla
caliente que usa el reporting día a día.
"""
import datetime
import logging
from typing import Dict, Type

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign_snapshot import CampaignSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.ad_snapshot import AdSnapshot
from app.models.snapshot_archive import (
    CampaignSnapshotArchive, AdsetSnapshotArchive, AdSnapshotArchive,
)

logger = logging.getLogger("lejovista.archiver")

# Columnas que existen por el mecanismo de archivado y no describen el negocio.
# Todo lo demas se copia.
_COLUMNAS_DE_ARCHIVO = frozenset({"id", "original_id", "archived_at"})


def campos_a_copiar(modelo_caliente, modelo_archivo) -> list:
    """Las columnas de negocio que se mueven al archivo.

    Se DERIVA de los dos modelos en vez de mantenerse a mano. La lista escrita a
    mano es como se perdieron `objective`, `results`, `effective_status` y las
    fechas estructurales: alguien anadio la columna a la tabla caliente y nadie
    se acordo de esta lista, y el hueco solo se nota meses despues, cuando el
    aprendizaje deja de encontrar la campana (spec 06).

    `tests/test_spec06_baseline.py` falla si una tabla caliente tiene una columna
    de negocio que su archivo no tiene.
    """
    calientes = [c.name for c in modelo_caliente.__table__.columns]
    del_archivo = {c.name for c in modelo_archivo.__table__.columns}
    return [
        nombre for nombre in calientes
        if nombre in del_archivo and nombre not in _COLUMNAS_DE_ARCHIVO
    ]


_ARCHIVE_SPECS = [
    (modelo, archivo, campos_a_copiar(modelo, archivo))
    for modelo, archivo in (
        (CampaignSnapshot, CampaignSnapshotArchive),
        (AdsetSnapshot, AdsetSnapshotArchive),
        (AdSnapshot, AdSnapshotArchive),
    )
]


BATCH_SIZE = 500


async def archive_old_snapshots(db: AsyncSession, retention_days: int, batch_size: int = BATCH_SIZE) -> Dict[str, int]:
    """Mueve a las tablas de archivo las filas con `date` anterior al retention
    window, y las borra de la tabla caliente. Devuelve cuántas filas se movieron
    por tabla. Procesa en lotes y usa savepoints por lote para garantizar
    atomicidad y evitar duplicados si falla el borrado o la inserción."""
    cutoff = datetime.date.today() - datetime.timedelta(days=retention_days)
    counts: Dict[str, int] = {}

    for hot_model, archive_model, fields in _ARCHIVE_SPECS:
        table_name = hot_model.__tablename__
        total_archived = 0
        try:
            while True:
                rows = (await db.execute(
                    select(hot_model).where(hot_model.date < cutoff).limit(batch_size)
                )).scalars().all()

                if not rows:
                    break

                ids = [row.id for row in rows]

                async with db.begin_nested():
                    for row in rows:
                        db.add(archive_model(
                            original_id=row.id,
                            **{f: getattr(row, f) for f in fields},
                        ))
                    await db.execute(delete(hot_model).where(hot_model.id.in_(ids)))

                await db.commit()
                total_archived += len(rows)

            counts[table_name] = total_archived
            if total_archived:
                logger.info("Archivadas %d filas de %s (anteriores a %s).",
                            total_archived, table_name, cutoff)
        except Exception:
            logger.exception("Fallo archivando %s; se continúa con las demás tablas.", table_name)
            counts[table_name] = total_archived

    return counts
