"""
Pydantic v2 schemas with strict validation and extra='forbid' to prevent mass assignment.
Every collection and string is bounded to mitigate resource-exhaustion (OWASP API4:2023).
"""

from __future__ import annotations
from typing import Annotated, Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from src.config import settings


class StrictBaseModel(BaseModel):
    """Base model forbidding unexpected extra fields (OWASP Mass Assignment Mitigation)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


TenantId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")]
ChannelName = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ShortLabel = Annotated[str, StringConstraints(min_length=1, max_length=64)]
PromptText = Annotated[str, StringConstraints(min_length=3, max_length=500)]
UrlString = Annotated[str, StringConstraints(min_length=8, max_length=2048)]


# --- Authentication Schemas ---

class TokenRequest(StrictBaseModel):
    tenant_id: TenantId = Field(..., description="Tenant Identifier")
    client_secret: SecretStr = Field(..., min_length=8, max_length=256, description="Tenant client secret")
    subject: str = Field(default="marketing-analyst", min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    role: Literal["analyst", "viewer"] = Field(default="analyst")


class TokenResponse(StrictBaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in_minutes: int
    tenant_id: str


# --- GEO Audit Schemas ---

class GeoAuditPromptItem(StrictBaseModel):
    prompt_text: PromptText
    category: str = Field(..., min_length=2, max_length=64)


class GeoAuditRequest(StrictBaseModel):
    tenant_id: TenantId
    target_brand: str = Field(..., min_length=1, max_length=64)
    category: str = Field(default="AI Developer Tools", min_length=2, max_length=64)
    competitors: list[ShortLabel] = Field(default_factory=lambda: ["CompetitorX", "CompetitorY"], max_length=10)
    engines: list[ShortLabel] = Field(
        default_factory=lambda: ["ChatGPT", "Perplexity", "Claude", "Gemini"], min_length=1, max_length=10
    )
    custom_prompts: Optional[list[PromptText]] = Field(default=None, min_length=1, max_length=50)
    brand_url: Optional[UrlString] = Field(default=None, description="Optional brand website to validate against SSRF")
    persist: bool = Field(default=False, description="Persist simulated citations to the database")


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
    normalized_som: float = Field(description="SoM / SoM_max, bounded to [0, 1]")
    share_of_voice: float = Field(description="SoM_b / sum of SoM over all audited brands")
    citation_rate: float
    citation_rate_ci_low: float
    citation_rate_ci_high: float
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
    target_brand_normalized_som: float
    target_brand_share_of_voice: float
    market_concentration_hhi: float = Field(description="Herfindahl-Hirschman Index of share of voice (0..10000)")
    engine_entropy_bits: float = Field(description="Shannon entropy of target citations across engines")
    competitor_metrics: list[BrandSoMMetric]
    engine_breakdown: dict[str, float]
    engine_som: dict[str, float]
    provenance_distribution: dict[str, int]
    citations_sample: list[CitationRecord]


# --- MTA Attribution Schemas ---

class CustomerJourneyInput(StrictBaseModel):
    session_hash: str = Field(..., min_length=4, max_length=64)
    path: list[ChannelName] = Field(..., min_length=1, max_length=settings.MAX_PATH_LENGTH)
    converted: bool
    conversion_value_usd: float = Field(default=0.0, ge=0.0, le=1e9)


class ChannelSpendInput(StrictBaseModel):
    channel_name: ChannelName
    monthly_spend_usd: float = Field(..., ge=0.0, le=1e10)


class MtaComputeRequest(StrictBaseModel):
    tenant_id: TenantId
    journeys: Optional[list[CustomerJourneyInput]] = Field(
        default=None,
        max_length=settings.MAX_JOURNEYS_PER_REQUEST,
        description="Optional list of in-memory journeys. If null, loads from DB."
    )
    channel_spends: Optional[list[ChannelSpendInput]] = Field(
        default=None,
        max_length=settings.MAX_DISTINCT_CHANNELS,
        description="Optional list of monthly spends. If null, loads from DB."
    )
    total_budget_usd: Optional[float] = Field(default=None, gt=0.0, le=1e10)
    markov_order: int = Field(default=1, ge=1, le=settings.MAX_MARKOV_ORDER)
    time_decay_half_life: float = Field(default=2.0, gt=0.0, le=50.0, description="Half-life in touchpoints")
    bootstrap_samples: int = Field(default=0, ge=0, le=settings.MAX_BOOTSTRAP_SAMPLES)
    response_elasticity: float = Field(
        default=0.5, gt=0.05, lt=0.95, description="beta in revenue_k(x) = a_k * x^beta (diminishing returns)"
    )
    reallocation_trust_region: float = Field(
        default=0.5, ge=0.0, le=3.0, description="Max relative change per channel: x_k in [(1-d)s_k, (1+d)s_k]"
    )
    persist: bool = Field(default=True, description="Persist results to the attribution_results table")


class ChannelAttributionShare(StrictBaseModel):
    channel_name: str
    last_click_share: float
    first_touch_share: float
    linear_share: float
    position_based_share: float
    time_decay_share: float
    markov_share: float
    markov_share_ci_low: Optional[float] = None
    markov_share_ci_high: Optional[float] = None
    shapley_share: float
    markov_removal_effect: float
    expected_visits_per_journey: float
    attributed_conversions_markov: float
    attributed_revenue_markov: float
    current_spend_usd: float
    cpa_markov: Optional[float] = None
    roas_last_click: float
    roas_markov: float
    roas_shapley: float
    marginal_roas_current: float
    marginal_roas_optimized: float
    recommended_budget_reallocation_usd: float
    marginal_roas_category: str  # Under-invested / Optimized / Over-invested


class ModelDivergence(StrictBaseModel):
    model: str
    jensen_shannon_bits: float
    total_variation: float


class MtaComputeResponse(StrictBaseModel):
    tenant_id: str
    total_journeys: int
    total_conversions: int
    baseline_conversion_rate: float
    empirical_conversion_rate: float
    expected_path_length: float
    markov_order: int
    total_revenue_usd: float
    total_current_spend_usd: float
    total_spend_usd: float
    blended_roas: float
    projected_revenue_current_usd: float
    projected_revenue_optimized_usd: float
    projected_revenue_lift_pct: float
    equilibrium_marginal_roas: float
    model_divergence_vs_markov: list[ModelDivergence]
    channels: list[ChannelAttributionShare]
    transition_matrix: dict[str, dict[str, float]]
    model_reallocation_insight: str


# --- JSON-LD Schema Schemas ---

class ProductSchemaInput(StrictBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    brand_name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=5, max_length=2000)
    sku: str = Field(..., min_length=1, max_length=64)
    price: float = Field(..., gt=0.0, le=1e9)
    price_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    url: UrlString = Field(..., description="Brand or product URL (validated for SSRF/safe scheme)")
    rating_value: Optional[float] = Field(default=4.8, ge=1.0, le=5.0)
    review_count: Optional[int] = Field(default=120, ge=1, le=10_000_000)


class FAQItemInput(StrictBaseModel):
    question: str = Field(..., min_length=5, max_length=300)
    answer: str = Field(..., min_length=5, max_length=2000)


class FAQPageSchemaInput(StrictBaseModel):
    url: UrlString
    faqs: list[FAQItemInput] = Field(..., min_length=1, max_length=20)


class OrganizationSchemaInput(StrictBaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: UrlString
    logo_url: Optional[UrlString] = None
    description: str = Field(..., min_length=5, max_length=1000)
    same_as_links: list[UrlString] = Field(default_factory=list, max_length=10)


class JsonLdRequest(StrictBaseModel):
    tenant_id: TenantId
    schema_type: Literal["Product", "FAQPage", "Organization"]
    product_data: Optional[ProductSchemaInput] = None
    faq_data: Optional[FAQPageSchemaInput] = None
    org_data: Optional[OrganizationSchemaInput] = None


class JsonLdResponse(StrictBaseModel):
    schema_type: str
    json_ld_string: str
    script_tag: str
    parsed_json: dict[str, Any]
    validation_status: str
    geo_optimization_tips: list[str]
