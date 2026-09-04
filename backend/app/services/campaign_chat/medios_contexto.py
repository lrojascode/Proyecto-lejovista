"""Lo que el sistema entendió de una foto o un video (spec 12, 10-12).

**Se muestra antes de usarse.** Una interpretación equivocada que nadie ve se
convierte en una campaña equivocada, y el momento barato de corregirla es antes
de gastar. El cliente ve qué se entendió, lo corrige si hace falta, y la
corrección manda sobre lo extraído.

**No es un juicio de calidad ni una promesa de rendimiento.** Está prohibido
por la spec 10 y además sería falso: el rendimiento de una creatividad no se lee
en sus píxeles. Lo que se extrae es descriptivo —qué se ve, qué texto tiene, qué
formato es— y los avisos son técnicos: pesa demasiado, es muy corto, la relación
de aspecto no encaja.

**Lo técnico se separa de lo interpretado, y por un motivo.** Las dimensiones y
la duración son hechos medidos del archivo: no hay nada que corregir. La
descripción de lo que se ve es una interpretación, y el cliente es la autoridad
sobre ella. Mezclarlas invitaría a "corregir" el peso del archivo, o a dar por
buena la interpretación porque viene junto a datos exactos.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("lejovista.chat.medios")

# Lo que el cliente puede corregir. El resto son hechos del archivo.
CAMPOS_CORREGIBLES = ("producto", "descripcion", "texto_visible", "elementos")


@dataclass(frozen=True)
class ContextoDelMedio:
    """Lo medido y lo interpretado de un medio, separados."""

    # --- Hechos del archivo. No se corrigen. ---
    kind: Optional[str] = None
    ancho: Optional[int] = None
    alto: Optional[int] = None
    duracion_segundos: Optional[float] = None
    tamano_bytes: Optional[int] = None

    # --- Interpretación. El cliente manda. ---
    producto: Optional[str] = None
    descripcion: Optional[str] = None
    texto_visible: Optional[str] = None
    elementos: tuple = ()

    # Avisos TÉCNICOS de `media_validation`. Nunca un juicio de rendimiento.
    avisos: tuple = ()
    # Qué corrigió una persona. Se conserva junto a lo extraído en vez de
    # pisarlo: sin esto no se puede saber si el sistema interpreta cada vez
    # peor, ni en qué se equivoca.
    correcciones: Dict[str, Any] = field(default_factory=dict)
    revisado: bool = False

    @property
    def hay_interpretacion(self) -> bool:
        return bool(self.producto or self.descripcion or self.texto_visible or self.elementos)

    def efectivo(self, campo: str) -> Any:
        """El valor que MANDA: la corrección si la hay, si no lo extraído."""
        if campo in self.correcciones:
            return self.correcciones[campo]
        return getattr(self, campo, None)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "medido": {
                "kind": self.kind,
                "ancho": self.ancho,
                "alto": self.alto,
                "duracion_segundos": self.duracion_segundos,
                "tamano_bytes": self.tamano_bytes,
            },
            "interpretado": {
                campo: self.efectivo(campo) for campo in CAMPOS_CORREGIBLES
            },
            "correcciones": dict(self.correcciones),
            "revisado": self.revisado,
            "avisos": [dict(a) if isinstance(a, dict) else a for a in self.avisos],
            "hay_interpretacion": self.hay_interpretacion,
        }


def desde_asset(asset: Any, avisos: Optional[List[Any]] = None) -> ContextoDelMedio:
    """El contexto que se puede construir sin interpretar nada.

    Es lo que hay hoy: dimensiones, duración y avisos técnicos. La
    interpretación visual —qué producto se ve— exige un modelo con visión, y
    hasta que exista se declara ausente en vez de inventarse.
    """
    # Los nombres son los de `MediaAsset` (spec 07), en castellano. Leer
    # `width`/`height` devolvería `None` en silencio y el cliente vería una
    # revisión vacía sin que nada fallara.
    return ContextoDelMedio(
        kind=getattr(asset, "kind", None),
        ancho=getattr(asset, "ancho", None),
        alto=getattr(asset, "alto", None),
        duracion_segundos=getattr(asset, "duracion_segundos", None),
        tamano_bytes=getattr(asset, "size_bytes", None),
        avisos=tuple(
            {"code": getattr(a, "code", None), "mensaje": getattr(a, "mensaje", str(a)),
             "bloquea": getattr(a, "bloquea", False)}
            for a in (avisos or [])
        ),
    )


def con_interpretacion(
    base: ContextoDelMedio,
    *,
    producto: Optional[str] = None,
    descripcion: Optional[str] = None,
    texto_visible: Optional[str] = None,
    elementos: Optional[List[str]] = None,
) -> ContextoDelMedio:
    """Añade lo interpretado sobre lo medido, sin tocar lo medido."""
    return ContextoDelMedio(
        kind=base.kind, ancho=base.ancho, alto=base.alto,
        duracion_segundos=base.duracion_segundos, tamano_bytes=base.tamano_bytes,
        producto=producto or base.producto,
        descripcion=descripcion or base.descripcion,
        texto_visible=texto_visible or base.texto_visible,
        elementos=tuple(elementos) if elementos else base.elementos,
        avisos=base.avisos,
        correcciones=dict(base.correcciones),
        revisado=base.revisado,
    )


class CorreccionInvalida(ValueError):
    """Se intentó corregir algo que no es interpretación."""

    code = "MEDIA_CONTEXT_FIELD_NOT_EDITABLE"

    def as_detail(self) -> dict:
        return {"code": self.code, "message": str(self)}


def corregir(base: ContextoDelMedio, correcciones: Dict[str, Any]) -> ContextoDelMedio:
    """Aplica la corrección del cliente (criterio 11).

    Solo sobre lo interpretado. Corregir el ancho de una imagen no significa
    nada —el archivo mide lo que mide— y aceptarlo dejaría al sistema mandando a
    Meta unas dimensiones que la imagen no tiene.

    La corrección se GUARDA APARTE de lo extraído en vez de pisarlo: conservar
    las dos permite responder en qué se equivoca la interpretación, que es lo
    único que permite mejorarla.
    """
    ajenos = set(correcciones) - set(CAMPOS_CORREGIBLES)
    if ajenos:
        raise CorreccionInvalida(
            f"No se puede corregir {', '.join(sorted(ajenos))}: son datos medidos "
            f"del archivo, no interpretación. Corregibles: "
            f"{', '.join(CAMPOS_CORREGIBLES)}."
        )

    limpias = dict(base.correcciones)
    for campo, valor in correcciones.items():
        if isinstance(valor, str):
            valor = valor.strip()
        limpias[campo] = valor

    return ContextoDelMedio(
        kind=base.kind, ancho=base.ancho, alto=base.alto,
        duracion_segundos=base.duracion_segundos, tamano_bytes=base.tamano_bytes,
        producto=base.producto, descripcion=base.descripcion,
        texto_visible=base.texto_visible, elementos=base.elementos,
        avisos=base.avisos, correcciones=limpias, revisado=True,
    )


def texto_de_revision(contexto: ContextoDelMedio) -> List[str]:
    """Lo que se le muestra al cliente para que lo revise.

    Descriptivo y en su idioma. Ni una palabra sobre si el medio va a funcionar:
    nadie lo sabe antes de gastar, y decirlo empujaría a poner dinero sobre una
    intuición presentada como pronóstico.
    """
    lineas: List[str] = []

    if contexto.kind == "VIDEO" and contexto.duracion_segundos:
        lineas.append(f"Es un video de {round(contexto.duracion_segundos)} segundos.")
    elif contexto.kind == "IMAGE" and contexto.ancho and contexto.alto:
        lineas.append(f"Es una imagen de {contexto.ancho}×{contexto.alto} píxeles.")

    if contexto.efectivo("producto"):
        lineas.append(f"Entiendo que el producto es: **{contexto.efectivo('producto')}**.")
    if contexto.efectivo("descripcion"):
        lineas.append(f"Lo que veo: {contexto.efectivo('descripcion')}")
    if contexto.efectivo("texto_visible"):
        lineas.append(f"Texto que aparece en la pieza: «{contexto.efectivo('texto_visible')}»")

    for aviso in contexto.avisos:
        mensaje = aviso.get("mensaje") if isinstance(aviso, dict) else str(aviso)
        if mensaje:
            lineas.append(mensaje)

    if not contexto.hay_interpretacion:
        lineas.append(
            "Todavía no puedo describir lo que se ve en la pieza. Si querés, "
            "contame qué producto es para que el texto del anuncio hable de eso."
        )
    else:
        lineas.append("¿Está bien así, o corrijo algo?")

    return lineas
