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
from app.utils.security import (
    TOKEN_TYPE_WS_TICKET,
    consume_ws_ticket,
    decode_token,
    load_user_with_organizations,
    token_predates_session_cutoff,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websockets"])

class ConnectionManager:
    def __init__(self):
        # tenant_id -> list of active connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, tenant_id: str, subprotocol: Optional[str] = None):
        await websocket.accept(subprotocol=subprotocol)
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

# Prefijo del subprotocolo que transporta el ticket. El handshake de un
# WebSocket no admite cabeceras propias desde el navegador; `Sec-WebSocket-
# Protocol` es el único campo que la API del navegador deja controlar, y a
# diferencia de la URL no lo registran los logs de acceso del proxy.
WS_TICKET_SUBPROTOCOL_PREFIX = "lejovista.ticket."


def _ticket_from_subprotocols(websocket: WebSocket) -> Optional[str]:
    """Extrae el ticket de `Sec-WebSocket-Protocol`, si viene."""
    crudo = websocket.headers.get("sec-websocket-protocol") or ""
    for propuesto in (p.strip() for p in crudo.split(",")):
        if propuesto.startswith(WS_TICKET_SUBPROTOCOL_PREFIX):
            return propuesto[len(WS_TICKET_SUBPROTOCOL_PREFIX):]
    return None


async def _authenticate_ws(ticket: Optional[str], tenant_id: str) -> Optional[str]:
    """Valida el ticket y la pertenencia al tenant del canal.

    Devuelve el subprotocolo a confirmar en el `accept` (el handshake exige
    devolver uno de los propuestos) o `None` ante cualquier fallo: ticket
    ausente, de otro tipo, vencido, de un usuario inexistente, ajeno a la
    organización, revocado en bloque, o de una organización suspendida.

    El TIPO se comprueba igual que en la API: un refresh token bien firmado
    abría este canal, y por él viajan las notificaciones de toda la
    organización.
    """
    if not ticket:
        return None

    payload = decode_token(ticket, expected_type=TOKEN_TYPE_WS_TICKET)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None

    # Un ticket es de un solo uso: se marca consumido ANTES de aceptar. Si el
    # mecanismo no responde no se puede garantizar la unicidad, y como el ticket
    # ya vive 30 segundos, se prefiere aceptar a dejar sin notificaciones a todo
    # el producto por una caída del cache.
    if not await consume_ws_ticket(payload.get("jti")):
        return None

    async with async_session() as db:
        user = await load_user_with_organizations(db, user_id)
        if not user:
            return None
        if token_predates_session_cutoff(payload, user):
            return None
        for ou in (user.organizations or []):
            if ou.organization_id != tenant_id:
                continue
            org = ou.organization
            if org is not None and org.status != "ACTIVE" and not user.is_platform_admin:
                return None
            return f"{WS_TICKET_SUBPROTOCOL_PREFIX}{ticket}"
    return None


@router.websocket("/notifications/{tenant_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    tenant_id: str,
    token: Optional[str] = Query(default=None),
):
    """
    WebSocket endpoint para el frontend.

    `tenant_id` es el organization_id (UUID) del tenant. La credencial es un
    ticket de un solo uso (`POST /auth/ws-ticket`) que viaja en el subprotocolo
    `lejovista.ticket.<ticket>`, no en el query string.

    `?token=` sigue admitiéndose solo si `WS_ALLOW_QUERY_TOKEN` está encendido,
    para la ventana en la que conviven navegadores con la app vieja ya cargada.
    Por defecto está apagado.
    """
    ticket = _ticket_from_subprotocols(websocket)
    if ticket is None and token and settings.WS_ALLOW_QUERY_TOKEN:
        logger.warning(
            "WebSocket autenticado por query string (mecanismo obsoleto). "
            "Apagar WS_ALLOW_QUERY_TOKEN en cuanto no queden clientes viejos."
        )
        ticket = token

    subprotocolo = await _authenticate_ws(ticket, tenant_id)
    if subprotocolo is None:
        # 4401: violación de política / no autorizado en el handshake
        await websocket.close(code=4401)
        return

    await manager.connect(
        websocket,
        tenant_id,
        # Solo se confirma el subprotocolo si el cliente lo propuso: devolver uno
        # que no ofreció rompe el handshake.
        subprotocol=subprotocolo if _ticket_from_subprotocols(websocket) else None,
    )
    try:
        while True:
            # Mantener la conexión abierta, podemos recibir un ping del cliente
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, tenant_id)
