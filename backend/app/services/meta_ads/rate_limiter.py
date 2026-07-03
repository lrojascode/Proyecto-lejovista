import asyncio
import random
import logging
from typing import Dict, Any, Optional
import httpx
from app.config import settings
from app.database import async_session
from app.models.api_call_log import APICallLog

logger = logging.getLogger("lejovista.rate_limiter")

class MetaAPIRateLimiter:
    def __init__(self, ad_account_id: Optional[str] = None):
        self.ad_account_id = ad_account_id
        # Contador de llamadas en la sesión actual
        self.call_count = 0
        
        # Headers que imitan un navegador
        self.headers = {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/124.0.0.0 Safari/537.36'),
            'Accept': 'application/json',
            'Accept-Language': 'es-PE,es;q=0.9,en;q=0.8',
            'Connection': 'keep-alive'
        }

    async def _log_api_call(self, method: str, endpoint: str, params: Dict[str, Any], status_code: int, response_preview: str):
        # Guardar en base de datos de auditoría
        async with async_session() as session:
            try:
                log_entry = APICallLog(
                    ad_account_id=self.ad_account_id,
                    method=method,
                    endpoint=endpoint,
                    params={k: (v if k != 'access_token' else '***') for k, v in params.items()},
                    status_code=status_code,
                    response_preview=response_preview[:1000] # Limitar tamaño
                )
                session.add(log_entry)
                await session.commit()
            except Exception as e:
                logger.error(f"Error logging API call to DB: {e}")

    async def _apply_delay(self, es_post: bool):
        self.call_count += 1
        
        # En modo MOCK los delays se acortan para facilitar testing local ágil
        if settings.MOCK_SERVICES:
            if self.call_count % 3 == 0:
                delay = random.uniform(1.5, 3.0)
                logger.info(f"[MOCK] Pausa larga entre llamadas: {delay:.2f}s")
            else:
                delay = random.uniform(0.5, 1.5)
            await asyncio.sleep(delay)
            return

        # ---- PRODUCCIÓN: Delays anti-detección dinámicos y naturales ----
        # Evitamos pausas fijas masivas de 45-75s que bloquean workers. Usamos pausas cortas.
        if es_post:
            delay = random.uniform(3.0, 6.0)  # Pausa corta natural entre escrituras
        else:
            delay = random.uniform(1.0, 2.5)  # Pausa corta natural entre lecturas
            
        logger.info(f"Pausa preventiva anti-detección: {delay:.2f} segundos...")
        await asyncio.sleep(delay)

    async def call_api(
        self, 
        endpoint: str, 
        params: Dict[str, Any], 
        method: str = "GET", 
        access_token: str = ""
    ) -> Dict[str, Any]:
        """
        Realiza una llamada a la API de Meta Ads Graph de forma segura y respetuosa con los límites.
        Soporta MOCK_SERVICES para simular la API localmente.
        """
        es_post = method.upper() == "POST"
        
        # Aplicar el delay antes de la llamada
        await self._apply_delay(es_post)

        if settings.MOCK_SERVICES:
            # Retornamos respuestas mockeadas (serán manejadas por los servicios específicos)
            logger.info(f"[MOCK] API CALL - {method} - {endpoint} - Params: {list(params.keys())}")
            # Registramos la llamada ficticia en base de datos
            await self._log_api_call(method, endpoint, params, 200, "MOCK RESPONSE OK")
            return {"mock": True}

        # ---- PRODUCCIÓN: Llamada real ----
        url = f"https://graph.facebook.com/v19.0/{endpoint}"
        
        # Asegurar token en params
        all_params = {**params}
        if access_token:
            all_params["access_token"] = access_token

        # Reintentos seguros
        intentos = 0
        max_intentos = 2
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            while intentos < max_intentos:
                try:
                    if es_post:
                        response = await client.post(url, data=all_params, headers=self.headers)
                    else:
                        response = await client.get(url, params=all_params, headers=self.headers)
                    
                    status = response.status_code
                    data = response.json()
                    
                    # Registrar log
                    await self._log_api_call(method, endpoint, all_params, status, response.text)
                    
                    # Procesar cabeceras de rate limit de Meta (x-business-use-case-usage)
                    x_usage = response.headers.get("x-business-use-case-usage")
                    if x_usage:
                        try:
                            import json
                            usage_data = json.loads(x_usage)
                            for biz_id, usage_list in usage_data.items():
                                for usage in usage_list:
                                    call_count = usage.get("call_count", 0)
                                    total_time = usage.get("total_time", 0)
                                    total_cpu = usage.get("total_cputime", 0)
                                    regain_access = usage.get("estimated_time_to_regain_access", 0)
                                    
                                    max_usage = max(call_count, total_time, total_cpu)
                                    if regain_access > 0:
                                        wait_time = regain_access * 60 + 5
                                        logger.warning(f"Meta Rate Limit alcanzado ({max_usage}%). Esperando {wait_time}s...")
                                        await asyncio.sleep(wait_time)
                                    elif max_usage > 85:
                                        logger.warning(f"Meta Rate Limit alto ({max_usage}%). Pausa preventiva de 15s...")
                                        await asyncio.sleep(15)
                                    elif max_usage > 50:
                                        logger.info(f"Meta Rate Limit moderado ({max_usage}%). Pausa preventiva de 3s...")
                                        await asyncio.sleep(3)
                        except Exception as e:
                            logger.error(f"Error al analizar x-business-use-case-usage: {e}")
                    
                    # Si es error de rate limit u otros errores temporales, esperar
                    if status in [408, 429, 500, 502, 503, 504]:
                        intentos += 1
                        if intentos < max_intentos:
                            logger.warning(f"Error {status} detectado. Esperando 60 segundos antes de reintentar...")
                            await asyncio.sleep(60)
                            continue
                    
                    return data
                    
                except httpx.RequestError as exc:
                    logger.error(f"Error de red al llamar a {url}: {exc}")
                    intentos += 1
                    if intentos < max_intentos:
                        await asyncio.sleep(60)
                except Exception as exc:
                    logger.error(f"Excepción inesperada en llamada API: {exc}")
                    await self._log_api_call(method, endpoint, all_params, 500, str(exc))
                    return {"error": "excepcion", "detalle": str(exc)}
            
            return {"error": "failed_after_retries", "detalle": "Llamada fallida tras reintentos"}
