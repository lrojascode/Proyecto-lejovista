import logging
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_account import AdAccount
from app.models.meta_page import MetaPage
from app.services.asset_scope import page_ids_of_accounts

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
    cae en silencio a otra página); 2) sin default, exactamente 1 página activa
    con WhatsApp **de esta cuenta**.

    "De esta cuenta" y no "de la organización": con las páginas de tres clientes
    en la misma organización, la regla vieja no resolvía nunca —siempre
    encontraba más de una— y cuando encontraba exactamente una podía ser la de
    otro cliente. Acotar por cuenta la vuelve a hacer útil y deja de poder
    elegir mal.

    Devuelve `(page, None)` si se resolvió sin ambigüedad, o `(None, reason)`
    con un mensaje en español explicando qué falta — el llamador decide qué
    hacer con la ambigüedad (marcar NEEDS_INPUT, preguntar en el chat, etc.).
    """
    if ad_account.default_page_id:
        # La predeterminada también se verifica contra el alcance de la cuenta:
        # puede haber quedado apuntando a una página que la cuenta ya no
        # promociona (o que nunca fue suya, si se configuró antes de que el
        # alcance existiera).
        default_page = (await db.execute(
            select(MetaPage).where(
                MetaPage.id == ad_account.default_page_id,
                MetaPage.id.in_(page_ids_of_accounts([ad_account.id])),
            )
        )).scalar_one_or_none()
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
                MetaPage.id.in_(page_ids_of_accounts([ad_account.id])),
            )
        )
    ).scalars().all()
    pages_with_whatsapp = [p for p in pages if p.whatsapp_number]
    if len(pages_with_whatsapp) != 1:
        return None, (
            "Se requiere exactamente 1 página de Facebook activa con WhatsApp conectado "
            f"en esta cuenta para resolver el destino sin ambigüedad (se encontraron "
            f"{len(pages_with_whatsapp)}). "
            "Configura una página predeterminada para la cuenta en Configuración → Páginas y WhatsApp."
        )
    return pages_with_whatsapp[0], None
