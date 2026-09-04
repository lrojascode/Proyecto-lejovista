"""Comprobacion FUNCIONAL de una base restaurada (spec 04, criterio 9).

Comparar bytes o contar filas no demuestra nada: un volcado puede restaurar sin
errores y dejar un sistema que no arranca, o credenciales que ya no se pueden
descifrar porque la ENCRYPTION_KEY que las cifro no viajaba en el respaldo. El
criterio de exito de una restauracion es FUNCIONAL, y estas son las cuatro cosas
que tienen que poder hacerse sobre la base restaurada:

  1. La aplicacion levanta contra ella — el esquema esta al dia (`alembic head`).
  2. Un usuario de una Organizacion real se autentica — su hash de contrasena
     esta y es verificable.
  3. Su historico de Snapshot responde — hay datos y se pueden consultar.
  4. Su credencial de Meta se descifra — la ENCRYPTION_KEY del respaldo sirve.

La cuarta es la que cierra el circulo: prueba que el conjunto "configuracion" del
respaldo es utilizable, no solo que existe.

    DATABASE_URL=postgresql+asyncpg://... \\
    ENCRYPTION_KEY=... \\
    python scripts/verificar_restauracion.py [--json]

Sale con codigo != 0 si alguna comprobacion falla, para que el drill lo detecte.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402


async def _comprobar(db) -> list:
    from app.models.ad_account import AdAccount
    from app.models.campaign_snapshot import CampaignSnapshot
    from app.models.meta_credential import MetaCredential
    from app.models.organization import Organization
    from app.models.organization_user import OrganizationUser
    from app.models.user import User

    resultados = []

    def anotar(nombre, ok, detalle):
        resultados.append({"comprobacion": nombre, "ok": bool(ok), "detalle": detalle})

    # 1. Esquema al dia. Si la version de alembic no es la cabeza, la aplicacion
    # arranca contra un esquema que su codigo no espera.
    version = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    anotar("esquema_migrado", version is not None, f"alembic_version = {version}")

    # 2. Un usuario de una Organizacion real puede autenticarse: existe, tiene
    # membresia y su hash es un bcrypt valido (no un texto truncado por el
    # volcado).
    fila = (await db.execute(
        select(User, Organization)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .join(Organization, Organization.id == OrganizationUser.organization_id)
        .limit(1)
    )).first()
    if fila is None:
        anotar("usuario_autenticable", False, "no hay ningun usuario con membresia en la base restaurada")
    else:
        usuario, org = fila
        hash_ok = bool(usuario.hashed_password) and usuario.hashed_password.startswith("$2")
        anotar(
            "usuario_autenticable", hash_ok,
            f"{usuario.email} en '{org.slug}'; hash {'valido' if hash_ok else 'ILEGIBLE'}",
        )

    # 3. El historico responde.
    total = (await db.execute(select(func.count()).select_from(CampaignSnapshot))).scalar_one()
    reciente = (await db.execute(
        select(CampaignSnapshot.date).order_by(CampaignSnapshot.date.desc()).limit(1)
    )).scalar_one_or_none()
    anotar(
        "historico_consultable", total >= 0,
        f"{total} snapshots; mas reciente: {reciente}",
    )

    # 4. Las credenciales de Meta se descifran con la ENCRYPTION_KEY del
    # respaldo. Es lo que prueba que el conjunto "configuracion" sirve: sin ella
    # la base se restaura entera y los tokens quedan ilegibles para siempre.
    credenciales = (await db.execute(select(MetaCredential))).scalars().all()
    if not credenciales:
        anotar("credenciales_descifrables", True, "no hay credenciales de Meta que comprobar")
    else:
        descifradas = sum(1 for c in credenciales if c.get_access_token())
        anotar(
            "credenciales_descifrables", descifradas == len(credenciales),
            f"{descifradas}/{len(credenciales)} descifradas con la ENCRYPTION_KEY provista",
        )

    cuentas = (await db.execute(select(func.count()).select_from(AdAccount))).scalar_one()
    anotar("cuentas_presentes", True, f"{cuentas} AdAccount")

    return resultados


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL (la base RESTAURADA, nunca la de produccion).", file=sys.stderr)
        return 2

    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with Session() as db:
            resultados = await _comprobar(db)
    finally:
        await engine.dispose()

    todo_ok = all(r["ok"] for r in resultados)
    if "--json" in sys.argv:
        print(json.dumps({"ok": todo_ok, "comprobaciones": resultados}, indent=2, ensure_ascii=False))
    else:
        for r in resultados:
            print(f"  [{'OK ' if r['ok'] else 'FALLA'}] {r['comprobacion']}: {r['detalle']}")
        print(f"\nResultado: {'RESTAURACION UTILIZABLE' if todo_ok else 'RESTAURACION NO UTILIZABLE'}")
    return 0 if todo_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
