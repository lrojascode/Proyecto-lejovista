"""Validación única de toda salida del modelo (spec 10, criterios 1–5).

Un solo sitio que corre las cuatro guardas y registra el descarte. Antes cada
camino del chat decidía por su cuenta cuáles aplicar —el analista con
herramientas validaba números; el de la bolsa de datos validaba números y
limpiaba enlaces; el de redacción exigía igualdad exacta— y agregar una guarda
significaba acordarse de los tres. El camino que se agregara mañana no tendría
ninguna.

Las cuatro, en el orden en que conviene evaluarlas (de más barata y más
concluyente a menos):

1. **Cuenta ajena** — la respuesta habla de otra AdAccount. Barata y definitiva.
2. **Periodo cambiado** — la respuesta se mudó de ventana temporal.
3. **Promesa de éxito por apariencia** — historia 17.
4. **Cifras sin respaldo** — la guarda numérica que ya existía.

Una respuesta parcialmente inventada no es parcialmente confiable: cualquiera
de las cuatro invalida la salida COMPLETA. No se recorta la frase ofensora ni se
pide otra respuesta al modelo; se cae al camino determinista, que es correcto.

La única excepción es el enlace, que se limpia en vez de descartar: es lo único
que se puede quitar sin tocar las cifras, y descartar un análisis correcto por
un `http://` pegado al final ya costó respuestas de plantilla en producción.
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.models.ai_discard import (
    MOTIVO_CIFRA_SIN_RESPALDO,
    MOTIVO_CUENTA_AJENA,
    MOTIVO_PERIODO_CAMBIADO,
    MOTIVO_PROMESA_DE_EXITO,
)
from app.services.ai import descartes, guardas
from app.services.campaign_chat import grounding

logger = logging.getLogger("lejovista.ai.validacion")


@dataclass(frozen=True)
class Contexto:
    """Lo que la respuesta tiene permitido mencionar."""

    feature: str
    organization_id: Optional[str] = None
    ad_account_id: Optional[str] = None
    periodo: Optional[guardas.PeriodoAutorizado] = None
    cuenta: Optional[guardas.CuentaAutorizada] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None


@dataclass(frozen=True)
class Resultado:
    aceptada: bool
    texto: str
    motivo: Optional[str] = None
    detalle: Optional[str] = None

    def __bool__(self) -> bool:
        return self.aceptada


async def validar(
    *,
    hechos: Any,
    respuesta: str,
    contexto: Contexto,
) -> Resultado:
    """Corre las cuatro guardas. Devuelve el texto limpio o el motivo del descarte.

    En modo de registro (`GROUNDING_MODO_REGISTRO=true`) anota el descarte y
    devuelve la respuesta **aceptada**: es como la spec pide activar el
    endurecimiento —midiendo el falso positivo sobre tráfico real antes de
    descartar— porque el riesgo declarado no es teórico. Ya ocurrió una vez, con
    un tope mal calibrado, y el síntoma fue el analista contestando plantillas
    durante días.
    """
    texto = grounding.limpiar_enlaces(respuesta or "")

    motivo: Optional[str] = None
    detalle: Optional[str] = None

    ajena = guardas.cuenta_incoherente(contexto.cuenta, texto)
    if ajena:
        motivo, detalle = MOTIVO_CUENTA_AJENA, ajena

    if motivo is None:
        fuera = guardas.periodo_incoherente(contexto.periodo, texto)
        if fuera:
            motivo, detalle = MOTIVO_PERIODO_CAMBIADO, fuera

    if motivo is None:
        promesa = guardas.promesa_de_exito(texto)
        if promesa:
            motivo, detalle = MOTIVO_PROMESA_DE_EXITO, promesa

    if motivo is None:
        # Se pide el MOTIVO, no un booleano: que el modelo invente un
        # identificador y que calcule un promedio son fallos distintos, se
        # arreglan en sitios distintos, y con un motivo único la diferencia solo
        # quedaba en el log.
        fallo = grounding.motivo_de_rechazo(hechos, texto)
        if fallo is not None:
            motivo, detalle = fallo

    if motivo is None:
        return Resultado(aceptada=True, texto=texto)

    aplicado = descartes.descarte_activo()
    await descartes.registrar(
        motivo=motivo,
        feature=contexto.feature,
        organization_id=contexto.organization_id,
        ad_account_id=contexto.ad_account_id,
        detalle=detalle,
        extracto=texto,
        provider=contexto.provider,
        model=contexto.model,
        prompt_version=contexto.prompt_version,
        aplicado=aplicado,
    )

    if not aplicado:
        return Resultado(aceptada=True, texto=texto, motivo=motivo, detalle=detalle)
    return Resultado(aceptada=False, texto=texto, motivo=motivo, detalle=detalle)
