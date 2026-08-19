from pydantic import BaseModel, EmailStr
from typing import Literal


class InvitationCreateRequest(BaseModel):
    email: EmailStr
    role: Literal["ADMIN", "OPERATOR", "VIEWER"] = "OPERATOR"


class InvitationAcceptRequest(BaseModel):
    full_name: str
    password: str
