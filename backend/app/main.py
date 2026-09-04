from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.api.v1.auth import router as auth_router
from app.api.v1.organizations import router as org_router
from app.api.v1.campaign_chat import router as campaign_chat_router
from app.api.v1.campaigns import router as campaign_router
from app.api.v1.media import router as media_router
from app.api.v1.proposals import router as proposals_router
from app.api.v1.insights import router as insight_router
from app.api.v1.actions import router as action_router
from app.api.v1.reports import router as report_router
from app.api.v1.ai_usage import router as ai_usage_router
from app.api.v1.admin import router as admin_router
from app.api.v1.operacion import router as operacion_router
from app.services.interruptores import CapacidadApagada
from app.api.v1.invitations import router as invitations_router
from app.api.v1.ws import router as ws_router, redis_listener
from app.utils.log_sanitizer import instalar_saneador
from app.utils.security import require_agency, require_module
import logging
import asyncio
import re

# Configurar logging estructurado (formato con timestamp, nivel y logger).
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

# Saneado de registros (spec 07, base de la 09): se instala inmediatamente
# después de configurar el logging y ANTES de que la app registre nada. Borra
# credenciales y datos personales de TODO lo que se registre, incluido lo que
# emiten las librerías de terceros — confiar en que cada punto de llamada
# recuerde no registrar un secreto garantiza que alguna vez se olvide.
instalar_saneador()

logger = logging.getLogger("lejovista.main")

# Sentry opcional: se activa solo si hay DSN configurado. Import guardado para no
# romper si el paquete no está instalado.
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)
        logger.info("Sentry inicializado.")
    except Exception as e:
        logger.warning(f"No se pudo inicializar Sentry: {e}")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_TABLES:
        # Conveniencia de dev/test. En producción se usa `alembic upgrade head`.
        logger.info("AUTO_CREATE_TABLES=true → creando tablas con create_all (solo dev)...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Base de datos inicializada (create_all).")
    else:
        logger.info("AUTO_CREATE_TABLES=false → el esquema se gestiona con Alembic (alembic upgrade head).")

    # Catálogo de modelos de IA (spec 10). Idempotente y no destructivo: solo
    # inserta los que faltan, así que un precio corregido a mano o un modelo
    # puesto en retirada sobreviven al reinicio. La migración lo siembra en
    # producción; esto cubre los entornos que arrancan con `create_all`.
    try:
        from app.database import async_session
        from app.services.ai import catalogo
        async with async_session() as session:
            nuevos = await catalogo.sembrar(session)
            await session.commit()
        if nuevos:
            logger.info("Catálogo de modelos de IA: %d modelo(s) sembrado(s).", nuevos)
    except Exception:
        logger.exception(
            "No se pudo sembrar el catálogo de modelos de IA. El selector de "
            "modelos va a aparecer vacío hasta que se resuelva."
        )
    
    # Estado del write-back, bien visible al arrancar. Sin esto hay que deducirlo
    # de un `[MOCK] API CALL` perdido en los logs del worker, o peor: descubrirlo
    # porque la campaña "creada" no aparece en Ads Manager.
    motivo_global = settings.writeback_block_reason()
    if motivo_global:
        logger.warning(
            "ESCRITURAS A META SIMULADAS (%s): crear/pausar/ajustar campañas NO llega a Meta. "
            "Las campañas creadas reciben ids ficticios (mock_camp_...).", motivo_global,
        )
    elif settings.WRITEBACK_ACCOUNT_ALLOWLIST.strip():
        logger.warning(
            "Escrituras a Meta REALES, pero solo para las cuentas de "
            "WRITEBACK_ACCOUNT_ALLOWLIST=%s. El resto se simula. Se acepta el uuid interno "
            "o el id de Meta (act_...).", settings.WRITEBACK_ACCOUNT_ALLOWLIST,
        )
    else:
        logger.critical(
            "¡PELIGRO! Escrituras a Meta REALES para TODAS las cuentas (ENABLE_META_WRITEBACK=true con WRITEBACK_ACCOUNT_ALLOWLIST vacio). "
            "Cualquier accion aprobada se ejecutara con dinero real en cualquier cuenta conectada."
        )

    # Iniciar listener de Redis para WebSockets
    listener_task = asyncio.create_task(redis_listener())
    
    yield
    
    # Clean up
    listener_task.cancel()


app = FastAPI(
    title="LejoVista API",
    description="Backend de Análisis y Optimización Autónoma de Campañas con IA",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_API_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_API_DOCS else None,
)

