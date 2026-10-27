"""
Tests for the GEO Share-of-Model auditor and the Schema.org JSON-LD generator.
"""

from __future__ import annotations
import json
import math

import pytest

from src.schemas import (
    FAQItemInput,
    FAQPageSchemaInput,
    GeoAuditRequest,
    JsonLdRequest,
    OrganizationSchemaInput,
    ProductSchemaInput,
)
from src.services.geo_auditor import GeoAuditorService
from src.services.jsonld_generator import JsonLdGeneratorService


def audit(**overrides):
    params = dict(tenant_id="tenant-alpha", target_brand="TesserPulse", competitors=["VortexAI", "OmniAgent", "HyperDev"])
    params.update(overrides)
    return GeoAuditorService.run_deterministic_audit(GeoAuditRequest(**params))


def test_position_decay_is_strictly_decreasing():
    decays = [GeoAuditorService.position_decay(r) for r in range(1, 11)]
    assert all(a > b for a, b in zip(decays, decays[1:]))
    assert GeoAuditorService.position_decay(3) == pytest.approx(0.5)
    assert GeoAuditorService.position_decay(7) == pytest.approx(1 / 3)


def test_sentiment_multiplier_clamps_out_of_range_inputs():
    assert GeoAuditorService.sentiment_multiplier(5.0) == pytest.approx(1.5)
    assert GeoAuditorService.sentiment_multiplier(-9.0) == pytest.approx(0.5)


def test_som_upper_bound_and_normalization():
    perfect = [{"rank": 1, "sentiment_score": 1.0}] * 8
    som = GeoAuditorService.calculate_som_score(perfect, 8)
    assert som == pytest.approx(1.5)
    assert GeoAuditorService.normalized_som(som) == pytest.approx(1.0)
    assert GeoAuditorService.calculate_som_score([], 0) == 0.0


def test_audit_is_deterministic():
    a, b = audit(), audit()
    assert a.model_dump() == b.model_dump()


def test_audit_share_of_voice_and_metrics_are_consistent():
    res = audit()
    sov_total = sum(m.share_of_voice for m in res.competitor_metrics)
    assert sov_total == pytest.approx(1.0, abs=1e-3)
    hhi = 10000 * sum(m.share_of_voice ** 2 for m in res.competitor_metrics)
    assert res.market_concentration_hhi == pytest.approx(hhi, rel=1e-2)
    assert 0.0 <= res.engine_entropy_bits <= math.log2(4) + 1e-9
    assert sum(res.engine_breakdown.values()) == pytest.approx(1.0, abs=1e-2)
    for m in res.competitor_metrics:
        assert 0.0 <= m.normalized_som <= 1.0
        assert m.citation_rate_ci_low <= m.citation_rate <= m.citation_rate_ci_high
        assert m.citation_count == sum(m.sentiment_breakdown.values())
    target = next(m for m in res.competitor_metrics if m.is_target)
    assert target.share_of_model_score == res.target_brand_som_score
    # SoM per engine averages back to the overall SoM (same M per engine)
    assert sum(res.engine_som.values()) / len(res.engine_som) == pytest.approx(res.target_brand_som_score, abs=1e-3)


def test_audit_recomputes_som_from_raw_citations():
    """Independent recomputation of the governing equation from the returned sample is consistent."""
    res = audit(engines=["ChatGPT"], custom_prompts=["Which attribution sandbox is best?"])
    assert res.total_prompts_audited == 1
    expected = sum(
        GeoAuditorService.position_decay(c.citation_rank) * GeoAuditorService.sentiment_multiplier(c.sentiment_score)
        for c in res.citations_sample if c.is_target_brand
    )
    assert res.target_brand_som_score == pytest.approx(expected, abs=1e-4)


def test_audit_sanitizes_brand_and_competitor_names():
    res = audit(target_brand="<img src=x onerror=alert(1)>Tesser", competitors=["<script>x()</script>Vortex"])
    names = {m.brand_name for m in res.competitor_metrics}
    assert "Tesser" in names and "Vortex" in names
    assert not any("<" in n or "onerror" in n for n in names)


