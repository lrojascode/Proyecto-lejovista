"""Almacenamiento de medios y URL firmadas (spec 07).

Los archivos vivian en el disco del propio VPS, en un volumen COMPARTIDO entre
la API y el worker de creacion. Eso ataba los tres a la misma maquina, hacia que
los medios dependieran del ciclo de vida del servidor y los metia en el mismo
disco de 100 GB que la base de datos.

El acceso, ademas, era por ruta: quien conociera `MEDIA_ROOT/{org}/{uuid}.jpg`
la leia. La pertenencia se apoyaba en que el identificador de la organizacion
estuviera en la ruta, que no es una comprobacion, es una convencion.

Aca el almacenamiento es un **driver** con cuatro operaciones. `local` es el
disco (lo que hay hoy, y lo que usan los tests); `s3` es cualquier servicio
compatible con S3 en produccion. La interfaz es la misma, asi que mover un
cliente de uno a otro no toca ninguna logica de producto.

**Toda lectura pasa por una URL firmada de vida corta.** No hay lectura publica
ni por ruta adivinable, y la firma se emite solo despues de comprobar la
pertenencia contra la sesion que la pide.
"""
import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol

from app.config import settings

logger = logging.getLogger("lejovista.media_storage")

# Vida de una URL firmada. Lo justo para que el navegador la resuelva o el worker
# la descargue. Mas larga convierte un enlace filtrado en un acceso duradero.
FIRMA_TTL_SEGUNDOS = 300


class ErrorDeAlmacenamiento(Exception):
    """El almacenamiento no pudo completar la operacion."""


@dataclass(frozen=True)
class ObjetoAlmacenado:
    clave: str
    tamano_bytes: int
    checksum_sha256: str


class DriverDeAlmacenamiento(Protocol):
    async def guardar(self, clave: str, trozos: AsyncIterator[bytes],
                      *, limite_bytes: int) -> ObjetoAlmacenado: ...
    async def leer(self, clave: str) -> bytes: ...
    async def borrar(self, clave: str) -> None: ...
    async def existe(self, clave: str) -> bool: ...


class ExcedeElLimite(ErrorDeAlmacenamiento):
    """El archivo supero el tamano permitido DURANTE la transferencia.

    Se levanta a mitad de la escritura a proposito: comprobar el tamano al final
    obliga a haber materializado el archivo entero primero, que es exactamente lo
    que hay que evitar.
    """

    def __init__(self, limite_bytes: int):
        self.limite_bytes = limite_bytes
        super().__init__(
            f"El archivo supera el límite de {limite_bytes // 1024 // 1024} MB."
        )


class AlmacenamientoLocal:
    """Disco local. Para desarrollo, para los tests y como camino de transicion.

    NO es el destino de produccion: sigue atando los medios al ciclo de vida del
    servidor y compartiendo su disco con la base de datos.
    """

    def __init__(self, raiz: Optional[str] = None):
        self.raiz = raiz or settings.MEDIA_ROOT

    def _ruta(self, clave: str) -> str:
        # La clave nunca puede salir de la raiz: un `..` en el nombre convertiria
        # una subida en escritura arbitraria del sistema de archivos.
        destino = os.path.normpath(os.path.join(self.raiz, clave))
        if not destino.startswith(os.path.normpath(self.raiz) + os.sep):
            raise ErrorDeAlmacenamiento(f"Clave de almacenamiento inválida: {clave!r}")
        return destino

    async def guardar(self, clave: str, trozos, *, limite_bytes: int) -> ObjetoAlmacenado:
        destino = self._ruta(clave)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        digest = hashlib.sha256()
        total = 0
        parcial = destino + ".parcial"
        try:
            with open(parcial, "wb") as f:
                async for trozo in trozos:
                    total += len(trozo)
                    if total > limite_bytes:
                        raise ExcedeElLimite(limite_bytes)
                    digest.update(trozo)
                    f.write(trozo)
            # Rename atomico: un archivo a medio escribir no puede quedar
            # accesible como si fuera un medio completo.
            os.replace(parcial, destino)
        except Exception:
            if os.path.exists(parcial):
                os.remove(parcial)
            raise
        return ObjetoAlmacenado(clave=clave, tamano_bytes=total,
                                checksum_sha256=digest.hexdigest())

    async def leer(self, clave: str) -> bytes:
        with open(self._ruta(clave), "rb") as f:
            return f.read()

    async def borrar(self, clave: str) -> None:
        ruta = self._ruta(clave)
        if os.path.exists(ruta):
            os.remove(ruta)

    async def existe(self, clave: str) -> bool:
        return os.path.exists(self._ruta(clave))


