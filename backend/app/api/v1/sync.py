from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models.user import User
from app.models.sync_job import SyncJob
from app.models.ad_account import AdAccount
from app.utils.security import get_current_user
from app.workers.sync_tasks import task_sync_tenant_data
import uuid

router = APIRouter(prefix="/sync", tags=["Synchronization"])

@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def start_sync(
    ad_account_id: str,
    page_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Inicia el trabajo de sincronización asíncrona para el tenant autenticado.
    """
    tenant_id = current_user.organization_id

    # 1. Validar que la cuenta de ads pertenezca al tenant autenticado
    stmt = select(AdAccount).where(
        AdAccount.id == ad_account_id,
        AdAccount.organization_id == tenant_id
    )
    result = await db.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="AdAccount no encontrada para esta organización.")

    # 2. Crear SyncJob en PENDING
    sync_job = SyncJob(
        id=str(uuid.uuid4()),
        organization_id=tenant_id,
        ad_account_id=ad_account_id,
        status="PENDING",
        sync_type="FULL"
    )
    db.add(sync_job)
    await db.commit()
    await db.refresh(sync_job)

    # 3. Encolar tarea principal de Celery
    task_sync_tenant_data.delay(
        tenant_id=tenant_id,
        ad_account_id=ad_account_id,
        page_id=page_id,
        sync_job_id=sync_job.id
    )

    return {"message": "Sync started", "job_id": sync_job.id}


@router.get("/{job_id}/status")
async def get_sync_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Permite al frontend consultar el estado del Sync (polling).
    Si está READY, retorna el Master Brief JSON.
    """
    stmt = select(SyncJob).where(SyncJob.id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    # No revelar existencia de jobs de otros tenants (evita IDOR)
    if not job or job.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Sync Job not found")

    response = {
        "job_id": job.id,
        "status": job.status,
    }

    if job.status == "READY":
        response["brief"] = job.payload_json
    elif job.status == "FAILED":
        response["error"] = job.error_message

    return response
