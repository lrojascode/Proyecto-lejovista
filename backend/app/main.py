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
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lejovista.main")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Verificando y creando tablas de base de datos...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Base de datos inicializada correctamente.")
    yield


app = FastAPI(
    title="LejoVista API",
    description="Backend de Análisis y Optimización Autónoma de Campañas con IA",
    version="1.0.0",
    lifespan=lifespan
)

# Configurar CORS para permitir requests de subdominios locales y producción
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost",
        "https://lrojas.dev",
        # Permitir wildcard de subdominios
        "http://*.localhost:3000",
        "http://*.localhost",
        "https://*.lrojas.dev"
    ],
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

@app.get("/")
def read_root():
    return {
        "status": "online",
        "mock_mode": settings.MOCK_SERVICES,
        "base_domain": settings.BASE_DOMAIN
    }
