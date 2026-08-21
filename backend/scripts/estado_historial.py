"""Estado del backfill de historial, desde la terminal.

El banner del frontend dice "3 de 29 meses" y nada más. Cuando eso tarda, la
pregunta real es otra: **¿está avanzando o está colgado?** Un backfill lento y
uno muerto se ven exactamente igual desde afuera, y esperar de más a un job
caído es peor que cortarlo.

Este script responde eso: mira el `SyncJob`, cuenta las filas que aterrizaron
en los últimos minutos, y dice el ritmo real medido — no el estimado.

    python scripts/estado_historial.py            # una foto
    python scripts/estado_historial.py --seguir   # se refresca cada 30s
"""
import asyncio
import datetime
import sys

from sqlalchemy import func, select

from app.database import async_session
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.organization import Organization
from app.models.sync_job import SyncJob
from app.services.history_reconciliation import get_history_status

VENTANA_RITMO_MIN = 5


def _duracion(segundos: float) -> str:
    segundos = int(max(0, segundos))
    if segundos < 60:
        return f"{segundos}s"
    if segundos < 3600:
        return f"{segundos // 60}m {segundos % 60}s"
    return f"{segundos // 3600}h {(segundos % 3600) // 60}m"


async def _informe(db, acc: AdAccount, org_slug: str = "?", duplicada: bool = False) -> None:
    ahora = datetime.datetime.utcnow()
    print(f"\ncuenta {acc.external_account_id} ({acc.name})  ·  org {org_slug}")
    print("=" * 60)
    if duplicada:
        # La MISMA cuenta de Meta conectada dos veces son dos backfills
        # compitiendo por la misma cuota de API, dos historiales a medias, y un
        # chat que ve el de la organización que le toque.
        print("  ⚠ esta cuenta de Meta está conectada MÁS DE UNA VEZ")

    job = (await db.execute(
        select(SyncJob)
        .where(SyncJob.ad_account_id == acc.id, SyncJob.sync_type == "BACKFILL")
        .order_by(SyncJob.started_at.desc()).limit(1)
    )).scalars().first()

    if job is None:
        print("BACKFILL: nunca corrió para esta cuenta.")
    else:
        transcurrido = (ahora - job.started_at).total_seconds() if job.started_at else 0
        print(f"BACKFILL: {job.status}  ·  arrancó hace {_duracion(transcurrido)}")
        p = job.payload_json or {}
        if p.get("months_total"):
            print(f"  meses:   {p.get('months_done', 0)} de {p['months_total']}"
                  f"  ·  último cerrado: {p.get('last_month', '—')}")
        print(f"  filas:   {job.records_synced or 0}")
        if job.error_message:
            print(f"  ERROR:   {job.error_message[:300]}")

    # El dato que de verdad importa: ¿están entrando filas AHORA? Es lo único
    # que distingue "lento" de "colgado".
    desde = ahora - datetime.timedelta(minutes=VENTANA_RITMO_MIN)
    recientes = (await db.execute(
        select(func.count()).select_from(CampaignSnapshot)
        .where(CampaignSnapshot.ad_account_id == acc.id,
               CampaignSnapshot.created_at >= desde)
    )).scalar_one()
    ultima = (await db.execute(
        select(func.max(CampaignSnapshot.created_at))
        .where(CampaignSnapshot.ad_account_id == acc.id)
    )).scalar_one_or_none()

    print(f"\nritmo:   {recientes} filas en los últimos {VENTANA_RITMO_MIN} min")
    if ultima:
        print(f"  última fila hace {_duracion((ahora - ultima).total_seconds())}")

    estado = await get_history_status(db, acc.organization_id, acc.id)
    print(f"\ncobertura: {estado.months_covered} de {estado.months_total} meses"
          f"  ({estado.first_month or '—'} → {estado.last_month or '—'})")
    if estado.gaps:
        print(f"  faltan {len(estado.gaps)}:")
        for gap in estado.gaps[:12]:
            print(f"    {gap.year_month}  meta S/{gap.meta_spend:,.2f}"
                  f"  local S/{gap.local_spend:,.2f}  falta {gap.missing_pct:.0f}%")
        if len(estado.gaps) > 12:
            print(f"    …y {len(estado.gaps) - 12} meses más")

    print()
    if estado.is_complete:
        print(">>> COMPLETO.")
    elif recientes > 0:
        restante = estado.estimated_seconds
        print(f">>> AVANZANDO. Estimado para lo que falta: {_duracion(restante)}.")
    elif job and job.status == "RUNNING":
        print(">>> SIN FILAS NUEVAS y el job figura RUNNING.")
        print("    Puede estar esperando por rate limit de Meta (es normal hasta")
        print("    unos minutos) o haber muerto. Mirá los logs del worker de sync:")
        print("    si no aparece nada de 'backfill' en ese rato, está colgado.")
    else:
        print(">>> DETENIDO. El job no está corriendo y falta historial.")
        print("    Se vuelve a encolar solo desde Beat, o a mano con la tarea")
        print("    schedule_pending_history_backfills.")


async def main() -> None:
    seguir = "--seguir" in sys.argv
    while True:
        async with async_session() as db:
            cuentas = (await db.execute(
                select(AdAccount).where(AdAccount.status != "ARCHIVED")
            )).scalars().all()
            if not cuentas:
                print("No hay cuentas de anuncios activas.")
                return
            slugs = dict((await db.execute(
                select(Organization.id, Organization.slug)
            )).all())
            veces: dict = {}
            for acc in cuentas:
                veces[acc.external_account_id] = veces.get(acc.external_account_id, 0) + 1
            for acc in cuentas:
                await _informe(
                    db, acc,
                    org_slug=slugs.get(acc.organization_id, "?"),
                    duplicada=veces[acc.external_account_id] > 1,
                )
        if not seguir:
            return
        print("\n--- refresco en 30s (Ctrl+C para salir) ---")
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
