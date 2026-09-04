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
    # Spec 14, criterio 8. El commit del que salio la imagen que esta corriendo.
    # Lo hornea el Dockerfile como build arg; en desarrollo queda "desconocido",
    # que es la respuesta honesta cuando el codigo viene de un volumen montado y
    # no de un artefacto construido.
    GIT_SHA: str = Field(default="desconocido")

    # --- API credentials ---
    META_APP_ID: str = Field(default="mock_app_id")
    META_APP_SECRET: str = Field(default="mock_app_secret")
    # Versión de la Graph/Marketing API. Meta retira cada versión a los ~2 años
    # y a partir de ahí las llamadas devuelven error — para TODOS los clientes a
    # la vez. La expiración se vigila desde `meta_ads/version.py`.
    # El default sale de `app/services/meta_ads/version.py`, que es LA fuente de
    # verdad. Estaba en "v25.0" mientras produccion usaba v26: cualquier
    # despliegue que no inyectara la variable volvia a v25 en silencio, y un
    # entorno nuevo levantado desde la plantilla nacia con la version
    # equivocada. Ahora hay UN sitio donde cambiarlo, y CI comprueba que el
    # resto coincida.
    META_API_VERSION: str = Field(default="v26.0")
    OPENROUTER_API_KEY: str = Field(default="")
    GEMINI_API_KEY: str = Field(default="")
    OPENAI_API_KEY: str = Field(default="")
    # Modelo por defecto unificado (coincide con .env). google/gemini-2.5-flash existe;
    # el antiguo default "gemini-3.5-flash" no era un id válido.
    DEFAULT_AI_MODEL: str = Field(default="google/gemini-2.5-flash")

    # --- Gobernanza del LLM (spec 10) ---
    #
    # El proveedor y el modelo son configuración EXPLÍCITA de cada Organización
    # (`llm_provider` / `ai_model`). Las tres claves de arriba dejaron de elegir
    # proveedor: ahora solo aportan la credencial del proveedor que la
    # Organización ya declaró. Añadir o quitar una no cambia el proveedor de
    # nadie — es el criterio 9 de la spec, y era el cambio silencioso más caro
    # que tenía el sistema.
    #
    # Modo de registro del grounding: la guarda ANOTA lo que descartaría y no
    # descarta. Sirve para medir el falso positivo sobre tráfico real antes de
    # encender el descarte, que es como la spec pide activarlo. En true, el
    # producto se comporta como antes de la spec y `ai_discards` se llena.
    GROUNDING_MODO_REGISTRO: bool = Field(default=False)

    # Proveedor que se FIJA en una Organización nueva. Se escribe en su fila al
    # crearla y no vuelve a cambiar solo: eso es lo que lo distingue de la
    # cascada anterior. Añadir mañana la clave de otro proveedor no mueve a
    # nadie, porque la elección quedó pinchada en el dato el día del alta.
    #
    # Vacío = la Organización nace sin proveedor y hay que elegírselo. Es más
    # ruidoso, y para algunos despliegues es lo correcto: mejor que el alta se
    # note incompleta a que arranque apuntando a un proveedor que nadie decidió.
    DEFAULT_AI_PROVIDER: str = Field(default="openai")

    # Reintentos ADICIONALES de una llamada al modelo (0 = un solo intento). Un
    # bucle de reintentos consume el presupuesto del cliente en minutos, y la
    # clave del LLM es suya.
    AI_MAX_REINTENTOS: int = Field(default=2)

    # Llamadas al modelo por minuto y por Organización (0 = sin límite). Ataja
    # el bucle que no reintenta sino que vuelve a empezar, contra el que el tope
    # de reintentos no puede nada.
    AI_MAX_LLAMADAS_POR_MINUTO: int = Field(default=30)

    # Presupuesto mensual de IA por defecto, en USD, para organizaciones NUEVAS.
    # Vacío = sin techo, que es el estado de todas las anteriores a la spec:
    # aplicar un bloqueo retroactivo a quien nunca lo acordó le cortaría el
    # producto de un despliegue al otro.
    AI_DEFAULT_MONTHLY_BUDGET_USD: str = Field(default="")

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

    # --- Medios (spec 07) ---
    #
    # `local` = disco del VPS (lo que hay hoy, y lo que usan los tests).
    # `s3` = cualquier servicio compatible con S3, que es el destino de
    # produccion: desacopla API, workers y VPS, y saca los medios del mismo
    # disco de 100 GB donde vive la base de datos.
    MEDIA_STORAGE_DRIVER: str = Field(default="local")
    MEDIA_S3_BUCKET: str = Field(default="")
    MEDIA_S3_ENDPOINT: str = Field(default="")
    MEDIA_S3_REGION: str = Field(default="")

    # Cuota por organizacion. Sin ella, un solo cliente puede agotar el disco de
    # todos — y en un VPS de 100 GB compartido con PostgreSQL eso tumba el
    # producto entero, no solo su cuenta.
    MEDIA_QUOTA_BYTES_POR_ORG: int = Field(default=5 * 1024 * 1024 * 1024)  # 5 GB
    MEDIA_QUOTA_ARCHIVOS_POR_ORG: int = Field(default=2000)

    # Ciclo de vida. Todo medio nace con caducidad: un archivo subido para una
    # propuesta que nunca se aprobo permanecia indefinidamente.
    MEDIA_TTL_TEMPORAL_HORAS: int = Field(default=48)      # cargado y sin asociar
    MEDIA_TTL_SIN_CAMPANA_DIAS: int = Field(default=30)    # asociado a una propuesta que no llego a crear

    # Registro publico (`POST /auth/signup`). **Apagado por defecto** (spec 05).
    #
    # El alta de una organizacion es un flujo ASISTIDO: la agencia valida Meta,
    # IA, WhatsApp y permisos, y recien despues invita al cliente. Mientras
    # exista un camino de alta que no pase por ahi, existe la posibilidad de una
    # organizacion sin validar — un cliente que entra a un espacio a medio
    # configurar ve un producto que no funciona y no sabe por que.
    #
    # Se conserva como interruptor y no se borra el endpoint porque la suite de
    # tests lo usa para construir organizaciones (37 archivos); el punto de
    # aplicacion es esta variable, y en produccion vale `false`.
    ENABLE_PUBLIC_SIGNUP: bool = Field(default=False)

    # Zona horaria en la que se cuentan los meses calendario.
    #
    # No es cosmética: la ventana del límite mensual de presupuesto (spec 02) y
    # las fechas de los Snapshot tienen que estar en el MISMO calendario. Meta
    # reporta las fechas en la zona de la cuenta publicitaria; calcular el
    # inicio de mes en UTC hacía que, durante las últimas horas de cada mes en
    # Perú (UTC-5), el gasto del día quedara fuera de la ventana y el margen se
    # viera más grande de lo que es.
    #
    # Mientras el alcance sea Perú, esto es la zona de todas las cuentas. Cuando
    # haya cuentas en otras zonas, el valor tiene que pasar a la AdAccount.
    BUSINESS_TIMEZONE: str = Field(default="America/Lima")

    # Compatibilidad hacia atrás del handshake del WebSocket: aceptar todavía
    # `?token=<JWT>` en el query string. Apagado por defecto — la credencial en
    # la URL queda en los logs de acceso del proxy y en el historial del
    # navegador, que es justo lo que el ticket por subprotocolo evita.
    #
    # Se enciende solo durante el despliegue en el que aún puede haber pestañas
    # abiertas con la app anterior ya cargada, y se apaga después.
    WS_ALLOW_QUERY_TOKEN: bool = Field(default=False)

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
