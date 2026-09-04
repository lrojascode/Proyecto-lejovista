"""Un medio subido por un cliente, como entidad con dueno y ciclo de vida (spec 07).

Antes los archivos eran filas en un directorio: `MEDIA_ROOT/{org}/{uuid}.jpg`.
Eso dejaba tres huecos que esta tabla cierra:

* **Pertenencia**: se apoyaba en que el identificador de la organizacion
  estuviera en la ruta. Eso es una convencion, no una comprobacion.
* **Cuota**: no habia forma de saber cuanto ocupaba un cliente sin recorrer el
  disco, asi que no habia forma de limitarlo.
* **Caducidad**: un archivo subido para una propuesta que nunca se aprobo
  permanecia indefinidamente, y nadie sabia cuales eran esos.
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Estados del ciclo de vida.
TEMPORAL = "TEMPORAL"      # cargado, sin asociar a ninguna propuesta
ASOCIADO = "ASOCIADO"      # asociado a un Draft que aun no creo campana
EN_USO = "EN_USO"          # usado por una campana creada de verdad
ELIMINADO = "ELIMINADO"    # borrado del almacenamiento

KIND_IMAGEN = "IMAGE"
KIND_VIDEO = "VIDEO"


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    uploaded_by_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    draft_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    kind: Mapped[str] = mapped_column(String(10))          # IMAGE | VIDEO
    # Tipo REAL, detectado leyendo el contenido. No el declarado por el cliente:
    # un ejecutable renombrado se anuncia como imagen igual de bien.
    content_type: Mapped[str] = mapped_column(String(80))
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    storage_key: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)

    # Dimensiones y duracion, cuando se pudieron determinar. `None` significa
    # "no se pudo comprobar", que es distinto de "no aplica": la verificacion de
    # creatividad lo dice en vez de dar por bueno lo que no midio.
    ancho: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    alto: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duracion_segundos: Mapped[Optional[float]] = mapped_column(nullable=True)

    status: Mapped[str] = mapped_column(String(12), default=TEMPORAL, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
