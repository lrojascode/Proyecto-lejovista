from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.meta_credential import MetaCredential
from app.config import settings

class MetaCredentialService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_valid_token(self, organization_id: str) -> str:
        """
        Obtiene el token de Meta desencriptado para una organización,
        lanzando un error si no existe o expiró.
        """
        stmt = select(MetaCredential).where(
            MetaCredential.organization_id == organization_id,
            MetaCredential.status == "ACTIVE"
        )
        result = await self.session.execute(stmt)
        credential = result.scalar_one_or_none()
        
        if not credential:
            raise ValueError(f"No active Meta credentials found for organization {organization_id}")

        # S-6: rechazar tokens expirados y marcarlos para que el usuario reconecte.
        if credential.is_expired:
            credential.status = "EXPIRED"
            self.session.add(credential)
            await self.session.commit()
            raise ValueError(f"Meta token expired for organization {organization_id}; reconexión requerida.")

        token = credential.get_access_token()
        if not token:
            raise ValueError(f"Decrypted token is empty for organization {organization_id}")

        return token
