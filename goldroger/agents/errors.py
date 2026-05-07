"""Shared error helpers for agent runtime behavior."""
from __future__ import annotations

from typing import Any


_CAPACITY_TOKENS = (
    "429",
    "rate_limited",
    "rate limit",
    "service_tier_capacity_exceeded",
    "code\":\"3505",
    "code='3505",
    " code 3505",
)


class APICapacityError(RuntimeError):
    """Raised when provider/API capacity is temporarily unavailable."""


def is_api_capacity_error(exc: Any) -> bool:
    s = str(exc).lower()
    return any(tok in s for tok in _CAPACITY_TOKENS)

