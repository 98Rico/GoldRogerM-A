"""Structured market-context sourcing with source-backed and fallback modes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from goldroger.data.filings import FilingsPack
from goldroger.data.sector_profiles import get_sector_profile
from goldroger.utils.cache import market_context_cache


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _date_from_raw(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).date().isoformat()
        except Exception:
            return ""
    txt = _safe_str(raw)
    if not txt:
        return ""
    if "T" in txt:
        return txt.split("T")[0]
    if len(txt) >= 10 and txt[4] == "-" and txt[7] == "-":
        return txt[:10]
    return txt


@dataclass
class MarketContextItem:
    text: str
    source: str
    date: str
    confidence: str = "estimated"
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MarketContextItem":
        return MarketContextItem(
            text=_safe_str(d.get("text")),
            source=_safe_str(d.get("source")) or "unknown",
            date=_safe_str(d.get("date")),
            confidence=_safe_str(d.get("confidence")) or "estimated",
            url=_safe_str(d.get("url")),
        )


@dataclass
class MarketContextPack:
    source_backed: bool
    source_count: int
    trends: list[MarketContextItem] = field(default_factory=list)
    catalysts: list[MarketContextItem] = field(default_factory=list)
    risks: list[MarketContextItem] = field(default_factory=list)
    fallback_used: bool = False
    note: str = ""
    retrieved_at: str = field(default_factory=_utc_iso)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["trends"] = [x.to_dict() for x in self.trends]
        out["catalysts"] = [x.to_dict() for x in self.catalysts]
        out["risks"] = [x.to_dict() for x in self.risks]
        return out

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MarketContextPack":
        def _rows(k: str) -> list[MarketContextItem]:
            rows = d.get(k) if isinstance(d.get(k), list) else []
            return [MarketContextItem.from_dict(x) for x in rows if isinstance(x, dict)]

        return MarketContextPack(
            source_backed=bool(d.get("source_backed")),
            source_count=int(d.get("source_count") or 0),
            trends=_rows("trends"),
            catalysts=_rows("catalysts"),
            risks=_rows("risks"),
            fallback_used=bool(d.get("fallback_used")),
            note=_safe_str(d.get("note")),
            retrieved_at=_safe_str(d.get("retrieved_at")) or _utc_iso(),
        )


def _extract_news_entries(ticker: str, count: int = 10) -> list[dict[str, str]]:
    if not ticker:
        return []
    try:
        t = yf.Ticker(ticker)
        rows = t.get_news(count=count) if hasattr(t, "get_news") else t.news
    except Exception:
        return []
    if not isinstance(rows, list):
        return []

    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # yfinance payload can be flat or nested under "content".
        content = row.get("content") if isinstance(row.get("content"), dict) else {}
        title = _safe_str(content.get("title") or row.get("title"))
        if not title:
            continue
        url = ""
        if isinstance(content.get("canonicalUrl"), dict):
            url = _safe_str(content.get("canonicalUrl", {}).get("url"))
        if not url:
            url = _safe_str(content.get("clickThroughUrl", {}).get("url") if isinstance(content.get("clickThroughUrl"), dict) else "")
        if not url:
            url = _safe_str(row.get("link") or row.get("url"))
        source = _safe_str(content.get("provider", {}).get("displayName") if isinstance(content.get("provider"), dict) else "")
        if not source:
            source = _safe_str(row.get("publisher") or row.get("provider") or "yfinance_news")
        date_raw = content.get("pubDate") or row.get("providerPublishTime") or row.get("pubDate")
        out.append(
            {
                "title": title,
                "url": url,
                "source": source or "yfinance_news",
                "date": _date_from_raw(date_raw),
            }
        )
    return out


def _classify_news_item(title: str) -> str:
    txt = _safe_str(title).lower()
    risk_tokens = (
        "lawsuit",
        "regulat",
        "antitrust",
        "investigation",
        "probe",
        "fine",
        "decline",
        "downgrade",
        "warning",
    )
    catalyst_tokens = (
        "earnings",
        "guidance",
        "launch",
        "approval",
        "partnership",
        "acquisition",
        "buyback",
        "dividend",
        "results",
    )
    if any(tok in txt for tok in risk_tokens):
        return "risk"
    if any(tok in txt for tok in catalyst_tokens):
        return "catalyst"
    return "trend"


def build_market_context_pack(
    *,
    company: str,
    ticker: str,
    sector: str,
    industry: str = "",
    filings_pack: FilingsPack | None = None,
    max_items_per_bucket: int = 3,
) -> MarketContextPack:
    """
    Build deterministic market context from lightweight, source-carrying feeds.
    Used to reduce fallback-only thesis outputs in full mode.
    """
    cache_key = f"market_context:{ticker.upper() or company.lower()}"
    cached = market_context_cache.get(cache_key)
    if isinstance(cached, dict):
        try:
            return MarketContextPack.from_dict(cached)
        except Exception:
            pass

    trends: list[MarketContextItem] = []
    catalysts: list[MarketContextItem] = []
    risks: list[MarketContextItem] = []
    seen: set[tuple[str, str]] = set()

    # Filings anchor.
    if filings_pack and filings_pack.latest and filings_pack.latest.source_url:
        r = filings_pack.latest
        anchor = MarketContextItem(
            text=(
                f"Latest {r.filing_type} filing"
                + (f" ({r.filing_date})" if r.filing_date else "")
                + " is available for primary-source diligence."
            ),
            source=r.source_name or "filings",
            date=r.filing_date or "",
            confidence="verified" if r.confidence == "verified" else "estimated",
            url=r.source_url,
        )
        trends.append(anchor)
        seen.add((anchor.text.lower(), anchor.url))

    # yfinance news headlines with URL/date.
    for row in _extract_news_entries(ticker=ticker, count=12):
        title = _safe_str(row.get("title"))
        if not title:
            continue
        url = _safe_str(row.get("url"))
        key = (title.lower(), url)
        if key in seen:
            continue
        seen.add(key)
        item = MarketContextItem(
            text=title,
            source=_safe_str(row.get("source")) or "yfinance_news",
            date=_safe_str(row.get("date")),
            confidence="medium" if url else "low",
            url=url,
        )
        bucket = _classify_news_item(title)
        if bucket == "risk" and len(risks) < max_items_per_bucket:
            risks.append(item)
        elif bucket == "catalyst" and len(catalysts) < max_items_per_bucket:
            catalysts.append(item)
        elif len(trends) < max_items_per_bucket:
            trends.append(item)
        if (
            len(trends) >= max_items_per_bucket
            and len(catalysts) >= max_items_per_bucket
            and len(risks) >= max_items_per_bucket
        ):
            break

    source_urls = {
        _safe_str(x.url)
        for x in [*trends, *catalysts, *risks]
        if _safe_str(x.url).startswith("http")
    }
    source_backed = len(source_urls) >= 1
    fallback_used = False
    note = ""

    if not source_backed:
        prof = get_sector_profile(sector or "", industry or "")
        fallback_used = True
        note = "Source-backed context unavailable; using sector-profile fallback."
        trends = [
            MarketContextItem(
                text=t,
                source="sector_profile_fallback",
                date="",
                confidence="low",
                url="",
            )
            for t in list(prof.fallback_market_context or ())[:max_items_per_bucket]
        ]
        catalysts = [
            MarketContextItem(
                text=t,
                source="sector_profile_fallback",
                date="",
                confidence="low",
                url="",
            )
            for t in list(prof.fallback_catalysts or ())[:max_items_per_bucket]
        ]
        if not risks:
            risks = [
                MarketContextItem(
                    text=f"Sector risk watch: {r}",
                    source="sector_profile_fallback",
                    date="",
                    confidence="low",
                    url="",
                )
                for r in list(prof.common_risks or ())[:max_items_per_bucket]
            ]

    pack = MarketContextPack(
        source_backed=source_backed,
        source_count=len(source_urls),
        trends=trends[:max_items_per_bucket],
        catalysts=catalysts[:max_items_per_bucket],
        risks=risks[:max_items_per_bucket],
        fallback_used=fallback_used,
        note=note,
    )
    market_context_cache.set(cache_key, pack.to_dict())
    return pack
