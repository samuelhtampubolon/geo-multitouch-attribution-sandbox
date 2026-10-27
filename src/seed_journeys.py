"""
Deterministic Seeding Engine for GEO & Multi-Touch Attribution Sandbox.
Generates:
- 500 Realistic Multi-Touch Customer Journeys (AI Search, Virtual Influencer, Organic Tech Blog, Paid Social, etc.)
- 100 Generative Engine Prompt Citation Audits
- Initial Channel Spends for ROAS Analysis
"""

from __future__ import annotations
import hashlib
import json
import random
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from src.database import SessionLocal, init_db
from src.models import ChannelSpend, CustomerJourney, GeoPromptAudit
from src.services.geo_auditor import GeoAuditorService, PROMPT_BANK, SIMULATED_ENGINES, SOURCES_LIST


CHANNELS = [
    "AI Search",
    "Virtual Influencer",
    "Organic Tech Blog",
    "Paid Social",
    "Direct",
    "Email Newsletter",
    "Organic Search"
]

INITIAL_SPENDS: dict[str, float] = {
    "AI Search": 3500.0,
    "Virtual Influencer": 4500.0,
    "Organic Tech Blog": 2000.0,
    "Paid Social": 8000.0,       # High spend, frequently over-credited by Last-Click
    "Direct": 500.0,
    "Email Newsletter": 1500.0,
    "Organic Search": 2500.0
}


