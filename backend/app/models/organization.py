from datetime import datetime
from typing import Optional
import uuid
from decimal import Decimal
from sqlalchemy import String, Text, Float, DateTime, JSON, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship
from cryptography.fernet import Fernet
from app.config import settings
from app.database import Base

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    # Default para organizaciones nuevas. Es un default de APLICACION, no de la
    # base: las organizaciones existentes conservan el suyo.
    # OJO: el modelo tiene que corresponder al proveedor que realmente atiende
    # las llamadas. `gpt-5.6-luna` asume OpenAI; una org que use OpenRouter
    # tiene que cambiarlo en Configuracion.
    ai_model: Mapped[str] = mapped_column(String, default="gpt-5.6-luna")
    llm_provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # openai | gemini_direct | openrouter
    llm_api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Presupuesto mensual de consumo de IA, en USD (spec 10).
    #
    # La clave del LLM es del CLIENTE: un bucle de reintentos o un uso abusivo se
    # le factura a él, y hasta ahora el sistema no tenía techo — medía el gasto
    # con detalle y no lo detenía nunca. `Numeric` porque decide si se llama al
    # modelo, y una compuerta de gasto no se resuelve en coma flotante (spec 02).
    #
    # NULL = sin techo. Es el estado de toda organización anterior a esta spec, y
    # se conserva a propósito: activar un bloqueo retroactivo sobre clientes que
    # nunca lo acordaron cortaría su producto de un despliegue al otro. El
    # presupuesto se fija por organización, con las alertas andando antes que el
    # bloqueo.
    ai_monthly_budget_usd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    # Hasta qué umbral (70 / 90 / 100) ya se avisó este mes. Evita repetir la
    # misma alerta en cada llamada. Se reinicia junto con el mes: la columna
    # guarda "2026-09:90".
    ai_budget_alert_state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Límite financiero mensual de seguridad.
    #
    # `max_budget` (Float) se conserva durante la fase de EXPANSIÓN de la
    # migración: se sigue escribiendo para que la imagen anterior funcione sobre
    # este esquema. Lo que DECIDE es `max_budget_exact`, porque una comparación
    # en coma flotante binaria puede autorizar un gasto que excede el límite o
    # rechazar uno que cabe exactamente — en una compuerta financiera eso es un
    # fallo de autorización, no un problema de formato (spec 02).
    #
    # `max_budget` se retira en la fase de contracción.
    max_budget: Mapped[float] = mapped_column(Float, default=1000.0)
    max_budget_exact: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    # Rubro del negocio (ver app/services/policy_catalog.py) — determina qué
    # recordatorios de políticas de Meta se muestran en el dashboard.
    industry: Mapped[str] = mapped_column(String, nullable=True)
    # Marca por organización (Fase 6.2 / Roadmap 5)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    accent_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # M-2: overrides parciales por tenant sobre TENANT_CAMPAIGN_RULES /
    # AIThresholds (ver app/tenant_rules/campaign_defaults.py y
    # app/services/ai/thresholds.py). Vacío = usa los defaults globales tal
    # cual — el comportamiento de cualquier org existente no cambia hasta que
    # alguien la personalice explícitamente.
    campaign_rules_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_thresholds_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    # Módulos habilitados de la vista del cliente (ver app/tenant_rules/modules.py).
    # NULL o lista vacía = todos habilitados, igual que los overrides de arriba:
    # ninguna organización existente cambia de comportamiento hasta que alguien
    # la configure. La lista es de claves de AVAILABLE_MODULES.
    enabled_modules: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # Version CANDIDATA de la Graph/Marketing API para esta organizacion
    # (spec 08). Vacio = la configurada globalmente.
    #
    # Es el mecanismo de migracion: la cuenta piloto ejercita la version nueva
    # con un sync completo y una creacion pausada mientras el resto sigue en la
    # vigente, y el rollback es vaciar este campo — no desplegar una imagen.
    meta_api_version_candidata: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )

    # Estado de la baja (spec 09): fase, plazos, actor y detalle de lo eliminado.
    # Vacio = organizacion activa. La fila NO se borra al completar la baja: se
    # conserva vacia como constancia de que existio y de cuando se dio de baja.
    # Un DELETE aca cascadea a media base y deja la baja sin rastro auditable.
    offboarding_state: Mapped[dict] = mapped_column(JSON, default=dict)

    # Estado del alta asistida (spec 05). Que pasos se validaron CONTRA EL
    # SISTEMA EXTERNO real, quien y cuando. Vacio = alta no iniciada.
    #
    # La invitacion al cliente no se puede emitir hasta que esten todos: el
    # precedente conocido es una clave de IA guardada en la organizacion
    # equivocada, con el analista contestando con plantillas durante dias sin
    # que nada pareciera roto. Un cliente que entra a una organizacion a medio
    # configurar ve un producto que no funciona y no sabe por que.
    onboarding_state: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    users = relationship("OrganizationUser", back_populates="organization", cascade="all, delete-orphan")
    ad_accounts = relationship("AdAccount", back_populates="organization", cascade="all, delete-orphan")
    campaign_snapshots = relationship("CampaignSnapshot", back_populates="organization", cascade="all, delete-orphan")
    insights = relationship("AIInsight", back_populates="organization", cascade="all, delete-orphan")
    actions = relationship("AIAction", back_populates="organization", cascade="all, delete-orphan")
    sync_jobs = relationship("SyncJob", back_populates="organization", cascade="all, delete-orphan")
    meta_credentials = relationship("MetaCredential", back_populates="organization", cascade="all, delete-orphan")
    meta_pages = relationship("MetaPage", back_populates="organization", cascade="all, delete-orphan")
    whatsapp_numbers = relationship("WhatsAppNumber", back_populates="organization", cascade="all, delete-orphan")

    @property
    def limite_mensual(self) -> Decimal:
        """El límite que DECIDE, como decimal exacto.

        Cae a la columna antigua mientras la migración no haya poblado la nueva:
        durante la expansión conviven, y quedarse sin límite sería peor que
        arrastrar la imprecisión de origen un rato más.
        """
        from app.services.money import to_money

        if self.max_budget_exact is not None:
            return to_money(self.max_budget_exact, field="límite mensual")
        return to_money(self.max_budget or 0, field="límite mensual")

    def set_limite_mensual(self, valor) -> None:
        """Fija el límite en las dos columnas (fase de expansión)."""
        from app.services.money import to_money

        exacto = to_money(valor, field="límite mensual")
        self.max_budget_exact = exacto
        self.max_budget = float(exacto)

    def __init__(self, **kwargs):
        """Una Organización nace con proveedor de IA DECLARADO (spec 10).

        Se resuelve al construirla y no en cada llamada: es lo que convierte la
        elección en un dato pinchado el día del alta, en vez de en algo que se
        recalcula cada vez y cambia cuando cambia el entorno. Añadir mañana la
        credencial de otro proveedor no mueve a nadie porque no hay nada que
        recalcular.

        Va en `__init__` y no en `default=` de la columna a propósito: un default
        de columna solo se aplica al INSERT, así que una Organización construida
        en memoria —como las que usan las pruebas del cliente LLM— nacería sin
        proveedor y se comportaría distinto de una guardada. El invariante tiene
        que valer para el objeto, no para la fila.

        Un `llm_provider` explícito en los argumentos manda, incluido `None`
        pasado a propósito para probar el caso sin configurar.
        """
        if "llm_provider" not in kwargs:
            from app.services.ai.proveedor import proveedor_inicial
            kwargs["llm_provider"] = proveedor_inicial()
        super().__init__(**kwargs)

    def set_llm_api_key(self, key: Optional[str]) -> None:
        if not key or not key.strip():
            self.llm_api_key_encrypted = None
            return
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        self.llm_api_key_encrypted = fernet.encrypt(key.strip().encode()).decode()

    def get_llm_api_key(self) -> Optional[str]:
        if not self.llm_api_key_encrypted:
            return None
        try:
            fernet = Fernet(settings.ENCRYPTION_KEY.encode())
            return fernet.decrypt(self.llm_api_key_encrypted.encode()).decode()
        except Exception:
            return None

    @property
    def has_llm_api_key(self) -> bool:
        return bool(self.llm_api_key_encrypted)

    @property
    def llm_api_key_preview(self) -> Optional[str]:
        if not self.llm_api_key_encrypted:
            return None
        key = self.get_llm_api_key()
        if not key:
            return None
        if len(key) <= 8:
            return "••••"
        return f"••••••••{key[-4:]}"