class AlmacenamientoS3:
    """Cualquier servicio compatible con S3 (AWS, B2, MinIO, R2).

    `boto3` se importa dentro del constructor y no arriba: es una dependencia
    solo del despliegue que use este driver, y no tiene por que instalarse para
    correr los tests ni el entorno local.
    """

    def __init__(self):
        try:
            import boto3  # noqa: F401
        except ImportError as e:
            raise ErrorDeAlmacenamiento(
                "El driver de almacenamiento 's3' necesita boto3 instalado."
            ) from e
        import boto3

        self.bucket = settings.MEDIA_S3_BUCKET
        if not self.bucket:
            raise ErrorDeAlmacenamiento("MEDIA_S3_BUCKET está vacío.")
        self.cliente = boto3.client(
            "s3",
            endpoint_url=settings.MEDIA_S3_ENDPOINT or None,
            region_name=settings.MEDIA_S3_REGION or None,
        )

    async def guardar(self, clave: str, trozos, *, limite_bytes: int) -> ObjetoAlmacenado:
        import asyncio
        import tempfile

        digest = hashlib.sha256()
        total = 0
        # Buffer en disco y no en memoria: el punto de todo esto es que la
        # huella no crezca con el tamano del archivo.
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            try:
                async for trozo in trozos:
                    total += len(trozo)
                    if total > limite_bytes:
                        raise ExcedeElLimite(limite_bytes)
                    digest.update(trozo)
                    tmp.write(trozo)
                tmp.flush()
                await asyncio.to_thread(
                    self.cliente.upload_file, tmp.name, self.bucket, clave
                )
            finally:
                os.unlink(tmp.name)
        return ObjetoAlmacenado(clave=clave, tamano_bytes=total,
                                checksum_sha256=digest.hexdigest())

    async def leer(self, clave: str) -> bytes:
        import asyncio

        def _leer():
            respuesta = self.cliente.get_object(Bucket=self.bucket, Key=clave)
            return respuesta["Body"].read()

        return await asyncio.to_thread(_leer)

    async def borrar(self, clave: str) -> None:
        import asyncio

        await asyncio.to_thread(
            self.cliente.delete_object, Bucket=self.bucket, Key=clave
        )

    async def existe(self, clave: str) -> bool:
        import asyncio

        def _existe():
            try:
                self.cliente.head_object(Bucket=self.bucket, Key=clave)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_existe)


def obtener_almacenamiento() -> DriverDeAlmacenamiento:
    if settings.MEDIA_STORAGE_DRIVER == "s3":
        return AlmacenamientoS3()
    return AlmacenamientoLocal()


# --- URL firmadas -----------------------------------------------------------


def firmar_acceso(asset_id: str, *, ttl: int = FIRMA_TTL_SEGUNDOS) -> str:
    """Token de acceso temporal a un medio.

    HMAC sobre `(asset_id, vencimiento)` con la clave de la aplicacion. No lleva
    identificador de usuario a proposito: la pertenencia YA se comprobo al
    emitirlo, y meterla en el token invitaria a confiar en el token en vez de en
    la comprobacion.
    """
    vence = int(time.time()) + ttl
    mensaje = f"{asset_id}:{vence}".encode()
    firma = hmac.new(settings.SECRET_KEY.encode(), mensaje, hashlib.sha256).digest()
    return f"{vence}.{base64.urlsafe_b64encode(firma).decode().rstrip('=')}"


def verificar_acceso(asset_id: str, token: str) -> bool:
    """True si el token es valido y no vencio.

    `compare_digest` y no `==`: comparar firmas byte a byte con salida temprana
    filtra informacion sobre la firma correcta.
    """
    try:
        crudo, firma = token.split(".", 1)
        vence = int(crudo)
    except (ValueError, AttributeError):
        return False
    if vence < int(time.time()):
        return False
    esperada = firmar_acceso(asset_id, ttl=vence - int(time.time()))
    return hmac.compare_digest(esperada.split(".", 1)[1], firma)
