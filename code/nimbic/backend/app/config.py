import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "SaaN Shield Gateway"
    ENVIRONMENT: str = "dev"  # dev, staging, prod
    DEBUG: bool = True
    BACKEND_PORT: int = 8000
    LOG_LEVEL: str = "info"

    # CORS Settings
    ALLOWED_ORIGINS: str = "*"  # Comma-separated list of origins for production

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres_secure_password@db:5432/saan_ai_gateway"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Security
    SECRET_KEY: str = "replace-with-a-secure-random-secret-key-for-jwt-and-cookies"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Rate Limiting Defaults (overridable)
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000

    # External AI Providers (Optional, since keys may be provided at runtime)
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    COHERE_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Instantiate the global settings object
settings = Settings()
