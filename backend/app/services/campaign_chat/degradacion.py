"""Las cuatro situaciones que el cliente no debe confundir (spec 12, 13-15).

El chat puede quedarse corto por cuatro motivos distintos, y hasta ahora los
cuatro producían un texto parecido. El cliente veía lo mismo y sacaba la
conclusión más pesimista y más frecuente: que no hay datos.

| Situación | Qué pasó | Qué SIGUE funcionando |
|---|---|---|
| `ia_no_disponible` | El modelo no responde, no hay credencial o se agotó el presupuesto | Cifras, estados, listados y tablas: todo lo determinista |
| `meta_no_disponible` | La sincronización falla; los datos existen pero son de antes | Todo, con la frescura declarada |
| `no_autorizado` | Falta permiso sobre la cuenta, o la sesión ya no vale | Nada de esa cuenta, y es correcto |
| `sin_datos` | La cuenta no tiene histórico todavía | Nada que mostrar, y tampoco hay nada roto |

**El fallo de autorización conserva su semántica.** No se disfraza de estado
conversacional ni se responde con un mensaje amable de chat: un 403 que llega
como "no encontré datos de esa cuenta" le enseña al cliente que la cuenta está
vacía cuando lo que pasa es que no le corresponde. Eso es peor que un error.

**La ausencia de datos es la cuarta y no es una degradación.** Nada está roto:
la cuenta es nueva. Meterla en el mismo saco haría que un cliente nuevo creyera
que el producto está fallando el primer día.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

IA_NO_DISPONIBLE = "ia_no_disponible"
META_NO_DISPONIBLE = "meta_no_disponible"
NO_AUTORIZADO = "no_autorizado"
SIN_DATOS = "sin_datos"

SITUACIONES = (IA_NO_DISPONIBLE, META_NO_DISPONIBLE, NO_AUTORIZADO, SIN_DATOS)


@dataclass(frozen=True)
class Degradacion:
    """Una situación degradada, con lo que se puede y no se puede hacer."""

    situacion: str
    mensaje: str
    # Qué sigue funcionando. Se nombra primero en el texto porque es lo que
    # decide si el cliente puede seguir trabajando o tiene que volver luego.
    sigue_disponible: str
    detalle: Optional[str] = None

    @property
    def es_falta_de_datos(self) -> bool:
        return self.situacion == SIN_DATOS

    def as_dict(self) -> Dict[str, Any]:
        return {
            "situacion": self.situacion,
            "mensaje": self.mensaje,
            "sigue_disponible": self.sigue_disponible,
            "detalle": self.detalle,
        }

    def como_texto(self) -> str:
        partes = [self.mensaje]
        if self.sigue_disponible:
            partes.append(self.sigue_disponible)
        return "\n\n".join(partes)


def ia_no_disponible(detalle: Optional[str] = None) -> Degradacion:
    """El modelo no está. Las respuestas deterministas siguen (criterio 13)."""
    return Degradacion(
        situacion=IA_NO_DISPONIBLE,
        mensaje=(
            "Ahora mismo no puedo interpretar preguntas escritas libremente: el "
            "asistente que redacta no está disponible."
        ),
        sigue_disponible=(
            "Tus datos están completos y siguen consultables: cuánto gastaste, "
            "qué campañas tenés, cómo va cada una y las tablas de siempre. Lo "
            "único que falta es la explicación redactada."
        ),
        detalle=detalle,
    )


def meta_no_disponible(frescura: Optional[Dict[str, Any]] = None) -> Degradacion:
    """La sincronización falla. Los datos existen, son de antes (criterio 14)."""
    horas = (frescura or {}).get("horas_desde_la_sincronizacion")
    cuando = f"de hace {round(horas)} horas" if horas else "de la última sincronización correcta"
    return Degradacion(
        situacion=META_NO_DISPONIBLE,
        mensaje=(
            f"No estoy pudiendo traer datos nuevos de Meta, así que lo que ves es "
            f"{cuando}."
        ),
        sigue_disponible=(
            "No es que no haya datos: los que tenés son reales, solo que puede "
            "haber movimiento más reciente que todavía no llegó."
        ),
        detalle=(frescura or {}).get("motivo"),
    )


def no_autorizado(detalle: Optional[str] = None) -> Degradacion:
    """Falta permiso. NO se disfraza de estado conversacional (criterio 15)."""
    return Degradacion(
        situacion=NO_AUTORIZADO,
        mensaje="No tenés permiso para ver los datos de esta cuenta.",
        sigue_disponible=(
            "Esto no quiere decir que la cuenta esté vacía. Si creés que "
            "deberías tener acceso, pedíselo a quien administra la cuenta."
        ),
        detalle=detalle,
    )


def sin_datos(detalle: Optional[str] = None) -> Degradacion:
    """La cuarta. Nada está roto: la cuenta es nueva."""
    return Degradacion(
        situacion=SIN_DATOS,
        mensaje="Todavía no hay datos de esta cuenta para mostrarte.",
        sigue_disponible=(
            "No hay ningún problema técnico: en cuanto las campañas empiecen a "
            "gastar y a traer resultados, aparecen acá."
        ),
        detalle=detalle,
    )
