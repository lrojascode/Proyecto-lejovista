"""URLs públicas que el backend arma para mandar por correo."""
from app.config import settings

# Hosts donde no hay TLS: desarrollo local y túneles de prueba.
_LOCALES = ("localhost", "127.0.0.1", "0.0.0.0")


def _es_local(host: str) -> bool:
    return any(host.startswith(l) for l in _LOCALES)


def base_url() -> str:
    """Origen público de la app. `https` salvo en local."""
    esquema = "http" if _es_local(settings.BASE_DOMAIN) else "https"
    return f"{esquema}://{settings.BASE_DOMAIN}"


def invitation_url(token: str) -> str:
    """Enlace de invitación, sobre el DOMINIO BASE y no sobre el subdominio del
    cliente.

    `accept_invitation` busca la invitación **solo por token** — la organización
    sale de la propia invitación, así que el enlace funciona desde cualquier
    hostname. Apuntarlo al subdominio del cliente obligaba a tener DNS y
    certificado para ese cliente ANTES de que pudiera entrar por primera vez,
    que es justo el momento en el que todavía no hay nada configurado.

    Y era `http://` fijo: en producción eso es un enlace sin TLS por el que
    viaja la contraseña que el cliente acaba de elegir.
    """
    return f"{base_url()}/invitations/{token}"


def password_reset_url(token: str) -> str:
    """Enlace para elegir contraseña nueva.

    Mismo criterio que `invitation_url`: va sobre el dominio BASE y no sobre el
    subdominio del cliente. `reset_password` resuelve todo por el token —no
    necesita saber la organización— y apuntar al subdominio obligaría a tener
    DNS y certificado del cliente resueltos justo cuando la persona está afuera
    y no puede pedir ayuda por la app.
    """
    return f"{base_url()}/reset-password/{token}"
