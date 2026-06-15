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

    # --- Security config ---
    SECRET_KEY: str = Field(default="947c61f22e88a38b25be2f6bd9ad72f5d96207c4d51b32a76f285817d23e5904")
    ENCRYPTION_KEY: str = Field(default="sS31u3Gj6qK0T2lX8R7Jp7iXjV/eM+hQ3Z8wY0C7U0Y=")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # --- API credentials ---
    META_APP_ID: str = Field(default="mock_app_id")
    META_APP_SECRET: str = Field(default="mock_app_secret")
    OPENROUTER_API_KEY: str = Field(default="")
    DEFAULT_AI_MODEL: str = Field(default="google/gemini-2.5-flash")

    # Usar .env si existe en el directorio de ejecución (raíz del backend)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
