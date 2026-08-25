"""Qué páginas y qué números de WhatsApp alcanza una cuenta publicitaria.

`MetaPage` y `WhatsAppNumber` son por ORGANIZACIÓN. Eso alcanzaba mientras una
organización fuera un solo cliente; en una que aloja las cuentas de varios, el
dropdown de "Crear campaña" mezclaba las páginas y los números de todos. Las
tablas de enlace (`MetaPageAdAccount`, `WhatsAppNumberAdAccount`) son lo que
acota, y esto es el único lugar donde se arma ese filtro.

Único a propósito: son ocho los sitios que preguntan "qué páginas hay", y con
ocho criterios escritos a mano alcanza con que uno quede desalineado para
reabrir el leak. Igual que `granted_ad_account_ids` para las cuentas.
"""
from typing import Iterable, Optional

from sqlalchemy import Select, select

from app.models.meta_page_ad_account import MetaPageAdAccount
from app.models.whatsapp_number_ad_account import WhatsAppNumberAdAccount

# Un `IN ()` vacío no es SQL válido en todos los motores y, peor, un filtro que
# se omite "porque la lista está vacía" devuelve TODO. Con este centinela, sin
# cuentas no se alcanza nada — que es lo correcto y lo seguro.
_NINGUNA = [""]


def _ids(ad_account_ids: Optional[Iterable[str]]) -> list:
    ids = list(ad_account_ids or [])
    return ids or _NINGUNA


def page_ids_of_accounts(ad_account_ids: Optional[Iterable[str]]) -> Select:
    """Subconsulta con los `MetaPage.id` que alcanzan esas cuentas."""
    return select(MetaPageAdAccount.meta_page_id).where(
        MetaPageAdAccount.ad_account_id.in_(_ids(ad_account_ids))
    )


def whatsapp_number_ids_of_accounts(ad_account_ids: Optional[Iterable[str]]) -> Select:
    """Subconsulta con los `WhatsAppNumber.id` que alcanzan esas cuentas."""
    return select(WhatsAppNumberAdAccount.whatsapp_number_id).where(
        WhatsAppNumberAdAccount.ad_account_id.in_(_ids(ad_account_ids))
    )


async def link_number_to_accounts(db, number_id: str, organization_id: str, ad_account_ids) -> None:
    """Deja el alcance de un número EXACTAMENTE en esas cuentas (reemplaza).

    Los números MANUAL —el camino real hoy, porque el descubrimiento automático
    necesita un permiso que la mayoría de los tokens no trae— no se pueden
    reconciliar contra Meta: no hay nada con qué compararlos. Su alcance se fija
    acá, donde se cargan o se editan.
    """
    pedidas = set(ad_account_ids or [])
    actuales = {
        fila.ad_account_id: fila
        for fila in (await db.execute(
            select(WhatsAppNumberAdAccount).where(
                WhatsAppNumberAdAccount.whatsapp_number_id == number_id
            )
        )).scalars().all()
    }
    for acc_id, fila in actuales.items():
        if acc_id not in pedidas:
            await db.delete(fila)
    for acc_id in pedidas - set(actuales):
        db.add(WhatsAppNumberAdAccount(
            whatsapp_number_id=number_id,
            ad_account_id=acc_id,
            organization_id=organization_id,
        ))


async def organizacion_sin_cuentas(db, organization_id: str) -> bool:
    """¿La organización todavía no conectó ninguna cuenta publicitaria?

    Es la única situación en la que acotar por cuenta no tiene sentido: sin
    cuentas no hay clientes que separar, y una organización recién creada puede
    cargar su número de WhatsApp ANTES de conectar Meta. Sin esta excepción ese
    número se cargaba y desaparecía de la lista en el mismo paso.
    """
    from app.models.ad_account import AdAccount

    return (await db.execute(
        select(AdAccount.id).where(
            AdAccount.organization_id == organization_id
        ).limit(1)
    )).scalar_one_or_none() is None


async def adoptar_huerfanos(db, organization_id: str, ad_account_id: str) -> int:
    """Enlaza a `ad_account_id` las páginas y números de la organización que no
    están enlazados a ninguna cuenta.

    Es el otro lado de `organizacion_sin_cuentas`: lo que se cargó cuando no
    había cuentas queda sin dueño, y al conectar la primera hay que dárselo, o
    desaparecería para siempre en cuanto la excepción deje de aplicar.

    Solo adopta lo que no tiene dueño. Una página o un número que ya pertenecen
    a otra cuenta no se tocan: eso sería el leak al revés.
    """
    from app.models.meta_page import MetaPage
    from app.models.whatsapp_number import WhatsAppNumber

    adoptados = 0
    for origen, enlace, columna in (
        (MetaPage, MetaPageAdAccount, "meta_page_id"),
        (WhatsAppNumber, WhatsAppNumberAdAccount, "whatsapp_number_id"),
    ):
        huerfanos = (await db.execute(
            select(origen.id).where(
                origen.organization_id == organization_id,
                origen.id.notin_(select(getattr(enlace, columna))),
            )
        )).scalars().all()
        for propio_id in huerfanos:
            db.add(enlace(
                **{columna: propio_id},
                ad_account_id=ad_account_id,
                organization_id=organization_id,
            ))
            adoptados += 1
    return adoptados


async def condiciones_de_pagina(db, organization_id: str, ad_account_ids) -> list:
    """Condiciones WHERE para acotar `MetaPage` a lo que ve un usuario.

    Devuelve una lista (posiblemente vacía) en vez de aplicar el filtro siempre,
    porque la excepción de `organizacion_sin_cuentas` tiene que valer en TODOS
    los sitios que leen páginas. Aplicarla en el listado y olvidarla en el PATCH
    daba una lista con la página y un 404 al tocarla.
    """
    if await organizacion_sin_cuentas(db, organization_id):
        return []
    from app.models.meta_page import MetaPage

    return [MetaPage.id.in_(page_ids_of_accounts(ad_account_ids))]


async def condiciones_de_numero(db, organization_id: str, ad_account_ids) -> list:
    """Lo mismo para `WhatsAppNumber`."""
    if await organizacion_sin_cuentas(db, organization_id):
        return []
    from app.models.whatsapp_number import WhatsAppNumber

    return [WhatsAppNumber.id.in_(whatsapp_number_ids_of_accounts(ad_account_ids))]
