"""Carga, acceso y ciclo de vida de los medios (spec 07).

La carga anterior leia el archivo ENTERO en memoria antes de escribirlo
(`await file.read()`). Con un limite de 100 MB por video y un VPS de 4 GB
compartido entre API, workers, base de datos y Redis, unas pocas cargas
simultaneas agotaban la memoria del proceso que atiende a todos los clientes.

Aca el archivo viaja por trozos y nunca se materializa entero: el limite se
aplica DURANTE la transferencia, y la huella de memoria no crece con el tamano.
"""
import datetime
import logging
import os
import tempfile
import uuid
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.media_asset import (
    ASOCIADO, ELIMINADO, EN_USO, KIND_IMAGEN, KIND_VIDEO, TEMPORAL, MediaAsset,
)
from app.services.media_storage import (
    ExcedeElLimite, firmar_acceso, obtener_almacenamiento,
)
from app.services.media_validation import (
    MAX_IMAGEN_BYTES, MAX_VIDEO_BYTES, MedioInvalido, detectar_tipo,
    validar_imagen, validar_video, verificar_creatividad,
)

logger = logging.getLogger("lejovista.media")

# Cuanto se lee de una vez. 1 MB es suficiente para no hacer miles de syscalls y
# lo bastante chico para que diez cargas simultaneas no muevan la aguja de la
# memoria del proceso.
TROZO_BYTES = 1024 * 1024

# Bytes necesarios para reconocer el formato por su contenido. Los contenedores
# de video llevan su marca en el offset 4, asi que no basta con los primeros
# cuatro.
CABECERA_BYTES = 32


class CuotaExcedida(Exception):
    def __init__(self, usado: int, limite: int, archivos: int, limite_archivos: int):
        self.usado, self.limite = usado, limite
        self.archivos, self.limite_archivos = archivos, limite_archivos
        super().__init__("La organización alcanzó su cuota de medios.")

    def as_detail(self) -> dict:
        return {
            "code": "MEDIA_QUOTA_EXCEEDED",
            "message": (
                f"Esta organización alcanzó su cuota de medios "
                f"({self.archivos}/{self.limite_archivos} archivos, "
                f"{self.usado // 1024 // 1024}/{self.limite // 1024 // 1024} MB). "
                f"Elimina material que ya no uses o pide ampliarla."
            ),
            "usado_bytes": self.usado,
            "limite_bytes": self.limite,
            "archivos": self.archivos,
            "limite_archivos": self.limite_archivos,
        }


async def uso_de_la_organizacion(db: AsyncSession, org_id: str) -> Tuple[int, int]:
    """`(bytes, archivos)` que ocupa una Organizacion. Los eliminados no cuentan."""
    fila = (await db.execute(
        select(func.coalesce(func.sum(MediaAsset.size_bytes), 0), func.count())
        .where(MediaAsset.organization_id == org_id, MediaAsset.status != ELIMINADO)
    )).one()
    return int(fila[0] or 0), int(fila[1] or 0)


async def _assert_cuota(db: AsyncSession, org_id: str) -> None:
    usado, archivos = await uso_de_la_organizacion(db, org_id)
    if (usado >= settings.MEDIA_QUOTA_BYTES_POR_ORG
            or archivos >= settings.MEDIA_QUOTA_ARCHIVOS_POR_ORG):
        raise CuotaExcedida(
            usado, settings.MEDIA_QUOTA_BYTES_POR_ORG,
            archivos, settings.MEDIA_QUOTA_ARCHIVOS_POR_ORG,
        )


async def _trozos_con_cabecera(fuente, primero: bytes) -> AsyncIterator[bytes]:
    """Reencadena la cabecera ya leida con el resto del flujo.

    Hace falta leer los primeros bytes para saber QUE es el archivo antes de
    escribirlo; devolverlos al flujo evita tener que releer o duplicar.
    """
    yield primero
    while True:
        trozo = await fuente.read(TROZO_BYTES)
        if not trozo:
            break
        yield trozo


