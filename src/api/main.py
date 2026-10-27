"""
FastAPI Main Application entry point.
Implements healthz/readyz, OWASP security headers, CORS, and Rate Limiting.
"""

from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from src.api.routes_geo_mta import router as geo_mta_router
from src.config import settings
from src.database import engine, init_db
from src.seed_journeys import seed_database

# Rate Limiter
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """App lifespan: initialize tables and seed initial corpus."""
    init_db()
    try:
        seed_database(settings.DEFAULT_TENANT_ID, force_reseed=False)
    except Exception as e:
        print(f"Warning: Database seeding notice: {e}")
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Generative Engine Optimization (GEO) & Multi-Touch Attribution Sandbox API",
    lifespan=lifespan
)

# Attach SlowAPI limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Local sandbox live-server access
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# OWASP Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# --- Probes ---

@app.get("/healthz", summary="Liveness Probe", tags=["System Probes"])
async def healthz() -> dict[str, str]:
    """Liveness probe for orchestrators and container health."""
    return {"status": "ok", "app": settings.PROJECT_NAME, "version": settings.VERSION}


@app.get("/readyz", summary="Readiness Probe", tags=["System Probes"])
async def readyz() -> dict[str, str]:
    """Readiness probe verifying database connectivity."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "error": str(e)}
        )


# Include API routes
app.include_router(geo_mta_router)
