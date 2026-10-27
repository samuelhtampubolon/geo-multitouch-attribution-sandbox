"""
Test configuration: isolate every test run in a throwaway SQLite database and a fixed
test-only JWT secret, so the suite never mutates data/geo_mta.db and is fully deterministic.
Environment variables must be set before any `src` module is imported.
"""

from __future__ import annotations
import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="geo_mta_tests_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR / 'test.db'}"
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-only-secret-" + "x" * 32
os.environ["TENANT_CLIENT_SECRETS"] = "tenant-alpha:alpha-test-secret-123,tenant-beta:beta-test-secret-456"

import pytest  # noqa: E402

from src.api.rate_limit import limiter  # noqa: E402
from src.schemas import CustomerJourneyInput  # noqa: E402
from src.seed_journeys import seed_database  # noqa: E402

TENANT_SECRETS = {"tenant-alpha": "alpha-test-secret-123", "tenant-beta": "beta-test-secret-456"}


@pytest.fixture(scope="session", autouse=True)
def _seeded_database():
    seed_database("tenant-alpha", force_reseed=True)
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


def journey(path: list[str], converted: bool, value: float = 0.0, idx: int = 0) -> CustomerJourneyInput:
    return CustomerJourneyInput(
        session_hash=f"sess_{idx:04d}", path=path, converted=converted, conversion_value_usd=value
    )


@pytest.fixture
def sample_journeys() -> list[CustomerJourneyInput]:
    """Sample realistic multi-touch conversion journeys."""
    return [
        journey(["AI Search", "Organic Tech Blog", "Direct"], True, 200.0, 1),
        journey(["AI Search", "Paid Social"], True, 150.0, 2),
        journey(["Paid Social", "Direct"], True, 180.0, 3),
        journey(["Virtual Influencer", "AI Search", "Direct"], True, 300.0, 4),
        journey(["Organic Tech Blog", "Paid Social"], False, 0.0, 5),
        journey(["Paid Social"], False, 0.0, 6),
        journey(["Virtual Influencer"], False, 0.0, 7),
        journey(["AI Search"], False, 0.0, 8),
    ]


@pytest.fixture
def seeded_journeys() -> list[CustomerJourneyInput]:
    """The 500 deterministic journeys from the offline corpus."""
    import json
    from src.database import SessionLocal
    from src.models import CustomerJourney

    db = SessionLocal()
    try:
        rows = db.query(CustomerJourney).filter(CustomerJourney.tenant_id == "tenant-alpha").all()
        return [
            journey(json.loads(r.path_sequence_json), r.converted, r.conversion_value_usd, i)
            for i, r in enumerate(rows)
        ]
    finally:
        db.close()
