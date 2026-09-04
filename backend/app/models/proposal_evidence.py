"""Trazabilidad de una propuesta y aprendizaje de la revisión humana (spec 11).

Tres tablas que responden tres preguntas que hoy no se pueden responder:

**`proposal_evidence` — ¿en qué se basó esta propuesta?** Campañas fuente,
ventana, baseline congelado, versión de reglas, versión de prompt, modelo y
confianza. Se escribe al PROPONER, no al crear: si se escribiera al crear, una
propuesta rechazada no dejaría rastro, y las rechazadas son justamente las que
conviene revisar. El baseline se guarda congelado —no una referencia a él—
porque el vigente se recalibra y el veredicto tiene que compararse contra el que
estaba cuando se decidió (criterio 13).

**`proposal_change` — ¿qué cambió una persona, y por qué?** El motivo es
obligatorio y estructurado: un conjunto acotado más texto libre. El conjunto
acotado es lo que permite detectar patrones; el texto libre es lo que evita que
el conjunto acotado mienta. Sin motivo la modificación no se guarda, porque un
cambio sin motivo no enseña nada y es indistinguible de un error de tecleo.

**`candidate_rule` — ¿esto se repite lo bastante como para ser una regla?** El
sistema detecta que un mismo motivo vuelve y propone; una persona acepta o
rechaza. Convertir correcciones en política automáticamente sobreajusta a la
última opinión y hace el comportamiento impredecible. El rechazo también se
registra, y con él un plazo: volver a proponer lo mismo al día siguiente
convierte la propuesta en ruido y garantiza que se deje de leer.
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# --- Motivos de cambio humano ----------------------------------------------
#
# Conjunto acotado. Añadir uno es barato; quitarlo no, porque el histórico ya
# lo usa. Cada entrada describe una CAUSA, no el campo que se tocó: "subí el
# presupuesto" no enseña nada, "el presupuesto propuesto no alcanzaba para la
# temporada" sí.

MOTIVO_PRESUPUESTO_INSUFICIENTE = "presupuesto_insuficiente"
MOTIVO_PRESUPUESTO_EXCESIVO = "presupuesto_excesivo"
MOTIVO_PUBLICO_INCORRECTO = "publico_incorrecto"
MOTIVO_COPY_NO_ENCAJA = "copy_no_encaja"
MOTIVO_MEDIO_NO_ENCAJA = "medio_no_encaja"
MOTIVO_TEMPORADA = "temporada_distinta"
MOTIVO_RESTRICCION_DEL_CLIENTE = "restriccion_del_cliente"
MOTIVO_POLITICA_DE_META = "politica_de_meta"
MOTIVO_OTRO = "otro"

MOTIVOS_DE_CAMBIO = (
    MOTIVO_PRESUPUESTO_INSUFICIENTE,
    MOTIVO_PRESUPUESTO_EXCESIVO,
    MOTIVO_PUBLICO_INCORRECTO,
    MOTIVO_COPY_NO_ENCAJA,
    MOTIVO_MEDIO_NO_ENCAJA,
    MOTIVO_TEMPORADA,
    MOTIVO_RESTRICCION_DEL_CLIENTE,
    MOTIVO_POLITICA_DE_META,
    MOTIVO_OTRO,
)

MOTIVOS_LEGIBLES = {
    MOTIVO_PRESUPUESTO_INSUFICIENTE: "El presupuesto propuesto se quedaba corto",
    MOTIVO_PRESUPUESTO_EXCESIVO: "El presupuesto propuesto era demasiado",
    MOTIVO_PUBLICO_INCORRECTO: "El público no era el adecuado",
    MOTIVO_COPY_NO_ENCAJA: "El texto no encajaba con el negocio",
    MOTIVO_MEDIO_NO_ENCAJA: "La imagen o el video no encajaban",
    MOTIVO_TEMPORADA: "La temporada cambia lo que conviene hacer",
    MOTIVO_RESTRICCION_DEL_CLIENTE: "El cliente pidió algo distinto",
    MOTIVO_POLITICA_DE_META: "Riesgo de política de Meta",
    MOTIVO_OTRO: "Otro motivo",
}

# `otro` obliga a escribir. Es la válvula que evita que el conjunto acotado
# mienta: sin ella, quien no encuentre su motivo elegirá el más parecido y el
# histórico quedará contaminado con correcciones mal clasificadas.
MOTIVOS_QUE_EXIGEN_TEXTO = (MOTIVO_OTRO,)


class ProposalEvidence(Base):
    """La foto de en qué se basó una propuesta, en el momento de proponerla."""

    __tablename__ = "proposal_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"), index=True)
    ad_account_id: Mapped[str] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    draft_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    # La versión de propuesta de la spec 03, cuando la hay. La evidencia se
    # escribe al proponer y la versión puede llegar después.
    version_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    # Las campañas que la respaldan, con periodo y costo. Es el conjunto
    # identificable del criterio 1.
    campanas_fuente: Mapped[list] = mapped_column(JSON, default=list)
    # Baseline CONGELADO. No una referencia: el vigente se recalibra y el
    # veredicto tiene que compararse contra el que estaba (criterio 13).
    baseline_json: Mapped[dict] = mapped_column(JSON, default=dict)
    confianza_json: Mapped[dict] = mapped_column(JSON, default=dict)
    afirmaciones_json: Mapped[list] = mapped_column(JSON, default=list)
    excluidas_json: Mapped[list] = mapped_column(JSON, default=list)

    ventana_dias: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    campanas_comparables: Mapped[int] = mapped_column(Integer, default=0)
    nivel_confianza: Mapped[str] = mapped_column(String, index=True)
    exploratoria: Mapped[bool] = mapped_column(Integer, default=0, index=True)

    # De qué versiones salió. `reglas_version` es la de las reglas de campaña
    # del tenant; las otras dos vienen de la spec 10.
    reglas_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    modelo: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    proveedor: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProposalChange(Base):
    """Un cambio humano sobre una propuesta, con su motivo obligatorio."""

    __tablename__ = "proposal_changes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"), index=True)
    ad_account_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    draft_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    # De qué versión a cuál. Con las dos se puede reconstruir el antes y el
    # después sin depender de que el diff se haya guardado bien.
    version_anterior_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    version_nueva_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    motivo: Mapped[str] = mapped_column(String, index=True)
    motivo_detalle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Campo → {antes, despues}. Se guarda el diff resuelto para poder listar
    # "qué cambió" sin cargar y comparar las dos versiones completas.
    cambios_json: Mapped[dict] = mapped_column(JSON, default=dict)

    actor_user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# Estados de una regla candidata.
REGLA_PROPUESTA = "propuesta"
REGLA_ACEPTADA = "aceptada"
REGLA_RECHAZADA = "rechazada"

# Cuántas veces tiene que repetirse un motivo para proponerlo como regla. Tres
# es poco, y a propósito: el umbral solo dispara una PROPUESTA, que una persona
# lee y decide. Ponerlo alto haría que un patrón real tardara meses en salir a
# la superficie.
REPETICIONES_PARA_PROPONER = 3

# Cuánto espera el sistema antes de volver a proponer una regla rechazada.
# Reproponerla al día siguiente la convierte en ruido y garantiza que se deje
# de leer, que es la forma más segura de perder también las buenas.
DIAS_DE_ESPERA_TRAS_RECHAZO = 90


class CandidateRule(Base):
    """Un patrón de corrección repetido, propuesto como regla."""

    __tablename__ = "candidate_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String, ForeignKey("organizations.id"), index=True)
    ad_account_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    motivo: Mapped[str] = mapped_column(String, index=True)
    # Qué se propone. Texto, no configuración ejecutable: esta spec detecta y
    # propone; aplicar la regla es otra decisión y otro alcance.
    propuesta: Mapped[str] = mapped_column(Text)
    # Los cambios concretos que la sostienen. Sin ellos, "esto se repite" es una
    # afirmación que nadie puede verificar antes de aceptarla.
    ocurrencias_json: Mapped[list] = mapped_column(JSON, default=list)
    ocurrencias: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String, default=REGLA_PROPUESTA, index=True)
    resuelta_por_user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resuelta_en: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    nota_de_resolucion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
