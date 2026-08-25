from pydantic import BaseModel, EmailStr, Field
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
    # Mismo mínimo que el signup. El formulario ya exigía 8 y confirmación, pero
    # el schema no: una llamada directa a la API aceptaba una contraseña de un
    # carácter, y eso funda una cuenta real.
    password: str = Field(min_length=8)
