from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.organization_user import OrganizationUser
from app.schemas.schemas import UserCreate, TokenResponse, UserLogin, UserResponse
from app.utils.security import verify_password, get_password_hash, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

def enrich_user(user: User):
    org_role = "USER"
    org_id = ""
    if hasattr(user, 'organizations') and user.organizations:
        org_role = user.organizations[0].role
        org_id = user.organizations[0].organization_id
        
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": org_role,
        "organization_id": org_id,
        "created_at": user.created_at
    }

@router.post("/signup", response_model=TokenResponse)
async def signup(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    # 1. Comprobar si el email ya existe
    q_email = select(User).where(User.email == user_in.email)
    res_email = await db.execute(q_email)
    if res_email.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El correo electrónico ya está registrado."
        )

    # 2. Comprobar si la organización slug ya existe
    q_org = select(Organization).where(Organization.slug == user_in.organization_slug)
    res_org = await db.execute(q_org)
    org = res_org.scalar_one_or_none()
    
    if not org:
        # Crear nueva organización (Tenant)
        org = Organization(
            slug=user_in.organization_slug,
            name=user_in.organization_name
        )
        db.add(org)
        await db.flush() # Para obtener el ID generado

    # 3. Crear usuario
    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name
    )
    db.add(user)
    await db.flush()
    
    # Vincular usuario a la organizacion
    org_user = OrganizationUser(
        user_id=user.id,
        organization_id=org.id,
        role="ADMIN"
    )
    db.add(org_user)
    
    await db.commit()

    # 4. Crear token JWT
    access_token = create_access_token(subject=user.id)
    # Asignar la relación SIN disparar una carga lazy (que en async lanzaría
    # MissingGreenlet): fijamos el valor ya conocido como "committed".
    set_committed_value(user, "organizations", [org_user])

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": enrich_user(user)
    }

@router.post("/login", response_model=TokenResponse)
async def login(login_data: UserLogin, db: AsyncSession = Depends(get_db)):
    q = select(User).options(selectinload(User.organizations)).where(User.email == login_data.email)
    res = await db.execute(q)
    user = res.scalar_one_or_none()
    
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos."
        )
        
    access_token = create_access_token(subject=user.id)
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": enrich_user(user)
    }

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return enrich_user(current_user)
