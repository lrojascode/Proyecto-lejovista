from datetime import datetime, timedelta
from typing import Optional, Union, Any, Dict, Tuple
import jwt
import uuid
import time
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import get_db
from app.models.user import User

# Usamos bcrypt para hashes de contraseñas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login", auto_error=False)

# Token temporal: key -> (user_id, expiration_timestamp)
temp_tokens: Dict[str, Tuple[str, float]] = {}

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt

def create_temp_token(user_id: str, expires_in: int = 30) -> str:
    token = str(uuid.uuid4())
    temp_tokens[token] = (user_id, time.time() + expires_in)
    return token

def consume_temp_token(token: str) -> Optional[str]:
    now = time.time()
    # Limpieza perezosa de expirados
    expired = [k for k, v in list(temp_tokens.items()) if v[1] < now]
    for k in expired:
        temp_tokens.pop(k, None)
        
    val = temp_tokens.pop(token, None)
    if val:
        user_id, expiration = val
        if expiration >= now:
            return user_id
    return None

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudieron validar las credenciales de acceso.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    user_id = None
    
    # 1. Intentar validar usando token JWT en headers
    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("sub")
        except Exception:
            raise credentials_exception

    if not user_id:
        raise credentials_exception
        
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    q_user = select(User).options(selectinload(User.organizations)).where(User.id == user_id)
    res_user = await db.execute(q_user)
    user = res_user.scalar_one_or_none()
    
    if user is None:
        raise credentials_exception
        
    # Validar aislamiento de inquilino (tenant isolation)
    tenant_slug = request.headers.get("X-Tenant-Slug")
    if tenant_slug:
        from app.models.organization import Organization
        q_org = select(Organization).where(Organization.slug == tenant_slug)
        res_org = await db.execute(q_org)
        org = res_org.scalar_one_or_none()
        
        user_org_ids = [ou.organization_id for ou in user.organizations] if user.organizations else []
        if not org or org.id not in user_org_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acceso denegado: No tienes permisos para acceder a esta organización."
            )
            
    return user
