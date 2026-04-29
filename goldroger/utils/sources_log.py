"""
Sources tracking log — records every data point used in an analysis,
its origin, and confidence level. Written to sources.md in the output folder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SourceEntry:
    metric: str
    value: str
    source: str       # "pappers" / "yfinance" / "web_search" / "llm_estimated" / "sector_default"
    confidence: str   # "verified" / "estimated" / "inferred"
    url: str = ""


class SourcesLog:
    def __init__(self, company: str):
        self.company = company
        self._entries: list[SourceEntry] = []

    def add(
        self,
        metric: str,
        value: str,
        source: str,
        confidence: str,
        url: str = "",
    ) -> None:
        self._entries.append(SourceEntry(metric, value, source, confidence, url))

    def to_markdown(self) -> str:
        lines = [
            f"# Sources — {self.company}",
            "",
            "| Metric | Value | Source | Confidence |",
            "|--------|-------|--------|------------|",
        ]
        for e in self._entries:
            url_str = f" ([link]({e.url}))" if e.url else ""
            conf_emoji = {"verified": "✅", "estimated": "⚠️", "inferred": "🔵"}.get(e.confidence, "❓")
            lines.append(f"| {e.metric} | {e.value} | {e.source}{url_str} | {conf_emoji} {e.confidence} |")
        lines += [
            "",
            "## Legend",
            "- ✅ verified — from official filings or exchange data",
            "- ⚠️ estimated — from web search or LLM with cited basis",
            "- 🔵 inferred — sector default or model assumption",
        ]
        return "\n".join(lines)

    def save(self, output_dir: str) -> None:
        path = Path(output_dir) / "sources.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
