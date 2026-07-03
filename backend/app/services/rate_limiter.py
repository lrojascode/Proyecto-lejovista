import json
import logging
from typing import Dict, Optional
import redis.asyncio as redis
from app.config import settings

logger = logging.getLogger(__name__)

class RateLimiter:
    """
    Rate Limiter respaldado por Redis para llamadas a Meta API.
    Ajusta dinámicamente el límite basado en la cabecera X-Business-Use-Case-Usage.
    """
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.default_capacity = 60 # Default conservador: 60 llamadas / hora si no tenemos datos de Meta

    async def check_and_consume(self, tenant_id: str, ad_account_id: Optional[str] = None) -> bool:
        """
        Verifica si se puede realizar una llamada y consume una unidad.
        Retorna True si la llamada es permitida, False si se alcanzó el límite.
        """
        key = f"rate_limit:{tenant_id}"
        if ad_account_id:
            key += f":{ad_account_id}"

        # Lógica de fallback: un Token Bucket muy básico (60 por hora)
        # Se puede expandir con Lua script para atomicidad real.
        current_usage = await self.redis.get(f"{key}:usage")
        if current_usage and float(current_usage) >= 90.0:
            logger.warning(f"Rate limit approaching for {key}. Current Meta usage is {current_usage}%")
            # Si el uso reportado por Meta es > 90%, bloquear temporalmente
            return False

        # Si no hay datos de meta, usar contador fallback
        fallback_count = await self.redis.incr(f"{key}:fallback")
        if fallback_count == 1:
            await self.redis.expire(f"{key}:fallback", 3600) # expira en 1 hora
        
        if fallback_count > self.default_capacity:
            return False
            
        return True

    async def update_usage_from_headers(self, tenant_id: str, ad_account_id: Optional[str], headers: Dict[str, str]):
        """
        Lee X-Business-Use-Case-Usage de la respuesta de Meta y actualiza Redis.
        Ejemplo de header: {"1110545229007421":[{"type":"ads_management","call_count":1,"total_cputime":1,"total_time":1,"estimated_time_to_regain_access":0}]}
        """
        usage_header = headers.get("x-business-use-case-usage")
        if not usage_header:
            return

        try:
            usage_data = json.loads(usage_header)
            # Extraer el máximo call_count (porcentaje de 0 a 100)
            max_usage = 0.0
            for app_id, stats_list in usage_data.items():
                for stat in stats_list:
                    if stat.get("call_count", 0) > max_usage:
                        max_usage = stat.get("call_count")
            
            key = f"rate_limit:{tenant_id}"
            if ad_account_id:
                key += f":{ad_account_id}"
                
            await self.redis.set(f"{key}:usage", max_usage)
            # Expira la métrica en 5 minutos para que caiga al fallback si no hay llamadas nuevas
            await self.redis.expire(f"{key}:usage", 300) 
            
        except json.JSONDecodeError:
            logger.error(f"Failed to parse x-business-use-case-usage: {usage_header}")
