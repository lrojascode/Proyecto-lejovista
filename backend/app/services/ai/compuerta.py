"""Compuerta única antes de gastar IA (spec 10, criterios 13–15).

Reúne las dos comprobaciones que preceden a cualquier consumo del modelo —el
presupuesto mensual y el límite de frecuencia— y el disparo de las alertas
escalonadas. Existe para que ningún camino nuevo tenga que acordarse de hacer
las dos: se llama a `autorizar`, y lo que haya que comprobar se comprueba acá.

**El bloqueo no apaga el producto.** Lo que se bloquea son las llamadas al
modelo; todo lo que el sistema calcula de forma determinista —métricas, estados
de campaña, tablas, el clasificador, los informes— sigue funcionando (criterio
15). El cliente pierde el redactor, no el tablero, y se le dice exactamente eso.

**Las alertas se emiten aunque no haya bloqueo.** Son avisos a la agencia, no al
cliente: el 70 % y el 90 % existen para poder intervenir ANTES del corte, y
emitirlos junto con el bloqueo no serviría de nada.
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.services.ai import limites, presupuesto
from app.services.email_service import send_email

logger = logging.getLogger("lejovista.ai.compuerta")


async def autorizar(
    db: AsyncSession,
    org: Organization,
    *,
    avisar: bool = True,
) -> presupuesto.EstadoDeConsumo:
    """Comprueba presupuesto y frecuencia. Levanta si no se puede llamar.

    Levanta `PresupuestoDeIAAgotado` o `LimiteDeFrecuencia`; las dos traen su
    `as_detail()` para que la superficie responda con honestidad en vez de con
    un error genérico.

    El orden importa: primero el presupuesto, que es el techo acordado con el
    cliente, y después la frecuencia, que es una protección operativa. Con el
    presupuesto agotado, informar de un límite de frecuencia sería engañoso —el
    problema no es el ritmo.
    """
    estado = await presupuesto.assert_hay_presupuesto(db, org)

    if avisar:
        await emitir_alerta_si_toca(db, org, estado)

    await limites.assert_frecuencia(org.id)
    return estado


async def emitir_alerta_si_toca(
    db: AsyncSession, org: Organization, estado: presupuesto.EstadoDeConsumo
) -> Optional[int]:
    """Emite el aviso del umbral si corresponde. Devuelve el umbral avisado.

    Best-effort en el envío, **no** en la marca: si el correo falla, el umbral
    queda marcado igual. Reintentar en cada llamada al modelo convertiría un
    proveedor de correo caído en cientos de intentos por minuto, que es
    exactamente el tipo de bucle que esta spec existe para evitar.

    El fallo del envío sí queda en el log, con el umbral y la organización: es
    información suficiente para avisar a mano.
    """
    umbral = presupuesto.aviso_pendiente(org, estado)
    if umbral is None:
        return None

    presupuesto.marcar_avisado(org, estado, umbral)
    asunto, cuerpo = presupuesto.texto_del_aviso(org, estado, umbral)

    destinatarios = list((await db.execute(
        select(User)
        .join(OrganizationUser, OrganizationUser.user_id == User.id)
        .where(
            OrganizationUser.organization_id == org.id,
            OrganizationUser.role == "ADMIN",
        )
    )).scalars().all())

    if not destinatarios:
        logger.warning(
            "Presupuesto de IA de '%s' al %d %% y no hay ningún ADMIN a quien "
            "avisar. El aviso queda solo en este registro.", org.slug, umbral,
        )
        return umbral

    html = "<p>" + cuerpo.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    for destinatario in destinatarios:
        try:
            enviado = await send_email(
                to_email=destinatario.email,
                to_name=destinatario.full_name,
                subject=asunto,
                html_content=html,
                text_content=cuerpo,
            )
            if not enviado:
                logger.warning(
                    "No se pudo enviar el aviso de presupuesto de IA (%d %%) de "
                    "'%s' a %s.", umbral, org.slug, destinatario.email,
                )
        except Exception:
            logger.exception(
                "Fallo enviando el aviso de presupuesto de IA (%d %%) de '%s'.",
                umbral, org.slug,
            )

    logger.info("Aviso de presupuesto de IA al %d %% emitido para '%s'.", umbral, org.slug)
    return umbral


async def hay_margen(db: AsyncSession, org: Organization) -> bool:
    """¿Se puede llamar al modelo? Sin levantar, para los caminos que ya caen
    a una respuesta determinista y no necesitan explicar el motivo al usuario."""
    estado = await presupuesto.estado_del_mes(db, org.id, org=org)
    return not estado.agotado


async def verificar_presupuesto_autonomo(org_id: Optional[str]) -> None:
    """Compuerta de presupuesto con sesión propia, para el borde de la llamada.

    Se invoca desde `_post_chat`, que es el punto por el que pasa **toda**
    llamada real al modelo. Ahí es donde el bloqueo deja de depender de que cada
    superficie se acuerde de comprobarlo: la spec pide que el bloqueo sea de
    backend y que no lo evite ninguna superficie, y una comprobación que hay que
    repetir en cada camino nuevo no cumple eso — cumple hasta que alguien agrega
    un camino.

    Sesión propia, igual que `token_tracking`: el cliente LLM no recibe la del
    llamador y no debería, porque una excepción de este control no puede quedar
    enredada con la transacción de negocio en curso.

    Un fallo de la comprobación NO bloquea la llamada. Es la política de la ADR-13
    para lo que no autoriza gasto irreversible: el consumo de IA es acotado,
    medido y recuperable, y dejar sin producto a todos los clientes porque la
    base no respondió sería un daño mayor que el que se evita. Lo que sí queda es
    el registro del fallo.
    """
    if not org_id:
        return

    from app.database import async_session

    try:
        async with async_session() as session:
            org = await session.get(Organization, org_id)
            if org is None:
                return
            estado = await presupuesto.assert_hay_presupuesto(session, org)
            if await emitir_alerta_si_toca(session, org, estado) is not None:
                await session.commit()
    except presupuesto.PresupuestoDeIAAgotado:
        raise
    except Exception:
        logger.exception(
            "No se pudo comprobar el presupuesto de IA de la organización %s; "
            "la llamada sigue. Si esto se repite, el techo no se está aplicando.",
            org_id,
        )


async def verificar_interruptor_de_ia(org_id: Optional[str]) -> None:
    """El interruptor operativo de IA, en el borde de la llamada (spec 13).

    Mismo sitio y mismo motivo que la compuerta de presupuesto: es el punto por
    el que pasa toda llamada real al modelo, así que un camino nuevo queda
    cubierto por construcción en vez de por acordarse.

    A diferencia del presupuesto, un fallo comprobándolo SÍ bloquea: es la
    política de fallo cerrado del plano operativo (spec 13, criterio 7), y la
    resolución del interruptor ya la aplica por su cuenta.
    """
    if not org_id:
        return

    from app.database import async_session
    from app.models.operacion import CAP_IA
    from app.services import interruptores

    async with async_session() as session:
        await interruptores.assert_encendida(session, CAP_IA, organization_id=org_id)
