import asyncio
import json
import logging
from typing import Dict, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from app.config import settings

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

@router.websocket("/notifications/{tenant_id}")
async def websocket_endpoint(websocket: WebSocket, tenant_id: str):
    """
    WebSocket endpoint para el frontend.
    En un sistema real, tenant_id se extraería/validaría de un token (ej. pasando token por query param).
    """
    await manager.connect(websocket, tenant_id)
    try:
        while True:
            # Mantener la conexión abierta, podemos recibir un ping del cliente
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, tenant_id)
