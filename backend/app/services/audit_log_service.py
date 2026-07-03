from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.action_log import ActionLog
from app.models.api_call_log import APICallLog

class AuditLogService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_action(self, organization_id: str, action_type: str, status: str, actor_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        """Registra un evento de negocio (ej. SYNC_STARTED, CAMPAIGN_CREATED)"""
        log = ActionLog(
            organization_id=organization_id,
            actor_id=actor_id,
            action_type=action_type,
            status=status,
            details=details,
            timestamp=datetime.utcnow()
        )
        self.session.add(log)
        await self.session.commit()

    async def log_api_call(self, ad_account_id: Optional[str], method: str, endpoint: str, status_code: int, params: Dict[str, Any], response_preview: Optional[str] = None):
        """Registra una llamada HTTP cruda a graph.facebook.com para depuración."""
        log = APICallLog(
            ad_account_id=ad_account_id,
            method=method,
            endpoint=endpoint,
            params=params,
            status_code=status_code,
            response_preview=response_preview,
            timestamp=datetime.utcnow()
        )
        self.session.add(log)
        await self.session.commit()