# Configurar CORS para permitir requests de subdominios locales y de producción.
# El dominio real de despliegue se deriva de BASE_DOMAIN (ej.
# lejovista.agenciakibo.com y sus subdominios de tenant), así CORS funciona en
# cualquier dominio sin hardcodearlo. TUNNEL_ORIGIN_REGEX suma un hostname extra
# (túnel ngrok/Cloudflare) sin tocar el código.
_base_host = re.escape(settings.BASE_DOMAIN.split(":")[0])
_cors_origin_regex = rf"^https?://(.*\.)?({_base_host}|localhost:3000|localhost|lrojas\.dev)$"
if settings.TUNNEL_ORIGIN_REGEX:
    _cors_origin_regex = f"({_cors_origin_regex})|({settings.TUNNEL_ORIGIN_REGEX})"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost",
        "https://lrojas.dev",
    ],
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware de log para capturar subdominios (Tenant slugs)
@app.middleware("http")
async def tenant_slug_middleware(request: Request, call_next):
    # Intentamos leer la cabecera X-Tenant-Slug inyectada por Nginx
    tenant_slug = request.headers.get("X-Tenant-Slug")
    if tenant_slug:
        logger.info(f"Petición para inquilino (Tenant Slug): {tenant_slug}")
    return await call_next(request)



# Incluir Rutas de la API v1
# Una capacidad apagada por un interruptor operativo se convierte en 503 en
# cualquier ruta desde la que se levante (spec 13). Como manejador global y no
# como `try/except` por endpoint: los interruptores se aplican en el punto de
# EJECUCIÓN —el limitador de Meta, el servicio de medios, la creación— y esos
# puntos están a varias capas de distancia de la ruta. Un manejador cubre
# también el camino que se añada mañana.
#
# 503 y no 403: no es que falte permiso, es que la capacidad no está disponible
# ahora. La diferencia importa para el cliente y para cualquier reintento
# automático.
@app.exception_handler(CapacidadApagada)
async def _capacidad_apagada(request, exc: CapacidadApagada):
    logger.warning(
        "Petición rechazada por el interruptor '%s' (%s): %s",
        exc.capacidad, exc.ambito, request.url.path,
    )
    return JSONResponse(status_code=503, content={"detail": exc.as_detail()})


app.include_router(auth_router, prefix="/api/v1")
app.include_router(org_router, prefix="/api/v1")
# Módulos habilitables (execution/01 §3). Ojo con la granularidad: los módulos
# son PANTALLAS, pero varios endpoints son compartidos entre pantallas. El
# Resumen consume /reports/brief-data, /reports/kpis-summary, /reports/spend-history
# e /insights, así que gatear esos routers enteros dejaría el Resumen roto al
# apagar Reporte o Diagnósticos. Por eso solo se gatea aquí lo que pertenece a
# una sola pantalla; lo compartido se gatea endpoint por endpoint.
#
# Y esto NO es aislamiento de datos: esos endpoints compartidos devuelven los
# datos de la propia organización, que ya están acotados por tenant. Lo que el
# módulo decide es qué funcionalidad se contrató, no a qué datos se accede.
app.include_router(campaign_chat_router, prefix="/api/v1", dependencies=[Depends(require_module("chat"))])
app.include_router(campaign_router, prefix="/api/v1")
app.include_router(proposals_router, prefix="/api/v1")
app.include_router(insight_router, prefix="/api/v1")
app.include_router(action_router, prefix="/api/v1", dependencies=[Depends(require_module("acciones"))])
app.include_router(report_router, prefix="/api/v1")
# Consumo y costo de IA: altura de agencia. El modulo sigue existiendo para
# decidir si se OFRECE, pero la superficie es operativa — proveedor, modelo,
# tokens y costo son las palancas con las que la agencia opera el servicio, no
# informacion que el cliente pueda usar (spec 05).
app.include_router(
    ai_usage_router, prefix="/api/v1",
    dependencies=[Depends(require_module("ai_usage")), Depends(require_agency)],
)
app.include_router(media_router, prefix="/api/v1")
app.include_router(invitations_router, prefix="/api/v1")
# Panel de plataforma. Sin require_module: no es un modulo de cliente, y su
# gate --require_platform_admin-- vive en cada ruta del router.
app.include_router(admin_router, prefix="/api/v1")
# Operación (spec 13): interruptores, feature flags, señales, incidente y
# métricas. Sin `require_module`: apagar un módulo no puede dejar sin acceso
# a la palanca que sirve para contener un incidente.
app.include_router(operacion_router, prefix="/api/v1")
app.include_router(ws_router) # Not prefixed with api/v1 to keep ws://host/ws/

