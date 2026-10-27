"""
End-to-end API tests through the real ASGI stack (middleware, auth, rate limiting, headers).
"""

from __future__ import annotations
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from tests.conftest import TENANT_SECRETS


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def get_token(client, tenant="tenant-alpha", role="analyst") -> str:
    res = await client.post("/api/v1/auth/token", json={"tenant_id": tenant, "client_secret": TENANT_SECRETS[tenant], "role": role})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_fastapi_health_and_ready_probes(client):
    res_health = await client.get("/healthz")
    assert res_health.status_code == 200 and res_health.json()["status"] == "ok"
    res_ready = await client.get("/readyz")
    assert res_ready.status_code == 200 and res_ready.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_security_headers_present(client):
    res = await client.get("/healthz")
    h = res.headers
    assert h["content-security-policy"].startswith("default-src 'none'")
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert "max-age=" in h["strict-transport-security"]
    token_res = await client.post("/api/v1/auth/token", json={"tenant_id": "tenant-alpha", "client_secret": "wrong-secret-x"})
    assert token_res.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_token_requires_valid_client_secret(client):
    bad = await client.post("/api/v1/auth/token", json={"tenant_id": "tenant-alpha", "client_secret": "guess-guess"})
    assert bad.status_code == 401
    unknown = await client.post("/api/v1/auth/token", json={"tenant_id": "tenant-ghost", "client_secret": "guess-guess"})
    assert unknown.status_code == 401
    assert bad.json() == unknown.json()  # no tenant enumeration oracle
    admin = await client.post("/api/v1/auth/token", json={"tenant_id": "tenant-alpha", "client_secret": TENANT_SECRETS["tenant-alpha"], "role": "admin"})
    assert admin.status_code == 422


@pytest.mark.asyncio
async def test_protected_routes_require_bearer_token(client):
    for path in ("/api/v1/mta/compute", "/api/v1/geo/audit", "/api/v1/geo/jsonld"):
        res = await client.post(path, json={"tenant_id": "tenant-alpha"})
        assert res.status_code == 401
    res = await client.post("/api/v1/mta/compute", json={"tenant_id": "tenant-alpha"}, headers=bearer("garbage"))
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_bola_cross_tenant_request_is_forbidden(client):
    beta_token = await get_token(client, "tenant-beta")
    res = await client.post("/api/v1/mta/compute", json={"tenant_id": "tenant-alpha"}, headers=bearer(beta_token))
    assert res.status_code == 403
    res = await client.post("/api/v1/geo/audit", json={"tenant_id": "tenant-alpha", "target_brand": "X"}, headers=bearer(beta_token))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_mta_compute_end_to_end_on_seeded_corpus(client):
    token = await get_token(client)
    res = await client.post(
        "/api/v1/mta/compute",
        json={"tenant_id": "tenant-alpha", "markov_order": 2, "bootstrap_samples": 30, "persist": False},
        headers=bearer(token),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_journeys"] == 500
    assert body["markov_order"] == 2
    assert abs(sum(c["markov_share"] for c in body["channels"]) - 1.0) < 1e-3
    assert abs(sum(c["shapley_share"] for c in body["channels"]) - 1.0) < 1e-3
    assert body["baseline_conversion_rate"] == pytest.approx(body["empirical_conversion_rate"], abs=1e-4)


@pytest.mark.asyncio
async def test_viewer_role_cannot_persist(client):
    token = await get_token(client, role="viewer")
    res = await client.post("/api/v1/mta/compute", json={"tenant_id": "tenant-alpha", "persist": True}, headers=bearer(token))
    assert res.status_code == 403
    res = await client.post("/api/v1/mta/compute", json={"tenant_id": "tenant-alpha", "persist": False}, headers=bearer(token))
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_geo_audit_and_jsonld_endpoints(client):
    token = await get_token(client)
    audit = await client.post("/api/v1/geo/audit", json={"tenant_id": "tenant-alpha", "target_brand": "TesserPulse"}, headers=bearer(token))
    assert audit.status_code == 200
    assert abs(sum(m["share_of_voice"] for m in audit.json()["competitor_metrics"]) - 1.0) < 1e-3

    ssrf = await client.post("/api/v1/geo/audit", json={"tenant_id": "tenant-alpha", "target_brand": "X", "brand_url": "https://169.254.169.254/latest"}, headers=bearer(token))
    assert ssrf.status_code == 400

    ld = await client.post("/api/v1/geo/jsonld", json={
        "tenant_id": "tenant-alpha", "schema_type": "FAQPage",
        "faq_data": {"url": "https://93.184.215.14/faq", "faqs": [{"question": "Is </script> safe?", "answer": "Yes, it is escaped."}]},
    }, headers=bearer(token))
    assert ld.status_code == 200
    assert "</script" not in ld.json()["json_ld_string"]


@pytest.mark.asyncio
async def test_mass_assignment_rejected_over_http(client):
    token = await get_token(client)
    res = await client.post("/api/v1/mta/compute", json={"tenant_id": "tenant-alpha", "role": "admin"}, headers=bearer(token))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_auth_endpoint_is_rate_limited(client):
    codes = []
    for _ in range(14):
        res = await client.post("/api/v1/auth/token", json={"tenant_id": "tenant-alpha", "client_secret": "brute-force-guess"})
        codes.append(res.status_code)
    assert 429 in codes, codes
    assert codes[:10] == [401] * 10


@pytest.mark.asyncio
async def test_oversized_body_rejected_with_413(client):
    huge = "x" * 1_100_000
    res = await client.post("/api/v1/auth/token", content=huge, headers={"content-type": "application/json"})
    assert res.status_code == 413


@pytest.mark.asyncio
async def test_cors_allows_only_listed_origins(client):
    evil = await client.options("/api/v1/mta/compute", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert evil.headers.get("access-control-allow-origin") is None
    good = await client.options("/api/v1/mta/compute", headers={"Origin": "http://127.0.0.1:8501", "Access-Control-Request-Method": "POST"})
    assert good.headers.get("access-control-allow-origin") == "http://127.0.0.1:8501"
    assert good.headers.get("access-control-allow-credentials") is None


@pytest.mark.asyncio
async def test_errors_do_not_leak_internals(client):
    res = await client.get("/api/v1/does-not-exist")
    assert res.status_code == 404
    assert "Traceback" not in res.text
