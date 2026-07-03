import json
import logging
from redis import Redis
from app.config import settings

logger = logging.getLogger(__name__)

class NotificationService:
    """
    Publica mensajes en un canal de Redis (Pub/Sub) para notificar
    a los workers de FastAPI, que a su vez retransmitirán a los WebSockets.
    
    Nota: Usamos redis síncrono porque este servicio será típicamente llamado
    desde tareas de Celery (que son funciones síncronas).
    """
    def __init__(self, redis_url: str = settings.REDIS_URL):
        # Usamos StrictRedis síncrono para Celery
        self.redis_client = Redis.from_url(redis_url)
        self.channel = "ws_notifications"

    def notify_job_status(self, tenant_id: str, job_id: str, status: str, event_type: str = "sync_status", extra: dict = None):
        """
        Notifica el estado de un job (SyncJob o CampaignDraft).
        """
        payload = {
            "tenant_id": tenant_id,
            "event_type": event_type,
            "job_id": job_id,
            "status": status
        }
        if extra:
            payload.update(extra)
            
        try:
            message = json.dumps(payload)
            self.redis_client.publish(self.channel, message)
            logger.info(f"Published WS event for {tenant_id}: {event_type} {status}")
        except Exception as e:
            logger.error(f"Failed to publish WS event to redis: {e}")
