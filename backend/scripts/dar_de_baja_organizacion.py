"""Dar de baja una organización residual, sin llevarse datos por delante.

La base de producción tiene dos organizaciones: la real y una `default` que
quedó de una instalación anterior. El usuario pertenece a las dos, y hasta ahora
cuál quedaba activa en la sesión no estaba fijado — así fue como una API key de
OpenAI se guardó en la equivocada y el analista contestó con plantillas durante
días sin que nada se viera roto.

Este script la retira. Dos decisiones a propósito:

* **Read-only salvo que se pida lo contrario.** Sin `--aplicar` solo informa.
* **No borra: marca `INACTIVE` y quita las membresías.** `organizations` tiene
  `ondelete="CASCADE"` hacia media base —cuentas, snapshots, insights, chats,
  jobs—, así que un DELETE sobre la organización equivocada es irreversible y
  silencioso. Y se NIEGA a seguir si la organización tiene datos: entonces no es
  residual y darla de baja es una decisión de negocio, no una limpieza.

    python scripts/dar_de_baja_organizacion.py default
    python scripts/dar_de_baja_organizacion.py default --aplicar
"""
import asyncio
import sys

from sqlalchemy import func, select

from app.database import async_session
from app.models.ad_account import AdAccount
from app.models.campaign_snapshot import CampaignSnapshot
from app.models.meta_page import MetaPage
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.sync_job import SyncJob
from app.models.user import User

# Lo que hace que una organización NO sea residual. Si alguna de estas tiene
# filas, el script no la toca: perder esto en silencio sería peor que convivir
# con una organización de más.
_SEÑALES = [
    ("cuentas publicitarias", AdAccount),
    ("snapshots de campaña", CampaignSnapshot),
    ("páginas de Meta", MetaPage),
    ("trabajos de sincronización", SyncJob),
]


async def _contar(db, modelo, org_id: str) -> int:
    return (await db.execute(
        select(func.count()).select_from(modelo)
        .where(modelo.organization_id == org_id)
    )).scalar_one()


async def dar_de_baja(db, slug: str, aplicar: bool) -> int:
    """El trabajo real, separado de `main` para poder testearlo.

    `main` abre `async_session`, que apunta a la base de producción; un test que
    tuviera que pasar por ahí no probaría nada o probaría lo que no debe.
    """
    org = (await db.execute(
        select(Organization).where(Organization.slug == slug)
    )).scalar_one_or_none()
    if org is None:
        print(f"No existe ninguna organización con slug '{slug}'.")
        return 1

    print(f"organización '{org.slug}' ({org.name})  ·  estado {org.status}")
    print("=" * 60)

    con_datos = []
    for etiqueta, modelo in _SEÑALES:
        n = await _contar(db, modelo, org.id)
        print(f"  {etiqueta:32} {n}")
        if n:
            con_datos.append(etiqueta)

    miembros = (await db.execute(
        select(User.email, OrganizationUser.role)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(OrganizationUser.organization_id == org.id)
    )).all()
    print(f"  {'miembros':32} {len(miembros)}")
    for email, rol in miembros:
        print(f"      {email} ({rol})")

    if con_datos:
        print()
        print(">>> NO SE TOCA: esta organización tiene " + ", ".join(con_datos) + ".")
        print("    No es residual. Darla de baja es una decisión de negocio,")
        print("    no una limpieza, y este script no la toma por vos.")
        return 1

    print()
    if not aplicar:
        print(">>> RESIDUAL: sin datos propios. Se puede dar de baja.")
        print(f"    Para hacerlo: python scripts/dar_de_baja_organizacion.py {slug} --aplicar")
        print("    (marca INACTIVE y quita las membresías; NO borra la fila)")
        return 0

    for ou in (await db.execute(
        select(OrganizationUser).where(OrganizationUser.organization_id == org.id)
    )).scalars().all():
        await db.delete(ou)
    org.status = "INACTIVE"
    db.add(org)
    await db.commit()
    print(f">>> LISTO: '{slug}' quedó INACTIVE y sin miembros.")
    print("    La fila se conserva: `organizations` cascadea a media base y")
    print("    un DELETE acá es irreversible.")
    return 0


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2

    async with async_session() as db:
        return await dar_de_baja(db, args[0], aplicar="--aplicar" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
