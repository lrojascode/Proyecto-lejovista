"""El contexto que viaja con cada respuesta del chat (spec 12, criterios 3-5).

**Cuatro cosas que el cliente necesita para interpretar una cifra**, no para
creerla: de qué cuenta es, de qué periodo, de cuándo son los datos y si la
evidencia alcanza para lo que la frase afirma.

Va como estructura, no como nota al pie ni como frase incrustada en el texto. Un
texto con el contexto dentro se rompe al truncarlo, al copiarlo o al reenviarlo,
y sobre todo lo puede perder el modelo al redactar — que es exactamente el caso
en el que más falta hace.

**Por qué la frescura se declara siempre y no solo cuando hay problema.** Una
cifra sin fecha invita a decidir sobre una falsa sensación de actualidad, y el
cliente no tiene forma de distinguir "no pasó nada" de "no nos enteramos". Las
dos se ven igual en la pantalla y significan cosas opuestas.

**La suficiencia es lo que separa declarar de afirmar.** Con muestra corta el
producto puede seguir mostrando la cifra —es real— pero no puede presentarla
como conclusión. La distinción la fija `06` y aquí solo se transporta.
"""
import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("lejovista.chat.contexto")

# Cuántas horas puede tener la última sincronización antes de que los datos se
# declaren desactualizados. Un sync diario que se saltó una vuelta todavía
# sirve; dos vueltas ya es otra cosa. Es el mismo umbral que usa el panel de
# rendimiento: dos números distintos para la misma pregunta harían que la
# pantalla y el chat se contradijeran.
HORAS_PARA_DESACTUALIZADO = 36


@dataclass(frozen=True)
class Frescura:
    """De cuándo son los datos que sustentan esta respuesta."""

    hay_datos: bool
    ultima_sincronizacion: Optional[datetime.datetime] = None
    horas: Optional[float] = None
    desactualizado: bool = False

    @property
    def motivo(self) -> Optional[str]:
        if not self.hay_datos:
            return "Esta cuenta todavía no se ha sincronizado con Meta."
        if self.desactualizado and self.horas is not None:
            return (
                f"Los datos son de hace {round(self.horas)} horas: puede que "
                "haya movimiento más reciente que todavía no tenemos."
            )
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hay_datos": self.hay_datos,
            "ultima_sincronizacion": (
                self.ultima_sincronizacion.isoformat()
                if self.ultima_sincronizacion else None
            ),
            "horas_desde_la_sincronizacion": (
                None if self.horas is None else round(self.horas, 1)
            ),
            "desactualizado": self.desactualizado,
            "motivo": self.motivo,
        }


def frescura_de(cuenta: Any, *, ahora: Optional[datetime.datetime] = None) -> Frescura:
    ultimo = getattr(cuenta, "last_synced_at", None)
    if ultimo is None:
        return Frescura(hay_datos=False, desactualizado=True)
    ahora = ahora or datetime.datetime.utcnow()
    horas = (ahora - ultimo).total_seconds() / 3600
    return Frescura(
        hay_datos=True,
        ultima_sincronizacion=ultimo,
        horas=horas,
        desactualizado=horas > HORAS_PARA_DESACTUALIZADO,
    )


# --- Suficiencia de evidencia ----------------------------------------------
#
# Tres estados y no dos. "Suficiente / insuficiente" obligaría a clasificar como
# insuficiente el caso en que simplemente no aplica —una pregunta sobre el
# estado de una campaña no necesita muestra— y el cliente leería una advertencia
# donde no hay nada que advertir.

SUFICIENCIA_NO_APLICA = "no_aplica"
SUFICIENCIA_SUFICIENTE = "suficiente"
SUFICIENCIA_INSUFICIENTE = "insuficiente"


@dataclass(frozen=True)
class Suficiencia:
    nivel: str = SUFICIENCIA_NO_APLICA
    motivo: Optional[str] = None

    @property
    def permite_afirmar(self) -> bool:
        """Con muestra corta la cifra se muestra, pero no se concluye."""
        return self.nivel != SUFICIENCIA_INSUFICIENTE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "nivel": self.nivel,
            "motivo": self.motivo,
            "permite_afirmar": self.permite_afirmar,
        }


def suficiente(motivo: Optional[str] = None) -> Suficiencia:
    return Suficiencia(SUFICIENCIA_SUFICIENTE, motivo)


def insuficiente(motivo: str) -> Suficiencia:
    return Suficiencia(SUFICIENCIA_INSUFICIENTE, motivo)


@dataclass(frozen=True)
class ContextoDeRespuesta:
    """Cuenta, periodo, frescura y suficiencia de UNA respuesta del chat."""

    cuenta_id: Optional[str] = None
    cuenta_nombre: Optional[str] = None
    periodo_label: Optional[str] = None
    periodo_dias: Optional[int] = None
    periodo_desde: Optional[datetime.date] = None
    periodo_hasta: Optional[datetime.date] = None
    frescura: Optional[Frescura] = None
    suficiencia: Suficiencia = field(default_factory=Suficiencia)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cuenta": {"id": self.cuenta_id, "nombre": self.cuenta_nombre},
            "periodo": {
                "label": self.periodo_label,
                "dias": self.periodo_dias,
                "desde": self.periodo_desde.isoformat() if self.periodo_desde else None,
                "hasta": self.periodo_hasta.isoformat() if self.periodo_hasta else None,
            },
            "frescura": self.frescura.as_dict() if self.frescura else None,
            "suficiencia": self.suficiencia.as_dict(),
        }

    def avisos(self) -> list:
        """Lo que hay que DECIRLE al cliente, además de mostrarlo estructurado.

        La interfaz puede no renderizar el bloque de contexto —o el cliente
        puede no mirarlo— y estos dos avisos cambian cómo se debe leer la cifra.
        Van también como texto para que no dependan de que alguien despliegue un
        panel.
        """
        avisos = []
        if self.frescura and self.frescura.motivo:
            avisos.append(self.frescura.motivo)
        if not self.suficiencia.permite_afirmar and self.suficiencia.motivo:
            avisos.append(self.suficiencia.motivo)
        return avisos


def periodo_desde_dias(
    dias: Optional[int], label: Optional[str] = None,
    *, hoy: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Convierte la ventana del chat en fechas concretas.

    "Los últimos 7 días" es lo que el cliente entiende; las fechas son lo que
    permite comprobar si la respuesta habla del periodo que se consultó.
    """
    if not dias:
        return {"periodo_label": label, "periodo_dias": None,
                "periodo_desde": None, "periodo_hasta": None}
    hoy = hoy or datetime.date.today()
    return {
        "periodo_label": label or f"los últimos {dias} días",
        "periodo_dias": dias,
        "periodo_desde": hoy - datetime.timedelta(days=dias),
        "periodo_hasta": hoy,
    }
