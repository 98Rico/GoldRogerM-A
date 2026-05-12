"""Structured market-context sourcing with source-backed and fallback modes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from goldroger.data.filings import FilingsPack
from goldroger.data.sector_profiles import (
    archetype_keywords,
    detect_company_archetype,
    get_sector_profile,
)
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
    relevance_score: int = 0
    relevance_reason: str = ""

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
            relevance_score=int(d.get("relevance_score") or 0),
            relevance_reason=_safe_str(d.get("relevance_reason")),
        )


@dataclass
class MarketContextPack:
    source_backed: bool
    source_count: int
    fetched_source_count: int = 0
    relevant_source_count: int = 0
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
            fetched_source_count=int(d.get("fetched_source_count") or 0),
            relevant_source_count=int(d.get("relevant_source_count") or 0),
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


def _split_terms(text: str) -> list[str]:
    out = []
    for tok in str(text or "").lower().replace("/", " ").replace("-", " ").split():
        tok = tok.strip(".,:;()[]{}\"'")
        if len(tok) >= 3:
            out.append(tok)
    return out


def _company_aliases(company: str, ticker: str) -> tuple[str, ...]:
    raw = str(company or "").strip().lower()
    aliases = {raw}
    for suffix in (
        "inc.",
        "inc",
        "corporation",
        "corp.",
        "corp",
        "plc",
        "p.l.c.",
        "ltd",
        "limited",
        "asa",
        "ag",
        "sa",
    ):
        raw = raw.replace(suffix, " ")
    norm = " ".join(raw.split())
    if norm:
        aliases.add(norm)
        for tok in _split_terms(norm):
            if len(tok) >= 4:
                aliases.add(tok)
    tkr = str(ticker or "").strip().lower()
    if tkr:
        aliases.add(tkr)
    return tuple(sorted(x for x in aliases if x))


def _archetype_peer_terms(archetype: str) -> tuple[str, ...]:
    if archetype == "tobacco_nicotine_cash_return":
        return ("pm", "mo", "imb", "imbb", "japan tobacco", "nicotine", "tobacco")
    if archetype == "commodity_cyclical_aluminum":
        return ("aluminum", "aluminium", "lme", "rio", "bhp", "fcx", "cbam")
    if archetype in {"premium_device_platform", "consumer_hardware_ecosystem"}:
        return ("iphone", "ios", "app store", "smartphone", "wearable", "ecosystem")
    return ()


def _relevance_score(
    *,
    title: str,
    source: str,
    url: str,
    company_aliases: tuple[str, ...],
    ticker: str,
    archetype: str,
    sector: str,
    industry: str,
) -> tuple[int, str]:
    txt = f"{title} {source} {url}".lower()
    score = 0
    reasons: list[str] = []

    ticker_l = str(ticker or "").strip().lower()
    if ticker_l and any(f" {ticker_l}{sep}" in f" {txt}" for sep in (" ", ".", ",", ":", ";", "/", "-", "_", ")")):
        score += 70
        reasons.append("ticker_match")

    direct_alias_hit = False
    for alias in company_aliases:
        if len(alias) >= 4 and alias in txt:
            direct_alias_hit = True
            score += 65 if " " in alias else 45
            reasons.append("company_name_match")
            break

    if (
        ("sec.gov" in txt or "investor" in txt or "annual report" in txt or "results centre" in txt)
        and (direct_alias_hit or (ticker_l and ticker_l in txt))
    ):
        score += 30
        reasons.append("official_source_match")

    archetype_terms = archetype_keywords(archetype)
    archetype_hits = sum(1 for k in archetype_terms if k in txt)
    if archetype_hits:
        score += min(50, archetype_hits * 18)
        reasons.append("archetype_keyword_match")

    peer_terms = _archetype_peer_terms(archetype)
    peer_hits = sum(1 for k in peer_terms if k in txt)
    if peer_hits:
        score += min(36, peer_hits * 14)
        reasons.append("industry_peer_keyword_match")

    sector_terms = tuple(_split_terms(f"{sector} {industry}"))
    sector_hits = sum(1 for k in sector_terms if len(k) >= 4 and k in txt)
    if sector_hits:
        score += min(30, sector_hits * 10)
        reasons.append("sector_keyword_match")

    unrelated_penalty_terms = (
        "fast food",
        "mcdonald",
        "chipotle",
        "yum brands",
        "alibaba",
        "tower semiconductor",
    )
    if (not direct_alias_hit) and any(k in txt for k in unrelated_penalty_terms):
        score -= 35
        reasons.append("off_target_entity_penalty")

    strong_signal = bool(
        direct_alias_hit
        or ("ticker_match" in reasons)
        or ("official_source_match" in reasons)
        or (archetype_hits >= 2)
        or (peer_hits >= 2)
    )
    if not strong_signal:
        score = min(score, 55)
    score = max(0, min(100, score))
    reason = ",".join(reasons) if reasons else "weak_match"
    return score, reason


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
    relevant_threshold = 60
    fetched_count = 0
    relevant_count = 0
    archetype = detect_company_archetype(
        company=company,
        ticker=ticker,
        sector=sector,
        industry=industry,
    )
    aliases = _company_aliases(company=company, ticker=ticker)

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
        relevant_count += 1

    # yfinance news headlines with URL/date.
    for row in _extract_news_entries(ticker=ticker, count=12):
        fetched_count += 1
        title = _safe_str(row.get("title"))
        if not title:
            continue
        url = _safe_str(row.get("url"))
        key = (title.lower(), url)
        if key in seen:
            continue
        seen.add(key)
        _score, _reason = _relevance_score(
            title=title,
            source=_safe_str(row.get("source")),
            url=url,
            company_aliases=aliases,
            ticker=ticker,
            archetype=archetype,
            sector=sector,
            industry=industry,
        )
        if _score < relevant_threshold:
            continue
        relevant_count += 1
        item = MarketContextItem(
            text=title,
            source=_safe_str(row.get("source")) or "yfinance_news",
            date=_safe_str(row.get("date")),
            confidence="medium" if url else "low",
            url=url,
            relevance_score=_score,
            relevance_reason=_reason,
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

    fetched_urls = {
        _safe_str(x.url)
        for x in [*trends, *catalysts, *risks]
        if _safe_str(x.url).startswith("http")
    }
    source_urls = {
        _safe_str(x.url)
        for x in [*trends, *catalysts, *risks]
        if _safe_str(x.url).startswith("http")
    }
    source_backed = len(source_urls) >= 2 and relevant_count >= 2
    fallback_used = False
    note = ""

    if not source_backed:
        prof = get_sector_profile(sector or "", industry or "")
        fallback_used = True
        note = (
            "Source-backed context unavailable or relevance-filtered; "
            "using sector-profile fallback."
        )
        trends = [
            MarketContextItem(
                text=t,
                source="sector_profile_fallback",
                date="",
                confidence="low",
                url="",
                relevance_score=0,
                relevance_reason="fallback",
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
                relevance_score=0,
                relevance_reason="fallback",
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
                    relevance_score=0,
                    relevance_reason="fallback",
                )
                for r in list(prof.common_risks or ())[:max_items_per_bucket]
            ]

    pack = MarketContextPack(
        source_backed=source_backed,
        source_count=len(source_urls),
        fetched_source_count=max(fetched_count, len(fetched_urls)),
        relevant_source_count=max(relevant_count, len(source_urls)),
        trends=trends[:max_items_per_bucket],
        catalysts=catalysts[:max_items_per_bucket],
        risks=risks[:max_items_per_bucket],
        fallback_used=fallback_used,
        note=note,
    )
    market_context_cache.set(cache_key, pack.to_dict())
    return pack
