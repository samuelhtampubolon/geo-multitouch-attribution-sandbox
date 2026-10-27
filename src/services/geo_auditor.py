"""
Generative Engine Optimization (GEO) Auditor Service.
Audits brand citations, rank positions, sentiment polarity, and source provenance.
Calculates Share-of-Model (SoM) visibility indices.
"""

from __future__ import annotations
import hashlib
import math
import random
from typing import Any, Optional
from sqlalchemy.orm import Session

from src.models import GeoPromptAudit
from src.schemas import BrandSoMMetric, CitationRecord, GeoAuditRequest, GeoAuditResponse
from src.security import sanitize_text, validate_and_sanitize_url
from src.services.quant_metrics import herfindahl_index, shannon_entropy_bits, wilson_interval

# Standard engines & categories for deterministic local simulation
SIMULATED_ENGINES = ["ChatGPT", "Perplexity", "Claude", "Gemini"]

PROMPT_BANK: dict[str, list[str]] = {
    "AI Developer Tools": [
        "What are the best multi-agent AI coding orchestration tools?",
        "Compare top autonomous developer agents for enterprise Python and TypeScript.",
        "Which AI code review tool has the highest automated precision?",
        "Top recommended LLM evaluation and observability frameworks for production.",
        "Best developer tools for automated unit test generation and CI/CD integration.",
        "Which AI assistant offers the best repository context understanding and code search?",
        "Recommended tools for building Graph-based multi-agent workflows.",
        "Top autonomous coding assistants that comply with enterprise AppSec rules.",
        "Best tools for generative engine optimization and AI search citation tracking.",
        "Which platform provides the best local sandbox for multi-touch attribution analysis?"
    ],
    "Enterprise Cloud & DevOps": [
        "Best platforms for multi-cloud infrastructure cost governance and FinOps.",
        "Top Kubernetes continuous deployment and progressive delivery tools.",
        "Compare leading cloud native observability and distributed tracing tools.",
        "What are the best serverless data processing engines with lowest latency?",
        "Top automated disaster recovery and chaos engineering platforms for cloud."
    ],
    "Cybersecurity & Identity": [
        "Top Zero Trust network access and identity threat detection tools.",
        "Best automated API security testing and SSRF prevention solutions.",
        "Compare modern cloud security posture management (CSPM) platforms.",
        "Top software supply chain security and SBOM audit platforms.",
        "Best automated secret scanner and credential leak prevention solutions."
    ]
}

SOURCES_LIST = [
    "Authoritative Tech Documentation",
    "Reddit / r/programming Discussions",
    "Peer-Reviewed Technical Benchmark",
    "Independent Review Portal (G2/TrustRadius)",
    "GitHub Discussions & Star Trends",
    "Engineering Blog Whitepaper"
]


