import os
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.config import settings

logger = logging.getLogger(__name__)

class GeminiSummaryResponse(BaseModel):
    narrative_summary: str = Field(description="Un resumen narrativo (1-2 párrafos) de alto nivel del rendimiento.")
    key_findings: list[str] = Field(description="Lista de 3 a 5 hallazgos clave priorizados basados en anomalías o CPL.")
    actionable_recommendations: list[str] = Field(description="Lista de 2 a 3 recomendaciones concretas y accionables para mejorar el rendimiento.")

class GeminiClient:
    def __init__(self, api_key: str = None, model_name: str = "gemini-3.5-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name.replace("google/", "") if "google/" in model_name else model_name
        if not self.api_key:
            logger.warning("GEMINI_API_KEY no configurado. GeminiClient no funcionará.")
            
    async def generate_brief_narrative(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Envía los datos procesados de las Capas 1 y 2 a Gemini 2.5 Flash
        para obtener un resumen narrativo, hallazgos y recomendaciones estructuradas en JSON.
        """
        if not self.api_key:
            return None
            
        try:
            # Importación local para evitar fallos si google-genai no está instalado en un entorno
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=self.api_key)
            
            prompt = f"""
            Eres el Director de Estrategia de Performance para una agencia B2B en LejoVista.
            Analiza los siguientes datos extraídos de Meta Ads (KPIs, anomalías, ranking de campañas por CPL).
            Escribe un informe ejecutivo estructurado con una narrativa clara, hallazgos clave y recomendaciones.
            
            Datos:
            {raw_data}
            """
            
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GeminiSummaryResponse,
                ),
            )
            
            # El SDK ya devuelve un string JSON validado contra el schema si usamos response_schema
            import json
            return json.loads(response.text)
            
        except Exception as e:
            logger.error(f"Fallo al conectar con Gemini: {e}")
            return None
