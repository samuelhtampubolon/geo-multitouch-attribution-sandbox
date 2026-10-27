"""
SQLAlchemy models for GEO audits, customer journeys, channel spend, and attribution.
"""

from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from src.database import Base


class GeoPromptAudit(Base):
    """Stores simulated or live Generative Engine prompt citation audits."""
    __tablename__ = "geo_prompt_audits"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    category = Column(String(64), nullable=False, index=True)
    prompt_text = Column(Text, nullable=False)
    engine_name = Column(String(64), nullable=False, index=True)
    brand_cited = Column(String(64), nullable=False, index=True)
    is_target_brand = Column(Boolean, default=False, nullable=False)
    citation_rank = Column(Integer, default=1, nullable=False)
    sentiment_score = Column(Float, default=0.0, nullable=False)
    source_provenance = Column(String(128), default="Authoritative Web", nullable=False)
    response_snippet = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class CustomerJourney(Base):
    """Customer multi-touch conversion path."""
    __tablename__ = "customer_journeys"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    session_hash = Column(String(64), nullable=False, index=True)
    path_sequence_json = Column(Text, nullable=False)  # JSON array of channel names
    converted = Column(Boolean, default=False, nullable=False)
    conversion_value_usd = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ChannelSpend(Base):
    """Monthly marketing spend per channel."""
    __tablename__ = "channel_spend"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    channel_name = Column(String(64), nullable=False, index=True)
    monthly_spend_usd = Column(Float, default=0.0, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class AttributionResult(Base):
    """Historical or computed multi-touch attribution results and ROAS."""
    __tablename__ = "attribution_results"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    channel_name = Column(String(64), nullable=False)
    last_click_share = Column(Float, default=0.0, nullable=False)
    first_touch_share = Column(Float, default=0.0, nullable=False)
    linear_share = Column(Float, default=0.0, nullable=False)
    markov_share = Column(Float, default=0.0, nullable=False)
    shapley_share = Column(Float, default=0.0, nullable=False)
    attributed_revenue_markov = Column(Float, default=0.0, nullable=False)
    attributed_revenue_shapley = Column(Float, default=0.0, nullable=False)
    roas_markov = Column(Float, default=0.0, nullable=False)
    roas_shapley = Column(Float, default=0.0, nullable=False)
    recommended_spend_usd = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
