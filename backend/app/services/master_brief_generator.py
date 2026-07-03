from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

class MasterBriefGenerator:
    """
    Consolida la información obtenida de Meta y guardada en BD,
    y genera el JSON estructurado final (reemplazo del HTML).
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def generate_brief(self, organization_id: str, ad_account_id: str, results: list) -> Dict[str, Any]:
        """
        Calcula y compila el brief usando la base de datos (y posiblemente los
        resultados directos retornados por el chord si no se persistieron).
        """
        logger.info(f"Generando Master Brief JSON para la cuenta {ad_account_id} de org {organization_id}")
        
        # Estructura base del JSON
        brief = {
            "account_info": {},
            "alerts": [],
            "kpi_comparison": {},
            "campaign_ranking": [],
            "learning_curve_analysis": {},
            "weekly_trend": {},
            "segmentation_insights": {
                "age_gender": {},
                "platforms_placements": {},
                "devices": {},
                "geography": {}
            },
            "audiences": [],
            "creatives": [],
            "optimized_parameters": {},
            "action_plan": {
                "urgent": [],
                "this_week": [],
                "next_month": []
            }
        }
        
        # Aquí irían las agregaciones en PostgreSQL y la lógica de negocio
        # para rellenar los datos. Por ahora retornamos la estructura base.
        # Por ejemplo, calcular el CPL por campaña:
        # brief["campaign_ranking"] = await self._calculate_cpl_ranking(ad_account_id)
        
        # Llamar a Gemini (Capa 3)
        from app.models.organization import Organization
        org = await self.session.get(Organization, organization_id)
        model_name = org.ai_model if org else "google/gemini-3.5-flash"
        
        gemini = GeminiClient(model_name=model_name)
        ai_response = await gemini.generate_brief_narrative(brief)
        
        if ai_response:
            brief["ai_summary"] = ai_response
        else:
            brief["ai_summary"] = None
            logger.warning("Gemini falló o no está configurado, generando brief sin narrativa IA.")
            
        return brief
