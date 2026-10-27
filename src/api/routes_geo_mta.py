"""
FastAPI Routes for GEO Audit, Multi-Touch Attribution, and JSON-LD Generation.
Hardened against BOLA, Mass Assignment, SSRF, broken authentication and resource exhaustion.
Handlers are synchronous so CPU-bound math and blocking DB I/O run in the threadpool
instead of stalling the event loop.
"""

from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.api.rate_limit import RATE_LIMIT_COMPUTE, limiter
from src.config import settings
from src.database import get_db
from src.schemas import (
    GeoAuditRequest,
    GeoAuditResponse,
    JsonLdRequest,
    JsonLdResponse,
    MtaComputeRequest,
    MtaComputeResponse,
    TokenRequest,
    TokenResponse
)
from src.security import (
    authenticate_tenant_client,
    create_jwt_token,
    get_current_tenant,
    verify_tenant_isolation
)
from src.services.geo_auditor import GeoAuditorService
from src.services.markov_shapley_mta import MultiTouchAttributionService
from src.services.jsonld_generator import JsonLdGeneratorService

router = APIRouter(prefix="/api/v1", tags=["GEO & MTA Operations"])


# --- Authentication ---

@router.post(
    "/auth/token",
    response_model=TokenResponse,
    summary="Exchange tenant client credentials for a JWT access token"
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
def generate_token(request: Request, req: TokenRequest) -> TokenResponse:
    """
    Authenticates the tenant with its client secret (constant-time comparison) and issues a
    short-lived HS256 JWT carrying iss/aud/jti claims. Only least-privilege roles can be issued.
    """
    if not authenticate_tenant_client(req.tenant_id, req.client_secret.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid tenant credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_jwt_token(tenant_id=req.tenant_id, subject=req.subject, role=req.role)
    return TokenResponse(
        access_token=token,
        token_type="Bearer",
        expires_in_minutes=settings.JWT_EXPIRATION_MINUTES,
        tenant_id=req.tenant_id
    )


# --- GEO Audit ---

@router.post(
    "/geo/audit",
    response_model=GeoAuditResponse,
    summary="Run Share-of-Model (SoM) Citation Audit"
)
def audit_generative_share_of_model(
    req: GeoAuditRequest,
    auth_user: dict[str, Any] = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> GeoAuditResponse:
    """
    Audits Share of Model (SoM) visibility across simulated generative engines.
    Enforces BOLA tenant verification and SSRF URL validation.
    """
    verify_tenant_isolation(auth_user["tenant_id"], req.tenant_id)
    try:
        return GeoAuditorService.run_deterministic_audit(req, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# --- Multi-Touch Attribution ---

@router.post(
    "/mta/compute",
    response_model=MtaComputeResponse,
    summary="Compute Multi-Touch Attribution (Markov, Shapley, rule-based) and optimal budget"
)
@limiter.limit(RATE_LIMIT_COMPUTE)
def compute_multi_touch_attribution(
    request: Request,
    req: MtaComputeRequest,
    auth_user: dict[str, Any] = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> MtaComputeResponse:
    """
    Calculates order-k Markov removal effects (optional bootstrap CI), exact Shapley values,
    five rule-based models, model divergence, and a concave marginal-ROAS budget optimum.
    """
    verify_tenant_isolation(auth_user["tenant_id"], req.tenant_id)
    if auth_user.get("role") == "viewer" and req.persist:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Viewer role cannot persist results.")
    try:
        return MultiTouchAttributionService.execute_mta_pipeline(req, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# --- Schema.org JSON-LD Generator ---

@router.post(
    "/geo/jsonld",
    response_model=JsonLdResponse,
    summary="Generate Schema.org JSON-LD Markup for GEO"
)
def generate_jsonld_markup(
    req: JsonLdRequest,
    auth_user: dict[str, Any] = Depends(get_current_tenant)
) -> JsonLdResponse:
    """
    Generates sanitized Schema.org JSON-LD (Product, FAQPage, Organization), serialized so it
    can be embedded in <script type="application/ld+json"> without break-out. URLs are SSRF-validated.
    """
    verify_tenant_isolation(auth_user["tenant_id"], req.tenant_id)
    try:
        return JsonLdGeneratorService.process_request(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
