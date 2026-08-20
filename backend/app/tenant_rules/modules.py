"""Catálogo de módulos habilitables por organización.

El catálogo vive en el backend a propósito: si el frontend tuviera su propia
lista, las dos se desincronizarían y un módulo "apagado" seguiría sirviendo
datos por la API. El frontend lo consume por `GET /organizations/my/modules`.

**Vacío o ausente = todos habilitados.** Es el mismo contrato que
`campaign_rules_overrides`: ninguna organización existente cambia de
comportamiento hasta que alguien la toque explícitamente.

Ojo con la diferencia entre ocultar y apagar: filtrar el menú y redirigir la
ruta es experiencia de usuario. Lo único que es seguridad es `require_module`
en el router — sin eso, el módulo está escondido, no apagado.
"""
from typing import Any, Dict, List, Optional

AVAILABLE_MODULES: Dict[str, str] = {
    "resumen": "Resumen / dashboard",
    "campanas": "Campañas",
    "chat": "Chat analista",
    "chat_creacion": "Creación de campañas por chat",
    "reporte": "Reporte ejecutivo",
    "diagnosticos": "Diagnósticos de IA",
    "acciones": "Acciones sugeridas",
    "ai_usage": "Consumo de IA",
    "settings": "Configuración",
}

# `settings` no se puede apagar: es donde se conecta Meta y se administra la
# organización. Una org sin Configuración queda sin forma de repararse a sí
# misma desde la app.
UNDISABLEABLE_MODULES = frozenset({"settings"})


def normalize_enabled_modules(raw: Any) -> Optional[List[str]]:
    """Interpreta el valor crudo de `Organization.enabled_modules`.

    Devuelve `None` cuando significa "todos" (nulo, vacío o basura), y si no una
    lista saneada: solo claves del catálogo, sin duplicados, en el orden del
    catálogo, y siempre con los módulos no apagables incluidos.

    Que la basura caiga a "todos" es deliberado: el modo degradado de un dato
    corrupto tiene que ser el comportamiento de siempre, no dejar al cliente
    sin producto.
    """
    if not raw or not isinstance(raw, (list, tuple, set)):
        return None
    valid = {m for m in raw if isinstance(m, str) and m in AVAILABLE_MODULES}
    if not valid:
        return None
    valid |= set(UNDISABLEABLE_MODULES)
    return [m for m in AVAILABLE_MODULES if m in valid]


def enabled_modules_for(organization: Any) -> List[str]:
    """Lista efectiva de módulos de una organización, siempre completa."""
    normalized = normalize_enabled_modules(getattr(organization, "enabled_modules", None))
    return normalized if normalized is not None else list(AVAILABLE_MODULES)


def is_module_enabled(organization: Any, module: str) -> bool:
    if module not in AVAILABLE_MODULES:
        # Un módulo que no existe en el catálogo no se puede habilitar. Es un
        # error de programación, y fallar cerrado es lo correcto.
        return False
    return module in enabled_modules_for(organization)
