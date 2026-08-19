import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from app.config import settings

logger = logging.getLogger("lejovista.sync_progress")


class SyncProgressTracker:
    """Rastrea el progreso y resultado de la sincronización de una cuenta de anuncios,
    almacenando el estado en Redis (consultable vía REST) y emitiendo eventos WebSocket
    para actualizar la UI en tiempo real."""

    @staticmethod
    def _get_redis():
        try:
            import redis as redis_sync
            return redis_sync.Redis.from_url(settings.REDIS_URL)
        except Exception as e:
            logger.warning(f"Redis no disponible para SyncProgressTracker: {e}")
            return None

    @classmethod
    def report_progress(
        cls,
        organization_id: str,
        ad_account_id: str,
        step: int,
        total_steps: int,
        step_name: str,
        message: str,
        counts: Optional[Dict[str, int]] = None,
    ) -> None:
        payload = {
            "ad_account_id": ad_account_id,
            "organization_id": organization_id,
            "status": "RUNNING",
            "step": step,
            "total_steps": total_steps,
            "step_name": step_name,
            "message": message,
            "counts": counts or {},
            "updated_at": datetime.utcnow().isoformat(),
        }

        r = cls._get_redis()
        if r:
            try:
                r.set(f"sync_progress:{ad_account_id}", json.dumps(payload), ex=600)
            except Exception as e:
                logger.warning(f"No se pudo guardar sync_progress en Redis: {e}")

        try:
            from app.services.notification_service import NotificationService
            NotificationService().notify_job_status(
                tenant_id=organization_id,
                job_id=ad_account_id,
                status="RUNNING",
                event_type="sync_progress",
                extra=payload,
            )
        except Exception as e:
            logger.warning(f"No se pudo emitir evento WS sync_progress: {e}")

    @classmethod
    def report_completed(
        cls,
        organization_id: str,
        ad_account_id: str,
        records_synced: int,
        summary: Optional[Dict[str, int]] = None,
    ) -> None:
        payload = {
            "ad_account_id": ad_account_id,
            "organization_id": organization_id,
            "status": "COMPLETED",
            "step": 5,
            "total_steps": 5,
            "step_name": "completed",
            "message": "Sincronización completada con éxito",
            "records_synced": records_synced,
            "summary": summary or {},
            "updated_at": datetime.utcnow().isoformat(),
        }

        r = cls._get_redis()
        if r:
            try:
                r.set(f"sync_progress:{ad_account_id}", json.dumps(payload), ex=300)
            except Exception as e:
                logger.warning(f"No se pudo guardar sync_completed en Redis: {e}")

        try:
            from app.services.notification_service import NotificationService
            NotificationService().notify_job_status(
                tenant_id=organization_id,
                job_id=ad_account_id,
                status="COMPLETED",
                event_type="sync_completed",
                extra=payload,
            )
        except Exception as e:
            logger.warning(f"No se pudo emitir evento WS sync_completed: {e}")

    @classmethod
    def report_failed(
        cls,
        organization_id: str,
        ad_account_id: str,
        error_message: str,
        error_code: Optional[int] = None,
        error_detail: Optional[str] = None,
    ) -> None:
        payload = {
            "ad_account_id": ad_account_id,
            "organization_id": organization_id,
            "status": "FAILED",
            "message": error_message,
            "error_code": error_code,
            "error_detail": error_detail,
            "updated_at": datetime.utcnow().isoformat(),
        }

        r = cls._get_redis()
        if r:
            try:
                r.set(f"sync_progress:{ad_account_id}", json.dumps(payload), ex=300)
            except Exception as e:
                logger.warning(f"No se pudo guardar sync_failed en Redis: {e}")

        try:
            from app.services.notification_service import NotificationService
            NotificationService().notify_job_status(
                tenant_id=organization_id,
                job_id=ad_account_id,
                status="FAILED",
                event_type="sync_failed",
                extra=payload,
            )
        except Exception as e:
            logger.warning(f"No se pudo emitir evento WS sync_failed: {e}")

    @classmethod
    def get_sync_status(cls, ad_account_id: str) -> Optional[Dict[str, Any]]:
        r = cls._get_redis()
        if not r:
            return None
        try:
            raw = r.get(f"sync_progress:{ad_account_id}")
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Error al leer sync_progress de Redis: {e}")
        return None
