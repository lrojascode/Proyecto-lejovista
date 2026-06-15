from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.schemas.schemas import UserCreate, TokenResponse, UserLogin, UserResponse
from app.utils.security import verify_password, get_password_hash, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

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
        full_name=user_in.full_name,
        role="ADMIN",
        organization_id=org.id
    )
    db.add(user)
    await db.commit()

    # 4. Crear token JWT
    access_token = create_access_token(subject=user.id)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }

@router.post("/login", response_model=TokenResponse)
async def login(login_data: UserLogin, db: AsyncSession = Depends(get_db)):
    q = select(User).where(User.email == login_data.email)
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
        "user": user
    }

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