def test_audit_does_not_persist_by_default():
    from src.database import SessionLocal
    from src.models import GeoPromptAudit

    db = SessionLocal()
    try:
        before = db.query(GeoPromptAudit).count()
        GeoAuditorService.run_deterministic_audit(
            GeoAuditRequest(tenant_id="tenant-alpha", target_brand="Persisto"), db=db
        )
        assert db.query(GeoPromptAudit).count() == before
    finally:
        db.close()


def test_deterministic_seed_avoids_anagram_collisions():
    assert GeoAuditorService.deterministic_seed("t", "ab") != GeoAuditorService.deterministic_seed("t", "ba")


# --- JSON-LD -----------------------------------------------------------------

def product_request(**overrides):
    data = dict(
        name="TesserPulse Engine", brand_name="TesserPulse", description="Attribution platform for GEO teams.",
        sku="TP-1", price=499.0, url="https://93.184.215.14/products?a=1&b=2",
    )
    data.update(overrides)
    return JsonLdRequest(tenant_id="tenant-alpha", schema_type="Product", product_data=ProductSchemaInput(**data))


def test_jsonld_product_is_valid_json_and_preserves_query_strings():
    res = JsonLdGeneratorService.process_request(product_request())
    parsed = json.loads(res.json_ld_string)
    assert parsed == res.parsed_json
    assert parsed["@type"] == "Product"
    assert parsed["offers"]["url"] == "https://93.184.215.14/products?a=1&b=2"  # no &amp; corruption
    assert "\\u0026" in res.json_ld_string  # '&' escaped for script embedding
    assert res.script_tag.startswith('<script type="application/ld+json">')


def test_jsonld_script_breakout_is_impossible():
    payload = 'Evil</script><script>alert(document.cookie)</script>'
    res = JsonLdGeneratorService.process_request(product_request(description=payload + " text"))
    body = res.script_tag[len('<script type="application/ld+json">'):-len("</script>")]
    assert "</script" not in body.lower()
    assert "<" not in res.json_ld_string and ">" not in res.json_ld_string
    assert "alert" not in res.parsed_json["description"]


def test_jsonld_double_encoded_payload_is_neutralized():
    res = JsonLdGeneratorService.process_request(product_request(name="&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt;Safe"))
    assert res.parsed_json["name"] == "Safe"


def test_jsonld_plain_ampersand_text_is_not_double_escaped():
    res = JsonLdGeneratorService.process_request(product_request(brand_name="Tom & Jerry"))
    assert res.parsed_json["brand"]["name"] == "Tom & Jerry"


def test_jsonld_faq_and_organization():
    faq = JsonLdGeneratorService.process_request(JsonLdRequest(
        tenant_id="tenant-alpha", schema_type="FAQPage",
        faq_data=FAQPageSchemaInput(url="https://93.184.215.14/faq", faqs=[FAQItemInput(question="What is SoM?", answer="Share of Model.")]),
    ))
    assert faq.parsed_json["mainEntity"][0]["acceptedAnswer"]["text"] == "Share of Model."
    org = JsonLdGeneratorService.process_request(JsonLdRequest(
        tenant_id="tenant-alpha", schema_type="Organization",
        org_data=OrganizationSchemaInput(name="TesserPulse", url="https://93.184.215.14", description="GEO research lab",
                                         same_as_links=["https://140.82.112.3/samuelhtampubolon"]),
    ))
    assert org.parsed_json["sameAs"] == ["https://140.82.112.3/samuelhtampubolon"]


@pytest.mark.parametrize("bad_url", ["javascript:alert(1)", "data:text/html,<script>", "http://93.184.215.14", "https://169.254.169.254/"])
def test_jsonld_rejects_dangerous_urls(bad_url):
    from fastapi import HTTPException
    with pytest.raises((HTTPException, ValueError)):
        JsonLdGeneratorService.process_request(product_request(url=bad_url))


def test_jsonld_missing_payload_raises():
    with pytest.raises(ValueError):
        JsonLdGeneratorService.process_request(JsonLdRequest(tenant_id="tenant-alpha", schema_type="Product"))
