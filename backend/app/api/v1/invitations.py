import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.database import get_db
from app.models.invitation import Invitation, hash_invitation_token
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.models.user import User
from app.models.ad_account import AdAccount
from app.models.user_ad_account import UserAdAccount
from app.schemas.invitation_schemas import InvitationAcceptRequest
from app.schemas.schemas import TokenResponse
from app.schemas.response_schemas import InvitationDetailsResponse
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    set_refresh_cookie,
)
from app.api.v1.auth import enrich_user

router = APIRouter(prefix="/invitations", tags=["invitations"])


@router.get("/{token}", response_model=InvitationDetailsResponse)
async def get_invitation_details(
    token: str, db: AsyncSession = Depends(get_db)
):
    """Consulta pública de los detalles de una invitación por su token."""
    q = select(Invitation).where(Invitation.token_hash == hash_invitation_token(token))
    inv = (await db.execute(q)).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invitación no encontrada.")

    org = await db.get(Organization, inv.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    return {
        "email": inv.email,
        "role": inv.role,
        "organization_name": org.name,
        "organization_slug": org.slug,
        "is_expired": inv.is_expired,
        "is_accepted": inv.is_accepted,
    }


@router.post("/{token}/accept", response_model=TokenResponse)
async def accept_invitation(
    token: str,
    payload: InvitationAcceptRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Acepta una invitación, creando el usuario (si no existe) y vinculándolo con el rol asignado."""
    q = select(Invitation).where(Invitation.token_hash == hash_invitation_token(token))
    inv = (await db.execute(q)).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invitación no encontrada.")

    if inv.is_expired:
        raise HTTPException(
            status_code=400, detail="Esta invitación ha expirado. Solicita una nueva."
        )

    # CONSUMO ATÓMICO. Antes esto eran dos pasos —leer `accepted_at`, y más
    # abajo escribirlo— con todo el alta del usuario en medio: dos peticiones
    # simultáneas con el mismo enlace leían las dos "sin aceptar" y las dos
    # seguían adelante. El UPDATE condicional hace la comprobación y la marca en
    # una sola operación: exactamente una de las dos cambia una fila.
    marcada = await db.execute(
        update(Invitation)
        .where(Invitation.id == inv.id, Invitation.accepted_at.is_(None))
        .values(accepted_at=datetime.datetime.utcnow())
    )
    if marcada.rowcount != 1:
        raise HTTPException(
            status_code=400, detail="Esta invitación ya fue aceptada previamente."
        )

    org = await db.get(Organization, inv.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    # 1. Comprobar si el usuario ya existe con ese email
    q_user = select(User).where(User.email == inv.email)
    user = (await db.execute(q_user)).scalar_one_or_none()

    if user:
        # Si ya existe, verificar si ya está en esta organización
        q_ou = select(OrganizationUser).where(
            OrganizationUser.user_id == user.id,
            OrganizationUser.organization_id == org.id,
        )
        existing_ou = (await db.execute(q_ou)).scalar_one_or_none()
        if existing_ou:
            org_user = existing_ou
            org_user.role = inv.role
        else:
            org_user = OrganizationUser(
                user_id=user.id,
                organization_id=org.id,
                role=inv.role,
            )
            db.add(org_user)
    else:
        user = User(
            email=inv.email,
            hashed_password=get_password_hash(payload.password),
            full_name=payload.full_name,
        )
        db.add(user)
        await db.flush()

        org_user = OrganizationUser(
            user_id=user.id,
            organization_id=org.id,
            role=inv.role,
        )
        db.add(org_user)

    await db.flush()

    # Conceder las cuentas publicitarias que fijó quien invitó. El modelo de
    # concesiones es fail-closed (ver UserAdAccount): sin esto el invitado entra
    # y no ve ninguna cuenta. `ad_account_ids = None` significa "todas las de la
    # organización" —el caso del equipo propio—; una lista acota, que es el caso
    # de un cliente que solo debe ver la suya.
    if inv.ad_account_ids is None:
        conceder = (await db.execute(
            select(AdAccount.id).where(AdAccount.organization_id == org.id)
        )).scalars().all()
    else:
        # Se revalida contra la organización: entre invitar y aceptar pueden
        # haber pasado días y una cuenta puede haberse borrado o movido.
        conceder = (await db.execute(
            select(AdAccount.id).where(
                AdAccount.organization_id == org.id,
                AdAccount.id.in_(inv.ad_account_ids or [""]),
            )
        )).scalars().all()

    ya_tiene = set((await db.execute(
        select(UserAdAccount.ad_account_id).where(UserAdAccount.user_id == user.id)
    )).scalars().all())
    for acc_id in conceder:
        if acc_id not in ya_tiene:
            db.add(UserAdAccount(
                user_id=user.id, ad_account_id=acc_id, organization_id=org.id,
            ))

    try:
        await db.commit()
    except IntegrityError:
        # La restricción única de membresía disparó: otra transacción insertó la
        # misma (usuario, organización) entre nuestra comprobación y nuestro
        # INSERT. La base es la que decide, y decidió que la fila ya existe.
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="Esta invitación ya fue aceptada previamente."
        )

    access_token = create_access_token(subject=user.id, org=org.id)
    refresh_token = create_refresh_token(subject=user.id, org=org.id)
    set_committed_value(user, "organizations", [org_user])
    set_refresh_cookie(response, request, refresh_token)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": enrich_user(user),
    }
