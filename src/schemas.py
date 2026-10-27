"""
Pydantic v2 schemas with strict validation and extra='forbid' to prevent mass assignment.
"""

from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """Base model forbidding unexpected extra fields (OWASP Mass Assignment Mitigation)."""
    model_config = ConfigDict(extra="forbid")


# --- Authentication Schemas ---

class TokenRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64, description="Tenant Identifier")
    subject: str = Field(default="marketing-analyst", min_length=2, max_length=64)
    role: str = Field(default="analyst", max_length=32)


class TokenResponse(StrictBaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in_minutes: int
    tenant_id: str


# --- GEO Audit Schemas ---

class GeoAuditPromptItem(StrictBaseModel):
    prompt_text: str = Field(..., min_length=3, max_length=500)
    category: str = Field(..., min_length=2, max_length=64)


class GeoAuditRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64)
    target_brand: str = Field(..., min_length=1, max_length=64)
    category: str = Field(default="AI Developer Tools", min_length=2, max_length=64)
    competitors: list[str] = Field(default_factory=lambda: ["CompetitorX", "CompetitorY"])
    engines: list[str] = Field(default_factory=lambda: ["ChatGPT", "Perplexity", "Claude", "Gemini"])
    custom_prompts: Optional[list[str]] = Field(default=None, max_length=50)
    brand_url: Optional[str] = Field(default=None, description="Optional brand website to validate against SSRF")


class CitationRecord(StrictBaseModel):
    engine_name: str
    prompt_text: str
    brand_cited: str
    is_target_brand: bool
    citation_rank: int
    sentiment_score: float
    sentiment_label: str
    source_provenance: str
    snippet: str


class BrandSoMMetric(StrictBaseModel):
    brand_name: str
    is_target: bool
    citation_count: int
    share_of_model_score: float
    average_rank: float
    average_sentiment: float
    sentiment_breakdown: dict[str, int]


class GeoAuditResponse(StrictBaseModel):
    tenant_id: str
    category: str
    target_brand: str
    total_prompts_audited: int
    total_citations: int
    target_brand_som_score: float
    competitor_metrics: list[BrandSoMMetric]
    engine_breakdown: dict[str, float]
    provenance_distribution: dict[str, int]
    citations_sample: list[CitationRecord]


# --- MTA Attribution Schemas ---

class CustomerJourneyInput(StrictBaseModel):
    session_hash: str = Field(..., min_length=4, max_length=64)
    path: list[str] = Field(..., min_length=1, max_length=25)
    converted: bool
    conversion_value_usd: float = Field(default=0.0, ge=0.0)


class ChannelSpendInput(StrictBaseModel):
    channel_name: str = Field(..., min_length=1, max_length=64)
    monthly_spend_usd: float = Field(..., ge=0.0)


class MtaComputeRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64)
    journeys: Optional[list[CustomerJourneyInput]] = Field(
        default=None,
        description="Optional list of in-memory journeys. If null, loads from DB."
    )
    channel_spends: Optional[list[ChannelSpendInput]] = Field(
        default=None,
        description="Optional list of monthly spends. If null, loads from DB."
    )
    total_budget_usd: Optional[float] = Field(default=None, ge=0.0)


class ChannelAttributionShare(StrictBaseModel):
    channel_name: str
    last_click_share: float
    first_touch_share: float
    linear_share: float
    markov_share: float
    shapley_share: float
    markov_removal_effect: float
    attributed_conversions_markov: float
    attributed_revenue_markov: float
    current_spend_usd: float
    roas_last_click: float
    roas_markov: float
    roas_shapley: float
    recommended_budget_reallocation_usd: float
    marginal_roas_category: str  # Under-invested / Optimized / Over-invested


class MtaComputeResponse(StrictBaseModel):
    tenant_id: str
    total_journeys: int
    total_conversions: int
    baseline_conversion_rate: float
    total_revenue_usd: float
    total_spend_usd: float
    blended_roas: float
    channels: list[ChannelAttributionShare]
    transition_matrix: dict[str, dict[str, float]]
    model_reallocation_insight: str


# --- JSON-LD Schema Schemas ---

class ProductSchemaInput(StrictBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    brand_name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=5, max_length=2000)
    sku: str = Field(..., min_length=1, max_length=64)
    price: float = Field(..., gt=0.0)
    price_currency: str = Field(default="USD", max_length=3)
    url: str = Field(..., description="Brand or product URL (validated for SSRF/safe scheme)")
    rating_value: Optional[float] = Field(default=4.8, ge=1.0, le=5.0)
    review_count: Optional[int] = Field(default=120, ge=1)


class FAQItemInput(StrictBaseModel):
    question: str = Field(..., min_length=5, max_length=300)
    answer: str = Field(..., min_length=5, max_length=2000)


class FAQPageSchemaInput(StrictBaseModel):
    url: str
    faqs: list[FAQItemInput] = Field(..., min_length=1, max_length=20)


class OrganizationSchemaInput(StrictBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: str
    logo_url: Optional[str] = None
    description: str = Field(..., min_length=5, max_length=1000)
    same_as_links: list[str] = Field(default_factory=list, max_length=10)


class JsonLdRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64)
    schema_type: str = Field(..., description="'Product' | 'FAQPage' | 'Organization'")
    product_data: Optional[ProductSchemaInput] = None
    faq_data: Optional[FAQPageSchemaInput] = None
    org_data: Optional[OrganizationSchemaInput] = None


class JsonLdResponse(StrictBaseModel):
    schema_type: str
    json_ld_string: str
    parsed_json: dict[str, Any]
    validation_status: str
    geo_optimization_tips: list[str]
