"""
Security and threat mitigation test suite:
- SSRF prevention (cloud metadata 169.254.169.254, loopback 127.0.0.1, private IPs, scheme validation)
- XSS and HTML injection sanitization
- Pydantic extra='forbid' mass assignment mitigation
- BOLA tenant isolation enforcement
- API liveness and readiness probe verification
"""

from __future__ import annotations
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.schemas import GeoAuditRequest, ProductSchemaInput, TokenRequest
from src.security import (
    create_jwt_token,
    decode_jwt_token,
    sanitize_text,
    validate_and_sanitize_url,
    verify_tenant_isolation
)
from src.services.jsonld_generator import JsonLdGeneratorService


def test_ssrf_blocks_cloud_metadata_ip():
    """
    CRITICAL MANDATORY CONTRACT CHECK:
    Verify SSRF validator blocks http://169.254.169.254 and https://169.254.169.254 with HTTP 400.
    """
    with pytest.raises(HTTPException) as exc_http:
        validate_and_sanitize_url("http://169.254.169.254/latest/meta-data/")
    assert exc_http.value.status_code == 400

    with pytest.raises(HTTPException) as exc_https:
        validate_and_sanitize_url("https://169.254.169.254/computeMetadata/v1/")
    assert exc_https.value.status_code == 400
    assert "SSRF violation" in exc_https.value.detail or "restricted IP" in exc_https.value.detail


def test_ssrf_blocks_loopback_and_localhost():
    """
    CRITICAL MANDATORY CONTRACT CHECK:
    Verify SSRF validator blocks http://127.0.0.1, https://127.0.0.1, and localhost.
    """
    with pytest.raises(HTTPException) as exc1:
        validate_and_sanitize_url("http://127.0.0.1:8000/admin")
    assert exc1.value.status_code == 400

    with pytest.raises(HTTPException) as exc2:
        validate_and_sanitize_url("https://127.0.0.1:8000/admin")
    assert exc2.value.status_code == 400

    with pytest.raises(HTTPException) as exc3:
        validate_and_sanitize_url("https://localhost:8501")
    assert exc3.value.status_code == 400


def test_ssrf_blocks_private_networks():
    """Verify SSRF validator blocks RFC1918 private subnets."""
    private_urls = [
        "https://10.0.0.1/internal-api",
        "https://192.168.1.1/router",
        "https://172.16.0.5/secrets",
    ]
    for url in private_urls:
        with pytest.raises(HTTPException) as exc:
            validate_and_sanitize_url(url)
        assert exc.value.status_code == 400
        assert "SSRF violation" in exc.value.detail


def test_ssrf_blocks_insecure_schemes():
    """Verify SSRF validator blocks non-https schemes (http, ftp, file, gopher)."""
    disallowed = [
        "http://example.com/login",
        "ftp://internal.backup/db.tar.gz",
        "file:///etc/passwd",
        "gopher://evil.server"
    ]
    for url in disallowed:
        with pytest.raises(HTTPException) as exc:
            validate_and_sanitize_url(url)
        assert exc.value.status_code == 400
        assert "Insecure URL scheme" in exc.value.detail or "SSRF" in exc.value.detail


def test_ssrf_allows_legitimate_public_https():
    """Verify legitimate public HTTPS URLs pass validation."""
    valid_url = "https://github.com/samuelhtampubolon"
    sanitized = validate_and_sanitize_url(valid_url)
    assert sanitized.startswith("https://github.com")


def test_pydantic_extra_forbid_mitigates_mass_assignment():
    """Verify that models with extra='forbid' reject unrecognized injection payload parameters."""
    # Valid payload
    valid_data = {"tenant_id": "tenant-test", "target_brand": "BrandAlpha"}
    model = GeoAuditRequest(**valid_data)
    assert model.tenant_id == "tenant-test"

    # Malicious payload with extra unapproved administrative/override fields
    malicious_data = {
        "tenant_id": "tenant-test",
        "target_brand": "BrandAlpha",
        "is_admin": True,
        "internal_bypass_security": True
    }
    with pytest.raises(ValidationError) as exc:
        GeoAuditRequest(**malicious_data)
    assert "extra_forbidden" in str(exc.value)


def test_xss_sanitization_in_jsonld():
    """Verify that script tags and malicious attributes are sanitized in JSON-LD generator."""
    xss_payload = "<script>alert('xss')</script>Apex Developer Suite"
    sanitized = sanitize_text(xss_payload)
    assert "<script>" not in sanitized
    assert "alert('xss')" not in sanitized
    assert "Apex Developer Suite" in sanitized


def test_bola_tenant_isolation_rejection():
    """Verify that BOLA validator denies cross-tenant access with HTTP 403."""
    # Same tenant passes
    verify_tenant_isolation("tenant-alpha", "tenant-alpha")

    # Mismatched tenant raises HTTP 403 Forbidden
    with pytest.raises(HTTPException) as exc:
        verify_tenant_isolation("tenant-alpha", "tenant-beta")
    assert exc.value.status_code == 403
    assert "BOLA Access Denied" in exc.value.detail


def test_jwt_token_generation_and_verification():
    """Verify JWT token encoding and claims verification."""
    token = create_jwt_token(tenant_id="tenant-security-test", subject="sec-analyst", role="admin")
    payload = decode_jwt_token(token)
    assert payload["tenant_id"] == "tenant-security-test"
    assert payload["sub"] == "sec-analyst"
    assert payload["role"] == "admin"


@pytest.mark.asyncio
async def test_fastapi_health_and_ready_probes():
    """Verify healthz and readyz probes return HTTP 200 OK using httpx."""
    from httpx import ASGITransport, AsyncClient
    from src.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Liveness probe
        res_health = await client.get("/healthz")
        assert res_health.status_code == 200
        assert res_health.json()["status"] == "ok"

        # Readiness probe
        res_ready = await client.get("/readyz")
        assert res_ready.status_code == 200
        assert res_ready.json()["status"] == "ready"
