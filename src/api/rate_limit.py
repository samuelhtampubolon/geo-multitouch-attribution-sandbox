"""
Shared SlowAPI limiter. Default limits are enforced globally by SlowAPIMiddleware;
sensitive routes (token issuance, heavy computation) add stricter per-route limits.
"""

from __future__ import annotations
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    headers_enabled=False,
)

RATE_LIMIT_COMPUTE = "30/minute"
