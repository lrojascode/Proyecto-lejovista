"""Contrato de salida del modelo (spec 10, criterio 4).

**El contrato precede a la llamada.** Toda invocación declara qué forma puede
tener la respuesta: qué campos, de qué tipo y, para los campos de elección, qué
valores concretos son admisibles. Lo que no encaja se descarta.

Tres reglas que parecen detalles y no lo son:

**No se corrige.** Si el modelo devuelve `objetivo: "LEADS"` y el catálogo tiene
`OUTCOME_LEADS`, la respuesta se descarta; no se busca el valor más parecido. Un
corrector de "casi encaja" es una elección que nadie tomó, hecha por código que
no sabe qué quiso decir el modelo, sobre un campo que puede terminar en una
campaña con dinero real.

**No se rellena.** Un campo requerido ausente no toma su valor por defecto. Un
default silencioso convierte "el modelo no supo" en "el modelo eligió esto".

**El descarte es un fallo registrado, no un silencio.** El llamador cae a su
camino determinista, que es correcto, pero el descarte queda anotado: una tasa de
descarte que sube es la señal más temprana de que un prompt se rompió, y sin
registro se manifiesta como "el producto suena a plantilla" semanas después.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


class ContratoIncumplido(ValueError):
    """La salida del modelo no encaja en el contrato declarado."""

    def __init__(self, motivos: Sequence[str]):
        self.motivos = list(motivos)
        super().__init__("; ".join(self.motivos))


@dataclass(frozen=True)
class Campo:
    """Un campo de la respuesta esperada.

    `catalogo` es lo que convierte esto en una guarda y no en una comprobación de
    tipos: sin él, `str` admite cualquier cadena, y el modelo puede proponer un
    objetivo de campaña, un tipo de puja o una ubicación que Meta no acepta —o
    peor, que acepta y no es la que se quería.
    """

    nombre: str
    tipo: type = str
    requerido: bool = True
    catalogo: Optional[Tuple[Any, ...]] = None
    max_chars: Optional[int] = None
    # Para campos de lista: cuántos elementos, y de qué tipo son.
    lista_de: Optional[type] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None

    def _validar_escalar(self, valor: Any, etiqueta: str, motivos: List[str]) -> None:
        if self.tipo is float and isinstance(valor, int) and not isinstance(valor, bool):
            valor = float(valor)
        if isinstance(valor, bool) and self.tipo is not bool:
            motivos.append(f"{etiqueta}: se esperaba {self.tipo.__name__} y llegó un booleano")
            return
        if not isinstance(valor, self.tipo):
            motivos.append(
                f"{etiqueta}: se esperaba {self.tipo.__name__} y llegó "
                f"{type(valor).__name__}"
            )
            return
        if self.catalogo is not None and valor not in self.catalogo:
            motivos.append(
                f"{etiqueta}: {valor!r} está fuera del catálogo cerrado "
                f"({', '.join(str(c) for c in self.catalogo)})"
            )
            return
        if self.max_chars is not None and isinstance(valor, str) and len(valor) > self.max_chars:
            motivos.append(
                f"{etiqueta}: {len(valor)} caracteres, el máximo es {self.max_chars}"
            )

    def validar(self, datos: Dict[str, Any], motivos: List[str]) -> None:
        if self.nombre not in datos or datos[self.nombre] is None:
            if self.requerido:
                motivos.append(f"{self.nombre}: falta y es obligatorio")
            return

        valor = datos[self.nombre]

        if self.lista_de is not None:
            if not isinstance(valor, list):
                motivos.append(f"{self.nombre}: se esperaba una lista y llegó {type(valor).__name__}")
                return
            if self.min_items is not None and len(valor) < self.min_items:
                motivos.append(f"{self.nombre}: {len(valor)} elementos, el mínimo es {self.min_items}")
            if self.max_items is not None and len(valor) > self.max_items:
                motivos.append(f"{self.nombre}: {len(valor)} elementos, el máximo es {self.max_items}")
            for i, item in enumerate(valor):
                hijo = Campo(
                    nombre=f"{self.nombre}[{i}]", tipo=self.lista_de,
                    catalogo=self.catalogo, max_chars=self.max_chars,
                )
                hijo._validar_escalar(item, hijo.nombre, motivos)
            return

        self._validar_escalar(valor, self.nombre, motivos)


@dataclass(frozen=True)
class ContratoDeSalida:
    """El conjunto de campos admisibles de una respuesta.

    `estrictos` rechaza los campos que el contrato no declara. Está apagado por
    defecto: un modelo que agrega `"razonamiento"` a su JSON no está inventando
    un hecho, y descartar por eso sería el tipo de estrictez que deja al
    analista sin analizar. Se enciende donde el campo extra SÍ importa.
    """

    nombre: str
    campos: Tuple[Campo, ...] = field(default_factory=tuple)
    estricto: bool = False

    def motivos(self, datos: Any) -> List[str]:
        if not isinstance(datos, dict):
            return [f"la salida no es un objeto JSON sino {type(datos).__name__}"]

        motivos: List[str] = []
        for campo in self.campos:
            campo.validar(datos, motivos)

        if self.estricto:
            declarados = {c.nombre for c in self.campos}
            for clave in datos:
                if clave not in declarados:
                    motivos.append(f"{clave}: el contrato no declara este campo")

        return motivos

    def cumple(self, datos: Any) -> bool:
        return not self.motivos(datos)

    def validar(self, datos: Any) -> Dict[str, Any]:
        """Devuelve los datos si cumplen; si no, levanta `ContratoIncumplido`.

        Devuelve SOLO los campos declarados. Un campo que el contrato no nombra
        no llega al resto del sistema aunque se haya tolerado su presencia: que
        pase la validación no lo convierte en dato de confianza.
        """
        motivos = self.motivos(datos)
        if motivos:
            raise ContratoIncumplido(motivos)
        declarados = {c.nombre for c in self.campos}
        return {k: v for k, v in datos.items() if k in declarados}
