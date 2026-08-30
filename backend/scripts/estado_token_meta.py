"""Estado real de los tokens de Meta, desde la terminal.

El panel dice "Token de Meta Expirado" y no dice por qué. Y "expirado" era, hasta
hace poco, el cajón donde caían tres cosas distintas: un token muerto de verdad,
un rate limit clasificado mal, y —la más frecuente— un `debug_token` que no se
pudo responder porque preguntamos con la app de la agencia por un token que
emitió la app del cliente.

Este script las separa. Por cada cuenta muestra lo que hay guardado, lo que Meta
contesta ahora mismo, y cuando `debug_token` no alcanza, sondea el token contra
la cuenta publicitaria — que es lo único que de verdad importa: si el sync de
mañana va a funcionar.

    python scripts/estado_token_meta.py            # solo mira
    python scripts/estado_token_meta.py --reparar  # devuelve a ACTIVE las sanas
"""
import asyncio
import datetime
import sys
from pathlib import Path

# `python scripts/x.py` pone `scripts/` en sys.path, NO el directorio actual, así
# que `app` no se encuentra aunque estés parado en la raíz del backend. Mismo
# patrón que `estado_historial.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.ad_account import AdAccount
from app.models.organization import Organization
from app.services.meta_ads.token_inspection import (
    ESTADO_INDETERMINADO,
    ESTADO_INVALIDO,
    ESTADO_VALIDO,
    aplicar_inspeccion,
    inspect_token_expiration,
)

VEREDICTOS = {
    ESTADO_VALIDO: "✓ el token funciona",
    ESTADO_INVALIDO: "✗ Meta dice que el token está expirado o revocado",
    ESTADO_INDETERMINADO: "? no se pudo determinar",
}


def _caducidad(expires_at) -> str:
    if expires_at is None:
        return "sin caducidad"
    dias = (expires_at - datetime.datetime.utcnow()).days
    return f"{expires_at:%Y-%m-%d %H:%M} UTC (faltan {max(0, dias)} días)"


async def _informe(db, acc: AdAccount, org_slug: str, reparar: bool) -> None:
    print(f"\ncuenta {acc.external_account_id} ({acc.name})  ·  org {org_slug}")
    print("=" * 68)

    cred = acc.meta_credential
    if not cred:
        print("  sin credencial de Meta asociada — no hay token que revisar.")
        return

    print(f"  estado guardado:  {acc.status}  ·  credencial {cred.status}")
    print(f"  caducidad guardada: {_caducidad(cred.token_expires_at)}")
    if acc.last_sync_error_code:
        print(f"  último error de sync: {acc.last_sync_error_code} — "
              f"{(acc.last_sync_error_detail or '')[:200]}")

    # El par app propia. Con el app_id solo, `app_credentials` devuelve
    # (None, None) y se termina preguntando con la app global: el mismo bug con
    # otra cara, y en silencio.
    app_id, app_secret = cred.app_credentials
    if app_id:
        print(f"  app propia:       {app_id} (con secret)")
    elif cred.app_id:
        print(f"  app propia:       {cred.app_id} ⚠ SIN SECRET — se usa la app "
              f"de la agencia, que no puede depurar este token")
    else:
        print(f"  app propia:       ninguna → se usa la global "
              f"({settings.META_APP_ID[:8]}…)")

    raw_token = cred.get_access_token()
    if not raw_token:
        print("  ✗ no hay token guardado.")
        return

    inspeccion = await inspect_token_expiration(
        raw_token, app_id, app_secret, acc.external_account_id
    )
    estado = inspeccion["estado"]
    print(f"\n  Meta ahora mismo: {VEREDICTOS[estado]}")
    if estado == ESTADO_VALIDO:
        print(f"    caducidad real: {_caducidad(inspeccion['expires_at'])}")
        if inspeccion["scopes"]:
            print(f"    permisos:       {', '.join(inspeccion['scopes'])}")
    if inspeccion["error"]:
        print(f"    detalle:        {inspeccion['error'][:250]}")

    mal_marcada = estado == ESTADO_VALIDO and (
        acc.status == "EXPIRED"
        or acc.last_sync_error_code == 190
        or cred.token_expires_at != inspeccion["expires_at"]
    )
    if not mal_marcada:
        return

    if not reparar:
        print("    ⚠ la cuenta está marcada mal. `--reparar` la corrige "
              "(o esperá la revisión de las 08:00).")
        return

    aplicar_inspeccion(acc, cred, inspeccion)
    db.add(acc)
    db.add(cred)
    await db.commit()
    print(f"    ✓ reparada: estado {acc.status}, caducidad "
          f"{_caducidad(cred.token_expires_at)}")


async def main() -> None:
    reparar = "--reparar" in sys.argv

    if settings.MOCK_SERVICES:
        print("MOCK_SERVICES=true: no se le pregunta nada a Meta y todo lo que "
              "sigue es simulado. Corré esto contra el entorno real.")

    async with async_session() as db:
        cuentas = (await db.execute(
            select(AdAccount).where(AdAccount.status != "ARCHIVED")
        )).scalars().all()
        if not cuentas:
            print("No hay cuentas de anuncios activas.")
            return
        slugs = dict((await db.execute(
            select(Organization.id, Organization.slug)
        )).all())
        for acc in cuentas:
            await _informe(db, acc, slugs.get(acc.organization_id, "?"), reparar)

    if not reparar:
        print("\n(solo lectura — nada se modificó)")


if __name__ == "__main__":
    asyncio.run(main())
