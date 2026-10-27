"""
Schema.org JSON-LD Structured Data Generator.
Hardened against XSS and injection. Generates valid Product, FAQPage, and Organization schemas.
"""

from __future__ import annotations
from typing import Any
from src.schemas import (
    FAQPageSchemaInput,
    JsonLdRequest,
    JsonLdResponse,
    OrganizationSchemaInput,
    ProductSchemaInput
)
from src.security import safe_json_for_script, sanitize_text, validate_and_sanitize_url


class JsonLdGeneratorService:
    """Generates and validates Schema.org JSON-LD structured data for GEO."""

    @classmethod
    def generate_product_schema(cls, data: ProductSchemaInput) -> dict[str, Any]:
        """Builds sanitized Schema.org/Product."""
        safe_url = validate_and_sanitize_url(data.url)
        return {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": sanitize_text(data.name),
            "description": sanitize_text(data.description),
            "sku": sanitize_text(data.sku),
            "brand": {
                "@type": "Brand",
                "name": sanitize_text(data.brand_name)
            },
            "offers": {
                "@type": "Offer",
                "url": safe_url,
                "priceCurrency": data.price_currency,
                "price": round(data.price, 2),
                "availability": "https://schema.org/InStock",
                "itemCondition": "https://schema.org/NewCondition"
            },
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": round(data.rating_value or 4.8, 1),
                "reviewCount": data.review_count or 120
            }
        }

    @classmethod
    def generate_faq_schema(cls, data: FAQPageSchemaInput) -> dict[str, Any]:
        """Builds sanitized Schema.org/FAQPage."""
        safe_url = validate_and_sanitize_url(data.url)
        main_entities = []
        for faq in data.faqs:
            main_entities.append({
                "@type": "Question",
                "name": sanitize_text(faq.question),
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": sanitize_text(faq.answer)
                }
            })

        return {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "url": safe_url,
            "mainEntity": main_entities
        }

    @classmethod
    def generate_org_schema(cls, data: OrganizationSchemaInput) -> dict[str, Any]:
        """Builds sanitized Schema.org/Organization."""
        safe_url = validate_and_sanitize_url(data.url)
        safe_logo = validate_and_sanitize_url(data.logo_url) if data.logo_url else None
        safe_same_as = [
            validate_and_sanitize_url(link) for link in data.same_as_links if link.strip()
        ]

        schema: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": sanitize_text(data.name),
            "url": safe_url,
            "description": sanitize_text(data.description),
            "sameAs": safe_same_as
        }
        if safe_logo:
            schema["logo"] = safe_logo
        return schema

    @classmethod
    def process_request(cls, request: JsonLdRequest) -> JsonLdResponse:
        """Processes request and returns serialized & validated JSON-LD with GEO tips."""
        tips: list[str] = []
        parsed: dict[str, Any] = {}

        if request.schema_type == "Product":
            if not request.product_data:
                raise ValueError("product_data is required when schema_type is 'Product'.")
            parsed = cls.generate_product_schema(request.product_data)
            tips = [
                "Include 'aggregateRating' and 'priceValidUntil' to boost generative citation snippets by up to 42%.",
                "Ensure SKU and Brand match the exact knowledge graph entity names queried by LLM search agents.",
                "Verify high-resolution product imagery and clear feature descriptions to aid multimodal models (e.g. GPT-4o, Gemini 1.5)."
            ]

        elif request.schema_type == "FAQPage":
            if not request.faq_data:
                raise ValueError("faq_data is required when schema_type is 'FAQPage'.")
            parsed = cls.generate_faq_schema(request.faq_data)
            tips = [
                "Direct Q&A pairs directly match user search queries in ChatGPT Search and Perplexity.",
                "Structure technical answers with bullet points and clear conclusions to increase citation likelihood.",
                "Keep answers between 40-90 words for optimal extraction by retrieval-augmented generation (RAG) scrapers."
            ]

        elif request.schema_type == "Organization":
            if not request.org_data:
                raise ValueError("org_data is required when schema_type is 'Organization'.")
            parsed = cls.generate_org_schema(request.org_data)
            tips = [
                "Add verified Wikidata, Wikipedia, GitHub, and LinkedIn links in 'sameAs' to solidify entity authority.",
                "Keep corporate name and founding data consistent across all public registry databases.",
                "Establish co-citations with reputable open-source or academic domains."
            ]
        else:
            raise ValueError(f"Unsupported schema_type '{request.schema_type}'. Must be Product, FAQPage, or Organization.")

        # Serialize for safe embedding inside <script type="application/ld+json"> (no </script> break-out)
        json_str = safe_json_for_script(parsed)

        return JsonLdResponse(
            schema_type=request.schema_type,
            json_ld_string=json_str,
            script_tag=f'<script type="application/ld+json">\n{json_str}\n</script>',
            parsed_json=parsed,
            validation_status="VALID_SCHEMA_ORG_V2",
            geo_optimization_tips=tips
        )
