import os
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

    # --- API credentials ---
    META_APP_ID: str = Field(default="mock_app_id")
    META_APP_SECRET: str = Field(default="mock_app_secret")
    OPENROUTER_API_KEY: str = Field(default="")
    DEFAULT_AI_MODEL: str = Field(default="google/gemini-3.5-flash")

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
