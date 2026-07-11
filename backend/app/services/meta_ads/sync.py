import datetime
import random
import logging
from collections import defaultdict
from typing import List, Dict, Any
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
from app.models.sync_job import SyncJob
from app.models.ad_account_spend_monthly import AdAccountSpendMonthly
from app.services.meta_ads.rate_limiter import MetaAPIRateLimiter
from app.services.meta_ads.exceptions import TransientMetaError, classify_meta_error

logger = logging.getLogger("lejovista.sync")

class MetaAdsSyncService:
    def __init__(self, db: AsyncSession):
        self.db = db

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

        try:
            limiter = MetaAPIRateLimiter(ad_account_id=ad_account.id)
            
            if settings.MOCK_SERVICES:
                records_synced = await self._sync_mock_data(ad_account, sync_type, limiter)
            else:
                records_synced = await self._sync_real_data(ad_account, sync_type, limiter)
                
            job.status = "COMPLETED"
            job.records_synced = records_synced
            job.completed_at = datetime.datetime.utcnow()
        except Exception as e:
            logger.exception(f"Error en la sincronización {sync_type} de cuenta {ad_account.id}")
            job.status = "FAILED"
            job.error_message = str(e)
            job.completed_at = datetime.datetime.utcnow()
            await self.db.commit()

            classified = classify_meta_error(e)
            if isinstance(classified, TransientMetaError):
                # Propagar para que la tarea de Celery reintente con backoff.
                raise classified from e

            # Fallo permanente: no reintentar. Alertar al equipo (log de nivel error
            # que un colector como Sentry puede capturar).
            logger.error(
                "Sync permanentemente fallido para cuenta %s (org %s): %s",
                ad_account.id, ad_account.organization_id, e
            )
            return job

        await self.db.commit()
        return job

    async def _sync_mock_data(self, ad_account: AdAccount, sync_type: str, limiter: MetaAPIRateLimiter) -> int:
        """
        Genera datos mockeados realistas para 3 campañas de ejemplo con 30 días de historia.
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
        
        # 15 queries en full, 4 en incremental
        num_queries = 15 if sync_type == "FULL" else 4
        for i in range(2, num_queries + 1):
            await limiter.call_api(f"act_{ad_account.external_account_id}/insights", {"query_num": i})

        # Definir campañas mockeadas
        campaign_definitions = [
            {
                "id": "camp_cable_gpt",
                "name": "MTV_MSJ_CABLE_GPT_CERCADO_JUN2026",
                "status": "ACTIVE",
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
            },
            {
                "id": "camp_vulcanizado",
                "name": "MTV_MSJ_VULCANIZADO_SURQUILLO_JUN2026",
                "status": "ACTIVE",
                "budget": 50.0,
                "ctr_base": 0.6,
                "cpl_target": 4.50,
                "high_frequency": True
            },
            {
                "id": "camp_thhn",
                "name": "MTV_MSJ_THHN_LIMA_JUN2026",
                "status": "ACTIVE",
                "budget": 40.0,
                "ctr_base": 0.25,
                "cpl_target": 12.00,
                "failing": True
            }
        ]

        # Determinar el rango de días a sincronizar
        # En FULL creamos 30 días. En INCREMENTAL sólo ayer y hoy.
        days_to_sync = 30 if sync_type == "FULL" else 2
        today = datetime.date.today()
        
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
                conversations = conversions  # en campañas de mensajes, cada lead es una conversación
                reach = int(impressions / max(frequency, 1.0))
                cpl = spend / conversions if conversions > 0 else spend
                ctr = (clicks / impressions) * 100.0 if impressions > 0 else 0.0

                # Crear breakdowns ficticios
                if c.get("reels_better"):
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

                demographics = {
                    "25-34": {"spend": spend * 0.4, "conversions": int(conversions * 0.5)},
                    "35-44": {"spend": spend * 0.3, "conversions": int(conversions * 0.3)},
                    "45-54": {"spend": spend * 0.2, "conversions": int(conversions * 0.15)},
                    "55-64": {"spend": spend * 0.1, "conversions": int(conversions * 0.05)}
                }

                # P-2: breakdowns de género, dispositivo y geografía.
                extra = {
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
                    budget=c["budget"],
                    spend=spend,
                    impressions=impressions,
                    reach=reach,
                    clicks=clicks,
                    conversions=conversions,
                    conversations=conversations,
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

        # --- Inventario de creativos y audiencias (una vez por cuenta) ---
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

    async def _sync_real_data(self, ad_account: AdAccount, sync_type: str, limiter: MetaAPIRateLimiter) -> int:
        """
        Lógica real de producción usando llamadas a la Graph API de Meta Ads.
        """
        # NOTA: En producción, aquí se realizarían las queries 1-15 de la API.
        # Por ahora definimos la estructura básica. Cuando se conectan credenciales reales,
        # el rate limiter realiza los HTTP requests auténticos.
        token = ad_account.get_access_token()
        if not token:
            raise ValueError("Token de acceso de la cuenta no válido o expirado.")

        # Obtener información de la cuenta
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

        # 1. Obtener todas las campañas de la cuenta
        campaigns_res = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/campaigns",
            {
                "fields": "id,name,status,daily_budget,lifetime_budget",
                "limit": 200,
            },
            access_token=token
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
                "budget": budget
            }

        # 2. Insights diarios a nivel campaña (ahora con reach)
        date_preset = "last_30d" if sync_type == "FULL" else "yesterday"

        campaigns_insights = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/insights",
            {
                "level": "campaign",
                "date_preset": date_preset,
                "fields": "campaign_id,campaign_name,spend,impressions,reach,clicks,actions,frequency",
                "time_increment": 1,  # datos diarios
                # Sin `limit`, Meta devuelve su página default de 25 filas: con
                # ~30 días × ~10 campañas eso truncaba a los 25 días más viejos.
                # Subimos el tamaño de página y además paginamos (call_api_paginated).
                "limit": 500,
            },
            access_token=token
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
            snapshot.budget = camp_info["budget"]
            snapshot.spend = spend
            snapshot.impressions = impressions
            snapshot.reach = reach
            snapshot.clicks = clicks
            snapshot.conversions = conversions
            snapshot.conversations = conversations
            snapshot.cpl = cpl
            snapshot.ctr = ctr
            snapshot.frequency = frequency
            records_synced += 1
            snapshots_by_campaign[camp_id].append(snapshot)

        # 3. Adsets, anuncios e inventario (P-2.2)
        adset_budget_by_campaign = await self._sync_real_adsets(
            ad_account, token, limiter, date_preset
        )
        await self._sync_real_ads(ad_account, token, limiter, date_preset)
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
        await self._sync_real_breakdowns(ad_account, token, limiter, date_preset)

        # 5. Histórico de inversión mensual (backfill una vez, luego solo mes en curso).
        await self._sync_spend_history(ad_account, token, limiter)

        return records_synced

    # ------------------------------------------------------------------ helpers

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

    async def _sync_real_adsets(self, ad_account, token, limiter, date_preset) -> Dict[str, float]:
        """Sincroniza adsets y devuelve, por campaña, la suma del daily_budget de
        sus adsets ACTIVOS (para el fallback de presupuesto de campaña, Bug 5)."""
        # Estructura (targeting + presupuesto + estado por adset)
        targeting_map = {}
        budget_map: Dict[str, float] = {}      # adset_id -> presupuesto diario nominal
        status_map: Dict[str, str] = {}        # adset_id -> status
        campaign_of: Dict[str, str] = {}       # adset_id -> campaign_id
        structure = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/adsets",
            {"fields": "id,name,campaign_id,targeting,daily_budget,lifetime_budget,status",
             "limit": 200},
            access_token=token,
        )
        if not self._has_error(structure):
            for a in structure.get("data", []):
                aid = a.get("id")
                targeting_map[aid] = self._summarize_targeting(a.get("targeting", {}))
                budget_map[aid] = float(a.get("daily_budget") or a.get("lifetime_budget") or 0) / 100.0
                status_map[aid] = a.get("status")
                campaign_of[aid] = a.get("campaign_id")
        else:
            self._maybe_mark_expired(ad_account, structure)

        # Suma de presupuesto de adsets ACTIVOS por campaña (Bug 5).
        adset_budget_by_campaign: Dict[str, float] = defaultdict(float)
        for aid, camp_id in campaign_of.items():
            if camp_id and status_map.get(aid) == "ACTIVE":
                adset_budget_by_campaign[camp_id] += budget_map.get(aid, 0.0)

        insights = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/insights",
            {"level": "adset", "date_preset": date_preset,
             "fields": "adset_id,adset_name,campaign_id,spend,impressions,reach,clicks,actions,frequency",
             "time_increment": 1, "limit": 500},
            access_token=token,
        )
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
            row.targeting_summary = targeting_map.get(adset_id, {})
            row.budget = budget_map.get(adset_id, 0.0)

        return dict(adset_budget_by_campaign)

    async def _sync_real_ads(self, ad_account, token, limiter, date_preset):
        # Mapa ad_id -> {creative_id, title, body, cta_type}. El copy real
        # (title/body/call_to_action_type) del anuncio ganador nunca se
        # capturaba — solo la imagen (MetaCreative) — así que la IA no podía
        # inspirarse en el texto que ya funcionó (Fase 3B).
        creative_map = {}
        ads_struct = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/ads",
            {"fields": "id,creative{id,title,body,call_to_action_type}", "limit": 200}, access_token=token,
        )
        if not self._has_error(ads_struct):
            for a in ads_struct.get("data", []):
                creative = a.get("creative", {}) or {}
                creative_map[a.get("id")] = {
                    "id": creative.get("id"),
                    "title": creative.get("title"),
                    "body": creative.get("body"),
                    "cta_type": creative.get("call_to_action_type"),
                }

        insights = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/insights",
            {"level": "ad", "date_preset": date_preset,
             "fields": "ad_id,ad_name,adset_id,campaign_id,spend,impressions,reach,clicks,actions",
             "time_increment": 1, "limit": 500},
            access_token=token,
        )
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
            row.name = item.get("ad_name", "")
            row.spend = spend; row.impressions = impressions; row.reach = reach; row.clicks = clicks
            row.conversions = conversions; row.conversations = conversations; row.cpl = cpl; row.ctr = ctr

    async def _sync_real_inventory(self, ad_account, token, limiter):
        # Imágenes / creativos
        imgs = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/adimages",
            {"fields": "hash,name,url,width,height,created_time", "limit": "50"},
            access_token=token,
        )
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
        auds = await limiter.call_api_paginated(
            f"{ad_account.external_account_id}/customaudiences",
            # `approximate_count` ya no existe en la Graph API (confirmado contra la
            # API real: pedirlo devuelve OAuthException code 100 "Tried accessing
            # nonexisting field") — Meta lo reemplazó por lower/upper bound.
            {"fields": "id,name,subtype,approximate_count_lower_bound,approximate_count_upper_bound,time_created,description"},
            access_token=token,
        )
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
        """Fase 4 (plan de creación de campañas): descubre las páginas administradas
        por el token y el WhatsApp conectado a cada una, para que la UI ofrezca un
        dropdown de página en vez de que el usuario tipee `page_id`/`whatsapp_number`
        a mano (fuente del bug de WhatsApp de la reunión con Alejandro: "aparece un
        código en vez del número").

        `/me/accounts` es a nivel de usuario, no de cuenta publicitaria (playbook
        §1.4) — el mismo token de la cuenta sirve para ambos. El WhatsApp se pide
        en una llamada aparte POR página: si el campo no está soportado o falla
        para una página, solo esa página se queda sin número (no debe tumbar el
        descubrimiento de las demás — misma lección que el bug de
        `customaudiences`/`approximate_count`, HANDOFF hallazgo 3.8).
        """
        pages_res = await limiter.call_api(
            "me/accounts",
            {"fields": "name,id,category,connected_instagram_account", "limit": "100"},
            access_token=token,
        )
        if self._has_error(pages_res):
            logger.warning("No se pudieron leer las páginas del token (cuenta %s): %s",
                           ad_account.id, pages_res)
            return

        for p in pages_res.get("data", []):
            ext_id = p.get("id")
            if not ext_id:
                continue
            row = (await self.db.execute(select(MetaPage).where(
                MetaPage.organization_id == ad_account.organization_id,
                MetaPage.external_page_id == ext_id,
            ))).scalar_one_or_none()
            if row is None:
                row = MetaPage(organization_id=ad_account.organization_id, external_page_id=ext_id)
                self.db.add(row)
            row.name = p.get("name", "")
            row.instagram_actor_id = (p.get("connected_instagram_account") or {}).get("id")
            row.status = "ACTIVE"

            wa_res = await limiter.call_api(ext_id, {"fields": "whatsapp_number"}, access_token=token)
            if self._has_error(wa_res):
                logger.info(
                    "La página %s no expone whatsapp_number (o el token no tiene permiso): %s",
                    ext_id, wa_res,
                )
            else:
                row.whatsapp_number = wa_res.get("whatsapp_number")

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

    async def _sync_real_breakdowns(self, ad_account, token, limiter, date_preset):
        """Trae los desgloses (placement, edad, género, dispositivo, geografía) desde
        Meta y los fusiona en los snapshots de campaña ya creados en este sync. Cada
        dimensión es un request independiente; si Meta rechaza uno (permiso o combinación
        no permitida) se registra y se continúa, sin abortar el sync completo."""
        # (campaign_id, date) -> {placements:{}, age:{}, gender:{}, device:{}, region:{}}
        acc_map: Dict[Any, Dict[str, dict]] = defaultdict(
            lambda: {"placements": {}, "age": {}, "gender": {}, "device": {}, "region": {}}
        )

        for bd_param, bucket, key_of in self._BREAKDOWN_SPECS:
            res = await limiter.call_api_paginated(
                f"{ad_account.external_account_id}/insights",
                {"level": "campaign", "date_preset": date_preset, "time_increment": 1,
                 "fields": "campaign_id,spend,actions", "breakdowns": bd_param, "limit": 500},
                access_token=token,
            )
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
                {"fields": "spend", "time_increment": "monthly", "date_preset": "maximum", "limit": 100},
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
                spend = float(row.get("spend", 0) or 0)
                await self._upsert_spend_month(ad_account, ym, spend, closed=(ym != current_ym))
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
            {"fields": "spend", "date_preset": "this_month"},
            access_token=token,
        )
        if self._has_error(res):
            logger.warning("No se pudo actualizar el gasto del mes en curso (cuenta %s): %s",
                           ad_account.id, res)
            self._maybe_mark_expired(ad_account, res)
            return
        data = res.get("data", [])
        spend = float(data[0].get("spend", 0) or 0) if data else 0.0
        await self._upsert_spend_month(ad_account, current_ym, spend, closed=False)

    async def _upsert_spend_month(self, ad_account: AdAccount, year_month: str, spend: float, closed: bool) -> None:
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
        row.spend = spend
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
