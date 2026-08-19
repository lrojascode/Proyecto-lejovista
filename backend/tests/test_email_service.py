"""Tests para el servicio de email (Brevo) (Fase 6.0)."""
import pytest
import httpx
from app.config import settings
from app.services.email_service import send_email, BREVO_SMTP_URL


@pytest.mark.asyncio
async def test_email_service_mocked_under_mock_services(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_SERVICES", True)
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")

    sent = await send_email("test@example.com", "Bienvenido", "<p>Hola</p>")
    assert sent is True


@pytest.mark.asyncio
async def test_email_service_sends_to_brevo_api(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "BREVO_API_KEY", "xkeysib-testkey")

    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(201, json={"messageId": "msg-12345"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    sent = await send_email(
        to_email="cliente@example.com",
        to_name="Cliente Ejemplo",
        subject="Invitación a LejoVista",
        html_content="<p>Únete a nuestro equipo</p>",
        text_content="Únete a nuestro equipo",
    )

    assert sent is True
    assert captured["url"] == BREVO_SMTP_URL
    assert captured["headers"]["api-key"] == "xkeysib-testkey"
    assert captured["json"]["to"][0]["email"] == "cliente@example.com"
    assert captured["json"]["to"][0]["name"] == "Cliente Ejemplo"
    assert captured["json"]["subject"] == "Invitación a LejoVista"


@pytest.mark.asyncio
async def test_email_service_fails_gracefully_on_error(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_SERVICES", False)
    monkeypatch.setattr(settings, "BREVO_API_KEY", "xkeysib-testkey")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, headers=None):
            return httpx.Response(400, json={"message": "Invalid sender"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    sent = await send_email("bad@example.com", "Falla", "<p>Texto</p>")
    assert sent is False
