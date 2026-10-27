"""
Security and threat mitigation test suite:
- SSRF prevention (cloud metadata, loopback, RFC1918, CGNAT, IPv6 tunnelling, DNS answers, userinfo, ports)
- XSS / HTML injection sanitization
- Pydantic extra='forbid' mass assignment mitigation and input bounds
- BOLA tenant isolation, JWT hardening (alg=none, tampering, expiry, audience, roles)
- Configuration hardening (production refuses weak secrets)
"""

from __future__ import annotations
import socket
from datetime import timedelta

import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.config import Settings, settings
from src.schemas import (
    CustomerJourneyInput,
    GeoAuditRequest,
    JsonLdRequest,
    MtaComputeRequest,
    TokenRequest,
)
from src.security import (
    authenticate_tenant_client,
    create_jwt_token,
    decode_jwt_token,
    resolve_and_validate_url,
    safe_json_for_script,
    sanitize_text,
    validate_and_sanitize_url,
    verify_tenant_isolation,
)


def fake_dns(monkeypatch, *ips):
    """Make getaddrinfo return the given IPs so tests are deterministic and offline."""
    def _getaddrinfo(host, port, *args, **kwargs):
        family = lambda ip: socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family(ip), socket.SOCK_STREAM, 6, "", (ip, port or 443)) for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)


def assert_blocked(url: str, fragment: str | None = None):
    with pytest.raises(HTTPException) as exc:
        validate_and_sanitize_url(url)
    assert exc.value.status_code == 400
    if fragment:
        assert fragment in exc.value.detail
    return exc.value


# ----------------------------------------------------------------------------
# SSRF (original contract tests kept)
# ----------------------------------------------------------------------------

def test_ssrf_blocks_cloud_metadata_ip():
    """CONTRACT: http(s)://169.254.169.254 is blocked with HTTP 400."""
    assert_blocked("http://169.254.169.254/latest/meta-data/")
    err = assert_blocked("https://169.254.169.254/computeMetadata/v1/")
    assert "SSRF violation" in err.detail


def test_ssrf_blocks_loopback_and_localhost():
    """CONTRACT: http(s)://127.0.0.1 and localhost are blocked with HTTP 400."""
    assert_blocked("http://127.0.0.1:8000/admin")
    assert_blocked("https://127.0.0.1/admin")
    assert_blocked("https://localhost:8501")
    assert_blocked("https://localhost/")


def test_ssrf_blocks_private_networks():
    for url in ("https://10.0.0.1/internal-api", "https://192.168.1.1/router", "https://172.16.0.5/secrets", "https://172.31.255.255/"):
        assert_blocked(url, "SSRF violation")


def test_ssrf_blocks_insecure_schemes():
    for url in ("http://example.com/login", "ftp://internal.backup/db.tar.gz", "file:///etc/passwd",
                "gopher://evil.server", "javascript:alert(1)", "data:text/html,hi", "//example.com/x"):
        assert_blocked(url, "Insecure URL scheme")


def test_ssrf_allows_legitimate_public_https(monkeypatch):
    fake_dns(monkeypatch, "140.82.112.3")
    sanitized = validate_and_sanitize_url("https://github.com/samuelhtampubolon")
    assert sanitized == "https://github.com/samuelhtampubolon"


