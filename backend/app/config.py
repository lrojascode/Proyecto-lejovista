import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # --- Local vs Prod ---
    MOCK_SERVICES: bool = Field(default=True)

    # --- Domain config ---
    BASE_DOMAIN: str = Field(default="localhost:3000")
    API_URL: str = Field(default="http://api.localhost:8000")

    # --- Database config ---
    DATABASE_URL: str = Field(default="postgresql+asyncpg://lejovista:localpassword123@db:5432/lejovista")

    # --- Redis config ---
    REDIS_URL: str = Field(default="redis://:localredispassword123@redis:6379/0")
    CELERY_RESULT_BACKEND_URL: str = Field(default="redis://:localredispassword123@redis:6379/1")

    # --- Security config ---
    SECRET_KEY: str
    ENCRYPTION_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    # --- Observabilidad ---
    SENTRY_DSN: str = Field(default="")   # vacío => Sentry desactivado
    LOG_LEVEL: str = Field(default="INFO")

    # --- API credentials ---
    META_APP_ID: str = Field(default="mock_app_id")
    META_APP_SECRET: str = Field(default="mock_app_secret")
    # Versión de la Graph/Marketing API. Antes estaba hardcodeada en v19.0, que
    # EXPIRÓ (Meta retira cada versión a los ~2 años y las llamadas devuelven
    # error). Mantener al día con el changelog:
    # https://developers.facebook.com/docs/graph-api/changelog
    META_API_VERSION: str = Field(default="v25.0")
    OPENROUTER_API_KEY: str = Field(default="")
    GEMINI_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")
    # Modelo por defecto unificado (coincide con .env). google/gemini-2.5-flash existe;
    # el antiguo default "gemini-3.5-flash" no era un id válido.
    DEFAULT_AI_MODEL: str = Field(default="google/gemini-2.5-flash")

    # --- Email (Brevo) ---
    BREVO_API_KEY: str = Field(default="")
    EMAIL_FROM: str = Field(default="notificaciones@lejovista.com")
    EMAIL_FROM_NAME: str = Field(default="LejoVista")

    # --- Feature flags ---
    # Crear tablas con Base.metadata.create_all al arrancar. Solo para dev/test.
    # En producción debe ser false y usarse `alembic upgrade head` (evita drift).
    AUTO_CREATE_TABLES: bool = Field(default=False)

    # Escrituras reales a Meta (pausar/ajustar/crear). Independiente de MOCK_SERVICES:
    # aunque MOCK_SERVICES=false (lecturas reales), las escrituras siguen simuladas
    # hasta que esto sea true. Evita mutaciones accidentales con parámetros aún sin validar.
    ENABLE_META_WRITEBACK: bool = Field(default=False)

    # F-2: encendido controlado del write-back. ENABLE_META_WRITEBACK es un
    # flag global — sin esto, activarlo escribe de verdad en TODAS las cuentas
    # a la vez, no solo en la piloto. Lista separada por comas de AdAccount.id.
    # Vacío (default) = comportamiento anterior, sin restricción por cuenta.
    WRITEBACK_ACCOUNT_ALLOWLIST: str = Field(default="")

    # Política de datos históricos. Antes esto era UN solo número
    # (SNAPSHOT_RETENTION_DAYS=180) que gobernaba cuatro decisiones que no
    # tienen por qué coincidir, así que cambiar una arrastraba las otras tres.
    #
    #   caliente (0 → 400 días)   snapshots diarios: ranking, desgloses, alertas
    #   frío     (400 → inicio)   tablas _archive: solo agregados
    #   mensual  (todo)           ad_account_spend_monthly: gasto por mes
    #
    # 400 y no 365: son 12 meses MÁS un mes de margen, para que "este mes contra
    # el mismo mes del año pasado" se resuelva en caliente sin tocar el archivo.

    # Hasta dónde baja el backfill diario y hasta dónde puede preguntar el chat
    # con detalle de campaña.
    HOT_DAILY_RETENTION_DAYS: int = Field(default=400)

    # A partir de cuántos días el archivador mueve filas a las tablas _archive.
    # Igual al anterior por default: el archivo empieza donde termina lo
    # caliente. Se separan porque son decisiones distintas — se podría querer
    # conservar más en caliente de lo que se archiva, o al revés.
    SNAPSHOT_RETENTION_DAYS: int = Field(default=400)

    # Ventana que mira la Capa 2 estadística (tendencias, Z-scores). No necesita
    # el histórico entero: series muy largas diluyen las anomalías recientes,
    # que es justo lo que esa capa busca.
    STATISTICS_WINDOW_DAYS: int = Field(default=180)

    # Techo de lo que el chat admite como ventana de consulta con detalle
    # diario. Más atrás se responde con agregados mensuales, sin límite.
    CHAT_MAX_HISTORY_DAYS: int = Field(default=400)

    # Carpeta donde se guardan las imágenes subidas para crear campañas (ver
    # POST /campaigns/upload-image). Compartida entre `backend` y
    # `celery-worker-creation` — en dev por el bind mount ./backend:/app, en
    # prod por el volumen nombrado `media_data` (docker-compose.prod.yml).
    MEDIA_ROOT: str = Field(default="/app/media")

    # /docs, /redoc y /openapi.json exponen el mapa completo de la API sin
    # auth (default de FastAPI). Se apaga al exponer la app públicamente
    # (ej. detrás de un túnel) — default True para no romper el uso local.
    ENABLE_API_DOCS: bool = Field(default=True)

    # Además de los orígenes fijos de abajo, se puede sumar un patrón extra
    # (ej. el hostname de un túnel ngrok/Cloudflare) sin hardcodearlo en el
    # código versionado.
    TUNNEL_ORIGIN_REGEX: str = Field(default="")

    @property
    def SIMULATE_META_WRITES(self) -> bool:
        """True si NO se deben ejecutar escrituras reales en Meta (sin distinguir
        por cuenta). Usar `simulate_meta_writes_for` donde haya un ad_account_id
        disponible — respeta el allowlist de la cuenta piloto (F-2)."""
        return self.MOCK_SERVICES or not self.ENABLE_META_WRITEBACK

    @property
    def is_unrestricted_writeback(self) -> bool:
        """H1: True si el sistema está configurado para escribir dinero real en TODAS
        las cuentas sin restricción de allowlist (estado de alto riesgo)."""
        return not self.MOCK_SERVICES and self.ENABLE_META_WRITEBACK and not self.WRITEBACK_ACCOUNT_ALLOWLIST.strip()

    def simulate_meta_writes_for(
        self, ad_account_id: Optional[str], external_account_id: Optional[str] = None
    ) -> bool:
        """F-2: como SIMULATE_META_WRITES, pero además restringe el write-back
        real a las cuentas de WRITEBACK_ACCOUNT_ALLOWLIST cuando está configurado
        — así ENABLE_META_WRITEBACK=true no enciende escritura real para todo el
        parque de cuentas de golpe, solo para la(s) piloto.

        El allowlist acepta el id interno (uuid) O el de Meta (`act_123...`):
        poner el `act_` es lo que hace cualquiera que lea la variable, y como
        antes solo se comparaba contra el uuid, el efecto era apagar el
        write-back para TODAS las cuentas en silencio — parecía configurado y
        no lo estaba (14-ago-2026).
        """
        return self.writeback_block_reason(ad_account_id, external_account_id) is not None

    def writeback_block_reason(
        self, ad_account_id: Optional[str] = None, external_account_id: Optional[str] = None
    ) -> Optional[str]:
        """Por QUÉ se simulan las escrituras, nombrando la variable culpable, o
        `None` si son reales.

        Existe porque "simulado" a secas no le sirve a nadie: hay tres causas
        posibles y desde afuera son indistinguibles. Este texto viaja hasta la
        pantalla de "campaña creada" para que se pueda arreglar sin leer logs.
        """
        if self.MOCK_SERVICES:
            return "MOCK_SERVICES=true"
        if not self.ENABLE_META_WRITEBACK:
            return "ENABLE_META_WRITEBACK=false"
        allowlist = {a.strip() for a in self.WRITEBACK_ACCOUNT_ALLOWLIST.split(",") if a.strip()}
        if not allowlist:
            return None  # sin allowlist configurado: todas las cuentas escriben real
        if {ad_account_id, external_account_id} & allowlist:
            return None
        return (
            f"WRITEBACK_ACCOUNT_ALLOWLIST={self.WRITEBACK_ACCOUNT_ALLOWLIST} "
            f"no incluye esta cuenta ({external_account_id or ad_account_id})"
        )

    # Usar .env buscando de forma robusta en la raíz del proyecto o del backend
    model_config = SettingsConfigDict(
        env_file=(
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")),
            ".env"
        ),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
