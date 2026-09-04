"""Validacion de medios por su CONTENIDO, no por lo que dicen ser (spec 07).

La validacion anterior se hacia sobre el `content_type` que declaraba el cliente.
Un archivo cualquiera renombrado y anunciado como imagen se aceptaba, se guardaba
y se enviaba a Meta. Tampoco se comprobaba que estuviera completo, ni sus
dimensiones, ni su duracion.

Dos capas distintas, y conviene no confundirlas:

* **Validacion** (`detectar_tipo`, `validar_imagen`, `validar_video`): rechaza.
  Un archivo que no es lo que dice, esta truncado, esta vacio o se pasa de los
  limites no entra.
* **Verificacion tecnica** (`verificar_creatividad`): avisa. Comprueba lo que
  Meta pide de una creatividad —relacion de aspecto, resolucion, duracion, peso—
  y devuelve avisos accionables. **No emite juicio sobre si funcionara**: esa
  promesa esta expresamente prohibida, y ademas seria falsa.
"""
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("lejovista.media_validation")

# Firmas de los formatos que se admiten. La deteccion va por el CONTENIDO: es la
# unica forma de distinguir una imagen de un ejecutable con el nombre cambiado.
_FIRMAS = (
    (b"\xff\xd8\xff", "image/jpeg", "IMAGE"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "IMAGE"),
    (b"GIF87a", "image/gif", "IMAGE"),
    (b"GIF89a", "image/gif", "IMAGE"),
)

# Los limites que aplica Meta a los creativos. No son preferencias nuestras: un
# archivo fuera de estos rangos lo rechaza Meta, y descubrirlo despues de crear
# la campana cuesta una Saga entera.
MAX_IMAGEN_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SEGUNDOS = 60 * 15
MIN_LADO_PX = 400
# Relaciones de aspecto que Meta sirve bien. Fuera de este rango recorta el
# creativo, y el recorte se come el texto.
ASPECTO_MIN = 0.5   # 1:2 (vertical extremo)
ASPECTO_MAX = 1.91  # 1.91:1 (horizontal de feed)


class MedioInvalido(ValueError):
    """El archivo no es admisible. Lleva el motivo en lenguaje del cliente."""

    def __init__(self, mensaje: str, code: str = "MEDIA_INVALID"):
        self.code = code
        super().__init__(mensaje)

    def as_detail(self) -> dict:
        return {"code": self.code, "message": str(self)}


def detectar_tipo(cabecera: bytes) -> Tuple[Optional[str], Optional[str]]:
    """`(content_type, kind)` leidos del contenido, o `(None, None)`.

    Los contenedores de video (MP4/MOV) llevan el atomo `ftyp` en el offset 4,
    no al principio, asi que no entran en la tabla de firmas de arriba.
    """
    if len(cabecera) >= 12 and cabecera[4:8] == b"ftyp":
        marca = cabecera[8:12]
        if marca[:3] in (b"qt ", b"qt\x00"):
            return "video/quicktime", "VIDEO"
        return "video/mp4", "VIDEO"

    for firma, tipo, kind in _FIRMAS:
        if cabecera.startswith(firma):
            return tipo, kind

    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "image/webp", "IMAGE"
    return None, None


def validar_imagen(datos: bytes) -> Dict[str, Any]:
    """Abre la imagen de verdad. Devuelve dimensiones y formato.

    `verify()` detecta el archivo TRUNCADO, que es el caso que las firmas no
    pueden ver: los primeros bytes de una imagen cortada por la mitad son
    exactamente los de una imagen entera.
    """
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(datos)) as img:
            img.verify()
        # `verify()` deja el descriptor inutilizable: hay que reabrir para leer
        # las dimensiones. Es como funciona Pillow, no un descuido.
        with Image.open(BytesIO(datos)) as img:
            ancho, alto = img.size
            formato = (img.format or "").lower()
    except Exception as e:
        raise MedioInvalido(
            "La imagen está dañada o incompleta. Vuelve a subirla.",
            code="MEDIA_CORRUPT",
        ) from e

    if ancho <= 0 or alto <= 0:
        raise MedioInvalido("La imagen no tiene dimensiones válidas.", code="MEDIA_CORRUPT")
    return {"ancho": ancho, "alto": alto, "formato": formato}


