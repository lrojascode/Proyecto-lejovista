"""Operación: interruptores, feature flags, señales, incidente y métricas (spec 13).

Todo lo de aquí es de la AGENCIA salvo dos cosas, y las dos por un motivo:

* `GET /operacion/senales` no lleva autenticación, porque lo consulta un
  vigilante EXTERNO al VPS. Un endpoint que exige credenciales no lo puede
  sondear un monitor de disponibilidad simple, y un sistema que solo alerta
  desde dentro no puede informar de su propia muerte (criterio 12). No expone
  datos de ningún cliente: solo estados y umbrales.
* `GET /operacion/aviso` lo lee el cliente: es su mensaje de incidente, sin
  nombres de infraestructura.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.operacion import CAP_SOLO_LECTURA, CAPACIDADES
from app.models.user import User
from app.services import feature_flags, incidente, interruptores, metricas_producto, senales
from app.utils.security import get_current_user, require_agency

logger = logging.getLogger("lejovista.api.operacion")

router = APIRouter(prefix="/operacion", tags=["operacion"])


# --- Interruptores ---------------------------------------------------------

class CambioDeInterruptor(BaseModel):
    encendido: bool
    # El motivo no es obligatorio para ENCENDER —volver a la normalidad no
    # necesita explicación— pero sí conviene al apagar. Se pide siempre y se
    # acepta vacío: exigirlo en mitad de un incidente añade fricción justo
    # donde no debe haberla.
    motivo: Optional[str] = Field(default=None, max_length=500)
    # NULL = global. Con un id, solo esa Organización (criterio 5).
    organization_id: Optional[str] = None


@router.get("/interruptores")
async def ver_interruptores(
    organization_id: Optional[str] = None,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Qué está encendido y qué apagado, ahora mismo."""
    return await interruptores.panorama(db, organization_id=organization_id)


@router.put("/interruptores/{capacidad}")
async def cambiar_interruptor(
    capacidad: str,
    payload: CambioDeInterruptor,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Enciende o apaga una capacidad. Efecto inmediato, sin desplegar."""
    try:
        await interruptores.cambiar(
            db, capacidad,
            encendido=payload.encendido,
            organization_id=payload.organization_id,
            motivo=payload.motivo,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    return await interruptores.panorama(
        db, organization_id=payload.organization_id,
    )


# --- Feature flags ---------------------------------------------------------

class CambioDeFlag(BaseModel):
    activo: bool
    organization_id: Optional[str] = None
    # La cuenta piloto. Es el ámbito más estrecho y gana sobre la organización.
    ad_account_id: Optional[str] = None
    nota: Optional[str] = Field(default=None, max_length=500)


@router.get("/flags")
async def ver_flags(
    organization_id: Optional[str] = None,
    ad_account_id: Optional[str] = None,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Todos los flags con su valor efectivo y de dónde sale.

    El origen viaja con el valor porque "está encendido" no dice si lo está por
    su valor predeterminado, por una decisión global o porque esta cuenta es la
    piloto — y apagarlo exige saber cuál de las tres.
    """
    return await feature_flags.resolver_todos(
        db, organization_id=organization_id, ad_account_id=ad_account_id,
    )


@router.put("/flags/{clave}")
async def cambiar_flag(
    clave: str,
    payload: CambioDeFlag,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    try:
        await feature_flags.fijar(
            db, clave,
            activo_=payload.activo,
            organization_id=payload.organization_id,
            ad_account_id=payload.ad_account_id,
            nota=payload.nota,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
        )
    except feature_flags.FlagDesconocido as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    return await feature_flags.resolver_todos(
        db,
        organization_id=payload.organization_id,
        ad_account_id=payload.ad_account_id,
    )


# --- Señales, para el vigilante externo ------------------------------------

@router.get("/senales")
async def ver_senales(db: AsyncSession = Depends(get_db)):
    """Las señales funcionales con su umbral. **Sin autenticación** (criterio 12).

    Lo consulta un vigilante externo al VPS, que comprueba dos cosas: que haya
    alertas, y que `generado_en` AVANCE. Con el VPS apagado no hay respuesta que
    leer, y esa ausencia es en sí misma la alerta — un sistema que solo alerta
    desde dentro no puede informar de su propia muerte.

    No expone datos de ningún cliente: estados, umbrales y una marca de tiempo.
    """
    return await senales.resumen(db)


# --- Incidente: dos audiencias --------------------------------------------

@router.get("/diagnostico")
async def diagnostico(
    organization_id: Optional[str] = None,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Qué falla, qué está apagado y quién lo movió. Solo agencia (criterio 13)."""
    return await incidente.diagnostico_para_la_agencia(
        db, organization_id=organization_id,
    )


@router.get("/aviso")
async def aviso(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Qué puede y qué no puede hacer el cliente ahora. Sin infraestructura.

    Devuelve `hay_incidente: false` cuando todo funciona, en vez de un mensaje
    tranquilizador: una interfaz que siempre dice algo sobre el estado del
    sistema entrena a no mirar ese sitio, y el día que importe no se mirará.
    """
    return await incidente.aviso_para_el_cliente(
        db, organization_id=current_user.organization_id,
    )


# --- Métricas de producto --------------------------------------------------

@router.get("/metricas")
async def metricas(
    dias: int = 30,
    current_user: User = Depends(require_agency),
    db: AsyncSession = Depends(get_db),
):
    """Las seis métricas declaradas, para esta Organización y un periodo.

    Son las que responden si el producto cumple su promesa: ahorro operativo
    para la agencia y beneficio publicitario para el cliente.
    """
    return await metricas_producto.todas(
        db, current_user.organization_id, dias=dias,
    )
