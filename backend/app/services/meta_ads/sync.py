import asyncio
import datetime
import json
import random
import logging
from collections import defaultdict
from typing import List, Dict, Any, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.adset_snapshot import AdsetSnapshot
from app.models.ad_snapshot import AdSnapshot
from app.models.meta_creative import MetaCreative
from app.models.custom_audience import CustomAudience
from app.models.meta_page import MetaPage
from app.models.meta_page_ad_account import MetaPageAdAccount
from app.models.whatsapp_number_ad_account import WhatsAppNumberAdAccount
from app.models.sync_job import SyncJob
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter
from app.services.meta_ads.exceptions import TransientMetaError, classify_meta_error, extract_meta_error_diagnostic
from app.services.meta_ads.sync_progress import SyncProgressTracker

logger = logging.getLogger("lejovista.sync")

SYNC_TYPE_BACKFILL = "BACKFILL"

class MetaAdsSyncService:
    def __init__(self, db: AsyncSession):
        self.db = db
        # Estructura (campañas, conjuntos, anuncios) cacheada por instancia. El
        # backfill recorre un mes por vez, pero /campaigns, /adsets y /ads NO
        # están acotados por tiempo: pedirlos en cada chunk multiplicaría las
        # llamadas por la cantidad de meses sin traer un solo dato nuevo.
        self._structure_cache: Dict[str, Any] = {}

    async def _reconciliar_alcance(
        self, modelo, columna: str, ad_account: AdAccount, ids_propios: set
    ) -> None:
        """Deja los enlaces de ESTA cuenta exactamente en `ids_propios`.

        Guardar solo hacia adelante no alcanza: el seed de la migración enlazó
        cada página y cada número con TODAS las cuentas de su organización
        —había que hacerlo, si no "Crear campaña" se quedaba sin páginas hasta
        el sync siguiente— y sin poda ese seed amplio se quedaría amplio para
        siempre. Es exactamente lo que pasó con las páginas en 44f4ec2: se acotó
        el descubrimiento, pero lo ya guardado sobrevivió al arreglo.

        **No se poda con la lista vacía.** Un token vencido, un permiso que
        falta o Meta caído dan cero resultados igual que "esta cuenta no
        promociona ninguna página", y no hay forma de distinguirlos desde acá.
        Entre dejar un enlace de más y dejar una cuenta sin ninguna página para
        crear campañas, se deja el de más: la próxima corrida que sí vea algo lo
        corrige. Mismo criterio que `alguna_ok` en `refresh_org_pages_impl`.
        """
        if not ids_propios:
            return

        actuales = {
            getattr(fila, columna): fila
            for fila in (await self.db.execute(
                select(modelo).where(modelo.ad_account_id == ad_account.id)
            )).scalars().all()
        }
        for propio_id, fila in actuales.items():
            if propio_id not in ids_propios:
                await self.db.delete(fila)
                logger.info(
                    "La cuenta %s ya no alcanza %s=%s: se quita el enlace.",
                    ad_account.id, columna, propio_id,
                )
        for propio_id in ids_propios - set(actuales):
            self.db.add(modelo(
                **{columna: propio_id},
                ad_account_id=ad_account.id,
                organization_id=ad_account.organization_id,
            ))

    async def _structure_once(self, key: str, factory):
        """Ejecuta `factory()` la primera vez y reusa el resultado después.

        Solo se cachea una respuesta SIN error. `call_api` no levanta
        excepciones: ante un rate limit o un 5xx devuelve `{"error": ...}`, y
        cachear ese dict condenaba la corrida entera — el backfill recorre un
        mes por vez con la misma instancia, así que un tropiezo puntual en
        `/campaigns` hacía que TODOS los meses siguientes murieran con el mismo
        error de hace una hora. Un error se reintenta en el próximo mes; lo que
        no se puede es fosilizarlo.
        """
        if key in self._structure_cache:
            return self._structure_cache[key]
        res = await factory()
        if not self._has_error(res):
            self._structure_cache[key] = res
        return res

    async def run_sync(self, ad_account: AdAccount, sync_type: str = "INCREMENTAL") -> SyncJob:
        """
        Ejecuta la sincronización de la cuenta de anuncios.
        Soporta modo mock y modo real de producción.
        """
        job = SyncJob(
            ad_account_id=ad_account.id,
            organization_id=ad_account.organization_id,
            sync_type=sync_type,
            status="RUNNING",
            started_at=datetime.datetime.utcnow()
        )
        self.db.add(job)
        await self.db.commit()
        # Capturados antes de cualquier fallo: un rollback expira los objetos ORM
        # y volver a tocar `job.id`/`ad_account.id` dispararía un refresh.
        job_id = job.id
        acc_id = ad_account.id
        org_id = ad_account.organization_id

        from app.services.meta_ads.sync_progress import SyncProgressTracker
        SyncProgressTracker.report_progress(
            organization_id=org_id,
            ad_account_id=acc_id,
            step=1,
            total_steps=5,
            step_name="account",
            message="Verificando cuenta en Meta...",
        )

        try:
            limiter = MetaAPIRateLimiter(ad_account_id=ad_account.id, external_account_id=ad_account.external_account_id)

            if settings.MOCK_SERVICES:
                records_synced = await self._sync_mock_data(ad_account, sync_type, limiter)
            elif sync_type == SYNC_TYPE_BACKFILL:
                records_synced = await self._sync_backfill_by_months(ad_account, limiter, job)
            else:
                records_synced = await self._sync_real_data(ad_account, sync_type, limiter)

            # Un backfill que dejó meses sin bajar NO es COMPLETED. Marcarlo así
            # (lo que se hacía siempre) es lo que hacía que "3 de 29" se viera
            # idéntico a un historial completo: sin estado, sin error_message y
            # sin nada que explicara por qué se detuvo.
            avance = job.payload_json or {}
            meses_fallidos = avance.get("months_failed") or 0
            if sync_type == SYNC_TYPE_BACKFILL and meses_fallidos:
                job.status = "PARTIAL"
                job.error_message = (
                    f"{meses_fallidos} de {avance.get('months_total')} meses no se pudieron bajar. "
                    f"Último error — {avance.get('last_error')}"
                )
            else:
                job.status = "COMPLETED"
            job.records_synced = records_synced
            job.completed_at = datetime.datetime.utcnow()

            # 3.3: limpiar errores anteriores y registrar fecha de sync
            ad_account.last_sync_error_code = None
            ad_account.last_sync_error_detail = None
            ad_account.last_synced_at = datetime.datetime.utcnow()
            self.db.add(ad_account)

            # El commit final va DENTRO del try a propósito: buena parte de las
            # escrituras del sync quedan pendientes en la sesión y solo se vuelcan
            # aquí, así que es un punto de fallo real (p. ej. IntegrityError por
            # una carrera entre dos syncs de la misma cuenta). Cuando estaba fuera
            # del try, ese error escapaba sin marcar el job y lo dejaba en RUNNING
            # para siempre.
            await self.db.commit()

            # 3.2: reportar completado
            SyncProgressTracker.report_completed(
                organization_id=org_id,
                ad_account_id=acc_id,
                records_synced=records_synced,
            )
            return job
        except Exception as e:
            logger.exception(f"Error en la sincronización {sync_type} de cuenta {acc_id}")
            # Un fallo al volcar deja la sesión en "pending rollback": cualquier
            # commit posterior falla también. Hay que revertir antes de poder
            # marcar el job como FAILED.
            try:
                await self.db.rollback()
            except Exception:
                logger.exception("No se pudo revertir la sesión tras el fallo de sync")

            from app.services.meta_ads.exceptions import extract_meta_error_diagnostic
            err_code, err_detail = extract_meta_error_diagnostic(e)

            job = await self.db.get(SyncJob, job_id)
            if job is not None:
                job.status = "FAILED"
                job.error_message = str(e)
                job.completed_at = datetime.datetime.utcnow()

            # 3.3: registrar diagnóstico de error en AdAccount
            acc = await self.db.get(AdAccount, acc_id)
            if acc is not None:
                acc.last_sync_error_code = err_code
                acc.last_sync_error_detail = err_detail
                if err_code == 190:
                    acc.status = "EXPIRED"
                self.db.add(acc)

            try:
                await self.db.commit()
            except Exception:
                logger.exception("No se pudo marcar el SyncJob %s como FAILED", job_id)

            SyncProgressTracker.report_failed(
                organization_id=org_id,
                ad_account_id=acc_id,
                error_message=str(e),
                error_code=err_code,
                error_detail=err_detail,
            )

            classified = classify_meta_error(e)
            if isinstance(classified, TransientMetaError):
                # Propagar para que la tarea de Celery reintente con backoff.
                raise classified from e

            # Fallo permanente: no reintentar. Alertar al equipo (log de nivel error
            # que un colector como Sentry puede capturar).
            logger.error(
                "Sync permanentemente fallido para cuenta %s (org %s): %s",
                acc_id, org_id, e
            )
            return job

    async def _sync_mock_data(self, ad_account: AdAccount, sync_type: str, limiter: MetaAPIRateLimiter) -> int:
        """
        Genera datos mockeados realistas para 3 campañas de ejemplo.

        FULL conserva la ventana corta de 30 días e INCREMENTAL solo refresca
        ayer/hoy. BACKFILL llena la ventana de retención completa para que el
        stack local ejercite exactamente el flujo histórico de producción.
        Esto permite poblar la base de datos local para pruebas de análisis y optimización.
        """
        # Simulamos llamadas API ficticias en el rate limiter para registrar auditoría local
        # Query 1: Info de cuenta
        await limiter.call_api(f"act_{ad_account.external_account_id}", {"fields": "amount_spent,spend_cap,account_status"})
        
        # Actualizar campos de la cuenta en base a los mocks
        ad_account.spend_cap = 500000.0  # en centavos en Meta, pero guardamos valor nominal: 5000 PEN
        ad_account.amount_spent = 3200.0
        ad_account.status = "ACTIVE"
        self.db.add(ad_account)
        
        # 15 queries en full, 4 en incremental/backfill mock.
        num_queries = 15 if sync_type == "FULL" else 4
        for i in range(2, num_queries + 1):
            await limiter.call_api(f"act_{ad_account.external_account_id}/insights", {"query_num": i})

        from app.services.meta_ads.sync_progress import SyncProgressTracker
        SyncProgressTracker.report_progress(
            ad_account.organization_id, ad_account.id, 2, 5, "campaigns", "Sincronizando campañas e insights..."
        )

        today = datetime.date.today()

        def _mock_dt(days_ago: int) -> datetime.datetime:
            return datetime.datetime.combine(today - datetime.timedelta(days=days_ago), datetime.time(10, 30))

        # Definir campañas mockeadas
        campaign_definitions = [
            {
                "id": "camp_cable_gpt",
                "name": "MTV_MSJ_CABLE_GPT_CERCADO_JUN2026",
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
                "result_type": "MIXTA",   # formularios Y WhatsApp: el caso que rompia las cifras
                "budget": 70.0,  # Presupuesto diario S/.70
                "ctr_base": 1.4, # 1.4%
                "cpl_target": 1.50, # S/.1.50
                "reels_better": True,
                "creative_title": "Cable GPT en Cercado 🔥",
                "creative_body": (
                    "¿Buscas cable de confianza en Cercado? 😩\n\n"
                    "✅ Entrega rápida\n✅ Precios justos\n✅ Atención inmediata\n\n"
                    "📲 Escríbenos por WhatsApp y cotiza sin compromiso."
                ),
                "creative_cta_type": "WHATSAPP_MESSAGE",
                "objective": "OUTCOME_ENGAGEMENT",
                "created_time": _mock_dt(60),
                "start_time": _mock_dt(60),
                "stop_time": None,
            },
            {
                "id": "camp_vulcanizado",
                "name": "MTV_MSJ_VULCANIZADO_SURQUILLO_JUN2026",
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
                "result_type": "WHATSAPP",
                "budget": 50.0,
                "ctr_base": 0.6,
                "cpl_target": 4.50,
                "high_frequency": True,
                "objective": "OUTCOME_ENGAGEMENT",
                "created_time": _mock_dt(40),
                "start_time": _mock_dt(40),
                "stop_time": None,
            },
            {
                "id": "camp_thhn",
                "name": "MTV_MSJ_THHN_LIMA_JUN2026",
                "status": "ACTIVE",
                # A proposito: ACTIVE pero SIN ENTREGAR. Es el caso que hace que
                # filtrar por `status` de una respuesta equivocada, y en
                # simulacion es la unica forma de probar que el filtro real
                # (effective_status) funciona.
                "effective_status": "ADSET_PAUSED",
                "result_type": "LEADS",
                "budget": 40.0,
                "ctr_base": 0.25,
                "cpl_target": 12.00,
                "failing": True,
                "objective": "OUTCOME_LEADS",
                "created_time": _mock_dt(20),
                "start_time": _mock_dt(20),
                "stop_time": None,
            }
        ]

        # Objetivo y fechas son estructurales (de la campaña, no del día): un
        # solo UPDATE por campaña alcanza para poner al día TODOS sus snapshots
        # históricos ya guardados, igual que en `_sync_real_data`.
        for c in campaign_definitions:
            await self.db.execute(
                update(CampaignSnapshot)
                .where(
                    CampaignSnapshot.ad_account_id == ad_account.id,
                    CampaignSnapshot.campaign_id == c["id"],
                )
                .values(
                    objective=c["objective"],
                    created_time=c["created_time"],
                    start_time=c["start_time"],
                    stop_time=c["stop_time"],
                )
            )

        # Determinar el rango de días a sincronizar
        # En FULL creamos 30 días. En INCREMENTAL sólo ayer y hoy. El
        # BACKFILL llega hasta el mismo techo que aplica snapshot_archiver.
        if sync_type == SYNC_TYPE_BACKFILL:
            days_to_sync = settings.HOT_DAILY_RETENTION_DAYS
        else:
            days_to_sync = 30 if sync_type == "FULL" else 2

        records_created = 0
        
        for c in campaign_definitions:
            for d in range(days_to_sync):
                sync_date = today - datetime.timedelta(days=d)
                
                # Evitar duplicados comprobando si ya existe para esta fecha y campaña
                q = select(CampaignSnapshot).where(
                    CampaignSnapshot.ad_account_id == ad_account.id,
                    CampaignSnapshot.campaign_id == c["id"],
                    CampaignSnapshot.date == sync_date
                )
                res = await self.db.execute(q)
                existing = res.scalar_one_or_none()
                if existing:
                    continue

                # Generar métricas diarias realistas
                spend = c["budget"] * random.uniform(0.9, 1.05)
                
                # Fórmulas de conversión según estado de la campaña
                if c.get("reels_better"):
                    # Campaña muy exitosa
                    impressions = int(spend * 120 * random.uniform(0.9, 1.1))
                    clicks = int(impressions * (c["ctr_base"] / 100.0) * random.uniform(0.9, 1.1))
                    conversions = int(spend / (c["cpl_target"] * random.uniform(0.85, 1.05)))
                    frequency = 1.1 + (d * 0.05) # Sube lentamente
                elif c.get("high_frequency"):
                    # Campaña saturándose en frecuencia
                    frequency = 2.5 + (d * 0.1) # Sube rápido
                    effectiveness = max(0.4, 1.0 - (frequency - 2.0) * 0.25)
                    impressions = int(spend * 100 * random.uniform(0.9, 1.1))
                    clicks = int(impressions * (c["ctr_base"] / 100.0) * effectiveness * random.uniform(0.8, 1.1))
                    conversions = int((spend / c["cpl_target"]) * effectiveness * random.uniform(0.8, 1.1))
                elif c.get("failing"):
                    # Campaña fallando
                    impressions = int(spend * 80 * random.uniform(0.9, 1.1))
                    clicks = int(impressions * (c["ctr_base"] / 100.0) * random.uniform(0.7, 1.1))
                    # En los últimos 2 días tiene 0 leads
                    if d <= 1:
                        conversions = 0
                    else:
                        conversions = int(spend / (c["cpl_target"] * random.uniform(1.1, 1.5)))
                    frequency = 1.0 + (d * 0.02)
                else:
                    impressions = int(spend * 100)
                    clicks = int(impressions * 0.01)
                    conversions = int(spend / 3.0)
                    frequency = 1.2

                conversions = max(0, conversions)
                # Tipo de resultado por campaña, para que en simulación exista el
                # caso que rompía las cifras: una cuenta que corre formularios Y
                # WhatsApp a la vez. Con todas las campañas del mismo tipo, la
                # diferencia entre "elegir uno" y "sumar" no se ve nunca.
                tipo = c.get("result_type", "WHATSAPP")
                if tipo == "LEADS":
                    leads_reales, wa_reales = conversions, 0
                elif tipo == "MIXTA":
                    leads_reales = conversions
                    wa_reales = int(conversions * 1.4)
                else:
                    leads_reales, wa_reales = 0, conversions
                # `conversations` conserva el proxy histórico (cuando no hay
                # mensajería se rellena con los leads) para no cambiar lo que ya
                # leen otras partes; `results` es la suma honesta.
                conversations = wa_reales or leads_reales
                conversions = leads_reales
                resultados = leads_reales + wa_reales
                reach = int(impressions / max(frequency, 1.0))
                cpl = spend / conversions if conversions > 0 else spend
                ctr = (clicks / impressions) * 100.0 if impressions > 0 else 0.0

                # Los breakdowns históricos multiplicarían por cinco el costo
                # real y no los consumen ni Capa 2 ni el chat. El mock refleja
                # esa decisión: en BACKFILL solo llena campaña/adset/anuncio.
                if sync_type == SYNC_TYPE_BACKFILL:
                    placements = {}
                elif c.get("reels_better"):
                    placements = {
                        "facebook_reels": {"spend": spend * 0.6, "conversions": int(conversions * 0.8), "cpl": (spend * 0.6) / max(1, int(conversions * 0.8))},
                        "instagram_feed": {"spend": spend * 0.2, "conversions": int(conversions * 0.1), "cpl": (spend * 0.2) / max(1, int(conversions * 0.1))},
                        "facebook_feed": {"spend": spend * 0.2, "conversions": int(conversions * 0.1), "cpl": (spend * 0.2) / max(1, int(conversions * 0.1))}
                    }
                else:
                    # Distribución estándar con Instagram ineficiente si aplica
                    inst_cpl = 6.50 if c.get("high_frequency") else 3.50
                    placements = {
                        "facebook_feed": {"spend": spend * 0.5, "conversions": int(conversions * 0.6), "cpl": (spend * 0.5) / max(1, int(conversions * 0.6))},
                        "instagram_positions": {"spend": spend * 0.3, "conversions": int(conversions * 0.1), "cpl": inst_cpl},
                        "facebook_reels": {"spend": spend * 0.2, "conversions": int(conversions * 0.3), "cpl": (spend * 0.2) / max(1, int(conversions * 0.3))}
                    }

                demographics = {} if sync_type == SYNC_TYPE_BACKFILL else {
                    "25-34": {"spend": spend * 0.4, "conversions": int(conversions * 0.5)},
                    "35-44": {"spend": spend * 0.3, "conversions": int(conversions * 0.3)},
                    "45-54": {"spend": spend * 0.2, "conversions": int(conversions * 0.15)},
                    "55-64": {"spend": spend * 0.1, "conversions": int(conversions * 0.05)}
                }

                # P-2: breakdowns de género, dispositivo y geografía.
                extra = {} if sync_type == SYNC_TYPE_BACKFILL else {
                    "gender": {
                        "male": {"spend": spend * 0.55, "conversions": int(conversions * 0.55)},
                        "female": {"spend": spend * 0.45, "conversions": int(conversions * 0.45)},
                    },
                    "device": {
                        "mobile": {"spend": spend * 0.8, "conversions": int(conversions * 0.85)},
                        "desktop": {"spend": spend * 0.2, "conversions": int(conversions * 0.15)},
                    },
                    "region": {
                        "Lima": {"spend": spend * 0.7, "conversions": int(conversions * 0.75)},
                        "Callao": {"spend": spend * 0.3, "conversions": int(conversions * 0.25)},
                    },
                }

                snapshot = CampaignSnapshot(
                    ad_account_id=ad_account.id,
                    organization_id=ad_account.organization_id,
                    campaign_id=c["id"],
                    campaign_name=c["name"],
                    status=c["status"],
                    effective_status=c.get("effective_status"),
                    objective=c["objective"],
                    created_time=c["created_time"],
                    start_time=c["start_time"],
                    stop_time=c["stop_time"],
                    budget=c["budget"],
                    spend=spend,
                    impressions=impressions,
                    reach=reach,
                    clicks=clicks,
                    conversions=conversions,
                    conversations=conversations,
                    results=resultados,
                    cpl=cpl,
                    ctr=ctr,
                    frequency=frequency,
                    placements_breakdown=placements,
                    demographics_breakdown=demographics,
                    extra_breakdowns=extra,
                    date=sync_date
                )
                self.db.add(snapshot)
                records_created += 1

                # --- Adsets y anuncios del día (P-2.3) ---
                adset_defs = [
                    {"suffix": "AV", "label": "Advantage+", "share": 0.6,
                     "targeting": {"audience_type": "ADVANTAGE_PLUS", "geo": "Lima", "age": "25-54",
                                   "placements": ["facebook_feed", "facebook_reels"]}},
                    {"suffix": "IN", "label": "Intereses", "share": 0.4,
                     "targeting": {"audience_type": "INTERESTS", "geo": "Lima", "age": "25-54",
                                   "placements": ["facebook_feed", "instagram_feed"]}},
                ]
                for a in adset_defs:
                    a_spend = spend * a["share"]
                    a_conv = int(conversions * a["share"])
                    a_impr = int(impressions * a["share"])
                    a_clicks = int(clicks * a["share"])
                    a_reach = int(reach * a["share"])
                    a_cpl = a_spend / a_conv if a_conv > 0 else a_spend
                    a_ctr = (a_clicks / a_impr) * 100.0 if a_impr > 0 else 0.0
                    adset_id = f"adset_{c['id']}_{a['suffix']}"
                    self.db.add(AdsetSnapshot(
                        ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                        campaign_id=c["id"], adset_id=adset_id, name=f"{c['name']}_{a['label']}",
                        budget=round(c["budget"] * a["share"], 2),
                        spend=a_spend, reach=a_reach, impressions=a_impr, clicks=a_clicks,
                        conversions=a_conv, conversations=a_conv, cpl=a_cpl, ctr=a_ctr,
                        targeting_summary=a["targeting"], date=sync_date,
                    ))
                    self.db.add(AdSnapshot(
                        ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                        campaign_id=c["id"], adset_id=adset_id, ad_id=f"ad_{adset_id}",
                        creative_id=f"creative_{c['id']}", name=f"{c['name']}_{a['label']}_Cuadrado",
                        spend=a_spend, reach=a_reach, impressions=a_impr, clicks=a_clicks,
                        conversions=a_conv, conversations=a_conv, cpl=a_cpl, ctr=a_ctr, date=sync_date,
                        creative_title=c.get("creative_title"),
                        creative_body=c.get("creative_body"),
                        creative_cta_type=c.get("creative_cta_type"),
                    ))

        SyncProgressTracker.report_progress(
            ad_account.organization_id, ad_account.id, 3, 5, "adsets", "Sincronizando conjuntos de anuncios y audiencias..."
        )
        SyncProgressTracker.report_progress(
            ad_account.organization_id, ad_account.id, 4, 5, "ads", "Sincronizando anuncios y métricas..."
        )

        # El BACKFILL es deliberadamente una tarea de historia diaria, no otro
        # FULL: no repite inventario, páginas ni el histórico mensual de gasto.
        if sync_type != SYNC_TYPE_BACKFILL:
            SyncProgressTracker.report_progress(
                ad_account.organization_id, ad_account.id, 5, 5, "creatives", "Sincronizando creativos e inventario..."
            )
            await self._ensure_mock_inventory(ad_account)
            await self._sync_mock_spend_history(ad_account)

        await self.db.flush()
        return records_created

    async def _ensure_mock_inventory(self, ad_account: AdAccount) -> None:
        """Crea creativos y audiencias mock (idempotente) para el inventario del brief."""
        mock_creatives = [
            {"hash": "h_cable_cuadrado", "name": "MTV Cable — Cuadrado", "url": "https://cdn.example/cable_1x1.jpg", "w": 1080, "h": 1080},
            {"hash": "h_gpt_story", "name": "Cable GPT — Story", "url": "https://cdn.example/gpt_story.jpg", "w": 1080, "h": 1920},
            {"hash": "h_vulcano_cuadrado", "name": "Vulcanizado — Cuadrado", "url": "https://cdn.example/vulcano_1x1.jpg", "w": 1080, "h": 1080},
        ]
        for cr in mock_creatives:
            exists = (await self.db.execute(select(MetaCreative).where(
                MetaCreative.ad_account_id == ad_account.id,
                MetaCreative.image_hash == cr["hash"]))).scalar_one_or_none()
            if not exists:
                self.db.add(MetaCreative(
                    ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                    image_hash=cr["hash"], name=cr["name"], url=cr["url"],
                    width=cr["w"], height=cr["h"], meta_created_time=datetime.datetime.utcnow(),
                ))

        mock_audiences = [
            {"ext": "aud_lookalike_1", "name": "Lookalike Compradores 1%", "subtype": "LOOKALIKE", "count": 250000, "desc": "Similar a clientes que compraron"},
            {"ext": "aud_web_30d", "name": "Visitantes Web 30d", "subtype": "WEBSITE", "count": 12400, "desc": "Visitaron el sitio en los últimos 30 días"},
            {"ext": "aud_wa_engagers", "name": "Interacciones WhatsApp", "subtype": "ENGAGEMENT", "count": 5300, "desc": "Iniciaron chat de WhatsApp"},
        ]
        for au in mock_audiences:
            exists = (await self.db.execute(select(CustomAudience).where(
                CustomAudience.ad_account_id == ad_account.id,
                CustomAudience.external_id == au["ext"]))).scalar_one_or_none()
            if not exists:
                self.db.add(CustomAudience(
                    ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                    external_id=au["ext"], name=au["name"], subtype=au["subtype"],
                    description=au["desc"], approximate_count=au["count"],
                    meta_time_created=datetime.datetime.utcnow(),
                ))

        # Fase 4 (plan de creación de campañas): página + WhatsApp mock, para
        # que la UI pueda probar el dropdown de página sin depender de Meta real.
        exists_page = (await self.db.execute(select(MetaPage).where(
            MetaPage.organization_id == ad_account.organization_id,
            MetaPage.external_page_id == "mock_page_1"))).scalar_one_or_none()
        if not exists_page:
            self.db.add(MetaPage(
                organization_id=ad_account.organization_id, external_page_id="mock_page_1",
                name=f"Página de {ad_account.name}", whatsapp_number="51999888777",
                instagram_actor_id="mock_ig_1",
            ))

    async def refresh_pages(self, ad_account: AdAccount) -> set:
        """Corre SOLO el descubrimiento de páginas + WhatsApp para una cuenta,
        sin el resto del sync (campañas/insights/breakdowns). Lo dispara
        `POST /organizations/my/pages/refresh` para que el usuario no tenga que
        esperar al sync de las 06:00 después de otorgar accesos nuevos en Meta
        (caso "Althea pack": el acceso a la página se concede en el Business
        Manager y se quiere ver en el dropdown de inmediato).
        """
        if settings.MOCK_SERVICES:
            # El sync mock ya siembra su página de ejemplo; nada que refrescar.
            logger.info("refresh_pages en modo mock: no-op (cuenta %s).", ad_account.id)
            return set()
        token = ad_account.get_access_token()
        if not token:
            raise ValueError("Token de acceso de la cuenta no válido o expirado.")
        limiter = MetaAPIRateLimiter(ad_account_id=ad_account.id, external_account_id=ad_account.external_account_id)
        return await self._sync_real_pages(ad_account, token, limiter) or set()
        await self.db.commit()

    async def _sync_real_data(
        self, ad_account: AdAccount, sync_type: str, limiter: MetaAPIRateLimiter,
        insights_period: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Lógica real de producción usando llamadas a la Graph API de Meta Ads.
        """
        # NOTA: En producción, aquí se realizarían las queries 1-15 de la API.
        # Por ahora definimos la estructura básica. Cuando se conectan credenciales reales,
        # el rate limiter realiza los HTTP requests auténticos.
        token = ad_account.get_access_token()
        if not token:
            raise ValueError("Token de acceso de la cuenta no válido o expirado.")

        # Estado de la cuenta (gasto acumulado, tope, si está suspendida). En
        # BACKFILL NO se pide: son datos del PRESENTE, iguales para los 22 meses
        # que se recorren, y el sync diario ya los trae. Pedirlos una vez por mes
        # era una llamada de cada cuatro gastada en releer lo mismo — y con Meta
        # limitando a 15 s por llamada, eso es un cuarto del tiempo total.
        #
        # Mismo criterio que `_structure_once` para /campaigns, /adsets y /ads:
        # lo que no depende del mes no se pide por mes.
        if sync_type != SYNC_TYPE_BACKFILL:
            acc_info = await limiter.call_api(
                ad_account.external_account_id,
                {"fields": "amount_spent,spend_cap,account_status"},
                access_token=token
            )

            if "ERROR" in acc_info or "error" in acc_info:
                self._maybe_mark_expired(ad_account, acc_info)
                raise Exception(f"Error al verificar cuenta de anuncios: {acc_info}")

            # Actualizar ad_account
            ad_account.spend_cap = float(acc_info.get("spend_cap", 0)) / 100.0  # Meta retorna centavos
            ad_account.amount_spent = float(acc_info.get("amount_spent", 0)) / 100.0

            status_code = acc_info.get("account_status", 1)
            ad_account.status = "ACTIVE" if status_code == 1 else "SUSPENDED"
            self.db.add(ad_account)
            # Commit inmediato: si algo más adelante en este sync falla (campañas,
            # insights, adsets...), la excepción se propaga y el commit final de
            # `run_sync` nunca ocurre — sin este commit parcial, spend_cap/amount_spent/
            # status quedaban leídos de Meta pero descartados en silencio, y la cuenta
            # se quedaba con datos viejos hasta el próximo sync que lograra completar
            # TODO el pipeline sin errores.
            await self.db.commit()

        # 1. Campañas + insights de campaña (P-1): dos lecturas independientes —
        # los insights no necesitan `campaigns_map` para pedirse, solo para
        # combinarse después — se piden en paralelo en vez de una tras otra.
        insights_period = insights_period or self._insights_period_params(sync_type)

        campaigns_res, campaigns_insights = await asyncio.gather(
            self._structure_once("campaigns", lambda: limiter.call_api_paginated(
                f"{ad_account.external_account_id}/campaigns",
                {
                    "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,created_time,start_time,stop_time",
                    "limit": 200,
                },
                access_token=token
            )),
            limiter.call_api_paginated(
                f"{ad_account.external_account_id}/insights",
                {
                    "level": "campaign",
                    "fields": "campaign_id,campaign_name,spend,impressions,reach,clicks,actions,frequency",
                    "time_increment": 1,  # datos diarios
                    # Sin `limit`, Meta devuelve su página default de 25 filas: con
                    # ~30 días × ~10 campañas eso truncaba a los 25 días más viejos.
                    # Subimos el tamaño de página y además paginamos (call_api_paginated).
                    "limit": 500,
                    **insights_period,
                },
                access_token=token
            ),
        )

        if "ERROR" in campaigns_res or "error" in campaigns_res:
            self._maybe_mark_expired(ad_account, campaigns_res)
            raise Exception(f"Error al obtener campañas: {campaigns_res}")

        campaigns_map = {}
        for c in campaigns_res.get("data", []):
            budget = float(c.get("daily_budget") or c.get("lifetime_budget") or 0) / 100.0
            campaigns_map[c["id"]] = {
                "name": c.get("name", "Unknown"),
                "status": c.get("status", "UNKNOWN"),
                "effective_status": c.get("effective_status"),
                "budget": budget,
                "objective": c.get("objective"),
                "created_time": self._parse_meta_datetime(c.get("created_time")),
                "start_time": self._parse_meta_datetime(c.get("start_time")),
                "stop_time": self._parse_meta_datetime(c.get("stop_time")),
            }

        # Objetivo y fechas son datos de la CAMPAÑA, no del día: un solo UPDATE
        # por campaña alcanza para poner al día TODOS sus snapshots históricos
        # ya guardados, sin recorrer 180 días de insights (no son backfill diario).
        for camp_id, info in campaigns_map.items():
            await self.db.execute(
                update(CampaignSnapshot)
                .where(
                    CampaignSnapshot.ad_account_id == ad_account.id,
                    CampaignSnapshot.campaign_id == camp_id,
                )
                .values(
                    objective=info.get("objective"),
                    created_time=info.get("created_time"),
                    start_time=info.get("start_time"),
                    stop_time=info.get("stop_time"),
                )
            )

        if "ERROR" in campaigns_insights or "error" in campaigns_insights:
            self._maybe_mark_expired(ad_account, campaigns_insights)
            raise Exception(f"Error al obtener insights de campañas: {campaigns_insights}")

        records_synced = 0
        snapshots_by_campaign: Dict[str, list] = defaultdict(list)
        for item in campaigns_insights.get("data", []):
            camp_id = item.get("campaign_id")
            sync_date = self._parse_date(item.get("date_start"))
            if not camp_id or not sync_date:
                continue

            camp_info = campaigns_map.get(camp_id, {"name": item.get("campaign_name", "Unknown"), "status": "ACTIVE", "budget": 0})
            spend = float(item.get("spend", 0))
            impressions = int(float(item.get("impressions", 0)))
            reach = int(float(item.get("reach", 0)))
            clicks = int(float(item.get("clicks", 0)))
            frequency = float(item.get("frequency", 0))
            conversions, conversations = self._extract_conversions_conversations(item.get("actions"))
            cpl = (spend / conversions) if conversions > 0 else spend
            ctr = (clicks / impressions * 100) if impressions > 0 else 0

            # Upsert idempotente (protegido por uq_snapshot_acc_campaign_date).
            snapshot = (await self.db.execute(select(CampaignSnapshot).where(
                CampaignSnapshot.ad_account_id == ad_account.id,
                CampaignSnapshot.campaign_id == camp_id,
                CampaignSnapshot.date == sync_date,
            ))).scalar_one_or_none()
            if snapshot is None:
                snapshot = CampaignSnapshot(
                    ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                    campaign_id=camp_id, date=sync_date,
                )
                self.db.add(snapshot)
            snapshot.campaign_name = camp_info["name"]
            snapshot.status = camp_info["status"]
            snapshot.effective_status = camp_info.get("effective_status")
            snapshot.budget = camp_info["budget"]
            snapshot.objective = camp_info.get("objective")
            snapshot.created_time = camp_info.get("created_time")
            snapshot.start_time = camp_info.get("start_time")
            snapshot.stop_time = camp_info.get("stop_time")
            snapshot.spend = spend
            snapshot.impressions = impressions
            snapshot.reach = reach
            snapshot.clicks = clicks
            snapshot.conversions = conversions
            snapshot.conversations = conversations
            snapshot.results = self._count_results(item.get("actions"))
            snapshot.cpl = cpl
            snapshot.ctr = ctr
            snapshot.frequency = frequency
            records_synced += 1
            snapshots_by_campaign[camp_id].append(snapshot)

        # 3. Adsets, anuncios e inventario (P-2.2)
        SyncProgressTracker.report_progress(
            ad_account.organization_id, ad_account.id, 3, 5, "adsets", "Sincronizando conjuntos de anuncios y segmentación..."
        )
        adset_budget_by_campaign = await self._sync_real_adsets(
            ad_account, token, limiter, insights_period
        )
        SyncProgressTracker.report_progress(
            ad_account.organization_id, ad_account.id, 4, 5, "ads", "Sincronizando anuncios y creativos..."
        )
        await self._sync_real_ads(ad_account, token, limiter, insights_period)
        if sync_type != SYNC_TYPE_BACKFILL:
            SyncProgressTracker.report_progress(
                ad_account.organization_id, ad_account.id, 5, 5, "creatives", "Sincronizando creativos, páginas e inventario..."
            )
            await self._sync_real_inventory(ad_account, token, limiter)
            await self._sync_real_pages(ad_account, token, limiter)

        # Bug 5: en cuentas ABO el presupuesto vive a nivel adset, así que el
        # campaign-level daily_budget/lifetime_budget viene null/0. Cuando es 0,
        # usamos la suma de los daily_budget de los adsets ACTIVOS de la campaña.
        for camp_id, snaps in snapshots_by_campaign.items():
            if campaigns_map.get(camp_id, {}).get("budget", 0) > 0:
                continue
            adset_sum = adset_budget_by_campaign.get(camp_id, 0.0)
            if adset_sum > 0:
                for snap in snaps:
                    snap.budget = adset_sum

        # 4. Desgloses por placement/edad/género/dispositivo/geografía (P-2.7).
        # No se backfillean: cinco dimensiones × 180 días son el grueso del
        # costo y no alimentan ni Capa 2 ni las preguntas históricas del chat.
        if sync_type != SYNC_TYPE_BACKFILL:
            await self._sync_real_breakdowns(ad_account, token, limiter, insights_period)

        # 5. Histórico de inversión mensual (backfill una vez, luego solo mes en curso).
        if sync_type != SYNC_TYPE_BACKFILL:
            await self._sync_spend_history(ad_account, token, limiter)

        return records_synced

    # ------------------------------------------------------- backfill por meses

    async def _sync_backfill_by_months(
        self, ad_account: AdAccount, limiter: MetaAPIRateLimiter, job: SyncJob
    ) -> int:
        """Baja el histórico faltante un mes calendario por vez.

        Por qué chunkear y no pedir tres años de una sola llamada:
        `time_increment=1` sobre rangos largos a nivel anuncio hace que Meta
        rechace por volumen, y `call_api_paginated` tiene un tope de 50 páginas
        × 500 filas = 25.000 filas tras las cuales **trunca en silencio** — solo
        loguea un warning y devuelve datos parciales sin marca de error, así que
        el job se marcaría COMPLETED con la historia incompleta.

        Qué meses se piden lo decide la reconciliación mensual, no un "¿ya
        corrí?": sobre una cuenta ya completa esto cuesta dos queries y CERO
        llamadas a Meta, así que puede correr todos los días.

        Del más reciente al más viejo: si se corta, lo que quedó bajado es lo
        que más se consulta. Commit por mes, porque un fallo en el mes 20 no
        puede perder los 19 anteriores.
        """
        from app.services.history_reconciliation import get_history_status, month_range
        from app.services.meta_ads.sync_progress import SyncProgressTracker

        status = await get_history_status(self.db, ad_account.organization_id, ad_account.id)
        if status.is_complete:
            logger.info(
                "Backfill de la cuenta %s: nada que traer (%d meses ya cubiertos).",
                ad_account.id, status.months_covered,
            )
            job.payload_json = {"months_total": 0, "months_done": 0, "already_complete": True}
            self.db.add(job)
            return 0

        total = len(status.gaps)
        logger.info(
            "Backfill de la cuenta %s: %d meses faltantes (~%d s estimados).",
            ad_account.id, total, status.estimated_seconds,
        )

        # Los identificadores se copian a variables ANTES del bucle. Abajo hay un
        # rollback, y `rollback()` expira todos los objetos de la sesión — no lo
        # evita `expire_on_commit=False`, que solo aplica al commit. En async,
        # tocar un atributo expirado lanza "greenlet_spawn has not been called":
        # el refresh necesita IO y el acceso al atributo es sincrónico, así que
        # SQLAlchemy no puede awaitarlo.
        acc_id = ad_account.id
        org_id = ad_account.organization_id

        records = 0
        done = 0
        failed = 0
        last_error: Optional[str] = None

        def _avance(ultimo_mes: str) -> Dict[str, Any]:
            """Lo que ve `estado_historial.py` y el banner del frontend.

            Incluye `months_failed` y `last_error` porque sin eso un backfill
            que se queda en "3 de 29" es indistinguible de uno lento: el motivo
            real solo existía en el log del worker, y el job se marcaba
            COMPLETED igual.
            """
            return {
                "months_total": total,
                "months_done": done,
                "months_failed": failed,
                "last_month": ultimo_mes,
                "last_error": last_error,
                "estimated_seconds": status.estimated_seconds,
                # El mes que se está bajando AHORA, y desde cuándo. Sin esto el
                # contador solo se mueve al CERRAR un mes: si Meta está limitando
                # y un mes tarda diez minutos, la pantalla se ve congelada y
                # "lento" es indistinguible de "muerto".
                "month_in_progress": en_curso,
                "month_started_at": (
                    inicio_del_mes.isoformat() if inicio_del_mes else None
                ),
            }

        en_curso: Optional[str] = None
        inicio_del_mes: Optional[datetime.datetime] = None

        for gap in status.gaps:
            first, last = month_range(gap.year_month)

            # Se publica ANTES de pedirle nada a Meta: es el único momento en que
            # la pantalla puede decir en qué mes está mientras el mes dura.
            en_curso = gap.year_month
            inicio_del_mes = datetime.datetime.utcnow()
            job.payload_json = _avance(gap.year_month)
            self.db.add(job)
            await self.db.commit()
            SyncProgressTracker.report_progress(
                organization_id=org_id,
                ad_account_id=acc_id,
                step=done,
                total_steps=total,
                step_name="backfill",
                message=f"Trayendo historial: mes {done + 1} de {total} ({gap.year_month})...",
            )
            period = {
                "time_range": json.dumps(
                    {"since": first.isoformat(), "until": last.isoformat()},
                    separators=(",", ":"),
                )
            }
            try:
                records += await self._sync_real_data(
                    ad_account, SYNC_TYPE_BACKFILL, limiter, insights_period=period
                )
                # Commit por chunk: lo bajado hasta acá queda firme aunque el
                # mes siguiente falle.
                await self.db.commit()
                done += 1
            except Exception as e:
                await self.db.rollback()
                # Rehidratar lo que el rollback expiró, ANTES de volver a tocar
                # sus atributos. Sin esto el except no salvaba nada: el mes
                # siguiente moría en el primer `ad_account.algo`, y el backfill
                # entero se caía con "greenlet_spawn has not been called" en vez
                # de saltear el mes malo.
                await self.db.refresh(job)
                await self.db.refresh(ad_account)
                failed += 1
                last_error = f"{gap.year_month}: {e}"

                classified = classify_meta_error(e)
                if isinstance(classified, TransientMetaError):
                    # Un fallo transitorio (rate limit, 5xx, red) NO es del mes:
                    # es de la CUENTA. Seguir con los 26 meses restantes los
                    # quema todos contra la misma pared y deja el backfill en
                    # "3 de 29" sin haber reintentado nada.
                    #
                    # Al propagar, `run_sync` lo clasifica y Celery reintenta con
                    # backoff (`autoretry_for=(TransientMetaError,)`, ya
                    # configurado en la tarea). El reintento recalcula los huecos:
                    # los meses ya bajados no se vuelven a pedir.
                    logger.warning(
                        "Fallo transitorio bajando el mes %s de la cuenta %s "
                        "(%d de %d meses hechos); se corta para reintentar con backoff: %s",
                        gap.year_month, acc_id, done, total, e,
                    )
                    job.payload_json = _avance(gap.year_month)
                    self.db.add(job)
                    await self.db.commit()
                    raise classified from e

                # Fallo permanente de ESE mes (datos raros, un rango que Meta
                # rechaza): se saltea y se sigue. La reconciliación lo va a
                # volver a detectar en la próxima corrida.
                logger.exception(
                    "Fallo permanente bajando el mes %s de la cuenta %s; se continúa con los demás.",
                    gap.year_month, acc_id,
                )

            en_curso, inicio_del_mes = None, None
            job.payload_json = _avance(gap.year_month)
            self.db.add(job)
            await self.db.commit()

            SyncProgressTracker.report_progress(
                organization_id=org_id,
                ad_account_id=acc_id,
                step=done,
                total_steps=total,
                step_name="backfill",
                message=f"Trayendo historial: {done} de {total} meses...",
            )

        return records

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _insights_period_params(sync_type: str) -> Dict[str, Any]:
        """Parámetros de ventana para los insights diarios.

        BACKFILL acota la consulta en Meta, no después de descargarla: pedir
        ``maximum`` y tirar lo anterior a la retención gastaría llamadas y
        memoria sin ningún beneficio. Meta espera ``time_range`` como JSON.
        """
        if sync_type != SYNC_TYPE_BACKFILL:
            return {"date_preset": "last_30d" if sync_type == "FULL" else "yesterday"}

        today = datetime.date.today()
        oldest = today - datetime.timedelta(days=settings.HOT_DAILY_RETENTION_DAYS - 1)
        return {
            "time_range": json.dumps(
                {"since": oldest.isoformat(), "until": today.isoformat()},
                separators=(",", ":"),
            )
        }

    def _maybe_mark_expired(self, ad_account, res):
        """Si Meta responde OAuthException (code 190), marca la cuenta y su credencial
        como EXPIRED para que el frontend pida reconectar (S-6)."""
        try:
            err = res.get("error", {}) if isinstance(res, dict) else {}
            if err.get("code") == 190:
                ad_account.status = "EXPIRED"
                if ad_account.meta_credential:
                    ad_account.meta_credential.status = "EXPIRED"
                self.db.add(ad_account)
                logger.warning("Token de Meta expirado/ inválido para cuenta %s → EXPIRED.", ad_account.id)
        except Exception:
            pass

    @staticmethod
    def _parse_date(date_str):
        try:
            return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            return None

    @staticmethod
    def _parse_meta_datetime(value):
        """Parsea las fechas ISO 8601 con offset que devuelve Meta
        (`2026-01-15T10:30:00-0500`, sin los dos puntos del offset que exige
        `datetime.fromisoformat` antes de Python 3.11).

        Campo opcional en la respuesta de Meta: si no viene o no se puede
        parsear, devuelve `None` sin romper el sync.
        """
        if not value or not isinstance(value, str):
            return None
        normalized = value.strip()
        if len(normalized) >= 5 and normalized[-5] in "+-" and normalized[-3] != ":":
            normalized = f"{normalized[:-2]}:{normalized[-2:]}"
        try:
            dt = datetime.datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt

    # Whitelist explícita de action types que representan un "lead de formulario".
    # Antes se usaba un fallback difuso (`"lead" in at or "conversion" in at`) que
    # agarraba de casualidad los leads de esta cuenta
    # (`offsite_complete_registration_add_meta_leads`, `offsite_search_add_meta_leads`)
    # pero además podía DOBLE-contar (ambas variantes _add_meta_leads) o agarrar de
    # más (ej. `onsite_conversion.post_net_like` matchea "conversion"). Con la
    # whitelist contamos solo action types de lead reales y sin solapamiento.
    _LEAD_ACTION_TYPES = frozenset({
        "lead",
        "leadgen_grouped",
        "offsite_conversion.fb_pixel_lead",
        "onsite_conversion.lead_grouped",
        "offsite_complete_registration_add_meta_leads",
        "offsite_search_add_meta_leads",
    })

    @classmethod
    def _extract_raw_results(cls, actions):
        """Devuelve `(leads, wa)` CRUDOS, sin ningún proxy.

        Es la base honesta para `resultados = leads + wa`. No se puede sumar
        sobre `_extract_conversions_conversations` porque ese método rellena
        `conversations` con los leads cuando la campaña no tiene mensajería —
        sumar ahí contaría cada lead dos veces.
        """
        leads, wa = 0, 0
        for a in actions or []:
            at = a.get("action_type", "")
            val = int(float(a.get("value", 0) or 0))
            if at in cls._LEAD_ACTION_TYPES:
                leads += val
            if "messaging_conversation_started" in at:
                wa += val
        return leads, wa

    @classmethod
    def _count_results(cls, actions):
        """`resultados = leads + conversaciones de WhatsApp`, sobre valores crudos.

        El documento de ITS es tajante: dividir el gasto de una campaña de
        WhatsApp entre `leads` da cifras absurdas (S/101.78 por lead medido mal
        contra S/3.38 por conversación medido bien). Una cuenta que corre los
        dos tipos a la vez necesita la SUMA, no "el que haya".
        """
        leads, wa = cls._extract_raw_results(actions)
        return leads + wa

    @classmethod
    def _extract_conversions_conversations(cls, actions):
        """Cuenta leads (conversions) y conversaciones de mensajería iniciadas.

        `conversions` suma solo los action types de la whitelist de leads de
        formulario (sin fallback difuso, para no doble-contar ni contar de más).
        `conversations` suma los eventos de mensajería iniciada; si no hay ninguno
        se usan los leads como proxy (cuentas de solo-formulario).
        """
        actions = actions or []
        conversions, conversations = 0, 0
        has_messaging = False
        for a in actions:
            at = a.get("action_type", "")
            val = int(float(a.get("value", 0) or 0))
            if at in cls._LEAD_ACTION_TYPES:
                conversions += val
            if "messaging_conversation_started" in at:
                conversations += val
                has_messaging = True
        if not has_messaging:
            conversations = conversions  # sin evento de mensajería: usar leads como proxy
        return conversions, conversations

    @staticmethod
    def _summarize_targeting(t):
        t = t or {}
        geo = t.get("geo_locations", {})
        regions = [r.get("name") for r in geo.get("regions", []) if r.get("name")]
        return {
            "geo": regions or geo.get("countries", []),
            "age_min": t.get("age_min"),
            "age_max": t.get("age_max"),
            "publisher_platforms": t.get("publisher_platforms", []),
            "advantage_audience": (t.get("targeting_automation", {}) or {}).get("advantage_audience"),
        }

    @staticmethod
    def _has_error(res) -> bool:
        return not isinstance(res, dict) or "error" in res or "ERROR" in res

    async def _sync_real_adsets(self, ad_account, token, limiter, insights_period) -> Dict[str, float]:
        """Sincroniza adsets y devuelve, por campaña, la suma del daily_budget de
        sus adsets ACTIVOS (para el fallback de presupuesto de campaña, Bug 5)."""
        # Estructura (targeting + presupuesto + estado por adset)
        targeting_map = {}
        budget_map: Dict[str, float] = {}      # adset_id -> presupuesto diario nominal
        status_map: Dict[str, str] = {}        # adset_id -> status
        effective_status_map: Dict[str, str] = {}   # adset_id -> effective_status (entrega real)
        campaign_of: Dict[str, str] = {}       # adset_id -> campaign_id
        end_time_map: Dict[str, Any] = {}      # adset_id -> end_time (datetime u None)

        # P-1: estructura e insights son lecturas independientes (insights no
        # depende de la estructura para pedirse, solo para combinarse después).
        structure, insights = await asyncio.gather(
            self._structure_once("adsets", lambda: limiter.call_api_paginated(
                f"{ad_account.external_account_id}/adsets",
                {"fields": "id,name,campaign_id,targeting,daily_budget,lifetime_budget,status,effective_status,end_time",
                 "limit": 200},
                access_token=token,
            )),
            limiter.call_api_paginated(
                f"{ad_account.external_account_id}/insights",
                {"level": "adset",
                 "fields": "adset_id,adset_name,campaign_id,spend,impressions,reach,clicks,actions,frequency",
                 "time_increment": 1, "limit": 500, **insights_period},
                access_token=token,
            ),
        )

        if not self._has_error(structure):
            for a in structure.get("data", []):
                aid = a.get("id")
                targeting_map[aid] = self._summarize_targeting(a.get("targeting", {}))
                budget_map[aid] = float(a.get("daily_budget") or a.get("lifetime_budget") or 0) / 100.0
                status_map[aid] = a.get("status")
                effective_status_map[aid] = a.get("effective_status")
                campaign_of[aid] = a.get("campaign_id")
                end_time_map[aid] = self._parse_meta_datetime(a.get("end_time"))
        else:
            self._maybe_mark_expired(ad_account, structure)

        # Suma de presupuesto de adsets ACTIVOS por campaña (Bug 5).
        adset_budget_by_campaign: Dict[str, float] = defaultdict(float)
        for aid, camp_id in campaign_of.items():
            if camp_id and status_map.get(aid) == "ACTIVE":
                adset_budget_by_campaign[camp_id] += budget_map.get(aid, 0.0)

        if self._has_error(insights):
            logger.warning("No se pudieron obtener insights de adsets: %s", insights)
            self._maybe_mark_expired(ad_account, insights)
            return dict(adset_budget_by_campaign)

        for item in insights.get("data", []):
            adset_id = item.get("adset_id")
            sync_date = self._parse_date(item.get("date_start"))
            if not adset_id or not sync_date:
                continue
            spend = float(item.get("spend", 0)); impressions = int(float(item.get("impressions", 0)))
            reach = int(float(item.get("reach", 0))); clicks = int(float(item.get("clicks", 0)))
            conversions, conversations = self._extract_conversions_conversations(item.get("actions"))
            cpl = spend / conversions if conversions > 0 else spend
            ctr = (clicks / impressions * 100) if impressions > 0 else 0

            row = (await self.db.execute(select(AdsetSnapshot).where(
                AdsetSnapshot.ad_account_id == ad_account.id,
                AdsetSnapshot.adset_id == adset_id,
                AdsetSnapshot.date == sync_date,
            ))).scalar_one_or_none()
            if row is None:
                row = AdsetSnapshot(ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                                    adset_id=adset_id, date=sync_date)
                self.db.add(row)
            row.campaign_id = item.get("campaign_id", "")
            row.name = item.get("adset_name", "")
            row.spend = spend; row.impressions = impressions; row.reach = reach; row.clicks = clicks
            row.conversions = conversions; row.conversations = conversations; row.cpl = cpl; row.ctr = ctr
            row.results = self._count_results(item.get("actions"))
            row.targeting_summary = targeting_map.get(adset_id, {})
            row.effective_status = effective_status_map.get(adset_id)
            row.budget = budget_map.get(adset_id, 0.0)
            row.end_time = end_time_map.get(adset_id)

        return dict(adset_budget_by_campaign)

    async def _sync_real_ads(self, ad_account, token, limiter, insights_period):
        # Mapa ad_id -> {creative_id, title, body, cta_type}. El copy real
        # (title/body/call_to_action_type) del anuncio ganador nunca se
        # capturaba — solo la imagen (MetaCreative) — así que la IA no podía
        # inspirarse en el texto que ya funcionó (Fase 3B).
        creative_map = {}
        # P-1: estructura de ads (copy/creativo) e insights son lecturas
        # independientes — en paralelo en vez de una tras otra.
        ads_struct, insights = await asyncio.gather(
            self._structure_once("ads", lambda: limiter.call_api_paginated(
                f"{ad_account.external_account_id}/ads",
                {"fields": "id,effective_status,creative{id,title,body,call_to_action_type}", "limit": 200}, access_token=token,
            )),
            limiter.call_api_paginated(
                f"{ad_account.external_account_id}/insights",
                {"level": "ad",
                 "fields": "ad_id,ad_name,adset_id,campaign_id,spend,impressions,reach,clicks,actions",
                 "time_increment": 1, "limit": 500, **insights_period},
                access_token=token,
            ),
        )
        if not self._has_error(ads_struct):
            for a in ads_struct.get("data", []):
                creative = a.get("creative", {}) or {}
                creative_map[a.get("id")] = {
                    "id": creative.get("id"),
                    "title": creative.get("title"),
                    "body": creative.get("body"),
                    "cta_type": creative.get("call_to_action_type"),
                    "effective_status": a.get("effective_status"),
                }

        if self._has_error(insights):
            logger.warning("No se pudieron obtener insights de anuncios: %s", insights)
            self._maybe_mark_expired(ad_account, insights)
            return

        for item in insights.get("data", []):
            ad_id = item.get("ad_id")
            sync_date = self._parse_date(item.get("date_start"))
            if not ad_id or not sync_date:
                continue
            spend = float(item.get("spend", 0)); impressions = int(float(item.get("impressions", 0)))
            reach = int(float(item.get("reach", 0))); clicks = int(float(item.get("clicks", 0)))
            conversions, conversations = self._extract_conversions_conversations(item.get("actions"))
            cpl = spend / conversions if conversions > 0 else spend
            ctr = (clicks / impressions * 100) if impressions > 0 else 0

            row = (await self.db.execute(select(AdSnapshot).where(
                AdSnapshot.ad_account_id == ad_account.id,
                AdSnapshot.ad_id == ad_id,
                AdSnapshot.date == sync_date,
            ))).scalar_one_or_none()
            if row is None:
                row = AdSnapshot(ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                                 ad_id=ad_id, date=sync_date)
                self.db.add(row)
            row.campaign_id = item.get("campaign_id", "")
            row.adset_id = item.get("adset_id", "")
            creative_info = creative_map.get(ad_id) or {}
            row.creative_id = creative_info.get("id")
            row.creative_title = creative_info.get("title")
            row.creative_body = creative_info.get("body")
            row.creative_cta_type = creative_info.get("cta_type")
            row.effective_status = creative_info.get("effective_status")
            row.name = item.get("ad_name", "")
            row.spend = spend; row.impressions = impressions; row.reach = reach; row.clicks = clicks
            row.conversions = conversions; row.conversations = conversations; row.cpl = cpl; row.ctr = ctr
            row.results = self._count_results(item.get("actions"))

    async def _sync_real_inventory(self, ad_account, token, limiter):
        # P-1: imágenes y audiencias son lecturas independientes entre sí —
        # en paralelo en vez de una tras otra.
        imgs, auds = await asyncio.gather(
            limiter.call_api_paginated(
                f"{ad_account.external_account_id}/adimages",
                {"fields": "hash,name,url,width,height,created_time", "limit": "50"},
                access_token=token,
            ),
            limiter.call_api_paginated(
                f"{ad_account.external_account_id}/customaudiences",
                # `approximate_count` ya no existe en la Graph API (confirmado contra la
                # API real: pedirlo devuelve OAuthException code 100 "Tried accessing
                # nonexisting field") — Meta lo reemplazó por lower/upper bound.
                {"fields": "id,name,subtype,approximate_count_lower_bound,approximate_count_upper_bound,time_created,description"},
                access_token=token,
            ),
        )

        # Imágenes / creativos
        if self._has_error(imgs):
            logger.warning("No se pudo leer el inventario de creativos (cuenta %s): %s",
                           ad_account.id, imgs)
            self._maybe_mark_expired(ad_account, imgs)
        else:
            seen_hashes = set()
            for im in imgs.get("data", []):
                h = im.get("hash")
                if not h or h in seen_hashes:
                    continue
                seen_hashes.add(h)
                row = (await self.db.execute(select(MetaCreative).where(
                    MetaCreative.ad_account_id == ad_account.id, MetaCreative.image_hash == h,
                ))).scalar_one_or_none()
                if row is None:
                    row = MetaCreative(ad_account_id=ad_account.id, organization_id=ad_account.organization_id, image_hash=h)
                    self.db.add(row)
                row.name = im.get("name")
                row.url = im.get("url")
                row.width = im.get("width")
                row.height = im.get("height")

        # Audiencias personalizadas. Nota: muchas cuentas que solo usan Advantage+ o
        # segmentación por intereses no tienen audiencias guardadas — en ese caso Meta
        # responde una lista vacía (no un error). Un error aquí suele ser permiso del
        # token (falta ads_read / customaudience:read), por eso se distingue en el log.
        if self._has_error(auds):
            logger.warning("No se pudieron leer audiencias personalizadas (cuenta %s): "
                           "posible falta de permiso del token. Respuesta: %s", ad_account.id, auds)
            self._maybe_mark_expired(ad_account, auds)
        else:
            data = auds.get("data", [])
            if not data:
                logger.info("La cuenta %s no tiene audiencias personalizadas guardadas en Meta.",
                            ad_account.id)
            for au in data:
                ext = au.get("id")
                if not ext:
                    continue
                row = (await self.db.execute(select(CustomAudience).where(
                    CustomAudience.ad_account_id == ad_account.id, CustomAudience.external_id == ext,
                ))).scalar_one_or_none()
                if row is None:
                    row = CustomAudience(ad_account_id=ad_account.id, organization_id=ad_account.organization_id, external_id=ext)
                    self.db.add(row)
                row.name = au.get("name", "")
                row.subtype = au.get("subtype")
                row.description = au.get("description")
                row.approximate_count = au.get("approximate_count_lower_bound")

    async def _sync_real_pages(self, ad_account, token, limiter):
        """Fase 4 (plan de creación de campañas): descubre las páginas accesibles
        al token y el WhatsApp conectado a cada una, para que la UI ofrezca un
        dropdown de página en vez de que el usuario tipee `page_id`/`whatsapp_number`
        a mano (fuente del bug de WhatsApp de la reunión con Alejandro: "aparece un
        código en vez del número").

        Descubrimiento multi-fuente (caso real "Althea pack", 15-jul-2026: la
        página que Ads Manager ofrece promocionar no salía en `/me/accounts`
        porque el usuario del token no tenía rol directo en ella):
          1. `/me/accounts` — páginas con rol directo del usuario del token.
          2. `/me/businesses` → `{biz}/owned_pages` + `{biz}/client_pages` —
             páginas propias y de clientes de sus Business Manager.
          3. `act_X/promote_pages` — páginas que la cuenta publicitaria puede
             promocionar.
        Cada fuente es tolerante a fallo individual (log y continuar, mismo
        patrón que los breakdowns); se dedupe por `external_page_id`.

        El WhatsApp se pide en una llamada aparte POR página: si el campo no
        está soportado o falla para una página, solo esa página se queda sin
        número (no debe tumbar el descubrimiento de las demás — misma lección
        que el bug de `customaudiences`/`approximate_count`, HANDOFF 3.8).
        """
        discovered: Dict[str, dict] = {}
        # Las filas de MetaPage que toca ESTA cuenta, para acotar su alcance al
        # final (ver `_reconciliar_alcance`).
        filas_vistas: list = []

        def _collect(res, source: str):
            if self._has_error(res):
                logger.warning("Descubrimiento de páginas: fuente '%s' no disponible (cuenta %s): %s",
                               source, ad_account.id, res)
                self._maybe_mark_expired(ad_account, res)
                return
            for p in res.get("data", []):
                ext_id = p.get("id")
                if not ext_id:
                    continue
                entry = discovered.setdefault(ext_id, {})
                # No pisar con vacío lo que otra fuente ya aportó.
                if p.get("name"):
                    entry["name"] = p["name"]
                ig_id = (p.get("connected_instagram_account") or {}).get("id")
                if ig_id:
                    entry["instagram_actor_id"] = ig_id
                # Token de página: solo lo devuelve /me/accounts, y solo si se
                # pide explícitamente. Es lo único con lo que se puede leer
                # `whatsapp_number` (ver abajo). No se persiste — vive en
                # memoria durante este sync y nada más.
                if p.get("access_token"):
                    entry["page_token"] = p["access_token"]

        # FUENTE PRINCIPAL: lo que ESTA cuenta puede promocionar. Es la
        # respuesta de Meta a la pregunta que nos importa —"¿qué página le
        # adjunto a una campaña de esta cuenta?"— y viene acotada por cuenta.
        _collect(await limiter.call_api(
            f"{ad_account.external_account_id}/promote_pages",
            {"fields": "id,name", "limit": "100"},
            access_token=token,
        ), "promote_pages")

        # FUENTE SECUNDARIA: el Business DUEÑO de esta cuenta, y solo ese.
        #
        # Antes se recorrían TODOS los business del usuario del token
        # (`/me/businesses`) más `/me/accounts`. En una agencia eso es un leak
        # entre clientes: al cliente A le aparecían las páginas de los clientes
        # B, C y D. Caso real, con un token correctamente acotado a una sola
        # cuenta: `promote_pages` devolvía 1 página y la app mostraba 5, de
        # cuatro negocios distintos.
        #
        # Se conserva esta fuente porque `promote_pages` no siempre trae todo
        # (caso "Althea pack": la página que Ads Manager ofrecía promocionar no
        # figuraba en las fuentes directas), pero acotada al business correcto.
        business_ids = []
        acc_info = await limiter.call_api(
            ad_account.external_account_id, {"fields": "business"}, access_token=token,
        )
        owner_biz = ((acc_info or {}).get("business") or {}).get("id") if not self._has_error(acc_info) else None
        if owner_biz:
            business_ids.append(owner_biz)
            for edge in ("owned_pages", "client_pages"):
                _collect(await limiter.call_api(
                    f"{owner_biz}/{edge}",
                    {"fields": "id,name,connected_instagram_account", "limit": "100"},
                    access_token=token,
                ), f"{owner_biz}/{edge}")
        else:
            logger.warning(
                "La cuenta %s no declara business dueño; solo se usan las páginas "
                "que puede promocionar.", ad_account.id,
            )

        # El token de página hace falta para leer el WhatsApp, y solo lo
        # devuelve /me/accounts. Se pide DESPUÉS y se usa únicamente para
        # enriquecer las páginas ya descubiertas: nunca para agregar otras.
        me_res = await limiter.call_api(
            "me/accounts",
            {"fields": "name,id,category,connected_instagram_account,access_token", "limit": "100"},
            access_token=token,
        )
        if not self._has_error(me_res):
            for p in me_res.get("data", []):
                ext_id = p.get("id")
                if ext_id in discovered:
                    if p.get("access_token"):
                        discovered[ext_id]["page_token"] = p["access_token"]
                    ig_id = (p.get("connected_instagram_account") or {}).get("id")
                    if ig_id:
                        discovered[ext_id].setdefault("instagram_actor_id", ig_id)

        await self._sync_whatsapp_numbers(ad_account, token, limiter, business_ids)

        for ext_id, info in discovered.items():
            row = (await self.db.execute(select(MetaPage).where(
                MetaPage.organization_id == ad_account.organization_id,
                MetaPage.external_page_id == ext_id,
            ))).scalar_one_or_none()
            if row is None:
                row = MetaPage(organization_id=ad_account.organization_id, external_page_id=ext_id)
                self.db.add(row)
            if info.get("name"):
                row.name = info["name"]
            elif not row.name:
                row.name = ""
            if info.get("instagram_actor_id"):
                row.instagram_actor_id = info["instagram_actor_id"]
            row.status = "ACTIVE"
            filas_vistas.append(row)

            # `whatsapp_number` del nodo Page se lee con el TOKEN DE LA PÁGINA,
            # no con el del usuario: con el de usuario Meta responde error y el
            # número quedaba siempre vacío, obligando a cargarlo a mano aunque
            # el token tuviera todos los permisos. El de usuario queda como
            # fallback para las páginas que llegaron por los Business Manager o
            # por promote_pages, donde Meta no entrega token de página.
            page_token = info.get("page_token")
            wa_res = await limiter.call_api(
                ext_id, {"fields": "whatsapp_number"}, access_token=page_token or token,
            )
            if self._has_error(wa_res):
                logger.info(
                    "La página %s no expone whatsapp_number (token de %s): %s",
                    ext_id, "página" if page_token else "usuario", wa_res,
                )
            elif wa_res.get("whatsapp_number"):
                # Solo sobreescribir con un valor real: si Meta no devuelve el
                # campo (página sin número vinculado), NO borrar el número
                # cargado a mano vía PATCH /my/pages/{id}/whatsapp — antes el
                # sync nocturno lo pisaba con None.
                row.whatsapp_number = wa_res.get("whatsapp_number")

        # Qué páginas alcanza ESTA cuenta. `MetaPage` sigue siendo por
        # organización —una página puede promocionarse desde dos cuentas del
        # mismo cliente— pero el enlace por cuenta es lo que permite que el
        # dropdown de "Crear campaña" muestre las de la cuenta y no las de todos
        # los clientes de la organización.
        #
        # El flush va antes: `MetaPage.id` se genera al volcar, así que sin esto
        # las páginas nuevas de este sync entrarían al enlace con id `None`.
        await self.db.flush()
        await self._reconciliar_alcance(
            MetaPageAdAccount, "meta_page_id", ad_account,
            {fila.id for fila in filas_vistas},
        )

        # Se devuelve QUÉ se vio. El estado ACTIVE/REVOKED de la página no se
        # poda acá: eso es por organización y una cuenta no sabe lo que ven las
        # demás, así que vive en `refresh_org_pages`, que sí las recorre todas.
        return set(discovered)

    async def _sync_whatsapp_numbers(self, ad_account, token, limiter, business_ids):
        """Descubre los números de WhatsApp verificados del negocio (F1).

        Ruta: `{biz}/owned_whatsapp_business_accounts` + `client_whatsapp_...`
        → `{waba}/phone_numbers`.

        Necesita el permiso `whatsapp_business_management`, que la MAYORÍA de
        los tokens conectados no trae — por eso todo aquí es best-effort y la
        carga manual sigue siendo el camino principal, no un plan B. Un fallo
        aquí no debe tumbar el descubrimiento de páginas (misma lección que
        `customaudiences`/`approximate_count`, HANDOFF 3.8).
        """
        from app.models.whatsapp_number import WhatsAppNumber

        waba_ids = []
        for biz_id in business_ids:
            for edge in ("owned_whatsapp_business_accounts", "client_whatsapp_business_accounts"):
                res = await limiter.call_api(
                    f"{biz_id}/{edge}", {"fields": "id,name", "limit": "50"}, access_token=token,
                )
                if self._has_error(res):
                    logger.info(
                        "Números de WhatsApp: '%s' no disponible para el negocio %s "
                        "(¿falta el permiso whatsapp_business_management?): %s",
                        edge, biz_id, res,
                    )
                    continue
                waba_ids.extend(w["id"] for w in res.get("data", []) if w.get("id"))

        filas_vistas: list = []
        for waba_id in dict.fromkeys(waba_ids):  # dedup preservando orden
            res = await limiter.call_api(
                f"{waba_id}/phone_numbers",
                {"fields": "id,display_phone_number,verified_name", "limit": "50"},
                access_token=token,
            )
            if self._has_error(res):
                logger.info("Números de WhatsApp: no se pudo leer phone_numbers de %s: %s",
                            waba_id, res)
                continue

            for phone in res.get("data", []):
                number = (phone.get("display_phone_number") or "").strip()
                if not number:
                    continue
                ext_id = phone.get("id")

                # Se busca por external_id (clave estable de Meta) y, si no, por
                # el número: si el usuario ya lo había cargado a mano, se
                # "promueve" a META en vez de duplicarlo en la lista.
                row = (await self.db.execute(select(WhatsAppNumber).where(
                    WhatsAppNumber.organization_id == ad_account.organization_id,
                    WhatsAppNumber.external_id == ext_id,
                ))).scalar_one_or_none() if ext_id else None
                if row is None:
                    row = (await self.db.execute(select(WhatsAppNumber).where(
                        WhatsAppNumber.organization_id == ad_account.organization_id,
                        WhatsAppNumber.display_number == number,
                    ))).scalar_one_or_none()
                if row is None:
                    row = WhatsAppNumber(organization_id=ad_account.organization_id)
                    self.db.add(row)

                row.display_number = number
                row.external_id = ext_id
                row.verified_name = phone.get("verified_name")
                row.source = "META"
                row.status = "ACTIVE"
                filas_vistas.append(row)

        # Igual que con las páginas: qué números alcanza ESTA cuenta. Un número
        # de WhatsApp ajeno en un anuncio no es un dato de más — es que los
        # mensajes del cliente A le lleguen al B.
        #
        # Los MANUAL no pasan por acá y por eso no se los reconcilia: no hay
        # nada en Meta contra qué compararlos. Su alcance se fija donde se
        # cargan y se corrige a mano en Configuración.
        await self.db.flush()
        await self._reconciliar_alcance(
            WhatsAppNumberAdAccount, "whatsapp_number_id", ad_account,
            {fila.id for fila in filas_vistas},
        )

    # Cada dimensión de breakdown es una llamada aparte: la Graph API no permite
    # combinar publisher_platform+platform_position con age/gender/region en una
    # sola query de insights. bucket = destino lógico; key_of = clave de la fila.
    _BREAKDOWN_SPECS = [
        ("publisher_platform,platform_position", "placements",
         lambda r: "_".join(p for p in (r.get("publisher_platform"), r.get("platform_position")) if p)),
        ("age", "age", lambda r: r.get("age") or ""),
        ("gender", "gender", lambda r: r.get("gender") or ""),
        ("impression_device", "device", lambda r: r.get("impression_device") or ""),
        ("region", "region", lambda r: r.get("region") or ""),
    ]

    async def _sync_real_breakdowns(self, ad_account, token, limiter, insights_period):
        """Trae los desgloses (placement, edad, género, dispositivo, geografía) desde
        Meta y los fusiona en los snapshots de campaña ya creados en este sync. Cada
        dimensión es un request independiente; si Meta rechaza uno (permiso o combinación
        no permitida) se registra y se continúa, sin abortar el sync completo."""
        # (campaign_id, date) -> {placements:{}, age:{}, gender:{}, device:{}, region:{}}
        acc_map: Dict[Any, Dict[str, dict]] = defaultdict(
            lambda: {"placements": {}, "age": {}, "gender": {}, "device": {}, "region": {}}
        )

        # P-1: las 5 dimensiones son requests completamente independientes
        # entre sí (Meta no permite combinarlas en una sola query) — antes se
        # pedían una tras otra; ahora en paralelo.
        results = await asyncio.gather(*(
            limiter.call_api_paginated(
                f"{ad_account.external_account_id}/insights",
                {"level": "campaign", "time_increment": 1,
                 "fields": "campaign_id,spend,actions", "breakdowns": bd_param, "limit": 500,
                 **insights_period},
                access_token=token,
            )
            for bd_param, _bucket, _key_of in self._BREAKDOWN_SPECS
        ))

        for (bd_param, bucket, key_of), res in zip(self._BREAKDOWN_SPECS, results):
            if self._has_error(res):
                logger.warning("Breakdown '%s' no disponible para cuenta %s: %s",
                               bd_param, ad_account.id, res)
                self._maybe_mark_expired(ad_account, res)
                continue
            for row in res.get("data", []):
                camp_id = row.get("campaign_id")
                sync_date = self._parse_date(row.get("date_start"))
                key = key_of(row)
                if not camp_id or not sync_date or not key:
                    continue
                # En cuentas de mensajería, cada conversación es la conversión relevante.
                _, conv = self._extract_conversions_conversations(row.get("actions"))
                entry = acc_map[(camp_id, sync_date)][bucket].setdefault(key, {"spend": 0.0, "conversions": 0})
                entry["spend"] += float(row.get("spend", 0) or 0)
                entry["conversions"] += conv

        for (camp_id, sync_date), buckets in acc_map.items():
            snap = (await self.db.execute(select(CampaignSnapshot).where(
                CampaignSnapshot.ad_account_id == ad_account.id,
                CampaignSnapshot.campaign_id == camp_id,
                CampaignSnapshot.date == sync_date,
            ))).scalar_one_or_none()
            if snap is None:
                continue
            for grp in buckets.values():
                for e in grp.values():
                    e["spend"] = round(e["spend"], 2)
            snap.placements_breakdown = buckets["placements"]
            snap.demographics_breakdown = buckets["age"]
            snap.extra_breakdowns = {
                "gender": buckets["gender"], "device": buckets["device"], "region": buckets["region"],
            }

    # ------------------------------------------------------------- histórico mensual

    async def _sync_spend_history(self, ad_account: AdAccount, token: str, limiter: MetaAPIRateLimiter) -> None:
        """Gasto total por mes calendario, sin desglose por campaña.

        Backfill de una sola vez por cuenta con `time_increment=monthly` (una
        fila por mes, no por día — barato incluso para cuentas de varios años).
        En syncs siguientes solo se pide el mes en curso (`date_preset=this_month`,
        una fila) — el histórico completo nunca se vuelve a bajar.
        """
        current_ym = datetime.date.today().strftime("%Y-%m")

        has_history = (await self.db.execute(
            select(AdAccountSpendMonthly.id)
            .where(AdAccountSpendMonthly.ad_account_id == ad_account.id)
            .limit(1)
        )).scalar_one_or_none()

        if not has_history:
            res = await limiter.call_api_paginated(
                f"{ad_account.external_account_id}/insights",
                {"fields": "spend,impressions,reach,actions", "time_increment": "monthly",
                 "date_preset": "maximum", "limit": 100},
                access_token=token,
            )
            if self._has_error(res):
                logger.warning("No se pudo obtener el histórico mensual de gasto (cuenta %s): %s",
                               ad_account.id, res)
                self._maybe_mark_expired(ad_account, res)
                return
            for row in res.get("data", []):
                sync_date = self._parse_date(row.get("date_start"))
                if not sync_date:
                    continue
                ym = sync_date.strftime("%Y-%m")
                await self._upsert_spend_month(
                    ad_account, ym, self._monthly_metrics(row), closed=(ym != current_ym)
                )
            return

        # Backfill ya hecho: cerrar perezosamente meses viejos y refrescar solo el actual.
        await self.db.execute(
            update(AdAccountSpendMonthly)
            .where(
                AdAccountSpendMonthly.ad_account_id == ad_account.id,
                AdAccountSpendMonthly.year_month < current_ym,
                AdAccountSpendMonthly.closed.is_(False),
            )
            .values(closed=True)
        )

        res = await limiter.call_api(
            f"{ad_account.external_account_id}/insights",
            {"fields": "spend,impressions,reach,actions", "date_preset": "this_month"},
            access_token=token,
        )
        if self._has_error(res):
            logger.warning("No se pudo actualizar el gasto del mes en curso (cuenta %s): %s",
                           ad_account.id, res)
            self._maybe_mark_expired(ad_account, res)
            return
        data = res.get("data", [])
        metrics = self._monthly_metrics(data[0]) if data else {"spend": 0.0, "impressions": 0, "reach": 0, "results": 0}
        await self._upsert_spend_month(ad_account, current_ym, metrics, closed=False)

    def _monthly_metrics(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Extrae del insight mensual lo que se guarda. `results` sale de
        `actions` con el mismo criterio que el sync diario, para que el agregado
        mensual y la suma de los dias hablen de lo mismo."""
        return {
            "spend": float(row.get("spend", 0) or 0),
            "impressions": int(float(row.get("impressions", 0) or 0)),
            "reach": int(float(row.get("reach", 0) or 0)),
            "results": self._count_results(row.get("actions") or []),
        }

    async def _upsert_spend_month(self, ad_account: AdAccount, year_month: str, metrics: Dict[str, Any], closed: bool) -> None:
        row = (await self.db.execute(select(AdAccountSpendMonthly).where(
            AdAccountSpendMonthly.ad_account_id == ad_account.id,
            AdAccountSpendMonthly.year_month == year_month,
        ))).scalar_one_or_none()
        if row is None:
            row = AdAccountSpendMonthly(
                ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                year_month=year_month,
            )
            self.db.add(row)
        row.spend = metrics["spend"]
        row.impressions = metrics["impressions"]
        row.reach = metrics["reach"]
        row.results = metrics["results"]
        row.closed = closed

    async def _sync_mock_spend_history(self, ad_account: AdAccount) -> None:
        """Genera 18 meses de histórico ficticio (una sola vez por cuenta) para
        poder probar la línea de tiempo del dashboard en modo mock."""
        has_history = (await self.db.execute(
            select(AdAccountSpendMonthly.id)
            .where(AdAccountSpendMonthly.ad_account_id == ad_account.id)
            .limit(1)
        )).scalar_one_or_none()
        if has_history:
            return

        today = datetime.date.today()
        current_ym = today.strftime("%Y-%m")
        for i in range(17, -1, -1):
            year, month = today.year, today.month - i
            while month <= 0:
                month += 12
                year -= 1
            ym = f"{year:04d}-{month:02d}"
            self.db.add(AdAccountSpendMonthly(
                ad_account_id=ad_account.id, organization_id=ad_account.organization_id,
                year_month=ym, spend=round(random.uniform(800, 2500), 2), closed=(ym != current_ym),
            ))