def validar_video(ruta: str) -> Dict[str, Any]:
    """Dimensiones y duracion del video, con `ffprobe` cuando esta disponible.

    Sin `ffprobe` NO se da por bueno lo que no se midio: se devuelve
    `duracion_segundos = None` y la verificacion de creatividad lo dice
    explicitamente. Callar lo que no se comprobo es peor que no comprobarlo.
    """
    try:
        salida = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", ruta],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except FileNotFoundError:
        logger.info("ffprobe no está instalado: no se pueden medir duración ni dimensiones del video.")
        return {"ancho": None, "alto": None, "duracion_segundos": None, "medido": False}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise MedioInvalido(
            "El video está dañado o su formato no se puede leer.",
            code="MEDIA_CORRUPT",
        ) from e

    datos: Dict[str, Any] = {"medido": True}
    for linea in salida.splitlines():
        if "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        if clave == "width":
            datos["ancho"] = int(valor) if valor.isdigit() else None
        elif clave == "height":
            datos["alto"] = int(valor) if valor.isdigit() else None
        elif clave == "duration":
            try:
                datos["duracion_segundos"] = round(float(valor), 2)
            except ValueError:
                datos["duracion_segundos"] = None

    if not datos.get("ancho"):
        raise MedioInvalido(
            "El archivo no contiene ninguna pista de video.", code="MEDIA_CORRUPT"
        )
    return datos


@dataclass
class AvisoDeCreatividad:
    code: str
    mensaje: str
    bloquea: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "mensaje": self.mensaje, "bloquea": self.bloquea}


def verificar_creatividad(
    *, kind: str, size_bytes: int, ancho: Optional[int], alto: Optional[int],
    duracion_segundos: Optional[float], duracion_medida: bool = True,
) -> List[AvisoDeCreatividad]:
    """Avisos tecnicos accionables sobre una creatividad.

    Lo que se puede comprobar, se comprueba y se dice. Lo que NO se pudo medir,
    se dice tambien — dar por bueno lo que no se midio es la forma silenciosa de
    fallar.

    **Nada de esto predice si la creatividad va a funcionar.** Esa promesa esta
    prohibida (spec 10) y ademas seria falsa: el rendimiento de un creativo no se
    lee en sus pixeles.
    """
    avisos: List[AvisoDeCreatividad] = []

    if kind == "IMAGE":
        if size_bytes > MAX_IMAGEN_BYTES:
            avisos.append(AvisoDeCreatividad(
                "IMAGE_TOO_LARGE",
                f"La imagen pesa {size_bytes // 1024 // 1024} MB y Meta admite hasta "
                f"{MAX_IMAGEN_BYTES // 1024 // 1024} MB.",
                bloquea=True,
            ))
    else:
        if size_bytes > MAX_VIDEO_BYTES:
            avisos.append(AvisoDeCreatividad(
                "VIDEO_TOO_LARGE",
                f"El video pesa {size_bytes // 1024 // 1024} MB y el límite es "
                f"{MAX_VIDEO_BYTES // 1024 // 1024} MB.",
                bloquea=True,
            ))
        if not duracion_medida:
            avisos.append(AvisoDeCreatividad(
                "VIDEO_DURATION_UNKNOWN",
                "No se pudo medir la duración del video en este servidor. "
                "Compruébala antes de publicar: Meta rechaza los que superan "
                f"{MAX_VIDEO_SEGUNDOS // 60} minutos.",
            ))
        elif duracion_segundos and duracion_segundos > MAX_VIDEO_SEGUNDOS:
            avisos.append(AvisoDeCreatividad(
                "VIDEO_TOO_LONG",
                f"El video dura {int(duracion_segundos // 60)} minutos y Meta admite "
                f"hasta {MAX_VIDEO_SEGUNDOS // 60}.",
                bloquea=True,
            ))

    if ancho and alto:
        if min(ancho, alto) < MIN_LADO_PX:
            avisos.append(AvisoDeCreatividad(
                "RESOLUTION_TOO_LOW",
                f"La resolución ({ancho}×{alto}) es baja para anuncios; Meta "
                f"recomienda al menos {MIN_LADO_PX} px por lado.",
            ))
        aspecto = ancho / alto
        if aspecto < ASPECTO_MIN or aspecto > ASPECTO_MAX:
            avisos.append(AvisoDeCreatividad(
                "ASPECT_RATIO_OUT_OF_RANGE",
                f"La relación de aspecto ({aspecto:.2f}:1) queda fuera del rango que "
                f"Meta sirve sin recortar ({ASPECTO_MIN}–{ASPECTO_MAX}). El recorte "
                f"suele comerse el texto.",
            ))
    elif kind == "IMAGE":
        avisos.append(AvisoDeCreatividad(
            "DIMENSIONS_UNKNOWN",
            "No se pudieron leer las dimensiones de la imagen.",
        ))

    return avisos
