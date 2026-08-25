from pydantic import BaseModel, EmailStr
from typing import List, Literal, Optional


class InvitationCreateRequest(BaseModel):
    email: EmailStr
    role: Literal["ADMIN", "OPERATOR", "VIEWER"] = "OPERATOR"
    # A qué cuentas publicitarias accede este miembro. `None` = todas las de la
    # organización en el momento de aceptar (el caso del equipo propio); una
    # lista acota (el caso de un cliente que solo debe ver la suya).
    ad_account_ids: Optional[List[str]] = None


class InvitationAcceptRequest(BaseModel):
    full_name: str
    password: str