# Servir archivos estáticos subidos (/media)
import os
from fastapi.staticfiles import StaticFiles

# Solo los logos son públicos: el login los necesita ANTES de autenticar.
# MEDIA_ROOT guarda además los creativos de campaña de cada organización
# (MEDIA_ROOT/{org_id}/{uuid}.ext), así que montarlo entero dejaba las imágenes y
# videos de cualquier tenant accesibles sin autenticación. Se monta solo el
# subdirectorio de logos, que es el único que la app referencia por HTTP
# (`/media/logos/...`, ver POST /organizations/my/logo).
_LOGOS_DIR = os.path.join(settings.MEDIA_ROOT, "logos")
try:
    os.makedirs(_LOGOS_DIR, exist_ok=True)
except OSError:
    # Crear el directorio no puede tumbar el import: en CI MEDIA_ROOT conserva el
    # default de producción (/app/media) y el runner no puede escribir en /app.
    # Con check_dir=False el mount se registra igual y responde 404 hasta que el
    # directorio exista.
    logger.warning("No se pudo crear el directorio de logos %s", _LOGOS_DIR)
app.mount(
    "/media/logos",
    StaticFiles(directory=_LOGOS_DIR, check_dir=False),
    name="media-logos",
)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "mock_mode": settings.MOCK_SERVICES,
        "base_domain": settings.BASE_DOMAIN
    }


# D-2: chequeo profundo para monitoreo. El healthcheck del contenedor solo dice
# que FastAPI responde; esto dice si el PIPELINE está vivo: si ninguna cuenta
# sincronizó con éxito en 24h, el Beat/worker de sync está caído o roto y nadie
# se enteraría hasta que el cliente reclame. Sin auth a propósito (no expone
# datos de tenants, solo un timestamp global) para que un uptime-monitor simple
# pueda consultarlo.
@app.get("/health/deep")
async def health_deep():
    import datetime
    from sqlalchemy import select, func
    from app.database import async_session
    from app.models.sync_job import SyncJob

    async with async_session() as db:
        last_ok = (await db.execute(
            select(func.max(SyncJob.completed_at)).where(SyncJob.status == "COMPLETED")
        )).scalar_one_or_none()

    if last_ok is None:
        # Instalación nueva sin syncs todavía: no es un fallo del pipeline.
        return {"status": "ok", "last_successful_sync": None, "note": "sin syncs registrados aún"}

    age_hours = (datetime.datetime.utcnow() - last_ok).total_seconds() / 3600
    degraded = age_hours > 24
    return {
        "status": "degraded" if degraded else "ok",
        "last_successful_sync": last_ok.isoformat(),
        "hours_since_last_sync": round(age_hours, 1),
    }


# Spec 14, criterios 7 y 8. Un despliegue por identificador de commit no sirve de
# nada si despues no se puede comprobar cual llego. Esto responde tres cosas que
# el runbook y el smoke test necesitan y que hasta ahora habia que suponer:
#
#   - `commit`: el sha horneado en la imagen. "desconocido" en desarrollo, donde
#     el codigo viene de un volumen montado y no de un artefacto construido.
#   - `migracion_en_codigo` vs `migracion_aplicada`: si no coinciden, el
#     contenedor `migrate` no corrio o fallo, y el backend esta hablando con un
#     esquema que no es el suyo. Es el fallo silencioso mas caro de un
#     despliegue: la app arranca igual.
#
# Sin auth, como `/health/deep`: no expone datos de ningun tenant, y un
# despliegue tiene que poder verificarse desde fuera.
@app.get("/version")
async def version():
    from sqlalchemy import text
    from app.database import async_session

    en_codigo = _revision_de_codigo()
    try:
        async with async_session() as db:
            aplicada = (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except Exception:
        # Base inalcanzable o sin la tabla (instalacion que nunca migro). No se
        # inventa un valor: se dice que no se pudo leer.
        aplicada = None

    return {
        "commit": settings.GIT_SHA,
        "migracion_en_codigo": en_codigo,
        "migracion_aplicada": aplicada,
        "migraciones_al_dia": aplicada is not None and aplicada == en_codigo,
    }


def _revision_de_codigo() -> str | None:
    """La revision `head` de la cadena de migraciones que trae ESTA imagen."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = ScriptDirectory.from_config(Config(os.path.join(raiz, "alembic.ini")))
        return script.get_current_head()
    except Exception:
        return None
