"""Sourcing primitives for provider metadata and per-datum provenance."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceResult:
    """Per-datum source metadata attached to normalized values."""

    value: Any
    currency: str = "unknown"
    unit: str = "unknown"
    as_of_date: str = ""
    source_name: str = "unknown"
    source_url: str = ""
    source_confidence: str = "inferred"
    is_estimated: bool = False
    is_fallback: bool = False
    normalization_notes: str = ""
    warning_flags: list[str] = field(default_factory=list)
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderDescriptor:
    """Formal metadata contract for a data source provider."""

    name: str
    source_type: str
    coverage: list[str]
    freshness: str
    confidence_level: str
    limitations: list[str] = field(default_factory=list)
    raw_fields: list[str] = field(default_factory=list)
    normalized_fields: list[str] = field(default_factory=list)
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_source_result(
    value: Any,
    *,
    source_name: str,
    source_confidence: str,
    currency: str = "unknown",
    unit: str = "unknown",
    source_url: str = "",
    as_of_date: str | None = None,
    is_estimated: bool = False,
    is_fallback: bool = False,
    normalization_notes: str = "",
    warning_flags: list[str] | None = None,
    cached: bool = False,
) -> SourceResult:
    return SourceResult(
        value=value,
        currency=(currency or "unknown"),
        unit=(unit or "unknown"),
        as_of_date=as_of_date or utc_now_iso(),
        source_name=source_name or "unknown",
        source_url=source_url or "",
        source_confidence=source_confidence or "inferred",
        is_estimated=bool(is_estimated),
        is_fallback=bool(is_fallback),
        normalization_notes=normalization_notes or "",
        warning_flags=list(warning_flags or []),
        cached=bool(cached),
    )
