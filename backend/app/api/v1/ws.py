import asyncio
import json
import logging
from typing import Dict, List, Optional
import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.config import settings
from app.database import async_session
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websockets"])

class ConnectionManager:
    def __init__(self):
        # tenant_id -> list of active connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, tenant_id: str):
        await websocket.accept()
        if tenant_id not in self.active_connections:
            self.active_connections[tenant_id] = []
        self.active_connections[tenant_id].append(websocket)
        logger.info(f"WebSocket connected for tenant {tenant_id}. Total: {len(self.active_connections[tenant_id])}")

    def disconnect(self, websocket: WebSocket, tenant_id: str):
        if tenant_id in self.active_connections:
            if websocket in self.active_connections[tenant_id]:
                self.active_connections[tenant_id].remove(websocket)
            if not self.active_connections[tenant_id]:
                del self.active_connections[tenant_id]
        logger.info(f"WebSocket disconnected for tenant {tenant_id}.")

    async def broadcast_to_tenant(self, tenant_id: str, message: dict):
        if tenant_id in self.active_connections:
            message_text = json.dumps(message)
            dead_connections = []
            for connection in self.active_connections[tenant_id]:
                try:
                    await connection.send_text(message_text)
                except Exception:
                    dead_connections.append(connection)
            
            # Clean up dead connections
            for conn in dead_connections:
                self.disconnect(conn, tenant_id)

manager = ConnectionManager()

async def redis_listener():
    """
    Background task that listens to Redis Pub/Sub and broadcasts to WebSockets.
    Includes reconnection logic.
    """
    channel_name = "ws_notifications"
    
    while True:
        try:
            logger.info("Connecting to Redis Pub/Sub...")
            redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(channel_name)
            logger.info(f"Subscribed to Redis channel: {channel_name}")
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        tenant_id = data.get("tenant_id")
                        if tenant_id:
                            await manager.broadcast_to_tenant(tenant_id, data)
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON from Redis: {message['data']}")
        except asyncio.CancelledError:
            logger.info("Redis listener task cancelled.")
            break
        except Exception as e:
            logger.error(f"Redis Pub/Sub error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

async def _authenticate_ws(token: Optional[str], tenant_id: str) -> bool:
    """
    Valida el JWT recibido por query param y comprueba que el usuario
    pertenezca a la organización (tenant_id = organization_id) del canal.
    Devuelve False ante cualquier fallo (token ausente, inválido o sin pertenencia).
    """
    if not token:
        return False
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
    except Exception:
        return False

    if not user_id:
        return False

    async with async_session() as db:
        q = select(User).options(selectinload(User.organizations)).where(User.id == user_id)
        res = await db.execute(q)
        user = res.scalar_one_or_none()
        if not user:
            return False
        user_org_ids = [ou.organization_id for ou in user.organizations]
        return tenant_id in user_org_ids


@router.websocket("/notifications/{tenant_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    tenant_id: str,
    token: Optional[str] = Query(default=None)
):
    """
    WebSocket endpoint para el frontend.
    tenant_id es el organization_id (UUID) del tenant. Requiere ?token=<JWT>
    y que ese usuario pertenezca a la organización; si no, se cierra la conexión.
    """
    authorized = await _authenticate_ws(token, tenant_id)
    if not authorized:
        # 4401: violación de política / no autorizado en el handshake
        await websocket.close(code=4401)
        return

    await manager.connect(websocket, tenant_id)
    try:
        while True:
            # Mantener la conexión abierta, podemos recibir un ping del cliente
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, tenant_id)