def seed_database(tenant_id: str = "tenant-alpha", force_reseed: bool = False) -> None:
    """Seeds the SQLite database with 500 journeys and 100 prompt audits."""
    init_db()
    db: Session = SessionLocal()
    try:
        # Check if already seeded
        existing_journeys = db.query(CustomerJourney).filter(CustomerJourney.tenant_id == tenant_id).count()
        if existing_journeys >= 500 and not force_reseed:
            print(f"Database already contains {existing_journeys} journeys for tenant '{tenant_id}'. Skipping seed.")
            return

        if force_reseed:
            print(f"Clearing existing records for tenant '{tenant_id}'...")
            db.query(CustomerJourney).filter(CustomerJourney.tenant_id == tenant_id).delete()
            db.query(GeoPromptAudit).filter(GeoPromptAudit.tenant_id == tenant_id).delete()
            db.query(ChannelSpend).filter(ChannelSpend.tenant_id == tenant_id).delete()
            db.commit()

        print(f"Seeding realistic customer journeys and GEO audits for tenant '{tenant_id}'...")

        # 1. Seed Channel Spends
        for ch, spend in INITIAL_SPENDS.items():
            existing_cs = (
                db.query(ChannelSpend)
                .filter(ChannelSpend.tenant_id == tenant_id, ChannelSpend.channel_name == ch)
                .first()
            )
            if not existing_cs:
                db.add(ChannelSpend(
                    tenant_id=tenant_id,
                    channel_name=ch,
                    monthly_spend_usd=spend,
                    updated_at=datetime.now(timezone.utc)
                ))
        db.commit()

        # 2. Seed 500 Realistic Customer Journeys
        rnd = random.Random(42)  # Deterministic seed

        # Common touchpoint progression archetypes
        journey_archetypes = [
            # High-conversion modern AI-driven funnel
            ["AI Search", "Organic Tech Blog", "Direct"],
            ["AI Search", "Virtual Influencer", "Paid Social", "Direct"],
            ["AI Search", "Organic Tech Blog", "Email Newsletter", "Direct"],
            ["Virtual Influencer", "AI Search", "Direct"],
            ["Organic Tech Blog", "AI Search", "Paid Social"],
            # Traditional digital funnels
            ["Paid Social", "Organic Search", "Direct"],
            ["Paid Social", "Paid Social", "Direct"],
            ["Organic Search", "Email Newsletter", "Direct"],
            ["Organic Search", "Paid Social"],
            ["Direct", "Direct"],
            # Single-touch or drop-off journeys
            ["Paid Social"],
            ["Virtual Influencer"],
            ["AI Search"],
            ["Organic Search"],
            ["Paid Social", "Organic Search"]
        ]

        conversion_probabilities = {
            0: 0.72,  # Archetype 0 has high conversion
            1: 0.65,
            2: 0.80,
            3: 0.58,
            4: 0.60,
            5: 0.40,
            6: 0.35,
            7: 0.45,
            8: 0.25,
            9: 0.30,
            10: 0.08,
            11: 0.12,
            12: 0.18,
            13: 0.10,
            14: 0.15
        }

        journeys_to_insert = []
        for i in range(500):
            arch_idx = i % len(journey_archetypes)
            base_path = list(journey_archetypes[arch_idx])

            # Occasionally inject slight variation
            if rnd.random() < 0.25 and len(base_path) > 1:
                insert_ch = rnd.choice(CHANNELS)
                pos = rnd.randint(0, len(base_path) - 1)
                base_path.insert(pos, insert_ch)

            base_p = conversion_probabilities.get(arch_idx, 0.25)
            converted = rnd.random() < base_p

            if converted:
                conv_value = round(rnd.triangular(80.0, 450.0, 180.0), 2)
            else:
                conv_value = 0.0

            session_raw = f"{tenant_id}_{i}_{'-'.join(base_path)}_{converted}"
            session_hash = hashlib.sha256(session_raw.encode("utf-8")).hexdigest()[:16]

            journeys_to_insert.append(CustomerJourney(
                tenant_id=tenant_id,
                session_hash=f"sess_{session_hash}",
                path_sequence_json=json.dumps(base_path),
                converted=converted,
                conversion_value_usd=conv_value,
                created_at=datetime.now(timezone.utc)
            ))

        db.bulk_save_objects(journeys_to_insert)
        db.commit()
        print(f"Successfully seeded 500 customer journeys.")

        # 3. Seed 100 Generative Engine Prompt Audits
        target_brand = "TesserPulse"
        competitors = ["VortexAI", "OmniAgent", "HyperDev"]
        all_brands = [target_brand] + competitors

        audits_to_insert = []
        prompt_list = []
        for cat, prompts in PROMPT_BANK.items():
            for p in prompts:
                prompt_list.append((cat, p))

        # Replicate to reach at least 100 prompts
        while len(prompt_list) < 25:
            prompt_list.extend(prompt_list[:25 - len(prompt_list)])

        audit_counter = 0
        for cat, prompt in prompt_list:
            for engine in SIMULATED_ENGINES:
                if audit_counter >= 100:
                    break
                audit_counter += 1

                # Deterministic selection
                cited_brands = [target_brand]
                if rnd.random() < 0.6:
                    cited_brands.append(rnd.choice(competitors))
                rnd.shuffle(cited_brands)

                for rank_idx, brand in enumerate(cited_brands, start=1):
                    sentiment = round(rnd.uniform(0.4, 0.9) if brand == target_brand else rnd.uniform(0.1, 0.75), 2)
                    provenance = rnd.choice(SOURCES_LIST)
                    snippet = (
                        f"In benchmark evaluation for '{prompt}', {brand} ranked #{rank_idx} "
                        f"with superior latency and attribution precision, verified via {provenance}."
                    )
                    audits_to_insert.append(GeoPromptAudit(
                        tenant_id=tenant_id,
                        category=cat,
                        prompt_text=prompt,
                        engine_name=engine,
                        brand_cited=brand,
                        is_target_brand=(brand == target_brand),
                        citation_rank=rank_idx,
                        sentiment_score=sentiment,
                        source_provenance=provenance,
                        response_snippet=snippet,
                        created_at=datetime.now(timezone.utc)
                    ))

        db.bulk_save_objects(audits_to_insert)
        db.commit()
        print(f"Successfully seeded {len(audits_to_insert)} GEO prompt citation audits.")

    finally:
        db.close()


if __name__ == "__main__":
    seed_database("tenant-alpha", force_reseed=True)
