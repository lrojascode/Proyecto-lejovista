"""Interruptores operativos y feature flags (spec 13).

Dos tablas y una bitácora. Comparten forma —ámbito, clave, valor— y no se
fusionan a propósito: un interruptor se apaga en mitad de un incidente y un
flag se enciende para un rollout. Confundirlos haría que la lista de "qué está
apagado ahora mismo" se llenara de flags de despliegue, y esa lista se lee con
prisa.

**El estado vive en la base, no en la configuración.** Un interruptor que exige
un despliegue no sirve durante un incidente, que es el único momento en que se
usa. Cambiarlo tiene efecto inmediato y queda registrado con actor y momento.

**El ámbito es global o por Organización.** Nunca las dos a la vez: la
resolución mira primero lo específico y cae a lo global, de modo que apagar una
capacidad para un cliente no toca a los demás (criterio 5).
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# --- Las capacidades que se pueden apagar ----------------------------------
#
# Cinco, separadas, porque un incidente rara vez las afecta a todas. Apagar la
# creación de campañas porque Meta devuelve errores de escritura dejaría al
# cliente sin consultar su histórico, que no tiene nada que ver.

CAP_CREACION = "creacion"
CAP_WRITE_BACK = "write_back"
CAP_SYNC = "sync"
CAP_IA = "ia"
CAP_MEDIOS = "medios"

CAPACIDADES = (CAP_CREACION, CAP_WRITE_BACK, CAP_SYNC, CAP_IA, CAP_MEDIOS)

# El modo de solo lectura es una capacidad aparte, no la suma de las cinco.
# Se activa de una vez, que es lo que hace falta cuando todavía no se sabe cuál
# es la causa: ir apagando de a una mientras se investiga deja huecos.
CAP_SOLO_LECTURA = "solo_lectura"

# OJO: en las cinco capacidades, `encendido=True` significa "la capacidad
# funciona". En `solo_lectura` significa lo CONTRARIO: "está bloqueando". Por eso
# se resuelve con su propia función (`interruptores.solo_lectura_activo`) y no
# con la de las capacidades — mezclarlos produciría, tarde o temprano, un `not`
# en el sitio equivocado, y aquí el sitio equivocado decide si se puede escribir
# durante un incidente.

DESCRIPCION_DE_LA_CAPACIDAD = {
    CAP_CREACION: "Crear campañas nuevas",
    CAP_WRITE_BACK: "Enviar cambios a Meta (pausar, ajustar presupuesto)",
    CAP_SYNC: "Traer datos de Meta",
    CAP_IA: "Respuestas y textos redactados por el modelo",
    CAP_MEDIOS: "Subir fotos y videos",
    CAP_SOLO_LECTURA: "Modo de solo lectura: se bloquea toda escritura",
}


class OperationalSwitch(Base):
    """Una capacidad apagada, global o para una Organización.

    Solo existen filas para lo APAGADO y para lo encendido explícitamente. La
    ausencia de fila significa "encendido", que es el estado normal: una tabla
    con una fila por capacidad y por organización habría que mantenerla al alta
    de cada cliente, y el día que se olvidara, esa organización nacería con todo
    apagado.
    """

    __tablename__ = "operational_switches"
    __table_args__ = (
        UniqueConstraint("capacidad", "organization_id", name="uq_switch_capacidad_org"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    capacidad: Mapped[str] = mapped_column(String, index=True)
    # NULL = global. La resolución mira primero la fila de la Organización.
    organization_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    encendido: Mapped[bool] = mapped_column(Boolean, default=True)
    # Por qué se apagó. Se muestra en el diagnóstico de la agencia: un
    # interruptor apagado sin motivo obliga a preguntar a quien lo movió, y
    # durante un incidente esa persona está ocupada.
    motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    actualizado_por_user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actualizado_por_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FeatureFlag(Base):
    """Una capacidad nueva encendida para alguien.

    Ámbito global, por Organización o por AdAccount —la cuenta piloto es un
    caso de uso de este mecanismo, no un concepto aparte—. El valor
    predeterminado NO vive aquí: vive declarado en el código, junto al flag, para
    que un flag sin fila tenga un valor conocido y no un `NULL` que cada
    consumidor interprete a su manera.
    """

    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint(
            "clave", "organization_id", "ad_account_id", name="uq_flag_clave_ambito",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    clave: Mapped[str] = mapped_column(String, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # La cuenta piloto: el ámbito más estrecho posible. Probar una capacidad
    # nueva con una sola cuenta y no con una organización entera es la
    # diferencia entre un error acotado y un cliente enfadado.
    ad_account_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("ad_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )

    activo: Mapped[bool] = mapped_column(Boolean, default=False)
    nota: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    actualizado_por_user_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OperationalChange(Base):
    """Quién movió qué y cuándo (criterio 4).

    Aparte de la fila del interruptor porque esa guarda el estado ACTUAL y esto
    guarda la historia. Sin la historia, "¿desde cuándo está apagado esto?" solo
    se puede responder con el `updated_at`, que se pisa en cada cambio.
    """

    __tablename__ = "operational_changes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tipo: Mapped[str] = mapped_column(String, index=True)  # interruptor | flag
    clave: Mapped[str] = mapped_column(String, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    ad_account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    valor_anterior: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    valor_nuevo: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    motivo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    actor_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
