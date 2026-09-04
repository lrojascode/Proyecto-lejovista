"""Carga y acceso a medios (spec 07).

Sustituye a `POST /campaigns/upload-image` y `/upload-video`, que leian el
archivo ENTERO en memoria antes de escribirlo y validaban por el tipo declarado.
Aquellos se conservan por compatibilidad y delegan en este mismo servicio.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.media_asset import ELIMINADO, MediaAsset
from app.models.user import User
from app.schemas.response_schemas import MediaAssetResponse, MediaQuotaResponse
from app.services.media_service import (
    CuotaExcedida, avisos_de, guardar_medio, medio_de_la_organizacion,
    uso_de_la_organizacion, url_firmada,
)
from app.services.media_storage import ExcedeElLimite, obtener_almacenamiento, verificar_acceso
from app.services.media_validation import MedioInvalido
from app.utils.security import get_current_user, require_module, require_role

logger = logging.getLogger("lejovista.media")

router = APIRouter(prefix="/media", tags=["media"])


def _respuesta(asset: MediaAsset) -> MediaAssetResponse:
    return MediaAssetResponse(
        id=asset.id,
        kind=asset.kind,
        content_type=asset.content_type,
        filename=asset.filename,
        size_bytes=asset.size_bytes,
        ancho=asset.ancho,
        alto=asset.alto,
        duracion_segundos=asset.duracion_segundos,
        status=asset.status,
        expires_at=asset.expires_at.isoformat() if asset.expires_at else None,
        url=url_firmada(asset),
        avisos=avisos_de(asset),
    )


@router.post("", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_module("chat_creacion"))])
async def subir_medio(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("OPERATOR")),
    db: AsyncSession = Depends(get_db),
):
    """Sube una imagen o un video para el brief de una campaña.

    El archivo viaja por trozos y nunca se materializa entero en memoria: con un
    límite de 100 MB por video y un VPS de 4 GB compartido entre API, workers,
    base y Redis, unas pocas cargas simultáneas agotaban el proceso que atiende a
    todos los clientes.

    El formato se determina leyendo el CONTENIDO. El tipo que declara el cliente
    no es evidencia.
    """
    try:
        asset = await guardar_medio(
            db,
            fuente=file,
            filename=file.filename,
            organization_id=current_user.organization_id,
            uploaded_by_user_id=current_user.id,
        )
    except CuotaExcedida as e:
        raise HTTPException(status_code=413, detail=e.as_detail())
    except ExcedeElLimite as e:
        raise HTTPException(
            status_code=413,
            detail={"code": "MEDIA_TOO_LARGE", "message": str(e)},
        )
    except MedioInvalido as e:
        raise HTTPException(status_code=400, detail=e.as_detail())

    await db.commit()
    await db.refresh(asset)
    return _respuesta(asset)


@router.get("/quota", response_model=MediaQuotaResponse)
async def cuota_de_medios(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cuánto ocupa la organización y cuánto le queda."""
    from app.config import settings

    usado, archivos = await uso_de_la_organizacion(db, current_user.organization_id)
    return MediaQuotaResponse(
        usado_bytes=usado,
        limite_bytes=settings.MEDIA_QUOTA_BYTES_POR_ORG,
        archivos=archivos,
        limite_archivos=settings.MEDIA_QUOTA_ARCHIVOS_POR_ORG,
    )


@router.get("/{asset_id}", response_model=MediaAssetResponse)
async def detalle_del_medio(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Metadatos y una URL firmada nueva.

    La pertenencia se comprueba acá, contra la sesión que pregunta: 404 y no 403
    para no revelar qué medios existen en otras organizaciones.
    """
    asset = await medio_de_la_organizacion(db, asset_id, current_user.organization_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Medio no encontrado.")
    return _respuesta(asset)


@router.get("/{asset_id}/contenido")
async def contenido_del_medio(
    asset_id: str,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Sirve el archivo, solo con una firma válida y vigente.

    **No lleva sesión**: es el único camino por el que un `<img src>` puede
    cargar un medio privado. Por eso la firma es corta y de un solo recurso — la
    pertenencia ya se comprobó al emitirla.
    """
    if not verificar_acceso(asset_id, token):
        raise HTTPException(
            status_code=403,
            detail={"code": "MEDIA_SIGNATURE_INVALID",
                    "message": "El enlace del medio no es válido o ya caducó."},
        )

    asset = (await db.execute(
        select(MediaAsset).where(
            MediaAsset.id == asset_id, MediaAsset.status != ELIMINADO
        )
    )).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Medio no encontrado.")

    datos = await obtener_almacenamiento().leer(asset.storage_key)
    return Response(
        content=datos,
        media_type=asset.content_type,
        headers={
            # Privado y sin cache compartida: la URL es una credencial temporal,
            # y un proxy intermedio guardándola la convertiría en permanente.
            "Cache-Control": "private, max-age=60, no-store",
        },
    )