async def guardar_medio(
    db: AsyncSession,
    *,
    fuente,
    filename: Optional[str],
    organization_id: str,
    uploaded_by_user_id: Optional[str] = None,
) -> MediaAsset:
    """Guarda un medio por streaming, validando su contenido real.

    Orden deliberado:

    1. **Cuota**, antes de tocar el disco. Rechazar despues de escribir 100 MB
       es escribirlos igual.
    2. **Tipo real**, leyendo la cabecera. El `content_type` que declara el
       cliente no es evidencia: un ejecutable renombrado se anuncia como imagen
       igual de bien.
    3. **Escritura por trozos** con el limite aplicado durante la transferencia.
    4. **Validacion profunda** (que abra de verdad, que no este truncado).
    5. Si algo falla despues de escribir, se borra lo escrito: un archivo
       invalido en el almacenamiento consume cuota y no sirve para nada.
    """

    # Interruptor operativo de medios (spec 13). En el servicio y no en cada
    # endpoint: `POST /media`, `/upload-image` y `/upload-video` pasan los tres
    # por aquí, y ponerlo en uno solo dejaría los otros dos abiertos.
    from app.models.operacion import CAP_MEDIOS
    from app.services import interruptores

    await interruptores.assert_encendida(
        db, CAP_MEDIOS, organization_id=organization_id,
    )

    await _assert_cuota(db, organization_id)

    cabecera = await fuente.read(CABECERA_BYTES)
    if not cabecera:
        raise MedioInvalido("El archivo está vacío.", code="MEDIA_EMPTY")

    content_type, kind = detectar_tipo(cabecera)
    if kind is None:
        raise MedioInvalido(
            "El archivo no es una imagen ni un video reconocible. "
            "Formatos admitidos: JPG, PNG, WebP, GIF, MP4 y MOV.",
            code="MEDIA_UNSUPPORTED_TYPE",
        )

    limite = MAX_IMAGEN_BYTES if kind == KIND_IMAGEN else MAX_VIDEO_BYTES
    # El id se genera ACA y no se deja al default de la columna: ese default lo
    # aplica SQLAlchemy al INSERT, y para entonces la clave de almacenamiento ya
    # se construyo — con `None` en el medio, y todos los medios colisionando en
    # la misma clave.
    asset_id = str(uuid.uuid4())
    asset = MediaAsset(
        id=asset_id,
        organization_id=organization_id,
        uploaded_by_user_id=uploaded_by_user_id,
        kind=kind,
        content_type=content_type,
        filename=(filename or "")[:255] or None,
        status=TEMPORAL,
        expires_at=(
            datetime.datetime.utcnow()
            + datetime.timedelta(hours=settings.MEDIA_TTL_TEMPORAL_HORAS)
        ),
    )
    asset.storage_key = f"{organization_id}/{asset_id}{_extension(content_type)}"

    almacen = obtener_almacenamiento()
    guardado = await almacen.guardar(
        asset.storage_key,
        _trozos_con_cabecera(fuente, cabecera),
        limite_bytes=limite,
    )
    asset.size_bytes = guardado.tamano_bytes
    asset.checksum_sha256 = guardado.checksum_sha256

    try:
        medidas = await _medir(almacen, asset)
    except Exception:
        # Lo escrito se borra: un archivo invalido en el almacenamiento consume
        # cuota y no sirve para nada.
        await almacen.borrar(asset.storage_key)
        raise
    asset.ancho = medidas.get("ancho")
    asset.alto = medidas.get("alto")
    asset.duracion_segundos = medidas.get("duracion_segundos")

    if asset.content_type == "image/webp":
        await _convertir_webp_a_png(almacen, asset)

    db.add(asset)
    await db.flush()
    return asset


async def _medir(almacen, asset: MediaAsset) -> Dict[str, Any]:
    """Abre el medio de verdad para comprobar que esta completo y medirlo."""
    if asset.kind == KIND_IMAGEN:
        return validar_imagen(await almacen.leer(asset.storage_key))

    # `ffprobe` necesita un archivo en disco. Se materializa en un temporal que
    # se borra siempre — es el unico punto donde el video toca el disco local, y
    # dura lo que dura la medicion.
    datos = await almacen.leer(asset.storage_key)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=_extension(asset.content_type))
    try:
        tmp.write(datos)
        tmp.close()
        return validar_video(tmp.name)
    finally:
        os.unlink(tmp.name)