# ----------------------------------------------------------------------------
# SSRF bypass techniques
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://[::1]/",
    "https://[::ffff:127.0.0.1]/",          # IPv4-mapped IPv6 loopback
    "https://[::ffff:169.254.169.254]/",    # IPv4-mapped metadata
    "https://[64:ff9b::a9fe:a9fe]/",        # NAT64-embedded 169.254.169.254
    "https://[2002:a9fe:a9fe::]/",          # 6to4-embedded 169.254.169.254
    "https://[fd00:ec2::254]/",             # AWS IMDS IPv6
    "https://[fe80::1]/",
    "https://[fc00::1]/",
    "https://100.64.0.1/",                  # CGNAT
    "https://0.0.0.0/",
    "https://198.18.0.1/",                  # benchmarking range
    "https://224.0.0.1/",
    "https://255.255.255.255/",
])
def test_ssrf_blocks_ip_literal_bypasses(url):
    assert_blocked(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fd12::1", "100.100.100.200"])
def test_ssrf_blocks_hostnames_resolving_to_internal_ips(monkeypatch, ip):
    """DNS answers are checked, so attacker-controlled domains pointing inward are rejected."""
    fake_dns(monkeypatch, ip)
    assert_blocked("https://attacker-controlled.example/", "SSRF violation")


def test_ssrf_blocks_mixed_public_and_private_dns_answers(monkeypatch):
    """One internal A record among public ones is enough to block (round-robin rebinding)."""
    fake_dns(monkeypatch, "93.184.215.14", "10.0.0.7")
    assert_blocked("https://rebind.example/")


def test_ssrf_returns_resolved_ips_for_dns_pinning(monkeypatch):
    fake_dns(monkeypatch, "93.184.215.14", "93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c")
    v = resolve_and_validate_url("https://Example.COM./path?q=1")
    assert v.hostname == "example.com"
    assert v.resolved_ips == ("93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c")
    assert v.url == "https://example.com/path?q=1"


@pytest.mark.parametrize("url", [
    "https://metadata.google.internal/computeMetadata/v1/",
    "https://service.internal/",
    "https://printer.local/",
    "https://app.localhost/",
])
def test_ssrf_blocks_internal_hostnames_without_dns(url):
    assert_blocked(url, "internal service")


def test_ssrf_blocks_decimal_and_hex_encoded_loopback(monkeypatch):
    """Exotic IPv4 notations resolve to 127.0.0.1 through the system resolver and are rejected."""
    fake_dns(monkeypatch, "127.0.0.1")
    assert_blocked("https://2130706433/")
    assert_blocked("https://0x7f000001/")


def test_ssrf_rejects_userinfo_ports_and_injection_characters(monkeypatch):
    fake_dns(monkeypatch, "93.184.215.14")
    assert_blocked("https://user:pass@example.com/", "credentials")
    assert_blocked("https://example.com@127.0.0.1/")
    assert_blocked("https://example.com:8443/", "Port 8443")
    assert_blocked("https://example.com:99999/")
    assert_blocked('https://example.com/"><script>alert(1)</script>', "forbidden characters")
    assert_blocked("https://example.com/a b")
    assert_blocked("https://example.com/\\..\\")
    assert_blocked("https://exa\tmple.com/")
    assert_blocked("https://example.com/" + "a" * 2100, "maximum length")
    assert validate_and_sanitize_url("https://example.com:443/ok") == "https://example.com/ok"


def test_ssrf_unresolvable_host_is_rejected(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert_blocked("https://does-not-exist.invalid/", "Unable to resolve")


# ----------------------------------------------------------------------------
# XSS sanitization
# ----------------------------------------------------------------------------

def test_xss_sanitization_in_jsonld():
    sanitized = sanitize_text("<script>alert('xss')</script>Apex Developer Suite")
    assert "<script>" not in sanitized
    assert "alert('xss')" not in sanitized
    assert sanitized == "Apex Developer Suite"


@pytest.mark.parametrize("payload, expected", [
    ('<img src=x onerror="alert(1)">Brand', "Brand"),
    ("<svg onload=alert(1)><circle/></svg>Brand", "Brand"),
    ("<iframe src=javascript:alert(1)></iframe>Brand", "Brand"),
    ("&lt;script&gt;alert(1)&lt;/script&gt;Brand", "Brand"),
    ("<scr<script>ipt>alert(1)</script>Brand", "scrBrand"),  # nested-tag trick collapses to inert text
    ("Brand\u202eevil", "Brandevil"),         # bidi override (Trojan Source)
    ("Bra\u200bnd\x00", "Brand"),             # zero-width + NUL
    ("Tom & Jerry \"quoted\"", 'Tom & Jerry "quoted"'),
])
def test_sanitize_text_neutralizes_payloads(payload, expected):
    out = sanitize_text(payload)
    assert out == expected
    assert "<" not in out and ">" not in out


def test_safe_json_for_script_escapes_html_significant_characters():
    out = safe_json_for_script({"x": "</script><!--&\u2028\u2029"}, indent=None)
    assert out == '{"x": "\\u003c/script\\u003e\\u003c!--\\u0026\\u2028\\u2029"}'
    import json
    assert json.loads(out) == {"x": "</script><!--&\u2028\u2029"}


# ----------------------------------------------------------------------------
# Mass assignment & input bounds
# ----------------------------------------------------------------------------

def test_pydantic_extra_forbid_mitigates_mass_assignment():
    model = GeoAuditRequest(tenant_id="tenant-test", target_brand="BrandAlpha")
    assert model.tenant_id == "tenant-test"
    with pytest.raises(ValidationError) as exc:
        GeoAuditRequest(tenant_id="tenant-test", target_brand="BrandAlpha", is_admin=True, internal_bypass_security=True)
    assert "extra_forbidden" in str(exc.value)


@pytest.mark.parametrize("model, payload", [
    (TokenRequest, {"tenant_id": "tenant-alpha", "client_secret": "x" * 12, "is_admin": True}),
    (MtaComputeRequest, {"tenant_id": "tenant-alpha", "debug": True}),
    (JsonLdRequest, {"tenant_id": "tenant-alpha", "schema_type": "Product", "raw_html": "<b>"}),
    (CustomerJourneyInput, {"session_hash": "abcd", "path": ["A"], "converted": True, "tenant_id": "x"}),
])
def test_every_request_schema_forbids_extra_fields(model, payload):
    with pytest.raises(ValidationError):
        model(**payload)


@pytest.mark.parametrize("tenant", ["../etc", "Tenant-Alpha", "a", "tenant alpha", "t" * 65, "tenant;drop"])
def test_tenant_id_format_is_enforced(tenant):
    with pytest.raises(ValidationError):
        MtaComputeRequest(tenant_id=tenant)


def test_resource_bounds_cap_combinatorial_inputs():
    with pytest.raises(ValidationError):
        CustomerJourneyInput(session_hash="abcd", path=["A"] * (settings.MAX_PATH_LENGTH + 1), converted=True)
    with pytest.raises(ValidationError):
        MtaComputeRequest(tenant_id="tenant-alpha", markov_order=settings.MAX_MARKOV_ORDER + 1)
    with pytest.raises(ValidationError):
        MtaComputeRequest(tenant_id="tenant-alpha", bootstrap_samples=settings.MAX_BOOTSTRAP_SAMPLES + 1)
    with pytest.raises(ValidationError):
        GeoAuditRequest(tenant_id="tenant-alpha", target_brand="x", competitors=["c"] * 11)
    with pytest.raises(ValidationError):
        JsonLdRequest(tenant_id="tenant-alpha", schema_type="Script")
    with pytest.raises(ValidationError):
        TokenRequest(tenant_id="tenant-alpha", client_secret="x" * 12, role="admin")


# ----------------------------------------------------------------------------
# BOLA, authentication & JWT
# ----------------------------------------------------------------------------

def test_bola_tenant_isolation_rejection():
    verify_tenant_isolation("tenant-alpha", "tenant-alpha")
    with pytest.raises(HTTPException) as exc:
        verify_tenant_isolation("tenant-alpha", "tenant-beta")
    assert exc.value.status_code == 403
    assert "BOLA Access Denied" in exc.value.detail
    assert "tenant-beta" not in exc.value.detail  # no reflection of the probed tenant


def test_jwt_token_generation_and_verification():
    token = create_jwt_token(tenant_id="tenant-security-test", subject="sec-analyst", role="analyst")
    payload = decode_jwt_token(token)
    assert payload["tenant_id"] == "tenant-security-test"
    assert payload["sub"] == "sec-analyst"
    assert payload["role"] == "analyst"
    assert payload["iss"] == settings.JWT_ISSUER and payload["aud"] == settings.JWT_AUDIENCE
    assert len(payload["jti"]) >= 16


def test_privileged_roles_cannot_be_minted():
    with pytest.raises(ValueError):
        create_jwt_token(tenant_id="tenant-alpha", role="admin")


def forge(claims: dict, key: str | None = None, alg: str = "HS256") -> str:
    base = {"iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE, "sub": "x", "tenant_id": "tenant-alpha",
            "role": "analyst", "iat": 1, "nbf": 1, "exp": 4102444800}
    base.update(claims)
    return jwt.encode(base, key if key is not None else settings.JWT_SECRET, algorithm=alg)


def test_jwt_rejects_alg_none_tampering_and_wrong_key():
    unsigned = jwt.encode({"tenant_id": "tenant-beta", "sub": "x"}, key=None, algorithm="none")
    for bad in (unsigned, forge({}, key="attacker-key-" + "y" * 32), forge({}) + "x", "not.a.jwt"):
        with pytest.raises(HTTPException) as exc:
            decode_jwt_token(bad)
        assert exc.value.status_code == 401


def test_jwt_rejects_expired_wrong_audience_issuer_and_missing_claims():
    expired = create_jwt_token("tenant-alpha", expires_delta=timedelta(minutes=-5))
    for bad in (expired, forge({"aud": "other-api"}), forge({"iss": "evil"}), forge({"tenant_id": None})):
        with pytest.raises(HTTPException) as exc:
            decode_jwt_token(bad)
        assert exc.value.status_code == 401
    no_tenant = jwt.encode({"iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE, "sub": "x", "role": "analyst",
                            "iat": 1, "nbf": 1, "exp": 4102444800}, settings.JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException):
        decode_jwt_token(no_tenant)


def test_jwt_with_escalated_role_claim_is_rejected():
    with pytest.raises(HTTPException) as exc:
        decode_jwt_token(forge({"role": "admin"}))
    assert exc.value.status_code == 403


def test_tenant_client_authentication():
    assert authenticate_tenant_client("tenant-alpha", "alpha-test-secret-123")
    assert not authenticate_tenant_client("tenant-alpha", "beta-test-secret-456")
    assert not authenticate_tenant_client("tenant-ghost", "alpha-test-secret-123")


# ----------------------------------------------------------------------------
# Configuration hardening
# ----------------------------------------------------------------------------

def test_production_refuses_weak_or_missing_secrets(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "short")
    monkeypatch.setenv("TENANT_CLIENT_SECRETS", "tenant-alpha:" + "s" * 32)
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("JWT_SECRET", "k" * 48)
    monkeypatch.setenv("TENANT_CLIENT_SECRETS", "")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("TENANT_CLIENT_SECRETS", "tenant-alpha:weak")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("TENANT_CLIENT_SECRETS", "tenant-alpha:" + "s" * 32)
    assert Settings().is_production


def test_development_generates_ephemeral_secret_when_unset(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    a, b = Settings(), Settings()
    assert len(a.JWT_SECRET) >= 32 and a.JWT_SECRET != b.JWT_SECRET


def test_no_secrets_committed_in_example_files():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    env_example = (root / ".env.example").read_text()
    compose = (root / "docker-compose.yml").read_text()
    assert "super-secret" not in env_example and "production-secret" not in compose
    assert "JWT_SECRET=${JWT_SECRET" in compose.replace(" ", "").replace('"', "") or "JWT_SECRET:${JWT_SECRET" in compose.replace(" ", "").replace('"', "")


def test_sources_contain_no_trojan_source_characters():
    """Bidi overrides / zero-width characters must only appear as escapes, never literally (CVE-2021-42574)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    invisible = set(range(0x200B, 0x2010)) | set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x2060, 0xFEFF}
    patterns = ("src/**/*.py", "scripts/*", "tests/*.py", "web/*", "*.py", "*.html", "docs/*.html")
    offenders = []
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and path.suffix in {".py", ".js", ".html", ".css"}:
                text = path.read_text(encoding="utf-8")
                if any(ord(ch) in invisible for ch in text):
                    offenders.append(str(path.relative_to(root)))
    assert offenders == []
