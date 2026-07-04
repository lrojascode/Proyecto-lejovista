import datetime
import random
import logging
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.sync_job import SyncJob
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
                "reels_better": True
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
                    clicks=clicks,
                    conversions=conversions,
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

        await self.db.flush()
        return records_created

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
            raise Exception(f"Error al verificar cuenta de anuncios: {acc_info}")
            
        # Actualizar ad_account
        ad_account.spend_cap = float(acc_info.get("spend_cap", 0)) / 100.0  # Meta retorna centavos
        ad_account.amount_spent = float(acc_info.get("amount_spent", 0)) / 100.0
        
        status_code = acc_info.get("account_status", 1)
        ad_account.status = "ACTIVE" if status_code == 1 else "SUSPENDED"
        self.db.add(ad_account)

        # 1. Obtener todas las campañas de la cuenta
        campaigns_res = await limiter.call_api(
            f"{ad_account.external_account_id}/campaigns",
            {
                "fields": "id,name,status,daily_budget,lifetime_budget",
            },
            access_token=token
        )
        
        if "ERROR" in campaigns_res or "error" in campaigns_res:
            raise Exception(f"Error al obtener campañas: {campaigns_res}")
            
        campaigns_map = {}
        for c in campaigns_res.get("data", []):
            budget = float(c.get("daily_budget") or c.get("lifetime_budget") or 0) / 100.0
            campaigns_map[c["id"]] = {
                "name": c.get("name", "Unknown"),
                "status": c.get("status", "UNKNOWN"),
                "budget": budget
            }

        # 2. Descargar insights diarios
        date_preset = "last_30d" if sync_type == "FULL" else "yesterday"
        
        campaigns_insights = await limiter.call_api(
            f"{ad_account.external_account_id}/insights",
            {
                "level": "campaign",
                "date_preset": date_preset,
                "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,frequency",
                "time_increment": 1  # datos diarios
            },
            access_token=token
        )

        if "ERROR" in campaigns_insights or "error" in campaigns_insights:
            raise Exception(f"Error al obtener insights de campañas: {campaigns_insights}")

        records_synced = 0
        data_list = campaigns_insights.get("data", [])
        
        from app.models.campaign_snapshot import CampaignSnapshot
        from datetime import datetime
        
        for item in data_list:
            camp_id = item.get("campaign_id")
            if not camp_id:
                continue
                
            camp_info = campaigns_map.get(camp_id, {"name": item.get("campaign_name", "Unknown"), "status": "ACTIVE", "budget": 0})
            
            # Extraer métricas básicas
            spend = float(item.get("spend", 0))
            impressions = int(item.get("impressions", 0))
            clicks = int(item.get("clicks", 0))
            frequency = float(item.get("frequency", 0))
            
            # Contar conversiones desde actions
            conversions = 0
            actions = item.get("actions", [])
            for action in actions:
                if action.get("action_type") in ["lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"]:
                    conversions += int(action.get("value", 0))
            
            if conversions == 0:
                for action in actions:
                    if "conversion" in action.get("action_type", "") or "lead" in action.get("action_type", ""):
                        conversions += int(action.get("value", 0))
                        
            cpl = (spend / conversions) if conversions > 0 else spend
            ctr = (clicks / impressions * 100) if impressions > 0 else 0
            
            try:
                date_start_str = item.get("date_start")
                sync_date = datetime.strptime(date_start_str, "%Y-%m-%d").date()
            except:
                continue

            # Idempotencia: si ya existe el snapshot de esa campaña y fecha, se actualiza
            # en lugar de insertar un duplicado (protegido además por uq_snapshot_acc_campaign_date).
            q_existing = select(CampaignSnapshot).where(
                CampaignSnapshot.ad_account_id == ad_account.id,
                CampaignSnapshot.campaign_id == camp_id,
                CampaignSnapshot.date == sync_date
            )
            res_existing = await self.db.execute(q_existing)
            snapshot = res_existing.scalar_one_or_none()

            if snapshot is None:
                snapshot = CampaignSnapshot(
                    ad_account_id=ad_account.id,
                    organization_id=ad_account.organization_id,
                    campaign_id=camp_id,
                    date=sync_date
                )
                self.db.add(snapshot)

            snapshot.campaign_name = camp_info["name"]
            snapshot.status = camp_info["status"]
            snapshot.budget = camp_info["budget"]
            snapshot.spend = spend
            snapshot.impressions = impressions
            snapshot.clicks = clicks
            snapshot.conversions = conversions
            snapshot.cpl = cpl
            snapshot.ctr = ctr
            snapshot.frequency = frequency
            records_synced += 1

        return records_synced
