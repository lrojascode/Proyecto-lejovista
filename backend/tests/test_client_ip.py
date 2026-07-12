"""Tests de get_client_ip (S-4): X-Forwarded-For es spoofeable por cualquiera
que hable directo con el backend — solo debe confiarse en él cuando el peer
directo de la conexión es el proxy interno conocido (IP privada/loopback,
como Nginx dentro de la red de docker), no un peer arbitrario."""
from starlette.requests import Request

from app.api.v1.auth import get_client_ip


def _make_request(client_host: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_trusts_xff_from_private_peer():
    # El peer directo (172.x) es una IP de la red interna de docker — Nginx.
    req = _make_request("172.18.0.5", forwarded_for="8.8.8.8, 172.18.0.1")
    assert get_client_ip(req) == "8.8.8.8"


def test_trusts_xff_from_loopback_peer():
    req = _make_request("127.0.0.1", forwarded_for="8.8.8.8")
    assert get_client_ip(req) == "8.8.8.8"


def test_ignores_xff_from_public_peer():
    # El backend quedó expuesto directo (sin el proxy) y alguien manda un
    # X-Forwarded-For inventado — no debe confiarse en él. 1.1.1.1 (Cloudflare)
    # es una IP pública real, no un rango de documentación (a diferencia de
    # 203.0.113.0/24, que ipaddress.is_private trata como "privado" por RFC 5737).
    req = _make_request("1.1.1.1", forwarded_for="9.9.9.9")
    assert get_client_ip(req) == "1.1.1.1"


def test_falls_back_to_peer_without_xff():
    req = _make_request("172.18.0.5")
    assert get_client_ip(req) == "172.18.0.5"
