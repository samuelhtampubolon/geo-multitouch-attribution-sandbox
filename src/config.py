"""
Configuration module for GEO & Multi-Touch Attribution Sandbox.
"""

from __future__ import annotations
import os
from pathlib import Path
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseModel):
    """Application configuration and environment variables."""

    PROJECT_NAME: str = "Generative Engine Optimization & Multi-Touch Attribution Sandbox"
    VERSION: str = "1.0.0"
    ENV: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    DEBUG: bool = Field(default_factory=lambda: os.getenv("APP_DEBUG", "True").lower() == "true")

    # Network Binding
    HOST: str = Field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    API_PORT: int = Field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    STREAMLIT_PORT: int = Field(default_factory=lambda: int(os.getenv("STREAMLIT_PORT", "8501")))

    # Database
    DATABASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", f"sqlite:///{DATA_DIR / 'geo_mta.db'}"
        )
    )

    # Security & Auth
    JWT_SECRET: str = Field(
        default_factory=lambda: os.getenv(
            "JWT_SECRET", "super-secret-geo-mta-attributions-key-2026-owasp"
        )
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 1440
    DEFAULT_TENANT_ID: str = "tenant-alpha"

    # Rate Limiting & Resource Bounds (Denial-of-Service Mitigations)
    RATE_LIMIT_DEFAULT: str = "120/minute"
    MAX_PATH_LENGTH: int = 25
    MAX_SHAPLEY_CHANNELS: int = 12
    MAX_AUDIT_BATCH_SIZE: int = 200

    # SSRF Protection
    ALLOWED_SCHEMES: tuple[str, ...] = ("https",)


settings = Settings()
