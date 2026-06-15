from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.ad_account import AdAccount
from app.services.ai.engine import AIEngine
from app.services.ai.llm import OpenRouterClient
from app.utils.security import get_current_user

router = APIRouter(prefix="/reports", tags=["reports"])

@router.get("/brief", response_class=HTMLResponse)
async def get_master_brief_html(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Genera dinámicamente y retorna el HTML Master Brief del informe IA.
    """
    # 1. Obtener organización
    org = await db.get(Organization, current_user.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organización no encontrada.")

    # 2. Obtener primera cuenta activa
    q = select(AdAccount).where(
        AdAccount.organization_id == current_user.organization_id,
        AdAccount.status == "ACTIVE"
    )
    res = await db.execute(q)
    acc = res.scalars().first()
    if not acc:
        raise HTTPException(status_code=400, detail="No se encontró una cuenta de anuncios activa para generar el informe.")

    # 3. Compilar datos de los últimos 7 días
    engine = AIEngine(db)
    raw_data = await engine._get_recent_campaigns_summary(acc.id)
    if not raw_data:
        raise HTTPException(status_code=400, detail="No hay suficientes datos registrados para compilar un Master Brief. Ejecuta una sincronización primero.")

    # 4. Obtener la narrativa guardada (o generar una de prueba rápida si no existe)
    narrative = acc.cached_narrative
    if not narrative:
        # Generamos una rápida
        llm_client = OpenRouterClient()
        insights_summary = "Simulación inicial de Master Brief"
        narrative = await llm_client.analyze_campaigns(org, acc, insights_summary, raw_data)
        
        # Guardar en DB para siguientes llamadas
        acc.cached_narrative = narrative[:5000]
        db.add(acc)
        await db.commit()

    # 5. Generar HTML completo
    llm_client = OpenRouterClient()
    html_content = await llm_client.generate_html_brief(org, acc, narrative, raw_data)
    
    return html_content
