import asyncio
import argparse
import uuid
import sys
import os

from app.database import async_session
from app.models.organization import Organization
from app.models.user import User
from app.models.organization_user import OrganizationUser
from app.models.ad_account import AdAccount
from app.models.meta_credential import MetaCredential
from sqlalchemy.future import select

try:
    from app.utils.security import get_password_hash
    pw_hash = get_password_hash("admin123")
except Exception:
    pw_hash = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQGjZ3uC"

async def seed_tenant(org_name: str, token: str, account_id: str, page_id: str):
    async with async_session() as session:
        org_id = str(uuid.uuid4())
        slug = org_name.lower().replace(" ", "-") + "-" + str(uuid.uuid4())[:4]
        org = Organization(id=org_id, name=org_name, slug=slug)
        session.add(org)
        
        user_id = str(uuid.uuid4())
        user = User(
            id=user_id,
            email="admin@seed.local",
            hashed_password=pw_hash,
            full_name="Admin Seed"
        )
        session.add(user)
        
        org_user = OrganizationUser(
            user_id=user_id,
            organization_id=org_id,
            role="ADMIN"
        )
        session.add(org_user)
        
        cred_id = str(uuid.uuid4())
        cred = MetaCredential(
            id=cred_id,
            organization_id=org_id,
            status="ACTIVE"
        )
        cred.set_access_token(token)
        session.add(cred)
        
        account_id_clean = account_id if account_id.startswith("act_") else f"act_{account_id}"
        
        stmt = select(AdAccount).where(AdAccount.external_account_id == account_id_clean)
        result = await session.execute(stmt)
        existing_account = result.scalar_one_or_none()
        
        if existing_account:
            print(f"Warning: AdAccount {account_id_clean} already exists. Skipping creation.")
        else:
            ad_account = AdAccount(
                id=str(uuid.uuid4()),
                organization_id=org_id,
                meta_credential_id=cred_id,
                platform="META",
                external_account_id=account_id_clean,
                name=f"Cuentas Ads - {org_name}",
                status="ACTIVE"
            )
            session.add(ad_account)
        
        await session.commit()
        
        print("\n" + "="*50)
        print("✅ TENANT SEED COMPLETED SUCCESSFULLY")
        print("="*50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed initial tenant")
    parser.add_argument("--org", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--page-id", required=True)
    args = parser.parse_args()
    asyncio.run(seed_tenant(args.org, args.token, args.account_id, args.page_id))
