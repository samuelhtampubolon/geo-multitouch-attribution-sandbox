"""
FastAPI Main Application entry point.
Implements healthz/readyz, OWASP security headers + CSP, allow-listed CORS,
global rate limiting, request body size limits and leak-free error handling.
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from src.api.rate_limit import limiter
from src.api.routes_geo_mta import router as geo_mta_router
from src.config import settings
from src.database import engine, init_db
from src.seed_journeys import seed_database

logger = logging.getLogger("geo_mta.api")

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
# Swagger UI is served from jsDelivr and bootstraps with an inline script.
DOCS_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """App lifespan: initialize tables and seed initial corpus."""
    init_db()
    try:
        seed_database(settings.DEFAULT_TENANT_ID, force_reseed=False)
    except Exception:  # pragma: no cover - seeding is best effort
        logger.exception("Database seeding failed")
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Generative Engine Optimization (GEO) & Multi-Touch Attribution Sandbox API",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Rate limiting (default limit applies to every route via the middleware)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS: explicit origin allow-list, bearer tokens only (no cookies => no credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


class BodySizeLimitMiddleware:
    """Rejects request bodies above MAX_REQUEST_BODY_BYTES with HTTP 413 (declared or streamed)."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send) -> None:
        body = b'{"detail":"Request body too large."}'
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = True
            if too_large:
                await self._reject(send)
                return

        received = 0
        started = False
        exceeded = False

        async def limited_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message):
            nonlocal started
            if exceeded and not started:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited_receive, tracking_send)
        if exceeded and not started:
            await self._reject(send)


app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)


# OWASP Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    is_docs = request.url.path.startswith(DOCS_PATHS)
    response.headers["Content-Security-Policy"] = DOCS_CSP if is_docs else API_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"  # legacy auditor disabled per OWASP; CSP replaces it
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces or internal error strings to clients."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Internal server error."})


# --- Probes ---

@app.get("/healthz", summary="Liveness Probe", tags=["System Probes"])
@limiter.exempt
async def healthz() -> dict[str, str]:
    """Liveness probe for orchestrators and container health."""
    return {"status": "ok", "app": settings.PROJECT_NAME, "version": settings.VERSION}


@app.get("/readyz", summary="Readiness Probe", tags=["System Probes"])
@limiter.exempt
async def readyz():
    """Readiness probe verifying database connectivity (error details are logged, not returned)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "database": "unavailable"}
        )


# Include API routes
app.include_router(geo_mta_router)
