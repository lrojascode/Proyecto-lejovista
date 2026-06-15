import datetime
import math
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight

class Capa2Statistics:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze(self, ad_account: AdAccount) -> List[AIInsight]:
        """
        Analiza estadísticas de Capa 2 (medias móviles, tendencias y anomalías por Z-score).
        Requiere datos históricos (analizamos los últimos 14 a 30 días).
        """
        insights = []
        
        today = datetime.date.today()
        thirty_days_ago = today - datetime.timedelta(days=30)
        
        # Obtener historial de snapshots de la cuenta
        q = select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == ad_account.id,
            CampaignSnapshot.date >= thirty_days_ago
        ).order_by(CampaignSnapshot.date.desc())
        
        res = await self.db.execute(q)
        snapshots = res.scalars().all()
        
        if not snapshots:
            return insights

        # Organizar datos por campaña
        campaign_history: Dict[str, List[CampaignSnapshot]] = {}
        for s in snapshots:
            if s.campaign_id not in campaign_history:
                campaign_history[s.campaign_id] = []
            campaign_history[s.campaign_id].append(s)

        for c_id, history in campaign_history.items():
            # Necesitamos al menos 7 días de datos para un análisis estadístico mínimo
            if len(history) < 7:
                continue
                
            # Asumimos que history está ordenada desc (más reciente primero)
            latest_snapshots = history[:3]  # Últimos 3 días
            older_snapshots = history[3:14]  # Días 4 al 14
            
            # 1. Calcular CPL promedio de la última ventana (3 días) vs base (14 días)
            latest_spend = sum(s.spend for s in latest_snapshots)
            latest_leads = sum(s.conversions for s in latest_snapshots)
            latest_cpl = latest_spend / latest_leads if latest_leads > 0 else latest_spend
            
            base_spend = sum(s.spend for s in older_snapshots)
            base_leads = sum(s.conversions for s in older_snapshots)
            base_cpl = base_spend / base_leads if base_leads > 0 else base_spend
            
            # --- Regla de Tendencia: CPL Subiendo ---
            if latest_leads > 0 and base_leads > 0:
                percent_increase = ((latest_cpl - base_cpl) / base_cpl) * 100.0
                if percent_increase > 30.0:  # Incremento del CPL > 30%
                    insights.append(AIInsight(
                        ad_account_id=ad_account.id,
                        organization_id=ad_account.organization_id,
                        campaign_id=c_id,
                        level="WARNING",
                        category="PERFORMANCE",
                        title="Tendencia alcista de CPL detectada",
                        description=(
                            f"En los últimos 3 días, el CPL promedio de '{history[0].campaign_name}' subió a S/.{latest_cpl:.2f}, "
                            f"un aumento del {percent_increase:.1f}% respecto al promedio anterior de S/.{base_cpl:.2f}."
                        ),
                        suggested_action_type="MODIFY_BUDGET" # Reducir budget
                    ))
                elif percent_increase < -30.0: # CPL bajó > 30% (¡Oportunidad!)
                    insights.append(AIInsight(
                        ad_account_id=ad_account.id,
                        organization_id=ad_account.organization_id,
                        campaign_id=c_id,
                        level="INFO",
                        category="PERFORMANCE",
                        title="Excelente rendimiento: CPL a la baja",
                        description=(
                            f"El CPL promedio de '{history[0].campaign_name}' bajó un {abs(percent_increase):.1f}% "
                            f"en los últimos 3 días (S/.{latest_cpl:.2f} vs base de S/.{base_cpl:.2f}). Se sugiere ampliar presupuesto."
                        ),
                        suggested_action_type="MODIFY_BUDGET" # Aumentar budget
                    ))

            # 2. Detección de Anomalías (Z-Score)
            # Calculamos la media y desviación estándar de CPL sobre los últimos 14 días
            daily_cpls = []
            for s in history[:14]:
                if s.conversions > 0:
                    daily_cpls.append(s.spend / s.conversions)
                else:
                    daily_cpls.append(s.spend) # Si es 0, usamos spend como penalización
                    
            if len(daily_cpls) >= 5:
                mean_cpl = sum(daily_cpls) / len(daily_cpls)
                variance = sum((x - mean_cpl) ** 2 for x in daily_cpls) / len(daily_cpls)
                std_dev = math.sqrt(variance)
                
                if std_dev > 0.1: # Evitar división por cero o varianza ínfima
                    # Tomamos el CPL del día más reciente
                    today_cpl = daily_cpls[0]
                    z_score = (today_cpl - mean_cpl) / std_dev
                    
                    if z_score > 2.0:
                        insights.append(AIInsight(
                            ad_account_id=ad_account.id,
                            organization_id=ad_account.organization_id,
                            campaign_id=c_id,
                            level="CRITICAL",
                            category="PERFORMANCE",
                            title="Anomalía de costo detectada hoy",
                            description=(
                                f"El CPL de hoy para '{history[0].campaign_name}' es de S/.{today_cpl:.2f}, "
                                f"lo cual es una anomalía positiva (Z-Score = {z_score:.2f}) respecto a la media habitual de S/.{mean_cpl:.2f}."
                            ),
                            suggested_action_type="PAUSE_CAMPAIGN"
                        ))

        return insights
