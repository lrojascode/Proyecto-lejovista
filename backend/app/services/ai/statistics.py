import datetime
import math
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.ai_insight import AIInsight
from app.services.ai.thresholds import get_thresholds_for_org
from app.utils.formatting import money

INSUFFICIENT_HISTORY_TITLE = "Histórico insuficiente para análisis estadístico"

class Capa2Statistics:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze(self, ad_account: AdAccount) -> List[AIInsight]:
        """
        Analiza estadísticas de Capa 2 (medias móviles, tendencias y anomalías por Z-score).
        Requiere datos históricos (analizamos hasta SNAPSHOT_RETENTION_DAYS).
        """
        t = get_thresholds_for_org(getattr(ad_account, "organization", None))
        insights = []
        insufficient_history = []  # campañas sin días suficientes para estadística

        today = datetime.date.today()
        history_start = today - datetime.timedelta(days=settings.SNAPSHOT_RETENTION_DAYS)
        
        # Obtener historial de snapshots de la cuenta
        q = select(CampaignSnapshot).where(
            CampaignSnapshot.ad_account_id == ad_account.id,
            CampaignSnapshot.date >= history_start
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
            # Solo analizamos campañas activas en la última sincronización
            if not history or history[0].status != "ACTIVE":
                continue

            # Antigüedad real de la campaña: (max(date) - min(date)).days + 1
            dates = [s.date for s in history]
            real_history_days = (max(dates) - min(dates)).days + 1
            if real_history_days < t.min_history_days:
                insufficient_history.append((history[0].campaign_name, real_history_days))
                continue

            # Asumimos que history está ordenada desc (más reciente primero)
            latest_snapshots = history[:t.trend_window_days]           # ventana reciente
            older_snapshots = history[t.trend_window_days:t.base_window_days]  # ventana base
            
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
                if percent_increase > t.cpl_trend_pct:  # Incremento del CPL relevante
                    insights.append(AIInsight(
                        ad_account_id=ad_account.id,
                        organization_id=ad_account.organization_id,
                        campaign_id=c_id,
                        level="WARNING",
                        category="PERFORMANCE",
                        title="Tendencia alcista de CPL detectada",
                        description=(
                            f"En los últimos 3 días, el CPL promedio de '{history[0].campaign_name}' subió a {money(latest_cpl)}, "
                            f"un aumento del {percent_increase:.1f}% respecto al promedio anterior de {money(base_cpl)}."
                        ),
                        suggested_action_type="MODIFY_BUDGET" # Reducir budget
                    ))
                elif percent_increase < -t.cpl_trend_pct: # CPL bajó relevante (¡Oportunidad!)
                    insights.append(AIInsight(
                        ad_account_id=ad_account.id,
                        organization_id=ad_account.organization_id,
                        campaign_id=c_id,
                        level="INFO",
                        category="PERFORMANCE",
                        title="Excelente rendimiento: CPL a la baja",
                        description=(
                            f"El CPL promedio de '{history[0].campaign_name}' bajó un {abs(percent_increase):.1f}% "
                            f"en los últimos 3 días ({money(latest_cpl)} vs base de {money(base_cpl)}). Se sugiere ampliar presupuesto."
                        ),
                        suggested_action_type="MODIFY_BUDGET" # Aumentar budget
                    ))

            # 2. Detección de Anomalías (Z-Score)
            # Calculamos la media y desviación estándar de CPL sobre la ventana base
            daily_cpls = []
            for s in history[:t.base_window_days]:
                if s.conversions > 0:
                    daily_cpls.append(s.spend / s.conversions)
                else:
                    daily_cpls.append(s.spend) # Si es 0, usamos spend como penalización

            if len(daily_cpls) >= t.min_points_for_zscore:
                n = len(daily_cpls)
                mean_cpl = sum(daily_cpls) / n
                # Desviación muestral (n-1): más honesta con pocos datos que la poblacional.
                variance = sum((x - mean_cpl) ** 2 for x in daily_cpls) / (n - 1)
                std_dev = math.sqrt(variance)

                if std_dev > t.min_std_dev: # Evitar división por cero o varianza ínfima
                    # Tomamos el CPL del día más reciente
                    today_cpl = daily_cpls[0]
                    z_score = (today_cpl - mean_cpl) / std_dev

                    if z_score > t.zscore_anomaly:
                        insights.append(AIInsight(
                            ad_account_id=ad_account.id,
                            organization_id=ad_account.organization_id,
                            campaign_id=c_id,
                            level="CRITICAL",
                            category="PERFORMANCE",
                            title="Anomalía de costo detectada hoy",
                            description=(
                                f"El CPL de hoy para '{history[0].campaign_name}' es de {money(today_cpl)}, "
                                f"lo cual es una anomalía positiva (Z-Score = {z_score:.2f}) respecto a la media habitual de {money(mean_cpl)}."
                            ),
                            suggested_action_type="PAUSE_CAMPAIGN"
                        ))

        # AI-3: no callar cuando faltan datos. Emitir un aviso explícito para que
        # el usuario sepa que la Capa 2 aún no puede opinar sobre esas campañas.
        if insufficient_history:
            nombres = ", ".join(
                f"'{name}' ({days}d)" for name, days in insufficient_history
            )
            insights.append(AIInsight(
                ad_account_id=ad_account.id,
                organization_id=ad_account.organization_id,
                level="INFO",
                category="ACCOUNT",
                title=INSUFFICIENT_HISTORY_TITLE,
                description=(
                    f"Las siguientes campañas tienen menos de {t.min_history_days} días de datos, "
                    f"por lo que el análisis de tendencias y anomalías (Capa 2) aún no se aplica: {nombres}. "
                    f"Se activará automáticamente al acumular más historial."
                ),
                suggested_action_type=None
            ))

        return insights
