"""Servicio de envío de correos transaccionales vía Brevo (Fase 6.0).

Best-effort: si el envío falla o la API key no está configurada, se registra en
logs y no tumba la operación principal (invitación, aviso de token, etc.),
siguiendo el mismo patrón que `notification_service.py`.
Bajo `MOCK_SERVICES=true`, nunca llama a la red y simula éxito.
"""
import logging
from typing import Optional
import httpx

from app.config import settings

logger = logging.getLogger("lejovista.email")
BREVO_SMTP_URL = "https://api.brevo.com/v3/smtp/email"


def email_configurado() -> bool:
    """¿Hay proveedor de correo? Sirve para avisar ANTES de prometer un envío
    (p. ej. el flujo de recuperar contraseña, que sin esto no lleva a nada)."""
    return bool(settings.MOCK_SERVICES or settings.BREVO_API_KEY)


async def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    to_name: Optional[str] = None,
    text_content: Optional[str] = None,
) -> bool:
    """Envía un correo transaccional. Devuelve True si se envió (o se mockeó),
    False si falló o si NO HAY con qué enviarlo.

    Sin `BREVO_API_KEY` devolvía `True`, igual que en modo mock. Los dos casos no
    son lo mismo: en mock no hay nada que enviar, y sin API key en producción hay
    un correo que alguien está esperando y nunca va a llegar. Devolver `True`
    hacía que el panel dijera "invitación enviada" y que los avisos de token
    vencido se dieran por entregados — todo en silencio.
    """
    if settings.MOCK_SERVICES:
        logger.info(
            "[MOCK] Envío de email a %s (%s) — Asunto: '%s'",
            to_email,
            to_name or "sin nombre",
            subject,
        )
        return True

    if not settings.BREVO_API_KEY:
        logger.warning(
            "No se envió el email a %s ('%s'): BREVO_API_KEY no está configurada. "
            "Las invitaciones y los avisos de token no están saliendo.",
            to_email, subject,
        )
        return False

    payload = {
        "sender": {
            "name": settings.EMAIL_FROM_NAME,
            "email": settings.EMAIL_FROM,
        },
        "to": [
            {
                "email": to_email,
                "name": to_name or to_email,
            }
        ],
        "subject": subject,
        "htmlContent": html_content,
    }
    if text_content:
        payload["textContent"] = text_content

    headers = {
        "api-key": settings.BREVO_API_KEY,
        "Content-Type": "application/json",
        "accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(BREVO_SMTP_URL, json=payload, headers=headers)
            if res.status_code in (200, 201, 202):
                logger.info("Email enviado exitosamente a %s vía Brevo.", to_email)
                return True
            logger.error(
                "Brevo API error %s al enviar email a %s: %s",
                res.status_code,
                to_email,
                res.text,
            )
            return False
    except Exception as exc:
        logger.exception("Excepción enviando email a %s vía Brevo: %s", to_email, exc)
        return False
