from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.api.v1.auth import router as auth_router
from app.api.v1.organizations import router as org_router
from app.api.v1.campaigns import router as campaign_router
from app.api.v1.insights import router as insight_router
from app.api.v1.actions import router as action_router
from app.api.v1.reports import router as report_router
from app.api.v1.ai_usage import router as ai_usage_router
from app.api.v1.ws import router as ws_router, redis_listener
import logging
import asyncio

# Configurar logging estructurado (formato con timestamp, nivel y logger).
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
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

# Configurar CORS para permitir requests de subdominios locales y producción.
# TUNNEL_ORIGIN_REGEX permite sumar el hostname de un túnel (ngrok/Cloudflare)
# sin hardcodear un dominio de terceros en el código versionado.
_cors_origin_regex = r"^https?://(.*\.)?(localhost:3000|localhost|lrojas\.dev)$"
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
app.include_router(auth_router, prefix="/api/v1")
app.include_router(org_router, prefix="/api/v1")
app.include_router(campaign_router, prefix="/api/v1")
app.include_router(insight_router, prefix="/api/v1")
app.include_router(action_router, prefix="/api/v1")
app.include_router(report_router, prefix="/api/v1")
app.include_router(ai_usage_router, prefix="/api/v1")
app.include_router(ws_router) # Not prefixed with api/v1 to keep ws://host/ws/

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
