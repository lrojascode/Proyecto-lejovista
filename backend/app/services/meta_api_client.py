import httpx
import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException
from app.config import settings
from app.services.rate_limiter import RateLimiter
from app.services.meta_credential_service import MetaCredentialService
from app.services.audit_log_service import AuditLogService

logger = logging.getLogger(__name__)

class MetaApiClient:
    """
    Cliente HTTP para la API de Meta Ads (Graph API).
    Aplica rate limiting dinámico y registra todas las llamadas a nivel HTTP.
    """
    BASE_URL = f"https://graph.facebook.com/{settings.META_API_VERSION}"
    USER_AGENT = "LejoVista/1.0 (+https://lejovista.com)"

    def __init__(
        self,
        tenant_id: str,
        credential_service: MetaCredentialService,
        rate_limiter: RateLimiter,
        audit_service: AuditLogService
    ):
        self.tenant_id = tenant_id
        self.credential_service = credential_service
        self.rate_limiter = rate_limiter
        self.audit_service = audit_service
        # El cliente asíncrono debe inyectarse o crearse por llamada.
        # Para simplificar, usaremos httpx.AsyncClient como context manager por cada petición.

    async def _execute_request(
        self, method: str, endpoint: str, ad_account_id: Optional[str] = None, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        
        # 1. Validar cuota antes de llamar a la API
        allowed = await self.rate_limiter.check_and_consume(self.tenant_id, ad_account_id)
        if not allowed:
            raise HTTPException(status_code=429, detail="Meta API rate limit exceeded for this tenant/account. Try again later.")

        # 2. Obtener Token
        access_token = await self.credential_service.get_valid_token(self.tenant_id)
        
        req_params = params or {}
        req_params["access_token"] = access_token
        
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json"
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(method, url, params=req_params, json=data, headers=headers, timeout=15.0)
                
                # 3. Actualizar rate limiter basándose en cabeceras de respuesta
                await self.rate_limiter.update_usage_from_headers(self.tenant_id, ad_account_id, response.headers)
                
                response_data = response.json() if response.content else {}
                
                # 4. Auditoría de la llamada HTTP
                # Protegemos el token en los logs
                safe_params = req_params.copy()
                if "access_token" in safe_params:
                    safe_params["access_token"] = "***"
                    
                preview = str(response_data)[:500] if response_data else None
                await self.audit_service.log_api_call(
                    ad_account_id=ad_account_id,
                    method=method,
                    endpoint=endpoint,
                    status_code=response.status_code,
                    params=safe_params,
                    response_preview=preview
                )

                # 5. Manejar errores
                if response.status_code >= 400:
                    logger.error(f"Meta API Error {response.status_code}: {response_data}")
                    # Aquí es donde Celery autoretry actuará (ante 429/500 vs 400/403)
                    if response.status_code in (429, 500, 502, 503):
                        raise Exception(f"Transient Meta API error {response.status_code}: {response_data}")
                    elif response.status_code == 403 or response.status_code == 401:
                        raise HTTPException(status_code=403, detail="Meta credentials expired or unauthorized.")
                    else:
                        raise ValueError(f"Bad Request to Meta: {response_data}")

                return response_data
                
            except httpx.RequestError as e:
                logger.error(f"HTTP Request error to Meta: {str(e)}")
                raise Exception(f"Network error calling Meta: {str(e)}")

    async def get(self, endpoint: str, ad_account_id: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self._execute_request("GET", endpoint, ad_account_id, params=params)

    async def post(self, endpoint: str, ad_account_id: Optional[str] = None, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self._execute_request("POST", endpoint, ad_account_id, params=params, data=data)
