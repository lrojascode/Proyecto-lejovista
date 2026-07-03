import datetime
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight

class Capa1Rules:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze(self, ad_account: AdAccount) -> List[AIInsight]:
        """
        Analiza las reglas fijas de la Capa 1 sobre la cuenta de anuncios.
        Compara los datos agregados de los últimos 7 días.
        """
        insights = []
        
        # 1. Reglas a nivel de CUENTA de anuncios
        if ad_account.status != "ACTIVE":
            insights.append(AIInsight(
                ad_account_id=ad_account.id,
                organization_id=ad_account.organization_id,
                level="CRITICAL",
                category="ACCOUNT",
                title="Cuenta de anuncios inactiva o restringida",
                description=f"La cuenta de anuncios {ad_account.name} ({ad_account.external_account_id}) reporta un estado de {ad_account.status}.",
                suggested_action_type="PAUSE_CAMPAIGN"  # Pausar localmente
            ))

        # Alerta de límite de gasto (spend cap)
        if ad_account.spend_cap > 0 and (ad_account.spend_cap - ad_account.amount_spent) < 100:
            insights.append(AIInsight(
                ad_account_id=ad_account.id,
                organization_id=ad_account.organization_id,
                level="CRITICAL",
                category="ACCOUNT",
                title="Límite de gasto (spend cap) casi agotado",
                description=f"El saldo restante en el límite de la cuenta es de S/.{(ad_account.spend_cap - ad_account.amount_spent):.2f}. Las campañas podrían detenerse pronto.",
                suggested_action_type=None
            ))

        # 2. Reglas a nivel de CAMPAÑA (datos agregados de los últimos 7 días)
        # Buscar campañas activas
        today = datetime.date.today()
        seven_days_ago = today - datetime.timedelta(days=7)
        
        # Agrupamos los snapshots de los últimos 7 días por campaña
        q = select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == ad_account.id,
            CampaignSnapshot.date >= seven_days_ago
        )
        res = await self.db.execute(q)
        snapshots = res.scalars().all()
        
        # Organizar por campaña
        campaign_data = {}
        for s in snapshots:
            c_id = s.campaign_id
            if c_id not in campaign_data:
                campaign_data[c_id] = {
                    "name": s.campaign_name,
                    "status": s.status,
                    "spend": 0.0,
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0,
                    "snapshots": [],
                    "latest_snapshot": s # Guardamos el más reciente para breakdowns
                }
            
            campaign_data[c_id]["spend"] += s.spend
            campaign_data[c_id]["impressions"] += s.impressions
            campaign_data[c_id]["clicks"] += s.clicks
            campaign_data[c_id]["conversions"] += s.conversions
            campaign_data[c_id]["snapshots"].append(s)
            
            # Asegurar que guardamos el snapshot más reciente en fecha
            if s.date > campaign_data[c_id]["latest_snapshot"].date:
                campaign_data[c_id]["latest_snapshot"] = s

        # CPL objetivo de referencia dinámico basado en el promedio histórico de los snapshots de la cuenta
        q_avg = select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == ad_account.id
        )
        res_avg = await self.db.execute(q_avg)
        all_snapshots = res_avg.scalars().all()
        
        total_spend = sum(s.spend for s in all_snapshots)
        total_conversions = sum(s.conversions for s in all_snapshots)
        
        if total_conversions > 0:
            target_cpl = total_spend / total_conversions
            # Mantener un rango razonable por seguridad (entre S/.1.50 y S/.10.00)
            target_cpl = max(1.50, min(10.00, target_cpl))
        else:
            target_cpl = 3.50

        for c_id, data in campaign_data.items():
            # Solo analizamos campañas activas en la última sincronización
            if data["status"] != "ACTIVE":
                continue
                
            spend = data["spend"]
            conversions = data["conversions"]
            impressions = data["impressions"]
            clicks = data["clicks"]
            
            cpl = spend / conversions if conversions > 0 else spend
            ctr = (clicks / impressions) * 100.0 if impressions > 0 else 0.0
            
            # Calcular frecuencia promedio
            freqs = [s.frequency for s in data["snapshots"] if s.frequency > 0]
            avg_frequency = sum(freqs) / len(freqs) if freqs else 1.0

            # --- Regla A: CPL > 2x Objetivo (Campaña Fallida) ---
            if conversions > 0 and cpl > (target_cpl * 2.0):
                insights.append(AIInsight(
                    ad_account_id=ad_account.id,
                    organization_id=ad_account.organization_id,
                    campaign_id=c_id,
                    level="CRITICAL",
                    category="PERFORMANCE",
                    title="Costo por Lead (CPL) crítico",
                    description=f"La campaña '{data['name']}' tiene un CPL promedio de S/.{cpl:.2f} en los últimos 7 días, lo cual excede el doble del objetivo de S/.{target_cpl:.2f}.",
                    suggested_action_type="PAUSE_CAMPAIGN"
                ))

            # --- Regla B: Alta Frecuencia > 3.0 (Audiencia Saturada) ---
            if avg_frequency > 3.0:
                insights.append(AIInsight(
                    ad_account_id=ad_account.id,
                    organization_id=ad_account.organization_id,
                    campaign_id=c_id,
                    level="WARNING",
                    category="SATURATION",
                    title="Saturación de audiencia detectada",
                    description=f"La frecuencia promedio de '{data['name']}' es de {avg_frequency:.2f}. La audiencia está viendo el anuncio repetidamente, reduciendo la efectividad.",
                    suggested_action_type="MODIFY_TARGETING"
                ))

            # --- Regla C: CTR < 0.5% (Creatividad Débil) ---
            if impressions > 200 and ctr < 0.5:
                insights.append(AIInsight(
                    ad_account_id=ad_account.id,
                    organization_id=ad_account.organization_id,
                    campaign_id=c_id,
                    level="WARNING",
                    category="PERFORMANCE",
                    title="Tasa de Clics (CTR) muy baja",
                    description=f"La campaña '{data['name']}' tiene un CTR de {ctr:.2f}% (debajo del 0.5% mínimo recomendado). Indica que la creatividad o el copy no enganchan a la audiencia.",
                    suggested_action_type="CREATE_CAMPAIGN"  # Sugerir nueva campaña/anuncio
                ))

            # --- Regla D: Clics sin Leads (Configuración Errónea / Landing Rota) ---
            if impressions > 500 and conversions == 0:
                insights.append(AIInsight(
                    ad_account_id=ad_account.id,
                    organization_id=ad_account.organization_id,
                    campaign_id=c_id,
                    level="CRITICAL",
                    category="PERFORMANCE",
                    title="Conversiones en cero",
                    description=f"La campaña '{data['name']}' registra {clicks} clics e impresiones significativas, pero cero leads. Podría haber una mala configuración en el botón de WhatsApp o en el formulario.",
                    suggested_action_type="PAUSE_CAMPAIGN"
                ))

            # --- Reglas de Placements (Analizando el último snapshot disponible con breakdowns) ---
            latest = data["latest_snapshot"]
            if latest and latest.placements_breakdown:
                for pl_name, pl_data in latest.placements_breakdown.items():
                    pl_spend = pl_data.get("spend", 0.0)
                    pl_conv = pl_data.get("conversions", 0)
                    
                    if pl_spend > 15.0: # Solo si tiene gasto representativo
                        pl_cpl = pl_data.get("cpl", pl_spend)
                        
                        # Placements de Instagram ineficientes (CPL > S/.5.00)
                        if "instagram" in pl_name and pl_cpl > 5.0:
                            insights.append(AIInsight(
                                ad_account_id=ad_account.id,
                                organization_id=ad_account.organization_id,
                                campaign_id=c_id,
                                level="WARNING",
                                category="PLACEMENT",
                                title=f"Placement ineficiente en Instagram: {pl_name}",
                                description=f"El placement '{pl_name}' tiene un CPL de S/.{pl_cpl:.2f}. Se recomienda remover este placement de la campaña.",
                                suggested_action_type="MODIFY_PLACEMENTS"
                            ))
                            
                        # Oportunidad en Reels (CPL Reels < Feed CPL * 0.7)
                        if "reels" in pl_name:
                            feed_cpl = latest.placements_breakdown.get("facebook_feed", {}).get("cpl", target_cpl)
                            if pl_cpl < (feed_cpl * 0.7):
                                insights.append(AIInsight(
                                    ad_account_id=ad_account.id,
                                    organization_id=ad_account.organization_id,
                                    campaign_id=c_id,
                                    level="INFO",
                                    category="PLACEMENT",
                                    title="Oportunidad detectada en Facebook Reels",
                                    description=f"El CPL en Facebook Reels es de S/.{pl_cpl:.2f}, significativamente más barato que el feed (S/.{feed_cpl:.2f}). Se recomienda priorizar este placement.",
                                    suggested_action_type="MODIFY_PLACEMENTS"
                                ))

        return insights
