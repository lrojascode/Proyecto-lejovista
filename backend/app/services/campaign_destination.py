import logging
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage

logger = logging.getLogger(__name__)


async def resolve_destination(
    db: AsyncSession, ad_account: AdAccount, organization_id: str
) -> Tuple[Optional[MetaPage], Optional[str]]:
    """Resuelve la página de Facebook (+ WhatsApp) de destino de un anuncio sin
    que el llamador tenga que adivinar (regla AI-1: nunca inventar un dato que
    determina a quién le llega el anuncio).

    Orden: 1) la página predeterminada de la cuenta (`AdAccount.default_page_id`,
    configurable en Settings → "Páginas y WhatsApp") si está activa y tiene
    WhatsApp — si la default existe pero no tiene WhatsApp, se corta ahí (no se
    cae en silencio a otra página); 2) sin default, la regla original:
    exactamente 1 página activa con WhatsApp en la organización.

    Devuelve `(page, None)` si se resolvió sin ambigüedad, o `(None, reason)`
    con un mensaje en español explicando qué falta — el llamador decide qué
    hacer con la ambigüedad (marcar NEEDS_INPUT, preguntar en el chat, etc.).
    """
    if ad_account.default_page_id:
        default_page = await db.get(MetaPage, ad_account.default_page_id)
        if default_page and default_page.status == "ACTIVE":
            if default_page.whatsapp_number:
                return default_page, None
            return None, (
                f"La página predeterminada de la cuenta ('{default_page.name}') no tiene un número "
                "de WhatsApp conectado. Agrégalo en Configuración → Páginas y WhatsApp."
            )

    pages = (
        await db.execute(
            select(MetaPage).where(
                MetaPage.organization_id == organization_id,
                MetaPage.status == "ACTIVE",
            )
        )
    ).scalars().all()
    pages_with_whatsapp = [p for p in pages if p.whatsapp_number]
    if len(pages_with_whatsapp) != 1:
        return None, (
            "Se requiere exactamente 1 página de Facebook activa con WhatsApp conectado "
            f"para resolver el destino sin ambigüedad (se encontraron {len(pages_with_whatsapp)}). "
            "Configura una página predeterminada para la cuenta en Configuración → Páginas y WhatsApp."
        )
    return pages_with_whatsapp[0], None
