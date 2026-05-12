"""
Sources tracking log — records every data point used in an analysis,
its origin, and confidence level. Written to sources.md in the output folder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from goldroger.data.sourcing import SourceResult


@dataclass
class SourceEntry:
    metric: str
    value: str
    source: str       # "pappers" / "yfinance" / "web_search" / "llm_estimated" / "sector_default"
    confidence: str   # "verified" / "estimated" / "inferred"
    url: str = ""
    currency: str = ""
    unit: str = ""
    as_of_date: str = ""
    is_estimated: bool = False
    is_fallback: bool = False
    normalization_notes: str = ""
    warning_flags: list[str] = field(default_factory=list)
    cached: bool = False


class SourcesLog:
    def __init__(self, company: str):
        self.company = company
        self._entries: list[SourceEntry] = []

    def has_metric(self, metric: str) -> bool:
        return any(e.metric == metric for e in self._entries)

    def add(
        self,
        metric: str,
        value: str,
        source: str,
        confidence: str,
        url: str = "",
        *,
        currency: str = "",
        unit: str = "",
        as_of_date: str = "",
        is_estimated: bool = False,
        is_fallback: bool = False,
        normalization_notes: str = "",
        warning_flags: list[str] | None = None,
        cached: bool = False,
    ) -> None:
        self._entries.append(
            SourceEntry(
                metric,
                value,
                source,
                confidence,
                url,
                currency=currency,
                unit=unit,
                as_of_date=as_of_date,
                is_estimated=is_estimated,
                is_fallback=is_fallback,
                normalization_notes=normalization_notes,
                warning_flags=list(warning_flags or []),
                cached=cached,
            )
        )

    def add_once(
        self,
        metric: str,
        value: str,
        source: str,
        confidence: str,
        url: str = "",
        **kwargs,
    ) -> None:
        if not self.has_metric(metric):
            self.add(metric, value, source, confidence, url, **kwargs)

    def add_source_result(
        self,
        metric: str,
        result: SourceResult,
        *,
        source_override: str | None = None,
        confidence_override: str | None = None,
    ) -> None:
        value = str(result.value)
        source = source_override or result.source_name
        confidence = confidence_override or result.source_confidence
        self.add(
            metric=metric,
            value=value,
            source=source,
            confidence=confidence,
            url=result.source_url,
            currency=result.currency,
            unit=result.unit,
            as_of_date=result.as_of_date,
            is_estimated=result.is_estimated,
            is_fallback=result.is_fallback,
            normalization_notes=result.normalization_notes,
            warning_flags=result.warning_flags,
            cached=result.cached,
        )

    def to_markdown(self) -> str:
        lines = [
            f"# Sources — {self.company}",
            "",
            "| Metric | Value | Source | Confidence | Metadata |",
            "|--------|-------|--------|------------|----------|",
        ]
        for e in self._entries:
            url_str = f" ([link]({e.url}))" if e.url else ""
            conf_emoji = {
                "verified": "✅",
                "estimated": "⚠️",
                "inferred": "🔵",
                "unavailable": "⛔",
                "skipped": "⏭️",
            }.get(e.confidence, "❓")
            meta_bits: list[str] = []
            if e.currency:
                meta_bits.append(f"ccy={e.currency}")
            if e.unit:
                meta_bits.append(f"unit={e.unit}")
            if e.as_of_date:
                meta_bits.append(f"as_of={e.as_of_date}")
            if e.cached:
                meta_bits.append("cached")
            if e.is_fallback:
                meta_bits.append("fallback")
            if e.normalization_notes:
                meta_bits.append(e.normalization_notes)
            if e.warning_flags:
                meta_bits.append("warn:" + ",".join(e.warning_flags))
            meta = "; ".join(meta_bits) if meta_bits else "—"
            lines.append(
                f"| {e.metric} | {e.value} | {e.source}{url_str} | {conf_emoji} {e.confidence} | {meta} |"
            )
        lines += [
            "",
            "## Legend",
            "- ✅ verified — from official filings or exchange data",
            "- ⚠️ estimated — from web search or LLM with cited basis",
            "- 🔵 inferred — sector default or model assumption",
            "- ⛔ unavailable — source expected but no reliable datapoint available",
            "- ⏭️ skipped — intentionally skipped by pipeline mode/policy",
        ]
        return "\n".join(lines)

    def save(self, output_dir: str) -> None:
        path = Path(output_dir) / "sources.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