class GeoAuditorService:
    """Service to audit Share of Model (SoM) across generative engines."""

    # Best possible contribution of a single evaluation: rank 1 (decay 1.0) x sentiment +1 (1.5x)
    SOM_MAX_PER_EVALUATION = 1.5

    @staticmethod
    def position_decay(rank: int) -> float:
        """
        Logarithmic rank decay discount: 1 / log2(rank + 1).
        Rank 1 -> 1.0, Rank 2 -> ~0.6309, Rank 3 -> 0.50, etc.
        """
        if rank <= 0:
            return 1.0
        return 1.0 / math.log2(rank + 1)

    @staticmethod
    def sentiment_multiplier(sentiment_score: float) -> float:
        """
        Maps sentiment score [-1.0, 1.0] to a multiplier [0.5, 1.5].
        Neutral (0.0) -> 1.0.
        """
        clamped = max(-1.0, min(1.0, sentiment_score))
        return 1.0 + (0.5 * clamped)

    @classmethod
    def calculate_som_score(
        cls,
        citations: list[dict[str, Any]],
        total_prompts: int
    ) -> float:
        """
        Governing Equation:
        sum(citation_weight_i * position_decay_i * sentiment_multiplier_i) / total_category_prompts
        """
        if total_prompts <= 0:
            return 0.0

        som_numerator = 0.0
        for c in citations:
            citation_weight = 1.0
            decay = cls.position_decay(c.get("rank", 1))
            sentiment_mult = cls.sentiment_multiplier(c.get("sentiment_score", 0.0))
            som_numerator += citation_weight * decay * sentiment_mult

        score = som_numerator / float(total_prompts)
        return round(score, 4)

    @classmethod
    def normalized_som(cls, som_score: float) -> float:
        """SoM_norm = SoM / SoM_max in [0, 1]; SoM_max = 1.5 (cited at rank 1 with sentiment +1 in every prompt)."""
        return max(0.0, min(1.0, som_score / cls.SOM_MAX_PER_EVALUATION))

    @staticmethod
    def share_of_voice(som_by_brand: dict[str, float]) -> dict[str, float]:
        """SoV_b = SoM_b / sum_j SoM_j: the brand's slice of all visibility the engines handed out."""
        total = sum(max(0.0, v) for v in som_by_brand.values())
        if total <= 0.0:
            return {b: 0.0 for b in som_by_brand}
        return {b: max(0.0, v) / total for b, v in som_by_brand.items()}

    @staticmethod
    def deterministic_seed(*parts: str) -> int:
        """Stable 64-bit seed from SHA-256 (the old sum-of-ord seed collided for anagrams)."""
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big")

    @classmethod
    def run_deterministic_audit(
        cls,
        request: GeoAuditRequest,
        db: Optional[Session] = None
    ) -> GeoAuditResponse:
        """
        Executes a deterministic, offline-capable GEO audit across simulated generative engines.
        """
        # Validate brand URL if provided to prevent SSRF
        if request.brand_url:
            validate_and_sanitize_url(request.brand_url)

        category = request.category if request.category in PROMPT_BANK else "AI Developer Tools"
        prompts = [sanitize_text(p) for p in request.custom_prompts] if request.custom_prompts else PROMPT_BANK[category]
        engines = [sanitize_text(e) for e in request.engines if e.strip()] or SIMULATED_ENGINES
        engines = list(dict.fromkeys(engines))

        target_brand = sanitize_text(request.target_brand)
        competitors = [c for c in dict.fromkeys(sanitize_text(c) for c in request.competitors) if c and c != target_brand]
        if not target_brand:
            raise ValueError("target_brand is empty after sanitization.")
        if not competitors:
            competitors = ["Competitor-Vortex", "Competitor-Apex"]

        all_brands = [target_brand] + competitors

        # Seed pseudo-random generator with a SHA-256 of tenant, brand and category for determinism
        rnd = random.Random(cls.deterministic_seed(request.tenant_id, target_brand, category))

        citations_sample: list[CitationRecord] = []
        brand_citations: dict[str, list[dict[str, Any]]] = {b: [] for b in all_brands}
        engine_counts: dict[str, float] = {e: 0.0 for e in engines}
        provenance_counts: dict[str, int] = {s: 0 for s in SOURCES_LIST}

        total_evaluations = 0

        for prompt in prompts:
            for engine in engines:
                total_evaluations += 1
                # Determine cited brands for this prompt (between 1 and 3 brands cited)
                # Give target brand realistic 65% citation rate in its niche
                cited_this_turn: list[str] = []
                if rnd.random() < 0.65:
                    cited_this_turn.append(target_brand)

                for comp in competitors:
                    if rnd.random() < 0.55:
                        cited_this_turn.append(comp)

                if not cited_this_turn:
                    cited_this_turn.append(rnd.choice(all_brands))

                # Shuffle rankings
                rnd.shuffle(cited_this_turn)

                for rank_idx, brand in enumerate(cited_this_turn, start=1):
                    # Higher rank has slightly higher sentiment tendency
                    base_sent = rnd.uniform(0.3, 0.95) if brand == target_brand else rnd.uniform(0.1, 0.85)
                    sentiment_score = round(base_sent, 2)
                    sent_label = "Positive" if sentiment_score >= 0.3 else ("Neutral" if sentiment_score >= -0.2 else "Negative")
                    provenance = rnd.choice(SOURCES_LIST)
                    provenance_counts[provenance] += 1

                    snippet = (
                        f"According to {provenance}, {brand} is highlighted as a top-tier choice "
                        f"for '{prompt[:60]}...' citing superior architectural modularity and integration speed."
                    )

                    citation_data = {
                        "engine": engine,
                        "prompt": prompt,
                        "brand": brand,
                        "is_target": (brand == target_brand),
                        "rank": rank_idx,
                        "sentiment_score": sentiment_score,
                        "sentiment_label": sent_label,
                        "provenance": provenance,
                        "snippet": snippet
                    }

                    brand_citations[brand].append(citation_data)

                    if brand == target_brand:
                        engine_counts[engine] += 1.0

                    if len(citations_sample) < 30:
                        citations_sample.append(CitationRecord(
                            engine_name=engine,
                            prompt_text=prompt,
                            brand_cited=brand,
                            is_target_brand=(brand == target_brand),
                            citation_rank=rank_idx,
                            sentiment_score=sentiment_score,
                            sentiment_label=sent_label,
                            source_provenance=provenance,
                            snippet=snippet
                        ))

                    # Persist to database only when explicitly requested (prevents unbounded table growth)
                    if db is not None and request.persist:
                        audit_record = GeoPromptAudit(
                            tenant_id=request.tenant_id,
                            category=category,
                            prompt_text=prompt,
                            engine_name=engine,
                            brand_cited=brand,
                            is_target_brand=(brand == target_brand),
                            citation_rank=rank_idx,
                            sentiment_score=sentiment_score,
                            source_provenance=provenance,
                            response_snippet=snippet
                        )
                        db.add(audit_record)

        if db is not None and request.persist:
            db.commit()

        # Compute SoM scores per brand
        total_prompts_count = len(prompts) * len(engines)
        som_by_brand = {b: cls.calculate_som_score(brand_citations[b], total_prompts_count) for b in all_brands}
        sov = cls.share_of_voice(som_by_brand)
        target_som = som_by_brand[target_brand]

        competitor_metrics: list[BrandSoMMetric] = []
        for brand in all_brands:
            c_list = brand_citations[brand]
            som = som_by_brand[brand]
            ci_low, ci_high = wilson_interval(len(c_list), total_prompts_count)
            avg_rank = (sum(c["rank"] for c in c_list) / len(c_list)) if c_list else 0.0
            avg_sent = (sum(c["sentiment_score"] for c in c_list) / len(c_list)) if c_list else 0.0
            sent_counts = {"Positive": 0, "Neutral": 0, "Negative": 0}
            for c in c_list:
                sent_counts[c["sentiment_label"]] += 1

            competitor_metrics.append(BrandSoMMetric(
                brand_name=brand,
                is_target=(brand == target_brand),
                citation_count=len(c_list),
                share_of_model_score=som,
                normalized_som=round(cls.normalized_som(som), 4),
                share_of_voice=round(sov[brand], 4),
                citation_rate=round(len(c_list) / total_prompts_count, 4),
                citation_rate_ci_low=round(ci_low, 4),
                citation_rate_ci_high=round(ci_high, 4),
                average_rank=round(avg_rank, 2),
                average_sentiment=round(avg_sent, 2),
                sentiment_breakdown=sent_counts
            ))

        # Sort competitor metrics by SoM descending
        competitor_metrics.sort(key=lambda x: x.share_of_model_score, reverse=True)

        # Normalize engine breakdown
        total_target_citations = sum(engine_counts.values()) or 1.0
        normalized_engines = {
            eng: round(count / total_target_citations, 3)
            for eng, count in engine_counts.items()
        }

        engine_som = {
            eng: cls.calculate_som_score(
                [c for c in brand_citations[target_brand] if c["engine"] == eng], len(prompts)
            )
            for eng in engines
        }

        return GeoAuditResponse(
            tenant_id=request.tenant_id,
            category=category,
            target_brand=target_brand,
            total_prompts_audited=total_prompts_count,
            total_citations=sum(len(v) for v in brand_citations.values()),
            target_brand_som_score=target_som,
            target_brand_normalized_som=round(cls.normalized_som(target_som), 4),
            target_brand_share_of_voice=round(sov[target_brand], 4),
            market_concentration_hhi=round(herfindahl_index(sov.values()), 2),
            engine_entropy_bits=round(shannon_entropy_bits(engine_counts.values()), 4),
            competitor_metrics=competitor_metrics,
            engine_breakdown=normalized_engines,
            engine_som=engine_som,
            provenance_distribution=provenance_counts,
            citations_sample=citations_sample
        )
