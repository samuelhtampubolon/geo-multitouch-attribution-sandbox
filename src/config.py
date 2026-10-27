"""
Configuration module for GEO & Multi-Touch Attribution Sandbox.

Security notes:
- In production (APP_ENV=production) the JWT secret and tenant client secrets MUST be
  supplied through the environment; the application refuses to start otherwise.
- In development, a random ephemeral JWT secret is generated per process when none is
  configured, so no usable signing key is ever committed to the repository.
"""

from __future__ import annotations
import os
import secrets
from pathlib import Path
from pydantic import BaseModel, Field, model_validator

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

MIN_JWT_SECRET_LENGTH = 32

# Development-only demo credential for the seeded tenant. Never valid in production.
DEV_DEMO_CLIENT_SECRET = "dev-only-demo-client-secret-change-me"


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_tenant_secrets(raw: str) -> dict[str, str]:
    """Parses 'tenant-a:secret-a,tenant-b:secret-b' into a mapping."""
    parsed: dict[str, str] = {}
    for pair in _parse_csv(raw):
        tenant, sep, secret = pair.partition(":")
        if sep and tenant.strip() and secret.strip():
            parsed[tenant.strip()] = secret.strip()
    return parsed


class Settings(BaseModel):
    """Application configuration and environment variables."""

    PROJECT_NAME: str = "Generative Engine Optimization & Multi-Touch Attribution Sandbox"
    VERSION: str = "2.0.0"
    ENV: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development").lower())
    DEBUG: bool = Field(default_factory=lambda: os.getenv("APP_DEBUG", "false").lower() == "true")

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
    JWT_SECRET: str = Field(default_factory=lambda: os.getenv("JWT_SECRET", ""))
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "geo-mta-sandbox"
    JWT_AUDIENCE: str = "geo-mta-api"
    JWT_EXPIRATION_MINUTES: int = Field(
        default_factory=lambda: int(os.getenv("JWT_EXPIRATION_MINUTES", "60"))
    )
    DEFAULT_TENANT_ID: str = "tenant-alpha"
    TENANT_CLIENT_SECRETS: dict[str, str] = Field(
        default_factory=lambda: _parse_tenant_secrets(os.getenv("TENANT_CLIENT_SECRETS", ""))
    )
    ALLOWED_ROLES: tuple[str, ...] = ("analyst", "viewer")

    # CORS: explicit allow-list only (never '*' together with credentials)
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: _parse_csv(
            os.getenv(
                "CORS_ORIGINS",
                "http://127.0.0.1:8501,http://localhost:8501,"
                "https://samuelhtampubolon.github.io",
            )
        )
    )

    # Rate Limiting & Resource Bounds (Denial-of-Service Mitigations)
    RATE_LIMIT_DEFAULT: str = Field(default_factory=lambda: os.getenv("RATE_LIMIT_DEFAULT", "120/minute"))
    RATE_LIMIT_AUTH: str = Field(default_factory=lambda: os.getenv("RATE_LIMIT_AUTH", "10/minute"))
    MAX_REQUEST_BODY_BYTES: int = 1_000_000
    MAX_PATH_LENGTH: int = 25
    MAX_JOURNEYS_PER_REQUEST: int = 10_000
    MAX_DISTINCT_CHANNELS: int = 20
    MAX_SHAPLEY_CHANNELS: int = 12
    MAX_MARKOV_ORDER: int = 3
    MAX_BOOTSTRAP_SAMPLES: int = 500
    MAX_AUDIT_BATCH_SIZE: int = 200

    # SSRF Protection
    ALLOWED_SCHEMES: tuple[str, ...] = ("https",)
    ALLOWED_PORTS: tuple[int, ...] = (443,)
    MAX_URL_LENGTH: int = 2048

    @property
    def is_production(self) -> bool:
        return self.ENV in ("production", "prod")

    @model_validator(mode="after")
    def _harden_secrets(self) -> "Settings":
        if self.is_production:
            if len(self.JWT_SECRET) < MIN_JWT_SECRET_LENGTH:
                raise ValueError(
                    f"JWT_SECRET must be set to at least {MIN_JWT_SECRET_LENGTH} characters in production."
                )
            if not self.TENANT_CLIENT_SECRETS:
                raise ValueError("TENANT_CLIENT_SECRETS must be configured in production.")
            weak = [t for t, s in self.TENANT_CLIENT_SECRETS.items() if len(s) < 16 or s == DEV_DEMO_CLIENT_SECRET]
            if weak:
                raise ValueError(f"Tenant client secrets are too weak for: {', '.join(sorted(weak))}")
        else:
            if not self.JWT_SECRET:
                # Ephemeral per-process key: tokens are invalidated on restart, nothing leaks via git.
                self.JWT_SECRET = secrets.token_urlsafe(48)
            if not self.TENANT_CLIENT_SECRETS:
                self.TENANT_CLIENT_SECRETS = {self.DEFAULT_TENANT_ID: DEV_DEMO_CLIENT_SECRET}
        return self


settings = Settings()
