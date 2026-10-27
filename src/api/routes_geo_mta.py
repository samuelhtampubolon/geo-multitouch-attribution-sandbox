"""
FastAPI Routes for GEO Audit, Multi-Touch Attribution, and JSON-LD Generation.
Hardened against BOLA, Mass Assignment, and SSRF.
"""

from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

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
    summary="Generate Tenant JWT Access Token"
)
async def generate_token(req: TokenRequest) -> TokenResponse:
    """Authenticates tenant and issues signed HS256 JWT access token."""
    token = create_jwt_token(
        tenant_id=req.tenant_id,
        subject=req.subject,
        role=req.role
    )
    return TokenResponse(
        access_token=token,
        token_type="Bearer",
        expires_in_minutes=1440,
        tenant_id=req.tenant_id
    )


# --- GEO Audit ---

@router.post(
    "/geo/audit",
    response_model=GeoAuditResponse,
    summary="Run Share-of-Model (SoM) Citation Audit"
)
async def audit_generative_share_of_model(
    req: GeoAuditRequest,
    auth_user: dict[str, Any] = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> GeoAuditResponse:
    """
    Audits Share of Model (SoM) visibility across simulated and live generative engines.
    Enforces BOLA tenant verification and SSRF URL validation.
    """
    # OWASP BOLA check
    verify_tenant_isolation(auth_user["tenant_id"], req.tenant_id)

    response = GeoAuditorService.run_deterministic_audit(req, db=db)
    return response


# --- Multi-Touch Attribution ---

@router.post(
    "/mta/compute",
    response_model=MtaComputeResponse,
    summary="Compute Multi-Touch Attribution (Markov & Shapley)"
)
async def compute_multi_touch_attribution(
    req: MtaComputeRequest,
    auth_user: dict[str, Any] = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> MtaComputeResponse:
    """
    Calculates Markov-Chain removal effects, exact Shapley values, and marginal ROAS.
    Reallocates marketing budget from over-credited to high-removal-effect channels.
    """
    # OWASP BOLA check
    verify_tenant_isolation(auth_user["tenant_id"], req.tenant_id)

    try:
        response = MultiTouchAttributionService.execute_mta_pipeline(req, db=db)
        return response
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# --- Schema.org JSON-LD Generator ---

@router.post(
    "/geo/jsonld",
    response_model=JsonLdResponse,
    summary="Generate Schema.org JSON-LD Markup for GEO"
)
async def generate_jsonld_markup(
    req: JsonLdRequest,
    auth_user: dict[str, Any] = Depends(get_current_tenant)
) -> JsonLdResponse:
    """
    Generates verified, XSS-escaped Schema.org JSON-LD (Product, FAQPage, Organization).
    Protects against SSRF during URL validation.
    """
    verify_tenant_isolation(auth_user["tenant_id"], req.tenant_id)

    try:
        response = JsonLdGeneratorService.process_request(req)
        return response
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