async def _convertir_webp_a_png(almacen, asset: MediaAsset) -> None:
    """Meta `/adimages` rechaza WebP (error_subcode 1487411).

    WebP es un formato comun —fotos descargadas, capturas de Mac— y hacer que el
    cliente lo reconvierta a mano no es una regla de negocio, es una fuga de un
    detalle tecnico de Meta. Se convierte aca.

    Corre en un HILO aparte: descomprimir y recodificar una imagen de 8 MB es
    trabajo de CPU, y hacerlo en el bucle de eventos bloquea a todas las demas
    peticiones mientras dura.
    """
    import asyncio
    from io import BytesIO

    from PIL import Image

    def _convertir(datos: bytes) -> bytes:
        with Image.open(BytesIO(datos)) as img:
            if img.mode in ("P", "LA"):
                img = img.convert("RGBA")
            elif img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            salida = BytesIO()
            img.save(salida, format="PNG")
            return salida.getvalue()

    try:
        original = await almacen.leer(asset.storage_key)
        convertido = await asyncio.to_thread(_convertir, original)
    except Exception:
        logger.exception("No se pudo convertir el WebP %s a PNG", asset.id)
        raise MedioInvalido(
            "No se pudo procesar la imagen WebP. Súbela en JPG o PNG.",
            code="MEDIA_CONVERSION_FAILED",
        )

    clave_anterior = asset.storage_key
    asset.storage_key = clave_anterior.rsplit(".", 1)[0] + ".png"
    asset.content_type = "image/png"

    async def _un_trozo():
        yield convertido

    guardado = await almacen.guardar(
        asset.storage_key, _un_trozo(), limite_bytes=MAX_IMAGEN_BYTES
    )
    asset.size_bytes = guardado.tamano_bytes
    asset.checksum_sha256 = guardado.checksum_sha256
    await almacen.borrar(clave_anterior)


def _extension(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "video/mp4": ".mp4", "video/quicktime": ".mov",
    }.get(content_type, "")


def avisos_de(asset: MediaAsset) -> list:
    return [
        a.as_dict() for a in verificar_creatividad(
            kind=asset.kind, size_bytes=asset.size_bytes,
            ancho=asset.ancho, alto=asset.alto,
            duracion_segundos=asset.duracion_segundos,
            duracion_medida=asset.kind == KIND_IMAGEN or asset.duracion_segundos is not None,
        )
    ]


async def medio_de_la_organizacion(
    db: AsyncSession, asset_id: str, organization_id: str
) -> Optional[MediaAsset]:
    """El medio, SOLO si pertenece a esa Organizacion.

    La comprobacion es una consulta con la organizacion en el WHERE, no una
    lectura seguida de una comparacion sobre la ruta: la ruta contiene el
    identificador por conveniencia, y apoyarse en eso es confiar en una
    convencion.
    """
    return (await db.execute(
        select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.organization_id == organization_id,
            MediaAsset.status != ELIMINADO,
        )
    )).scalar_one_or_none()


def url_firmada(asset: MediaAsset) -> str:
    """URL temporal de lectura. Se emite DESPUES de comprobar la pertenencia."""
    return f"/api/v1/media/{asset.id}/contenido?token={firmar_acceso(asset.id)}"


async def asociar_a_draft(db: AsyncSession, asset: MediaAsset, draft_id: str) -> MediaAsset:
    """Un medio asociado deja de ser temporal y estira su caducidad."""
    asset.draft_id = draft_id
    asset.status = ASOCIADO
    asset.expires_at = (
        datetime.datetime.utcnow()
        + datetime.timedelta(days=settings.MEDIA_TTL_SIN_CAMPANA_DIAS)
    )
    db.add(asset)
    return asset


async def marcar_en_uso(db: AsyncSession, draft_id: str) -> int:
    """Los medios de una campana creada de verdad dejan de caducar por si solos.

    Pasan a seguir la politica de retencion general del cliente (spec 09): lo que
    los elimina ya no es el reloj de una propuesta, es la baja de la
    organizacion.
    """
    assets = (await db.execute(
        select(MediaAsset).where(MediaAsset.draft_id == draft_id)
    )).scalars().all()
    for a in assets:
        a.status = EN_USO
        a.expires_at = None
        db.add(a)
    return len(assets)


async def limpiar_vencidos(db: AsyncSession, *, ahora: Optional[datetime.datetime] = None) -> int:
    """Borra del almacenamiento los medios cuya caducidad paso.

    **Idempotente**: correrla dos veces seguidas da el mismo resultado, porque
    filtra por estado y marca lo que borra. Y no toca los medios EN USO: los que
    sostienen una campana viva no tienen caducidad.
    """
    ahora = ahora or datetime.datetime.utcnow()
    vencidos = (await db.execute(
        select(MediaAsset).where(
            MediaAsset.status.in_((TEMPORAL, ASOCIADO)),
            MediaAsset.expires_at.isnot(None),
            MediaAsset.expires_at < ahora,
        )
    )).scalars().all()

    almacen = obtener_almacenamiento()
    borrados = 0
    for asset in vencidos:
        try:
            await almacen.borrar(asset.storage_key)
        except Exception:
            # Que el archivo ya no este no es un error: la limpieza es
            # idempotente y la fila se marca igual.
            logger.warning("No se pudo borrar %s del almacenamiento", asset.storage_key)
        asset.status = ELIMINADO
        asset.deleted_at = ahora
        db.add(asset)
        borrados += 1
    return borrados
