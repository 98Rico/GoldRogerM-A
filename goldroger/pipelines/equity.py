"""Equity analysis pipeline — run_analysis()."""
from __future__ import annotations

import re as _re
import time
import copy as _copy
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta
from threading import Event

from dotenv import load_dotenv

from goldroger.config import DEFAULT_CONFIG as _cfg

from goldroger.agents.specialists import (
    DataCollectorAgent,
    FinancialModelerAgent,
    PeerFinderAgent,
    ReportWriterAgent,
    SectorAnalystAgent,
    TransactionCompsAgent,
    ValuationEngineAgent,
)
from goldroger.agents.errors import APICapacityError, is_api_capacity_error
from goldroger.data.transaction_comps import (
    add_comps,
    load_cache,
    parse_agent_output as parse_tx_output,
    sector_medians,
)
from goldroger.data.comparables import (
    PeerMultiples,
    build_peer_multiples,
    find_peers_deterministic_quick,
    parse_peer_agent_output,
    resolve_peer_tickers,
)
from goldroger.data.fetcher import (
    MarketData,
    fetch_market_data,
    resolve_ticker,
    resolve_ticker_with_context,
)
from goldroger.data.filings import FilingsPack, build_filings_pack
from goldroger.data.market_context import MarketContextPack, build_market_context_pack
from goldroger.data.normalization import (
    apply_currency_normalization as _apply_currency_normalization_impl,
    build_data_normalization_audit as _build_data_normalization_audit_impl,
)
from goldroger.data.private_quality import merge_private_market_data
from goldroger.data.quality_gate import assess_data_quality
from goldroger.data.sector_profiles import (
    archetype_fallback,
    detect_company_archetype,
    detect_sector_profile,
    get_sector_profile,
)
from goldroger.utils.json_parser import normalise_revenue_string
from goldroger.data.registry import DEFAULT_REGISTRY
from goldroger.finance.core.scenarios import run_scenarios
from goldroger.finance.core.valuation_service import ValuationService
from goldroger.models import (
    AnalysisResult,
    DCFAssumptions,
    Financials,
    FootballField,
    Fundamentals,
    ICScoreSummary,
    InvestmentThesis,
    MarketAnalysis,
    PeerComp,
    PeerCompsTable,
    ScenarioSummary,
    Valuation,
    ValuationMethod,
)
from goldroger.utils.money import format_money_millions as _fmt_money_millions
from goldroger.utils.money import format_price as _fmt_price
from goldroger.utils.logger import new_run
from goldroger.utils.sources_log import SourcesLog

from ._shared import (
    ValuationAssumptions,
    _client,
    _done,
    _fin_fallback,
    _fin_from_market,
    _fmt_ev_human,
    _fund_fallback,
    _parse_with_retry,
    _reconcile_financials,
    _step,
    console,
)
from .fill_gaps import fill_gaps

load_dotenv()
_MARKET_ANALYSIS_TIMEOUT = 30
_PEER_COMPS_TIMEOUT = 30
_FINANCIALS_TIMEOUT = 30
_TX_COMPS_TIMEOUT = 45
_REPORT_WRITER_TIMEOUT_QUICK = 8
_REPORT_WRITER_TIMEOUT_STANDARD = 18
_REPORT_WRITER_TIMEOUT_FULL = 35
_TOTAL_TIMEOUT_QUICK = 45
_TOTAL_TIMEOUT_FULL = 120

def _peer_similarity_score(target_mcap: float | None, peer_mcap: float | None, target_sector: str, peer_sector: str) -> float:
    score = 0.0
    if target_mcap and peer_mcap and target_mcap > 0 and peer_mcap > 0:
        ratio = max(target_mcap, peer_mcap) / min(target_mcap, peer_mcap)
        if ratio <= 2.0:
            score += 0.6
        elif ratio <= 5.0:
            score += 0.4
        elif ratio <= 10.0:
            score += 0.2
    ts = (target_sector or "").lower()
    ps = (peer_sector or "").lower()
    if ts and ps and any(tok in ps for tok in ts.split()):
        score += 0.4
    return round(min(score, 1.0), 2)


def _normalize_dividend_yield(raw) -> float | None:
    """Normalize dividend yield to decimal and enforce sanity bounds.
    Returns None when suspicious/unusable.
    """
    if raw is None:
        return None
    try:
        y = float(raw)
    except Exception:
        return None
    if y < 0:
        return None
    # Some feeds can return percent-like 5.75 instead of 0.0575.
    if y > 1.0:
        y = y / 100.0
    # Hard sanity gate: >25% is almost always bad parse/noisy field.
    if y > 0.25:
        return None
    return y


def _build_data_normalization_audit(market_data: MarketData | None) -> dict:
    """Compatibility wrapper — delegates to centralized normalization module."""
    return _build_data_normalization_audit_impl(market_data)


def _apply_currency_normalization(market_data: MarketData | None, audit: dict) -> tuple[MarketData | None, dict, bool]:
    """Compatibility wrapper — delegates to centralized normalization module."""
    return _apply_currency_normalization_impl(market_data, audit)


def _sanitize_catalysts(catalysts: list[str], run_year: int | None = None) -> list[str]:
    """Enforce time-aware catalyst labels:
    - 'Upcoming' only for future-dated events
    - stale events rewritten as recent-event context
    """
    _today = date.today()
    if run_year is None:
        run_year = _today.year
    run_month = _today.month
    run_q = ((run_month - 1) // 3) + 1
    run_ym = run_year * 12 + run_month
    month_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
        "october": 10, "nov": 11, "november": 11, "dec": 12, "déc": 12, "december": 12,
    }

    def _event_position(text: str) -> tuple[int | None, int | None, int | None]:
        y = None
        q = None
        mth = None
        ys = _re.findall(r"\b(20\d{2})\b", text)
        if ys:
            y = int(ys[0])
        qm = _re.search(r"\bQ([1-4])\b", text, flags=_re.IGNORECASE)
        if qm:
            q = int(qm.group(1))
        mm = _re.search(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
            r"Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
            text,
            flags=_re.IGNORECASE,
        )
        if mm:
            mth = month_map.get(mm.group(1).lower())
        return y, q, mth

    out: list[str] = []
    for c in catalysts or []:
        txt = str(c or "").strip()
        if not txt:
            continue
        if _re.search(r"\b(apple car|project titan|rumou?red|speculat(?:ive|ion))\b", txt, flags=_re.IGNORECASE):
            txt = f"Speculative catalyst (low confidence): {txt}"
        y, q, mth = _event_position(txt)
        is_upcoming_label = bool(_re.search(r"\b(upcoming|expected|will|next)\b", txt, flags=_re.IGNORECASE))

        stale = False
        very_old = False
        if y is not None:
            if mth is not None:
                event_ym = y * 12 + mth
            elif q is not None:
                event_ym = y * 12 + (q * 3)
            else:
                event_ym = y * 12 + 12
            delta_months = run_ym - event_ym
            stale = delta_months > 0
            very_old = delta_months > 3
            # hard reject ancient stale catalysts from the catalysts section
            if delta_months > 18:
                continue

        if stale and is_upcoming_label:
            txt = _re.sub(r"\b(upcoming|expected|will|next)\b", "recent", txt, flags=_re.IGNORECASE)
        if very_old:
            txt = f"Historical context: {txt}"
        elif stale and not txt.lower().startswith("recent"):
            txt = f"Recent event context: {txt}"
        # Avoid stale product-cycle naming when date provenance is weak.
        txt = _re.sub(r"\biPhone\s*\d+\b", "latest iPhone cycle", txt, flags=_re.IGNORECASE)
        txt = _re.sub(r"\biOS\s*\d+\b", "next major iOS release", txt, flags=_re.IGNORECASE)
        txt = _re.sub(r"\bmacOS\s*\d+\b", "next major macOS release", txt, flags=_re.IGNORECASE)
        txt = _re.sub(r"\bcurrent iPhone cycle\b", "latest iPhone cycle", txt, flags=_re.IGNORECASE)
        txt = _re.sub(r"\blatest iPhone cycle\s+cycle\b", "latest iPhone cycle", txt, flags=_re.IGNORECASE)
        txt = _re.sub(r"\bcycle\s+cycle\b", "cycle", txt, flags=_re.IGNORECASE)
        out.append(txt)
    return out


def _sanitize_thesis_language(text: str) -> str:
    txt = str(text or "")
    if not txt:
        return txt
    # Avoid stale/version-locked product labeling unless explicitly sourced.
    txt = _re.sub(r"\biPhone\s*\d+\b", "latest iPhone cycle", txt, flags=_re.IGNORECASE)
    txt = _re.sub(r"\biOS\s*\d+\b", "next major iOS release", txt, flags=_re.IGNORECASE)
    txt = _re.sub(r"\bmacOS\s*\d+\b", "next major macOS release", txt, flags=_re.IGNORECASE)
    # Soften overly specific unsourced phrasing.
    txt = _re.sub(
        r"\b30%\s+App\s*Store\s+commission(?:\s+structure)?\b",
        "App Store monetization model",
        txt,
        flags=_re.IGNORECASE,
    )
    txt = _re.sub(
        r"\bAI-enhanced\s+latest iPhone cycle\s+cycle\b",
        "AI-enabled latest iPhone cycle",
        txt,
        flags=_re.IGNORECASE,
    )
    txt = _re.sub(r"\bcycle\s+cycle\b", "cycle", txt, flags=_re.IGNORECASE)
    return txt


def _trend_is_placeholder(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return True
    return any(
        k in t
        for k in (
            "no market trend data available",
            "not available",
            "unavailable",
            "insufficient data",
            "no data",
        )
    )


def _soften_unsourced_scenario_specificity(text: str) -> str:
    txt = str(text or "")
    if not txt:
        return txt
    # Remove unsupported precision when research enrichment is degraded.
    txt = _re.sub(r"\b\d{1,2}(?:\.\d+)?\s*-\s*\d{1,2}(?:\.\d+)?%\s*CAGR\b", "growth acceleration", txt, flags=_re.IGNORECASE)
    txt = _re.sub(
        r"\(?\s*~?\s*\d{1,2}(?:\.\d+)?\s*-\s*\d{1,2}(?:\.\d+)?%\s*\)?",
        "resilient margins",
        txt,
        flags=_re.IGNORECASE,
    )
    txt = _re.sub(r"\b\d{1,2}(?:\.\d+)?%\s*CAGR\b", "growth trajectory", txt, flags=_re.IGNORECASE)
    # Avoid stale product cycle specifics when unsourced.
    txt = _re.sub(r"\bQ[1-4]\s+20\d{2}\s+earnings\b", "next earnings update", txt, flags=_re.IGNORECASE)
    # Remove unsourced dated-event precision.
    txt = _re.sub(
        r"\bQ[1-4]\s+20\d{2}\b",
        "upcoming reporting period",
        txt,
        flags=_re.IGNORECASE,
    )
    txt = _re.sub(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+20\d{2}\b",
        "a future period",
        txt,
        flags=_re.IGNORECASE,
    )
    # Soften hard regulatory event claims when unsourced.
    txt = _re.sub(
        r"\b(?:DOJ|DMA|FTC|SEC)\b[^.]*?(?:ruling|decision|approval|ban)[^.]*",
        "potential regulatory developments that could affect economics",
        txt,
        flags=_re.IGNORECASE,
    )
    # Replace unsupported exact operational quantities/dates with generic watch language.
    txt = _re.sub(
        r"\b(?:\d[\d,.]*\s*(?:Mt|kt|GW|GWh|tons?|tonnes?|capacity))\b",
        "operational capacity updates",
        txt,
        flags=_re.IGNORECASE,
    )
    # Repair common token-stitch artifacts after regex replacements.
    txt = _re.sub(r"\bbyresilient\b", "by resilient", txt, flags=_re.IGNORECASE)
    txt = _re.sub(r"\bmarginsin\b", "margins in", txt, flags=_re.IGNORECASE)
    txt = _re.sub(r"\baresilient\b", "a resilient", txt, flags=_re.IGNORECASE)
    txt = _re.sub(r"\btoresilient\b", "to resilient", txt, flags=_re.IGNORECASE)
    txt = _re.sub(r"\btoresilient(?=[a-z])", "to resilient ", txt, flags=_re.IGNORECASE)
    txt = _re.sub(r"([a-zA-Z])resilient margins", r"\1 resilient margins", txt)
    txt = _re.sub(r"resilient margins([a-zA-Z])", r"resilient margins \1", txt)
    txt = _re.sub(r"\s{2,}", " ", txt).strip()
    return txt


def _sync_canonical_recommendation_text(
    thesis_text: str,
    fair_value_text: str,
    point_estimate_text: str,
    final_recommendation: str,
) -> str:
    txt = str(thesis_text or "").strip()
    txt = _re.sub(
        r"(?im)^Valuation reference \(canonical\):[^\n]*\n*",
        "",
        txt,
    ).strip()
    txt = _re.sub(
        r"(?i)\bfinal recommendation(?: is)?\s+[A-Z][A-Z /-]*",
        f"final recommendation is {final_recommendation}",
        txt,
    )
    txt = _re.sub(
        r"(?im)^\s*-\s*recommendation(?:\s*:|\s+is)?\s*[A-Z][A-Z /-]*\s*$",
        f"- Recommendation: {final_recommendation}",
        txt,
    )
    txt = _re.sub(r"(LOW CONVICTION)\(", r"\1 (", txt)
    txt = _re.sub(r"(MODERATE CONVICTION)\(", r"\1 (", txt)
    canonical = (
        f"Valuation reference (canonical): fair value {fair_value_text}; "
        f"point estimate {point_estimate_text}; recommendation {final_recommendation}."
    )
    return f"{canonical}\n\n{txt}".strip()


def _text_missing(v) -> bool:
    if v is None:
        return True
    s = str(v).strip().lower()
    if not s:
        return True
    return any(tok in s for tok in ("n/a", "not available", "unknown", "none", "unavailable"))


def _has_source_backed_market_data(m: MarketAnalysis) -> bool:
    _sources = [str(s) for s in (m.sources or []) if str(s).strip()]
    return any(("http://" in s.lower()) or ("https://" in s.lower()) for s in _sources)


def _ensure_market_analysis_contract(m: MarketAnalysis) -> MarketAnalysis:
    """Enforce explicit market-analysis completeness/quality flags."""
    if not m.market_segments and not _text_missing(m.market_segment):
        m.market_segments = [str(m.market_segment).strip()]
    m.market_segments = [str(x).strip() for x in (m.market_segments or []) if str(x).strip()]
    missing = [str(x) for x in (m.missing_fields or []) if str(x).strip()]
    if _text_missing(m.market_size):
        missing.append("tam_estimate")
    if _text_missing(m.market_growth):
        missing.append("market_growth")
    if not [str(t).strip() for t in (m.key_trends or []) if str(t).strip()]:
        missing.append("key_trends")
    # Stable order, no duplicates
    m.missing_fields = sorted(set(missing))
    if not (m.source_quality or "").strip():
        m.source_quality = "high" if _has_source_backed_market_data(m) else "low"
    if not (m.data_status or "").strip():
        m.data_status = "COMPLETE" if not m.missing_fields else "PARTIAL"
    if m.data_status.upper() not in {"COMPLETE", "PARTIAL", "FAILED"}:
        m.data_status = "PARTIAL"
    if m.data_status.upper() == "COMPLETE" and m.missing_fields:
        m.data_status = "PARTIAL"
    return m


def _quality_tier(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _fallback_catalysts(
    company: str,
    sector: str,
    industry: str = "",
    ticker: str = "",
) -> list[str]:
    _arch = detect_company_archetype(company=company, ticker=ticker, sector=sector, industry=industry)
    _arch_tpl = archetype_fallback(_arch)
    _arch_cats = _arch_tpl.get("catalysts") if isinstance(_arch_tpl, dict) else None
    if isinstance(_arch_cats, tuple) and _arch_cats:
        return [str(x) for x in _arch_cats]
    prof = get_sector_profile(sector or "", industry or "")
    if prof.fallback_catalysts:
        return [str(x) for x in prof.fallback_catalysts]
    return [
        "Next earnings/filing update: demand, margins, and guidance.",
        "Strategy/product execution update: evidence of growth durability.",
        "Macro/regulatory developments: potential impact on assumptions.",
    ]


def _archetype_sector_display(
    *,
    archetype: str,
    profile_label: str,
    sector: str,
) -> str:
    """User-facing sector line for deterministic fallback thesis text."""
    if archetype in {"premium_device_platform", "consumer_hardware_ecosystem"}:
        return "Technology / Consumer Hardware & Services Ecosystem"
    if archetype == "tobacco_nicotine_cash_return":
        return "Consumer Staples / Tobacco"
    if archetype == "commodity_cyclical_aluminum":
        return "Materials / Aluminum"
    if archetype == "healthtech_platform":
        return "Healthcare / HealthTech Platform"
    if archetype == "fintech_digital_bank_payments":
        return "Financials / Fintech & Digital Payments"
    if archetype == "hrtech_saas":
        return "Technology / HR Tech SaaS"
    if archetype == "b2b_saas":
        return "Technology / B2B SaaS"
    if archetype == "marketplace":
        return "Consumer Internet / Marketplace"
    if archetype == "consumer_brand":
        return "Consumer / Brand-led"
    if archetype == "industrial_private":
        return "Industrials"
    if archetype == "professional_services":
        return "Professional Services"
    if archetype == "healthcare_services":
        return "Healthcare / Services"
    return profile_label or sector or "Default fallback"


def _archetype_market_segment(archetype: str) -> str:
    key = str(archetype or "").strip().lower()
    mapping = {
        "premium_device_platform": "Consumer hardware and services ecosystem",
        "consumer_hardware_ecosystem": "Consumer hardware and services ecosystem",
        "tobacco_nicotine_cash_return": "Tobacco and nicotine products",
        "commodity_cyclical_aluminum": "Aluminum, recycling, and low-carbon metals",
        "healthtech_platform": "Digital healthcare workflow and patient-access platforms",
        "fintech_digital_bank_payments": "Digital banking, payments, and financial services",
        "hrtech_saas": "Human-resources software and payroll/workforce platforms",
        "b2b_saas": "Enterprise subscription software platforms",
        "marketplace": "Online marketplace transactions and take-rate economics",
        "consumer_brand": "Brand-led consumer products and omnichannel distribution",
        "industrial_private": "Industrial production, automation, and backlog-driven demand",
        "professional_services": "Project-based advisory and professional services",
        "healthcare_services": "Healthcare delivery and reimbursement-linked services",
        "software_platform": "Software platforms and enterprise applications",
        "semiconductor": "Semiconductors and semiconductor value chain",
        "financials": "Financial services and balance-sheet businesses",
        "consumer_staples": "Consumer staples branded products",
        "healthcare": "Healthcare products and services",
    }
    return mapping.get(key, "")


def _private_archetype_peer_hints(archetype: str) -> list[str]:
    key = str(archetype or "").strip().lower()
    mapping: dict[str, list[str]] = {
        "healthtech_platform": ["TDOC", "DOCS", "VEEV", "EXAS", "ELV", "UNH"],
        "fintech_digital_bank_payments": ["PYPL", "SQ", "ADYEY", "WIZEY", "NU", "SOFI", "HOOD", "COIN"],
        "hrtech_saas": ["PAYC", "PAYX", "WDAY", "ADP", "SAP", "DAY"],
        "b2b_saas": ["CRM", "NOW", "ADBE", "ORCL", "SAP"],
        "marketplace": ["EBAY", "MELI", "ETSY", "SE", "DASH"],
        "consumer_brand": ["NKE", "LULU", "PG", "KO", "PEP"],
        "industrial_private": ["HON", "ETN", "EMR", "ROK", "ITW"],
        "professional_services": ["ACN", "CTSH", "GIB", "EPAM", "IBM"],
        "healthcare_services": ["HCA", "UHS", "DVA", "EHC", "THC"],
    }
    return mapping.get(key, [])


def _parse_iso_date(raw: str) -> date | None:
    txt = str(raw or "").strip()
    if not txt:
        return None
    try:
        if len(txt) >= 10 and txt[4] == "-" and txt[7] == "-":
            return date.fromisoformat(txt[:10])
    except Exception:
        return None
    try:
        return datetime.fromisoformat(txt).date()
    except Exception:
        return None


def _has_recent_company_specific_catalyst(pack: MarketContextPack | None) -> bool:
    """Corroboration helper for extreme-signal review in public equities."""
    if pack is None:
        return False
    now = date.today()
    for item in (pack.catalysts or []):
        if int(getattr(item, "relevance_score", 0) or 0) < 70:
            continue
        reason = str(getattr(item, "relevance_reason", "") or "").lower()
        if not any(tok in reason for tok in ("ticker_match", "company_name_match", "official_source_match")):
            continue
        d = _parse_iso_date(str(getattr(item, "date", "") or ""))
        if d is None:
            # Allow undated but high-relevance company-specific catalysts.
            return True
        if abs((now - d).days) <= 365:
            return True
    return False


def _build_fallback_thesis(
    company: str,
    sector: str,
    recommendation: str,
    reason: str,
    model_signal: str = "N/A",
    industry: str = "",
    ticker: str = "",
) -> InvestmentThesis:
    _arch = detect_company_archetype(company=company, ticker=ticker, sector=sector, industry=industry)
    _arch_tpl = archetype_fallback(_arch)
    prof = get_sector_profile(sector or "", industry or "")
    cats = _fallback_catalysts(company, sector, industry, ticker=ticker)
    _arch_label = str(_arch_tpl.get("label") or _arch) if isinstance(_arch_tpl, dict) else _arch
    _sector_display = _archetype_sector_display(
        archetype=_arch,
        profile_label=str(prof.label or ""),
        sector=sector,
    )
    _arch_drivers = _arch_tpl.get("demand_drivers") if isinstance(_arch_tpl, dict) else None
    _arch_margins = _arch_tpl.get("margin_drivers") if isinstance(_arch_tpl, dict) else None
    _arch_risks = _arch_tpl.get("risks") if isinstance(_arch_tpl, dict) else None
    _drivers = ", ".join(_arch_drivers[:3]) if isinstance(_arch_drivers, tuple) and _arch_drivers else (
        ", ".join(prof.demand_drivers[:3]) if prof.demand_drivers else "demand resilience and execution discipline"
    )
    _margins = ", ".join(_arch_margins[:3]) if isinstance(_arch_margins, tuple) and _arch_margins else (
        ", ".join(prof.margin_drivers[:3]) if prof.margin_drivers else "mix and operating leverage"
    )
    _risks = ", ".join(_arch_risks[:3]) if isinstance(_arch_risks, tuple) and _arch_risks else (
        ", ".join(prof.common_risks[:3]) if prof.common_risks else "competition, regulation, and macro volatility"
    )
    _cash_return_line = (
        "- Cash return frame: dividends, payout durability, and deleveraging remain key.\n"
        if _arch == "tobacco_nicotine_cash_return"
        else ""
    )
    thesis = (
        f"Thesis:\n"
        f"- Sector profile: {_sector_display}.\n"
        f"- Archetype: {_arch_label}.\n"
        f"- Demand drivers: {_drivers}.\n"
        f"- Margin drivers: {_margins}.\n"
        f"{_cash_return_line}"
        f"- Valuation: model signal is {model_signal}, but final recommendation is {recommendation} "
        "because valuation confidence is low and source-backed market context is unavailable.\n"
        f"- Confidence note: {reason}.\n"
        f"\nRisks:\n"
        f"- {_risks}.\n"
        f"- Because full research was unavailable, this thesis is intentionally conservative and based only on "
        "verified financials, sector profile, and peer valuation outputs."
    )
    return InvestmentThesis(
        thesis=thesis,
        catalysts=cats,
        key_questions=[
            "What is the highest-confidence valuation anchor today?",
            "Which assumption drives most of the downside risk?",
            "What near-term datapoint would change conviction?",
        ],
    )


def _enforce_profile_context_guard(text: str, profile_key: str) -> str:
    txt = str(text or "")
    if not txt:
        return txt
    forbidden_map = {
        "consumer_staples_tobacco": (
            "app store",
            "platform/services monetization",
            "device upgrade cycle",
            "hardware demand",
        ),
        "technology_consumer_electronics": (
            "excise tax",
            "combustible volume decline",
            "nicotine pouch",
            "plain packaging",
        ),
    }
    for token in forbidden_map.get(profile_key, ()):
        if token in txt.lower():
            return (
                "Fallback Market Context — sector profile only, not source-backed. "
                "Used for qualitative framing only, not valuation inputs."
            )
    return txt


def _country_hint_from_market_data(market_data: MarketData | None) -> str:
    """Best-effort country hint inferred from provider/source name."""
    _src = (market_data.data_source or "").lower() if market_data else ""
    return (
        "FR" if "infogreffe" in _src or "pappers" in _src
        else "GB" if "companies house" in _src
        else "DE" if "handelsregister" in _src
        else "NL" if "kvk" in _src
        else "ES" if "registro" in _src
        else "US" if "sec" in _src or "edgar" in _src
        else ""
    )


def _fetch_provider(provider_name: str, company: str, siren: str | None = None):
    """Dynamically call a named data provider and return MarketData or None."""
    _map = {
        "infogreffe":        ("goldroger.data.providers.infogreffe",        "InfogreffeProvider"),
        "pappers":           ("goldroger.data.providers.pappers",            "PappersProvider"),
        "companies_house":   ("goldroger.data.providers.companies_house",    "CompaniesHouseProvider"),
        "handelsregister":   ("goldroger.data.providers.handelsregister",    "HandelsregisterProvider"),
        "kvk":               ("goldroger.data.providers.kvk",                "KVKProvider"),
        "registro_mercantil":("goldroger.data.providers.registro_mercantil", "RegistroMercantilProvider"),
        "sec_edgar":         ("goldroger.data.providers.sec_edgar",          "SECEdgarProvider"),
        "crunchbase":        ("goldroger.data.providers.crunchbase",         "CrunchbaseProvider"),
        "bloomberg":         ("goldroger.data.providers.bloomberg",          "BloombergProvider"),
        "capitaliq":         ("goldroger.data.providers.capitaliq",          "CapitalIQProvider"),
    }
    if provider_name not in _map:
        return None
    mod_path, cls_name = _map[provider_name]
    import importlib
    mod = importlib.import_module(mod_path)
    provider = getattr(mod, cls_name)()
    if not provider.is_available():
        return None
    if siren and hasattr(provider, "fetch_by_siren"):
        return provider.fetch_by_siren(siren, company)
    return provider.fetch_by_name(company)


def run_analysis(
    company: str,
    company_type: str = "public",
    llm: str | None = None,
    siren: str | None = None,
    interactive: bool = False,
    data_sources: list[str] | None = None,
    country_hint: str = "",
    company_identifier: str = "",
    manual_revenue: float | None = None,
    manual_revenue_currency: str = "USD",
    manual_revenue_year: int | None = None,
    manual_revenue_source_note: str = "",
    manual_identity_confirmed: bool = False,
    quick_mode: bool = False,
    full_report: bool = False,
    debug: bool = False,
    cli_mode: bool = False,
) -> AnalysisResult:
    log = new_run(company, company_type)
    _run_started = time.time()
    _total_budget_s = _TOTAL_TIMEOUT_QUICK if quick_mode else _TOTAL_TIMEOUT_FULL
    _report_mode = "quick" if quick_mode else ("full" if full_report else "standard")
    client = _client(llm)
    svc = ValuationService()
    sources = SourcesLog(company)

    data_agent = DataCollectorAgent(client)
    market_agent = SectorAnalystAgent(client)
    fin_agent = FinancialModelerAgent(client)
    val_agent = ValuationEngineAgent(client)
    thesis_agent = ReportWriterAgent(client)
    peer_agent = PeerFinderAgent(client)
    tx_agent = TransactionCompsAgent(client)

    console.rule(f"[EQUITY] {company}")

    # ── 0. REAL DATA ──────────────────────────────────────────────────────
    market_data: MarketData | None = None
    market_data_valuation: MarketData | None = None
    filings_pack: FilingsPack | None = None
    market_context_pack: MarketContextPack | None = None
    normalization_audit: dict = _build_data_normalization_audit(None)
    normalization_blocked = False
    _private_triangulation_used = False
    _private_provider_merge_notes: list[str] = []
    _private_identity_resolved = False
    _private_identity_status = "UNRESOLVED"
    _private_revenue_status = "unavailable"
    _private_revenue_quality = "UNAVAILABLE"
    _private_financials_quality = "UNAVAILABLE"
    _private_valuation_mode = "FAILED"
    _private_peers_state = "FAILED"
    _private_screen_only = False
    _private_screen_only_reasons: list[str] = []
    _private_missing_pappers_key = False
    _private_missing_companies_house_key = False
    _private_missing_provider_notes: list[str] = []
    _private_state = "IDENTITY_UNRESOLVED"
    _private_manual_revenue_used = False
    _private_manual_identity_override = False
    _private_provider_state = "FAILED"
    _private_identity_source_state = "unavailable"
    _private_used_providers: list[str] = []
    _private_skipped_providers: list[str] = []
    if company_type == "private":
        t0 = _step("Registry (EU filings)")
        _provider_records: list[MarketData] = []
        if siren:
            console.print(f"  [dim]SIREN {siren} — direct lookup[/dim]")
            from goldroger.data.providers.pappers import PappersProvider
            from goldroger.data.providers.infogreffe import InfogreffeProvider
            pp = PappersProvider()
            market_data = pp.fetch_by_siren(siren, company) if pp.is_available() else None
            if not market_data:
                market_data = InfogreffeProvider().fetch_by_siren(siren, company)
        else:
            if country_hint.upper() == "GB" and company_identifier:
                try:
                    from goldroger.data.providers.companies_house import CompaniesHouseProvider
                    ch = CompaniesHouseProvider()
                    _by_id = ch.fetch_by_company_number(company_identifier, fallback_name=company)
                    if _by_id:
                        market_data = _by_id
                        console.print(
                            f"  [green]Registry[/green] (companies_house)"
                            f" [dim]company_number={company_identifier}[/dim]"
                        )
                except Exception:
                    pass
            from goldroger.data.name_resolver import resolve as resolve_company_name
            _ids = resolve_company_name(
                company,
                country_hint=country_hint or "",
                llm_provider=client,
            )
            _q = _ids.infogreffe_query or (_ids.variants[0] if _ids.variants else company)
            console.print(f"  [dim]Querying as: {_q}[/dim]")
            if market_data is None:
                market_data = DEFAULT_REGISTRY.fetch_by_name(company, country_hint=country_hint or "")
        if market_data:
            _provider_records.append(market_data)
        if market_data and market_data.revenue_ttm:
            _conf_tag = " [verified]" if market_data.confidence == "verified" else " [estimated]"
            console.print(
                f"  [green]Registry[/green] ({market_data.data_source})"
                f"{_conf_tag} Rev=${market_data.revenue_ttm:.0f}M"
            )
            sources.add(
                "Revenue TTM", f"${market_data.revenue_ttm:.0f}M",
                market_data.data_source, market_data.confidence,
            )
        elif market_data:
            console.print(
                f"  [yellow]Registry[/yellow] ({market_data.data_source}) sector only — no revenue"
            )
            sources.add("Sector", market_data.sector or "unknown", market_data.data_source, "inferred")
        else:
            console.print(
                "  [dim]No registry/provider identity payload found — private run will remain screen-only "
                "unless verified revenue or manual override is provided.[/dim]"
            )
        log.end_step("market_data", t0)

        # ── Data-source selection (interactive or CLI) ───────────────────
        from goldroger.data.source_selector import run_source_selection, resolve_source_selection
        _country_hint = (country_hint or _country_hint_from_market_data(market_data)).upper()
        if company_identifier:
            sources.add_once(
                "Confirmed Company Identifier",
                str(company_identifier),
                "confirmation_input",
                "verified",
            )
            if _country_hint == "GB":
                sources.add_once("Company Number (GB)", str(company_identifier), "confirmation_input", "verified")
            if _country_hint == "FR":
                sources.add_once("SIREN (FR)", str(company_identifier), "confirmation_input", "verified")
        if interactive:
            _sel = run_source_selection(company, country_hint=_country_hint, console=console)
        else:
            # By default for private companies, use "auto": relevant free/keyed sources.
            _requested = data_sources if data_sources is not None else ["auto"]
            _sel = resolve_source_selection(_requested, country_hint=_country_hint)

            if _sel.unknown_sources:
                console.print(
                    f"  [yellow]Unknown sources ignored:[/] {', '.join(_sel.unknown_sources)}"
                )
            if _sel.skipped_missing_credentials:
                console.print(
                    "  [yellow]Skipped (missing credentials):[/] "
                    + ", ".join(_sel.skipped_missing_credentials)
                )
                _private_missing_provider_notes = list(_sel.skipped_missing_credentials)
                _private_skipped_providers = list(_sel.skipped_missing_credentials)
                _private_missing_pappers_key = "pappers" in _sel.skipped_missing_credentials
                _private_missing_companies_house_key = "companies_house" in _sel.skipped_missing_credentials
                sources.add_once(
                    "Private Providers Skipped",
                    ", ".join(_sel.skipped_missing_credentials),
                    "source_selector",
                    "inferred",
                )
            if _sel.selected_providers:
                console.print(
                    "  [dim]Using additional sources:[/] " + ", ".join(_sel.selected_providers)
                )
                _private_used_providers = list(_sel.selected_providers)
                sources.add_once(
                    "Private Providers Used",
                    ", ".join(_sel.selected_providers),
                    "source_selector",
                    "verified",
                )
            if _country_hint == "FR" and _private_missing_pappers_key:
                console.print(
                    "  [yellow]Verified revenue unavailable because Pappers key is not configured.[/yellow]"
                )
                sources.add_once(
                    "Private Revenue Limitation",
                    "Verified revenue unavailable because Pappers key is not configured.",
                    "source_selector",
                    "inferred",
                )
            if _country_hint == "GB" and _private_missing_companies_house_key:
                console.print(
                    "  [yellow]Companies House API key missing; UK registry enrichment may be limited.[/yellow]"
                )
                sources.add_once(
                    "Private Identity Limitation",
                    "Companies House API key missing; UK private identity/filing enrichment may be limited.",
                    "source_selector",
                    "inferred",
                )

        # Manual revenue override takes precedence over registry.
        # Explicit CLI override wins over interactive/source-selector input.
        _manual_revenue_m = None
        _manual_revenue_ccy = str(manual_revenue_currency or "USD").strip().upper() or "USD"
        if not _re.match(r"^[A-Z]{3}$", _manual_revenue_ccy):
            _manual_revenue_ccy = "USD"
        _manual_revenue_note = str(manual_revenue_source_note or "").strip()
        _manual_revenue_year = manual_revenue_year
        if manual_revenue is not None:
            try:
                _manual_revenue_m = float(manual_revenue)
            except Exception:
                _manual_revenue_m = None
        elif _sel.manual_revenue_usd_m:
            _manual_revenue_m = float(_sel.manual_revenue_usd_m)
            _manual_revenue_ccy = "USD"
        if _manual_revenue_m and _manual_revenue_m > 0:
            from goldroger.data.fetcher import MarketData as _MD
            if market_data is None:
                market_data = _MD(
                    ticker="",
                    company_name=company,
                    sector="",
                    revenue_ttm=_manual_revenue_m,
                    data_source="manual_user_input",
                    confidence="manual",
                    additional_metadata={
                        "financial_currency": _manual_revenue_ccy,
                        "manual_revenue_user_provided": True,
                        "manual_revenue_year": _manual_revenue_year,
                        "manual_revenue_source_note": _manual_revenue_note,
                    },
                )
            else:
                market_data.revenue_ttm = _manual_revenue_m
                market_data.data_source = "manual_user_input"
                market_data.confidence = "manual"
                if not isinstance(market_data.additional_metadata, dict):
                    market_data.additional_metadata = {}
                market_data.additional_metadata["financial_currency"] = _manual_revenue_ccy
                market_data.additional_metadata["manual_revenue_user_provided"] = True
                market_data.additional_metadata["manual_revenue_year"] = _manual_revenue_year
                market_data.additional_metadata["manual_revenue_source_note"] = _manual_revenue_note
            _private_manual_revenue_used = True
            if manual_identity_confirmed:
                _private_manual_identity_override = True
            console.print(
                "  [green]Manual revenue set:[/green] "
                f"{_manual_revenue_ccy} {_manual_revenue_m:.0f}M"
                + (f" (FY{_manual_revenue_year})" if _manual_revenue_year else "")
                + " [manual user-provided, unverified]"
            )
            sources.add(
                "Revenue TTM",
                f"{_manual_revenue_ccy} {_manual_revenue_m:.0f}M (manual user-provided, unverified)",
                "manual_user_input",
                "manual",
            )
            sources.add_once(
                "Manual Revenue Input",
                "yes",
                "manual_user_input",
                "manual",
            )
            sources.add_once(
                "Revenue Year",
                str(_manual_revenue_year) if _manual_revenue_year else "manual year not provided",
                "manual_user_input",
                "manual",
            )
            if _manual_revenue_note:
                sources.add_once(
                    "Manual Revenue Source Note",
                    _manual_revenue_note,
                    "manual_user_input",
                    "manual",
                )
            if manual_identity_confirmed:
                sources.add_once(
                    "Manual Identity Confirmation",
                    "user confirmed unresolved identity for prototype valuation",
                    "manual_user_input",
                    "manual",
                )
        elif manual_revenue is not None:
            console.print(
                "  [yellow]Manual revenue override ignored:[/yellow] value must be positive."
            )

        # Query selected providers (skip unavailable automatically)
        for _pname in _sel.selected_providers:
            if _pname in ("infogreffe", "pappers") and siren:
                continue  # already queried via direct SIREN path above
            try:
                _prov_data = _fetch_provider(_pname, company, siren)
            except Exception as _e:
                console.print(f"  [yellow]{_pname} failed: {_e}[/yellow]")
                continue

            if not _prov_data:
                continue

            _provider_records.append(_prov_data)

            if _prov_data.revenue_ttm:
                console.print(f"  [green]{_pname}[/green] Rev=${_prov_data.revenue_ttm:.0f}M")
                sources.add(
                    "Revenue TTM", f"${_prov_data.revenue_ttm:.0f}M",
                    _pname, _prov_data.confidence,
                )
            elif _prov_data.sector:
                console.print(f"  [dim]{_pname}[/dim] sector={_prov_data.sector} (no revenue)")
                sources.add("Sector", _prov_data.sector, _pname, _prov_data.confidence)

        # Deterministic merge across all selected providers.
        _merge = merge_private_market_data(market_data, _provider_records)
        market_data = _merge.market_data
        _private_provider_merge_notes = list(_merge.notes or [])
        for _i_note, _note in enumerate(_merge.notes, start=1):
            console.print(f"  [dim]{_note}[/dim]")
            sources.add_once(
                f"Private Data Merge Note {_i_note}",
                _note,
                "private_quality_merge",
                "inferred",
            )

        # If still no revenue from providers, triangulate as deterministic fallback.
        if market_data and not market_data.revenue_ttm:
            try:
                from goldroger.data.private_triangulation import triangulate_revenue
                crunchbase_data = None
                try:
                    from goldroger.data.providers.crunchbase import CrunchbaseProvider
                    cb = CrunchbaseProvider()
                    if cb.is_available():
                        cb_md = (
                            cb.fetch_by_name(company)
                            if hasattr(cb, "fetch_by_name") else None
                        )
                        crunchbase_data = getattr(cb_md, "_raw", None) if cb_md else None
                except Exception:
                    pass

                tri = triangulate_revenue(
                    company_name=company,
                    sector=market_data.sector or "",
                    country=_country_hint_from_market_data(market_data),
                    crunchbase_data=crunchbase_data,
                )
                if tri and tri.revenue_estimate_m > 0:
                    _identity_source = market_data.data_source or "unknown"
                    market_data.revenue_ttm = tri.revenue_estimate_m
                    market_data.confidence = tri.confidence
                    market_data.data_source = "triangulation"
                    _private_triangulation_used = True
                    if not isinstance(market_data.additional_metadata, dict):
                        market_data.additional_metadata = {}
                    market_data.additional_metadata["identity_source"] = _identity_source
                    market_data.additional_metadata["triangulation_used"] = True
                    market_data.additional_metadata["triangulation_signal_count"] = len(tri.signals or [])
                    console.print(
                        f"  [cyan]Triangulation ({tri.confidence}) "
                        f"Rev=${tri.revenue_estimate_m:.0f}M "
                        f"from {len(tri.signals)} signal(s)[/cyan]"
                    )
                    sources.add(
                        "Revenue TTM",
                        f"${tri.revenue_estimate_m:.0f}M",
                        "triangulation",
                        tri.confidence,
                    )
            except Exception as _tri_e:
                console.print(f"  [dim]Triangulation skipped: {_tri_e}[/dim]")
        if market_data and not market_data.revenue_ttm:
            sources.add_once(
                "Revenue TTM",
                "N/A",
                "private_pipeline",
                "unavailable",
            )
        _private_strong_identity_sources = {
            "manual_user_input",
            "pappers",
            "infogreffe",
            "companies_house",
            "handelsregister",
            "registro_mercantil",
            "kvk",
            "sec_edgar",
        }
        if market_data:
            _private_revenue_status = (
                "verified"
                if market_data.revenue_ttm and str(market_data.confidence or "").lower() == "verified"
                else "manual"
                if market_data.revenue_ttm and str(market_data.confidence or "").lower() == "manual"
                else "estimated"
                if market_data.revenue_ttm and str(market_data.confidence or "").lower() == "estimated"
                else "inferred"
                if market_data.revenue_ttm
                else "unavailable"
            )
            _id_source = str(market_data.data_source or "").strip().lower()
            _md_meta = market_data.additional_metadata if isinstance(market_data.additional_metadata, dict) else {}
            _has_strong_id = bool(
                company_identifier
                or str(_md_meta.get("company_number") or "").strip()
                or str(_md_meta.get("siren") or "").strip()
                or str(_md_meta.get("siret") or "").strip()
                or str(_md_meta.get("registration_number") or "").strip()
                or str(_md_meta.get("cik") or "").strip()
                or str(_md_meta.get("lei") or "").strip()
            )
            _private_identity_resolved = bool(
                _has_strong_id
                or (
                    _id_source == "triangulation"
                    and isinstance(market_data.additional_metadata, dict)
                    and str(market_data.additional_metadata.get("identity_source") or "").strip().lower()
                    in _private_strong_identity_sources
                )
            )
            if (
                _private_manual_revenue_used
                and not _private_identity_resolved
                and manual_identity_confirmed
            ):
                _private_manual_identity_override = True
            _private_identity_status = (
                "RESOLVED"
                if _private_identity_resolved
                else ("WEAK" if str(market_data.company_name or "").strip() else "UNRESOLVED")
            )
            _private_identity_source_state = (
                "source-backed"
                if _private_identity_resolved
                else ("fallback" if _private_identity_status == "WEAK" else "unavailable")
            )
            _rev_source = str(market_data.data_source or "").strip().lower()
            if _private_revenue_status == "verified":
                _private_revenue_quality = "VERIFIED"
            elif _private_revenue_status == "manual":
                _private_revenue_quality = "MANUAL"
            elif _private_revenue_status == "estimated":
                if _rev_source in {"companies_house", "sec_edgar", "handelsregister"} and not _private_triangulation_used:
                    _private_revenue_quality = "HIGH_CONFIDENCE_ESTIMATE"
                else:
                    _private_revenue_quality = "LOW_CONFIDENCE_ESTIMATE"
            elif _private_revenue_status == "inferred":
                _private_revenue_quality = "LOW_CONFIDENCE_ESTIMATE"
            else:
                _private_revenue_quality = "UNAVAILABLE"
            _private_valuation_grade_ready = bool(
                (_private_identity_status == "RESOLVED" or _private_manual_identity_override)
                and _private_revenue_quality in {"VERIFIED", "HIGH_CONFIDENCE_ESTIMATE", "MANUAL"}
            )
            _private_screen_only = not _private_valuation_grade_ready
            if _private_screen_only:
                if _private_identity_status != "RESOLVED" and not _private_manual_identity_override:
                    _private_screen_only_reasons.append("legal identity unresolved")
                if _private_revenue_quality in {"UNAVAILABLE", "LOW_CONFIDENCE_ESTIMATE"}:
                    _private_screen_only_reasons.append("verified revenue unavailable")
            _rev_ccy = "USD"
            if isinstance(market_data.additional_metadata, dict):
                _rev_ccy = str(
                    market_data.additional_metadata.get("financial_currency")
                    or market_data.additional_metadata.get("currency")
                    or "USD"
                ).upper()
            if not _re.match(r"^[A-Z]{3}$", _rev_ccy):
                _rev_ccy = "USD"
            if market_data.revenue_ttm:
                if _private_revenue_quality == "VERIFIED":
                    sources.add_once(
                        "Revenue TTM",
                        f"{_rev_ccy} {float(market_data.revenue_ttm):.0f}M",
                        market_data.data_source or "private_pipeline",
                        "verified",
                    )
                elif _private_revenue_quality == "MANUAL":
                    sources.add_once(
                        "Revenue TTM",
                        f"{_rev_ccy} {float(market_data.revenue_ttm):.0f}M (manual user-provided, unverified)",
                        market_data.data_source or "manual_user_input",
                        "manual",
                    )
                else:
                    sources.add_once(
                        "Revenue TTM",
                        f"{_rev_ccy} {float(market_data.revenue_ttm):.0f}M [indicative estimate — not valuation-grade]",
                        market_data.data_source or "private_pipeline",
                        str(market_data.confidence or "estimated"),
                    )
            sources.add_once(
                "Private Revenue Status",
                _private_revenue_status,
                market_data.data_source or "private_pipeline",
                "inferred",
            )
            sources.add_once(
                "Private Revenue Quality",
                _private_revenue_quality,
                "private_revenue_gate",
                "inferred",
            )
            sources.add_once(
                "Private Identity Resolution",
                "resolved" if _private_identity_resolved else "unresolved",
                "private_identity_guard",
                "inferred",
            )
            sources.add_once(
                "Private Identity Status",
                _private_identity_status,
                "private_identity_guard",
                "inferred",
            )
            sources.add_once(
                "Private Identity Source State",
                _private_identity_source_state,
                "private_identity_guard",
                "inferred",
            )
            if _private_screen_only_reasons:
                sources.add_once(
                    "Private Valuation Mode",
                    "SCREEN_ONLY",
                    "private_valuation_gate",
                    "inferred",
                )
            if _private_revenue_quality in {"LOW_CONFIDENCE_ESTIMATE", "UNAVAILABLE"}:
                sources.add_once(
                    "Revenue Estimate Excluded From Valuation",
                    "true",
                    "private_valuation_gate",
                    "inferred",
                )
            elif _private_revenue_quality == "MANUAL":
                sources.add_once(
                    "Revenue Estimate Excluded From Valuation",
                    "false",
                    "private_valuation_gate",
                    "inferred",
                )
            if _private_identity_status == "UNRESOLVED":
                _private_state = "IDENTITY_UNRESOLVED"
            elif _private_identity_status == "RESOLVED" and _private_revenue_quality == "UNAVAILABLE":
                _private_state = "IDENTITY_RESOLVED_NO_REVENUE"
            elif _private_screen_only:
                _private_state = "SCREEN_ONLY"
            else:
                _private_state = "VALUATION_READY"
        else:
            _private_identity_status = "UNRESOLVED"
            _private_revenue_quality = "UNAVAILABLE"
            _private_screen_only = True
            _private_screen_only_reasons = ["identity and revenue data unavailable"]
            _private_state = "IDENTITY_UNRESOLVED"

    if company_type == "public":
        t0 = _step("Market Data (yfinance)")
        _ticker_ctx = resolve_ticker_with_context(company)
        ticker = str((_ticker_ctx or {}).get("selected_symbol") or "").strip().upper() or resolve_ticker(company)
        if ticker:
            log.ticker = ticker
            console.print(f"  Resolved ticker: [bold]{ticker}[/bold]")
            market_data = fetch_market_data(ticker)
            if market_data:
                if isinstance(market_data.additional_metadata, dict) and isinstance(_ticker_ctx, dict):
                    market_data.additional_metadata.setdefault("selected_listing_symbol", _ticker_ctx.get("selected_symbol"))
                    market_data.additional_metadata.setdefault("primary_listing_symbol", _ticker_ctx.get("primary_listing_symbol"))
                    market_data.additional_metadata.setdefault("ticker_resolution_reason", _ticker_ctx.get("reason"))
                    if _ticker_ctx.get("selected_exchange"):
                        market_data.additional_metadata.setdefault("selected_exchange", _ticker_ctx.get("selected_exchange"))
                normalization_audit = _build_data_normalization_audit(market_data)
                market_data_valuation = _copy.deepcopy(market_data)
                market_data_valuation, normalization_audit, _fx_applied = _apply_currency_normalization(
                    market_data_valuation,
                    normalization_audit,
                )
                normalization_blocked = str(normalization_audit.get("status", "")).upper() == "FAILED"
                _q_ccy = str(normalization_audit.get("quote_currency") or "USD")
                _f_ccy = str(normalization_audit.get("financial_statement_currency") or _q_ccy)
                _rev_txt = f"{_f_ccy} {market_data.revenue_ttm:.0f}M" if market_data.revenue_ttm is not None else "N/A"
                _mcap_txt = f"{_q_ccy} {market_data.market_cap:.0f}M" if market_data.market_cap is not None else "N/A"
                console.print(
                    f"  [green]Verified[/green] Rev={_rev_txt} "
                    f"EBITDA={market_data.ebitda_margin:.1%} β={market_data.beta} "
                    f"MCap={_mcap_txt}"
                )
                if normalization_blocked:
                    console.print(
                        "  [red]Data normalization audit FAILED:[/red] "
                        f"{normalization_audit.get('reason')}."
                    )
                else:
                    console.print(
                        "  [dim]Data normalization audit:[/dim] "
                        f"{normalization_audit.get('status')} — "
                        f"quote {normalization_audit.get('quote_currency')}, "
                        f"financials {normalization_audit.get('financial_statement_currency')}, "
                        f"share basis {normalization_audit.get('share_count_basis')}."
                    )
                    if _fx_applied:
                        console.print(
                            "  [dim]FX normalization applied:[/dim] "
                            f"{normalization_audit.get('reason')}"
                        )
                try:
                    filings_pack = build_filings_pack(
                        company=company,
                        ticker=ticker,
                        market_data=market_data,
                    )
                except Exception:
                    filings_pack = None
                if filings_pack and filings_pack.records:
                    _latest_filing = filings_pack.latest
                    if _latest_filing:
                        _filing_txt = _latest_filing.filing_type
                        if _latest_filing.filing_date:
                            _filing_txt += f" ({_latest_filing.filing_date})"
                        sources.add_once(
                            "Latest Filing",
                            _filing_txt,
                            _latest_filing.source_name or "filings",
                            _latest_filing.confidence or "estimated",
                            _latest_filing.source_url or "",
                        )
                    for _i, _rec in enumerate(filings_pack.records[:3], start=1):
                        _v = _rec.filing_type + (f" ({_rec.filing_date})" if _rec.filing_date else "")
                        sources.add_once(
                            f"Filing Source {_i}",
                            _v,
                            _rec.source_name or "filings",
                            _rec.confidence or "estimated",
                            _rec.source_url or "",
                            as_of_date=_rec.filing_date or "",
                        )
                    if isinstance(market_data.additional_metadata, dict):
                        market_data.additional_metadata["filings_pack"] = filings_pack.to_dict()
                    console.print(
                        "  [dim]Filings pack:[/dim] "
                        f"{len(filings_pack.records)} record(s), "
                        f"source-backed={str(filings_pack.source_backed).lower()}"
                    )
                else:
                    console.print("  [dim]Filings pack: unavailable (no source-backed filing links resolved).[/dim]")
                if market_data.forward_revenue_growth is not None:
                    _fg_src = (
                        "yfinance_analyst_revenue"
                        if market_data.forward_revenue_1y is not None
                        else "yfinance_earnings_proxy"
                    )
                    _fg_conf = "verified" if market_data.forward_revenue_1y is not None else "estimated"
                    console.print(
                        f"  [cyan]Forward growth: {market_data.forward_revenue_growth:+.1%}[/cyan]"
                        + (" [dim](earnings-growth proxy)[/dim]" if _fg_conf == "estimated" else "")
                    )
                sources.add("Revenue TTM", _rev_txt, "yfinance", "verified")
                sources.add("EBITDA Margin", f"{market_data.ebitda_margin:.1%}", "yfinance", "verified")
                sources.add("Market Cap", _mcap_txt, "yfinance", "verified")
                _sr = market_data.additional_metadata.get("source_results") if isinstance(market_data.additional_metadata, dict) else None
                if isinstance(_sr, dict):
                    _sr_metric_map = {
                        "revenue_ttm": "Revenue TTM",
                        "ebitda_ttm": "EBITDA (TTM)",
                        "market_cap": "Market Cap",
                        "enterprise_value": "Enterprise Value",
                        "free_cash_flow": "Free Cash Flow",
                        "shares_outstanding": "Shares Outstanding",
                    }
                    for _key, _metric in _sr_metric_map.items():
                        _payload = _sr.get(_key)
                        if not isinstance(_payload, dict):
                            continue
                        if sources.has_metric(_metric):
                            continue
                        sources.add(
                            _metric,
                            str(_payload.get("value")),
                            str(_payload.get("source_name") or "unknown"),
                            str(_payload.get("source_confidence") or "inferred"),
                            str(_payload.get("source_url") or ""),
                            currency=str(_payload.get("currency") or ""),
                            unit=str(_payload.get("unit") or ""),
                            as_of_date=str(_payload.get("as_of_date") or ""),
                            is_estimated=bool(_payload.get("is_estimated")),
                            is_fallback=bool(_payload.get("is_fallback")),
                            normalization_notes=str(_payload.get("normalization_notes") or ""),
                            warning_flags=list(_payload.get("warning_flags") or []),
                            cached=bool(_payload.get("cached")),
                        )
                if market_data.beta:
                    sources.add("Beta (β)", f"{market_data.beta:.3f}", "yfinance", "verified")
                if market_data.forward_revenue_growth is not None:
                    sources.add(
                        "Forward Revenue Growth",
                        f"{market_data.forward_revenue_growth:+.1%}",
                        _fg_src, _fg_conf,
                    )
                if market_data.gross_margin is not None:
                    sources.add("Gross Margin", f"{market_data.gross_margin:.1%}", "yfinance", "verified")
                if market_data.net_margin is not None:
                    sources.add("Net Margin", f"{market_data.net_margin:.1%}", "yfinance", "verified")
                if market_data.fcf_ttm is not None:
                    _fcf_val = f"{_f_ccy} {market_data.fcf_ttm:.0f}M"
                    _md_country = ""
                    if isinstance(market_data.additional_metadata, dict):
                        _md_country = str(market_data.additional_metadata.get("country") or "").strip().lower()
                    if _md_country and _md_country not in {"united states", "usa", "us"}:
                        _fcf_val += " [check currency/ADR basis]"
                    sources.add("Free Cash Flow", _fcf_val, "yfinance", "verified")
                if market_data.net_debt is not None:
                    sources.add("Net Debt", f"{_f_ccy} {market_data.net_debt:.0f}M", "yfinance", "verified")
                    sources.add_once(
                        "Net Debt (original currency)",
                        f"{_f_ccy} {market_data.net_debt:.0f}M",
                        "yfinance",
                        "verified",
                    )
                if market_data_valuation and market_data_valuation.net_debt is not None:
                    _nd_norm = f"{_q_ccy} {market_data_valuation.net_debt:.0f}M"
                    _nd_conf = (
                        "inferred"
                        if str(normalization_audit.get("status") or "").upper() == "OK_FX_NORMALIZED"
                        else "verified"
                    )
                    sources.add_once(
                        "Net Debt (valuation currency)",
                        _nd_norm,
                        "normalization_audit",
                        _nd_conf,
                    )
                if market_data.shares_outstanding is not None:
                    sources.add("Shares Outstanding", f"{market_data.shares_outstanding:.0f}M", "yfinance", "verified")
                if market_data.current_price is not None:
                    sources.add("Current Price", f"{_q_ccy} {market_data.current_price:.2f}", "yfinance", "verified")
                if market_data.sector:
                    sources.add_once("Sector", str(market_data.sector), "yfinance", "verified")
                if isinstance(market_data.additional_metadata, dict):
                    _ind = str(market_data.additional_metadata.get("industry") or "").strip()
                    if _ind:
                        sources.add_once("Industry", _ind, "yfinance", "verified")
                    _country = str(market_data.additional_metadata.get("country") or "").strip()
                    if _country:
                        sources.add_once("Country", _country, "yfinance", "verified")
                    _exch = str(market_data.additional_metadata.get("exchange") or "").strip()
                    if _exch:
                        sources.add_once("Exchange", _exch, "yfinance", "verified")
                    sources.add_once(
                        "Currency normalization",
                        (
                            f"status={normalization_audit.get('status')}; "
                            f"financials={normalization_audit.get('financial_statement_currency')}; "
                            f"quote={normalization_audit.get('quote_currency')}; "
                            f"share_basis={normalization_audit.get('share_count_basis')}; "
                            f"listing_type={normalization_audit.get('listing_type')}; "
                            f"selected={normalization_audit.get('selected_listing')}; "
                            f"primary={normalization_audit.get('primary_listing')}; "
                            f"adr_ratio={normalization_audit.get('adr_ratio') or 'unknown'}"
                        ),
                        "normalization_audit",
                        "verified" if not normalization_blocked else "inferred",
                    )
                    _fx_rate_used = market_data_valuation.additional_metadata.get("fx_rate_used_fin_to_quote") if (
                        market_data_valuation and isinstance(market_data_valuation.additional_metadata, dict)
                    ) else None
                    if _fx_rate_used:
                        try:
                            sources.add_once(
                                "FX Rate (financial->quote)",
                                f"{float(_fx_rate_used):.6f}",
                                str(normalization_audit.get("fx_source") or "normalization_audit"),
                                str(normalization_audit.get("fx_confidence") or "inferred"),
                                currency=str(normalization_audit.get("quote_currency") or ""),
                                unit=(
                                    f"{normalization_audit.get('quote_currency')} per "
                                    f"{normalization_audit.get('financial_statement_currency')}"
                                ),
                                as_of_date=str(normalization_audit.get("fx_timestamp") or ""),
                                is_fallback=bool(
                                    str(normalization_audit.get("fx_source") or "").startswith("static")
                                ),
                            )
                        except Exception:
                            pass
                    _div_yld = market_data.additional_metadata.get("dividend_yield")
                    _dy = _normalize_dividend_yield(_div_yld)
                    if _dy is not None:
                        sources.add_once("Dividend Yield", f"{_dy:.1%}", "yfinance", "verified")
                    elif _div_yld is not None:
                        sources.add_once(
                            "Dividend Yield",
                            "unavailable (yfinance field failed sanity check)",
                            "yfinance",
                            "inferred",
                        )
                    _div_rate = market_data.additional_metadata.get("dividend_rate")
                    if _div_rate is not None:
                        try:
                            _dr = float(_div_rate)
                            if _dr >= 0:
                                sources.add_once("Dividend Rate", f"{_q_ccy} {_dr:.2f}", "yfinance", "verified")
                        except Exception:
                            pass
                if market_data.fcf_ttm is not None and market_data.market_cap and market_data.market_cap > 0:
                    try:
                        _fcf_yield = float(market_data.fcf_ttm) / float(market_data.market_cap)
                        if _fcf_yield == _fcf_yield:
                            _fcf_yield_val = f"{_fcf_yield:.1%}"
                            if _md_country and _md_country not in {"united states", "usa", "us"}:
                                _fcf_yield_val += " (yfinance FCF basis)"
                            sources.add_once("FCF Yield on Market Cap", _fcf_yield_val, "derived_yfinance", "inferred")
                    except Exception:
                        pass
                if (
                    market_data.net_debt is not None
                    and market_data.ebitda_ttm is not None
                    and market_data.ebitda_ttm > 0
                ):
                    try:
                        _ndebt_ebitda = float(market_data.net_debt) / float(market_data.ebitda_ttm)
                        sources.add_once("Net Debt / EBITDA", f"{_ndebt_ebitda:.2f}x", "derived_yfinance", "inferred")
                    except Exception:
                        pass
                if (
                    market_data.ebit_ttm is not None
                    and market_data.interest_expense is not None
                    and market_data.interest_expense > 0
                ):
                    try:
                        _int_cov = float(market_data.ebit_ttm) / float(market_data.interest_expense)
                        sources.add_once("Interest Coverage", f"{_int_cov:.2f}x", "derived_yfinance", "inferred")
                    except Exception:
                        pass
                if (
                    market_data.fcf_ttm is not None
                    and market_data.market_cap
                    and market_data.market_cap > 0
                    and isinstance(market_data.additional_metadata, dict)
                ):
                    _dyf = _normalize_dividend_yield(market_data.additional_metadata.get("dividend_yield"))
                    if _dyf is not None and _dyf > 0:
                        try:
                            _div_cash = float(market_data.market_cap) * _dyf
                            if _div_cash > 0:
                                _div_cov = float(market_data.fcf_ttm) / _div_cash
                                if _div_cov > 0:
                                    sources.add_once(
                                        "Dividend Coverage",
                                        f"{_div_cov:.2f}x (verify payout basis)",
                                        "derived_yfinance",
                                        "inferred; payout basis may differ",
                                    )
                                else:
                                    sources.add_once(
                                        "Dividend Coverage",
                                        "unavailable (insufficient verified dividend cash paid)",
                                        "derived_yfinance",
                                        "inferred",
                                    )
                        except Exception:
                            pass
                    else:
                        sources.add_once(
                            "Dividend Coverage",
                            "unavailable (insufficient verified dividend cash paid)",
                            "derived_yfinance",
                            "inferred",
                        )
        log.end_step("market_data", t0)
        _done("Market Data", t0)

    # ── 1. FUNDAMENTALS ───────────────────────────────────────────────────
    t0 = _step("Fundamentals")
    try:
        fund = _parse_with_retry(
            data_agent,
            company,
            company_type,
            {"quick_mode": quick_mode, "cli_mode": cli_mode, "debug_retries": debug},
            Fundamentals,
            _fund_fallback(company),
            log_raw_errors=debug,
        )
    except APICapacityError:
        console.print("  [yellow]Fundamentals LLM unavailable; using deterministic fallback.[/yellow]")
        fund = _fund_fallback(company)
    if market_data:
        _fund_name = str(fund.company_name or "").strip()
        _input_name = str(company or "").strip()
        _ticker_name = str(market_data.ticker or "").strip()
        if market_data.company_name and (
            not _fund_name
            or _fund_name.upper() in {_ticker_name.upper(), _input_name.upper()}
            or _fund_name.lower() in {"unknown", "n/a", "na"}
        ):
            fund.company_name = market_data.company_name
        if not fund.ticker:
            fund.ticker = market_data.ticker
        if not fund.sector:
            fund.sector = market_data.sector
        _meta = market_data.additional_metadata if isinstance(market_data.additional_metadata, dict) else {}
        if _meta.get("date_of_creation") and not fund.founded:
            fund.founded = _meta.get("date_of_creation")
        _addr = _meta.get("registered_office_address") or {}
        _hq = ", ".join([x for x in [_addr.get("locality"), _addr.get("country")] if x])
        if _hq and not fund.headquarters:
            fund.headquarters = _hq
        if not fund.headquarters:
            _country = str(_meta.get("country") or "").strip()
            if _country:
                fund.headquarters = _country
        if (
            (not (fund.description or "").strip())
            or "description not available" in (fund.description or "").lower()
        ):
            _ind = str(_meta.get("industry") or "").strip()
            _ex = str(_meta.get("exchange") or "").strip()
            _country = str(_meta.get("country") or "").strip()
            _bits = [b for b in [_ind, _country, _ex] if b]
            if _bits:
                fund.description = f"{fund.company_name or market_data.company_name} is a publicly listed company ({' | '.join(_bits)})."
        _sic_details = _meta.get("sic_details") or []
        _sic_labels = [d.get("description") for d in _sic_details if d.get("description")]
        if _sic_labels and (
            "Not publicly disclosed" in (fund.business_model or "")
            or not (fund.business_model or "").strip()
        ):
            fund.business_model = " / ".join(_sic_labels[:2])
        _dir_count = _meta.get("director_count_active")
        if _dir_count is not None and (not fund.employees or fund.employees == "N/A"):
            fund.employees = f"Directors listed: {_dir_count}"
        if _meta.get("company_number"):
            sources.add_once("Company Number (GB)", str(_meta.get("company_number")), "companies_house", "verified")
        if _meta.get("sic_codes"):
            sources.add_once(
                "SIC Codes (GB)",
                ", ".join(_meta.get("sic_codes")[:4]),
                "companies_house",
                "verified",
            )
        if _meta.get("director_count_active") is not None:
            sources.add_once(
                "Directors (active)",
                str(_meta.get("director_count_active")),
                "companies_house",
                "verified",
            )
        if _meta.get("last_filing_date"):
            sources.add_once(
                "Latest Filing Date",
                str(_meta.get("last_filing_date")),
                "companies_house",
                "verified",
            )
        if _meta.get("filing_count_total") is not None:
            sources.add_once(
                "Filings Read (GB)",
                str(_meta.get("filing_count_total")),
                "companies_house",
                "verified",
            )
        if _meta.get("document_count_total") is not None:
            sources.add_once(
                "Documents Indexed (GB)",
                str(_meta.get("document_count_total")),
                "companies_house",
                "verified",
            )
        _soc = _meta.get("statement_of_capital") or {}
        if _soc.get("share_class"):
            sources.add_once("Share Class (GB)", str(_soc.get("share_class")), "companies_house", "verified")
        if _soc.get("total_shares") is not None:
            sources.add_once("Total Shares (GB)", f"{int(_soc.get('total_shares'))}", "companies_house", "verified")
        if _soc.get("aggregate_nominal_value") is not None:
            _cur = _soc.get("share_capital_currency") or "GBP"
            sources.add_once(
                "Aggregate Nominal Value (GB)",
                f"{_cur} {float(_soc.get('aggregate_nominal_value')):,.0f}",
                "companies_house",
                "verified",
            )
        if _soc.get("aggregate_unpaid") is not None:
            _cur = _soc.get("share_capital_currency") or "GBP"
            sources.add_once(
                "Aggregate Unpaid Capital (GB)",
                f"{_cur} {float(_soc.get('aggregate_unpaid')):,.0f}",
                "companies_house",
                "verified",
            )
    # Identity guardrail: if we only have registry identity (no verified business description),
    # avoid hallucinated business models from similarly named entities.
    if (
        company_type == "private"
        and company_identifier
        and market_data
        and market_data.data_source == "companies_house"
        and not market_data.revenue_ttm
    ):
        # For confirmed GB entity lookups, registry sector takes precedence over LLM guess.
        if market_data.sector:
            fund.sector = market_data.sector
        fund.company_name = market_data.company_name or fund.company_name
        fund.description = (
            f"UK registered private company (Companies House #{company_identifier}). "
            "Verified filings provide limited public business detail."
        )
        fund.business_model = (
            "Not publicly disclosed in verified filings; additional primary-source diligence required."
        )
    # Deterministic company-description fallback from verified market metadata.
    if market_data and isinstance(market_data.additional_metadata, dict):
        _bs = str(market_data.additional_metadata.get("business_summary") or "").strip()
        _desc = str(fund.description or "").strip()
        if _bs and (
            (not _desc)
            or ("not available" in _desc.lower())
            or (_desc.lower() in {"n/a", "na", "unknown"})
        ):
            fund.description = _bs
            sources.add_once("Company Description", "yfinance business summary", "yfinance", "verified")
    log.end_step("fundamentals", t0)
    _done("Fundamentals", t0)

    # ── 2+2b+3. MARKET / PEERS / FINANCIALS — parallel ────────────────────
    _parallel_t0 = time.time()
    _peer_rev = market_data.revenue_ttm if market_data and market_data.revenue_ttm else None
    _mega_cap_usd_m = _cfg.lbo.mega_cap_skip_usd_bn * 1000
    _skip_tx_comps = bool(
        company_type == "public"
        and market_data
        and market_data.market_cap
        and market_data.market_cap > _mega_cap_usd_m
    )
    _cancel_market = Event()
    _cancel_peers = Event()
    _cancel_fin = Event()
    _cancel_tx = Event()

    def _finish_step(name: str, started_at: float, cancel_event: Event, log_key: str | None = None) -> None:
        if cancel_event.is_set():
            return
        if log_key:
            log.end_step(log_key, started_at)
        _done(name, started_at)

    def _do_market():
        _t = _step("Market Analysis")
        if quick_mode:
            console.print("  [dim]Quick mode: skipping deep market analysis.[/dim]")
            _finish_step("Market Analysis", _t, _cancel_market, "market_analysis")
            return MarketAnalysis(), "skipped_quick_mode"
        try:
            result = _parse_with_retry(
                market_agent, company, company_type,
                {
                    "sector": fund.sector or "",
                    "description": fund.description,
                    "run_date": date.today().isoformat(),
                    "current_year": date.today().year,
                    "quick_mode": quick_mode,
                    "cli_mode": cli_mode,
                    "debug_retries": debug,
                    "max_queries": 5,
                    "max_results": 3,
                },
                MarketAnalysis, MarketAnalysis(),
                fatal_on_fail=True,
                retry_on_fail=(not quick_mode),
                log_raw_errors=debug,
            )
            status = "ok"
        except APICapacityError:
            if not _cancel_market.is_set():
                console.print("  [yellow]Market analysis LLM unavailable; using qualitative fallback.[/yellow]")
            result = MarketAnalysis()
            status = "degraded_api_capacity"
        except Exception as e:
            if not _cancel_market.is_set():
                console.print(
                    "  [yellow]Market analysis unavailable; using sector-profile fallback for thesis only "
                    f"({e}).[/yellow]"
                )
            result = MarketAnalysis()
            status = "failed"
        _step_name = "Market Analysis"
        if status in {"degraded_api_capacity", "failed"}:
            _step_name = "Market Analysis (fallback/partial)"
        _finish_step(_step_name, _t, _cancel_market, "market_analysis")
        return result, status

    def _do_peers():
        _t = _step("Peer Selection")
        if quick_mode:
            try:
                tickers = find_peers_deterministic_quick(
                    target_md=market_data,
                    target_sector=fund.sector or "",
                    target_industry=(
                        str((market_data.additional_metadata or {}).get("industry") or "")
                        if market_data and isinstance(market_data.additional_metadata, dict)
                        else ""
                    ),
                    target_peers=12,
                )
                result = ({"mode": "quick_deterministic", "tickers": tickers}, None)
            except Exception as e:
                result = (None, e)
            _finish_step("Peer Selection", _t, _cancel_peers, "peer_selection")
            console.print("  [dim]Peer selection complete; validating peer market data...[/dim]")
            return result
        if cli_mode:
            # Interactive CLI policy: deterministic peer core first, no LLM discovery latency.
            try:
                tickers = find_peers_deterministic_quick(
                    target_md=market_data,
                    target_sector=fund.sector or "",
                    target_industry=(
                        str((market_data.additional_metadata or {}).get("industry") or "")
                        if market_data and isinstance(market_data.additional_metadata, dict)
                        else ""
                    ),
                    target_peers=16,
                )
                result = ({"mode": "quick_deterministic", "tickers": tickers}, None)
            except Exception as e:
                result = (None, e)
            _finish_step("Peer Selection", _t, _cancel_peers, "peer_selection")
            console.print("  [dim]Peer selection complete; validating peer market data...[/dim]")
            return result
        try:
            raw = peer_agent.run(company, company_type, {
                "sector": fund.sector or "",
                "description": fund.description or "",
                "revenue_usd_m": _peer_rev,
                "quick_mode": quick_mode,
                "cli_mode": cli_mode,
                "debug_retries": debug,
                "max_queries": 5,
                "max_results": 3,
            })
            result = (raw, None)
        except APICapacityError as e:
            if not _cancel_peers.is_set():
                console.print("  [yellow]PeerFinder LLM unavailable; using deterministic peer fallback.[/yellow]")
            try:
                _fallback = find_peers_deterministic_quick(
                    target_md=market_data,
                    target_sector=fund.sector or "",
                    target_industry=(
                        str((market_data.additional_metadata or {}).get("industry") or "")
                        if market_data and isinstance(market_data.additional_metadata, dict)
                        else ""
                    ),
                    target_peers=16,
                )
            except Exception:
                _fallback = []
            result = ({"mode": "quick_deterministic", "tickers": _fallback}, e)
        except Exception as e:
            result = (None, e)
        _finish_step("Peer Selection", _t, _cancel_peers, "peer_selection")
        console.print("  [dim]Peer selection complete; validating peer market data...[/dim]")
        return result

    def _do_financials():
        _t = _step("Financials")
        try:
            if company_type == "private" and _private_screen_only:
                f = _fin_fallback()
                console.print(
                    "  [dim]Private screen-only mode: valuation-grade financial modeling skipped "
                    "(identity/revenue gate not satisfied).[/dim]"
                )
            elif market_data and market_data.revenue_ttm:
                f = _fin_from_market(market_data)
                if company_type == "private":
                    _meta = market_data.additional_metadata if isinstance(market_data.additional_metadata, dict) else {}
                    _f_ccy = str(
                        _meta.get("financial_currency")
                        or _meta.get("currency")
                        or "USD"
                    ).upper()
                    if not _re.match(r"^[A-Z]{3}$", _f_ccy):
                        _f_ccy = "USD"
                else:
                    _f_ccy = str(normalization_audit.get("financial_statement_currency") or "USD")
                console.print(
                    f"  [green]Using {market_data.data_source} financials "
                    f"(Rev={_f_ccy} {market_data.revenue_ttm:.0f}M)[/green]"
                )
            elif quick_mode:
                # Quick mode avoids deep/slow LLM financial modeling paths.
                f = _fin_fallback()
                console.print(
                    "  [dim]Quick mode: no verified revenue feed; using compact financial fallback.[/dim]"
                )
            else:
                f = _parse_with_retry(
                    fin_agent, company, company_type,
                    {
                        "sector": fund.sector or "",
                        "description": fund.description,
                        "quick_mode": quick_mode,
                        "cli_mode": cli_mode,
                        "debug_retries": debug,
                    },
                    Financials, _fin_fallback(),
                    retry_on_fail=(not quick_mode),
                    log_raw_errors=debug,
                )
                # Normalise "~$700M", "€700 million", "1.2B" → plain USD-millions string
                f.revenue_current = normalise_revenue_string(f.revenue_current)
        except APICapacityError:
            if not _cancel_fin.is_set():
                console.print("  [yellow]FinancialModeler LLM unavailable; using deterministic fallback.[/yellow]")
            f = _fin_fallback()
        except Exception as e:
            if not _cancel_fin.is_set():
                console.print(f"  [yellow]Financials fallback: {e}[/yellow]")
            f = _fin_fallback()
        _finish_step("Financials", _t, _cancel_fin, "financials")
        return f

    def _do_tx_comps():
        _t = _step("Transaction Comps")
        if quick_mode:
            _finish_step("Transaction Comps", _t, _cancel_tx, "tx_comps")
            return (None, None)
        try:
            import datetime
            raw = tx_agent.run(company, company_type, {
                "sector": fund.sector or "",
                "current_year": str(datetime.date.today().year),
                "quick_mode": quick_mode,
                "cli_mode": cli_mode,
                "debug_retries": debug,
                "max_queries": 5,
                "max_results": 3,
            })
            result = (raw, None)
        except APICapacityError as e:
            if not _cancel_tx.is_set():
                console.print("  [yellow]Transaction comps LLM unavailable; using cached/none fallback.[/yellow]")
            result = (None, e)
        except Exception as e:
            result = (None, e)
        _finish_step("Transaction Comps", _t, _cancel_tx, "tx_comps")
        return result

    market_analysis_failed = False
    peer_timeout_or_fail = False
    market_status = "OK"
    peers_status = "OK"
    valuation_status = "OK"
    _pool = ThreadPoolExecutor(max_workers=_cfg.agent.parallel_workers)
    try:
        _fut_mkt = _pool.submit(_do_market)
        _fut_peers = _pool.submit(_do_peers)
        _fut_fin = _pool.submit(_do_financials)
        _fut_tx = None if (_skip_tx_comps or quick_mode) else _pool.submit(_do_tx_comps)
        try:
            mkt, _mkt_status = _fut_mkt.result(timeout=_MARKET_ANALYSIS_TIMEOUT)
            if _mkt_status == "failed":
                market_analysis_failed = True
                market_status = "FAILED"
            elif _mkt_status == "degraded_api_capacity":
                market_status = "DEGRADED_API_CAPACITY"
            elif _mkt_status == "skipped_quick_mode":
                market_status = "SKIPPED_QUICK_MODE"
            else:
                mkt = _ensure_market_analysis_contract(mkt)
                market_status = "OK"
                _trend_vals = [str(t) for t in (mkt.key_trends or []) if str(t).strip()]
                _has_placeholder_only = bool(_trend_vals) and all(_trend_is_placeholder(t) for t in _trend_vals)
                if (
                    (_text_missing(mkt.market_size) and _text_missing(mkt.market_growth))
                    or (not _trend_vals)
                    or _has_placeholder_only
                ):
                    market_status = "DEGRADED"
                if str(mkt.data_status or "").upper() in {"PARTIAL", "FAILED"}:
                    market_status = "DEGRADED"
        except FutureTimeoutError:
            market_analysis_failed = True
            market_status = "TIMEOUT"
            mkt = MarketAnalysis()
            _cancel_market.set()
            _fut_mkt.cancel()
            console.print(
                "  [yellow]Market analysis unavailable after timeout; using sector-profile fallback for thesis only "
                f"(>{_MARKET_ANALYSIS_TIMEOUT}s).[/yellow]"
            )
        try:
            _peers_raw, _peers_err = _fut_peers.result(timeout=_PEER_COMPS_TIMEOUT)
            peer_timeout_or_fail = bool(_peers_err)
            if _peers_err:
                if isinstance(_peers_err, APICapacityError) or is_api_capacity_error(_peers_err):
                    peers_status = "DEGRADED_API_CAPACITY"
                else:
                    peers_status = "FAILED"
            else:
                peers_status = "OK"
        except FutureTimeoutError:
            _peers_raw, _peers_err = None, TimeoutError("peer timeout")
            peer_timeout_or_fail = True
            peers_status = "TIMEOUT"
            _cancel_peers.set()
            _fut_peers.cancel()
            console.print(f"  [red]Peer comparables failed: timeout > {_PEER_COMPS_TIMEOUT}s[/red]")
        try:
            fin = _fut_fin.result(timeout=_FINANCIALS_TIMEOUT)
        except FutureTimeoutError:
            _cancel_fin.set()
            _fut_fin.cancel()
            console.print(f"  [yellow]Financials timeout > {_FINANCIALS_TIMEOUT}s — using fallback.[/yellow]")
            fin = _fin_fallback()
        if _fut_tx is not None:
            try:
                _tx_raw, _tx_err = _fut_tx.result(timeout=_TX_COMPS_TIMEOUT)
            except FutureTimeoutError:
                _tx_raw, _tx_err = None, TimeoutError("tx comps timeout")
                _cancel_tx.set()
                _fut_tx.cancel()
                console.print(f"  [yellow]Transaction comps timeout > {_TX_COMPS_TIMEOUT}s — skipped.[/yellow]")
        else:
            _tx_raw, _tx_err = None, None
            console.rule("[bold cyan]Transaction Comps")
            if quick_mode:
                console.print("  [dim]Skipped in quick mode (tx comps disabled).[/dim]")
            else:
                console.print("  [dim]Skipped for mega-cap public company (tx weight forced to 0%).[/dim]")
            log.step_times["tx_comps"] = 0.0
            _done("Transaction Comps", time.time())
    finally:
        # Best-effort cancellation: do not block on timed-out side tasks.
        _pool.shutdown(wait=False, cancel_futures=True)

    # Override LLM-derived financials with registry-verified values when available
    fin = _reconcile_financials(fin, market_data, console)
    # Public display policy: prefer verified forward growth from market data.
    if (
        company_type == "public"
        and market_data
        and market_data.forward_revenue_growth is not None
    ):
        fin.revenue_growth = f"{market_data.forward_revenue_growth:+.1%}"
        sources.add_once(
            "Revenue Growth",
            f"{market_data.forward_revenue_growth:+.1%}",
            "yfinance",
            "verified",
        )
    # Strict provenance policy for private screen-only entities:
    # do not surface unsourced LLM financial metrics as factual numbers.
    _strict_registry_mode = bool(
        company_type == "private"
        and (
            _private_screen_only
            or (
                company_identifier
                and market_data
                and market_data.data_source == "companies_house"
                and not market_data.revenue_ttm
            )
        )
    )
    if _strict_registry_mode:
        fin.revenue_current = "N/A"
        fin.revenue_series = []
        fin.revenue_growth = "Not available [screen-only: non-valuation-grade]"
        fin.gross_margin = "Not available [screen-only: non-valuation-grade]"
        fin.ebitda_margin = "Not available [screen-only: non-valuation-grade]"
        fin.free_cash_flow = "Not available [screen-only: non-valuation-grade]"
        fin.net_margin = "Not available [screen-only: non-valuation-grade]"
    if company_type == "private":
        _has_provider_payload = bool(market_data and str(market_data.data_source or "").strip())
        if _has_provider_payload and _private_revenue_quality in {"VERIFIED", "HIGH_CONFIDENCE_ESTIMATE"}:
            _private_provider_state = "OK"
        elif _has_provider_payload or _private_missing_provider_notes:
            _private_provider_state = "PARTIAL"
        else:
            _private_provider_state = "FAILED"
        if _private_revenue_quality == "VERIFIED":
            _private_financials_quality = "VERIFIED"
        elif _private_revenue_quality == "MANUAL":
            _private_financials_quality = "ESTIMATED"
        elif _private_revenue_quality in {"HIGH_CONFIDENCE_ESTIMATE", "LOW_CONFIDENCE_ESTIMATE"}:
            _private_financials_quality = "ESTIMATED"
        else:
            _private_financials_quality = "UNAVAILABLE"
        if (country_hint or "").upper() == "DE" and _private_identity_status != "RESOLVED":
            console.print(
                "  [yellow]German registry identity unresolved; use a legal-entity identifier "
                "or provide verified revenue manually. Run remains screen-only.[/yellow]"
            )
            sources.add_once(
                "Private Identity Limitation",
                "German registry identity unresolved; use manual legal entity identifier or verified revenue input.",
                "identity_resolution_guard",
                "inferred",
            )

    _parallel_elapsed = time.time() - _parallel_t0
    if debug:
        console.print(
            f"  [dim]Parallel agents: {_parallel_elapsed:.1f}s (≈3× faster than sequential)[/dim]"
        )
    else:
        console.print(f"  [dim]Research agents completed in {_parallel_elapsed:.1f}s[/dim]")
    if (not quick_mode) and company_type == "public" and market_data:
        _mc_industry = ""
        if isinstance(market_data.additional_metadata, dict):
            _mc_industry = str(market_data.additional_metadata.get("industry") or "")
        try:
            market_context_pack = build_market_context_pack(
                company=company,
                ticker=(market_data.ticker or log.ticker or ""),
                sector=fund.sector or "",
                industry=_mc_industry,
                filings_pack=filings_pack,
            )
        except Exception:
            market_context_pack = None
        if market_context_pack:
            _ctx_urls = [x.url for x in [*market_context_pack.trends, *market_context_pack.catalysts, *market_context_pack.risks] if str(x.url or "").startswith("http")]
            if _ctx_urls:
                mkt.sources = list(dict.fromkeys([*(mkt.sources or []), *_ctx_urls]))
            if market_context_pack.source_backed:
                _ctx_lines: list[str] = []
                _ctx_lines.extend([f"Trend: {x.text}" for x in market_context_pack.trends if x.text][:2])
                _ctx_lines.extend([f"Catalyst: {x.text}" for x in market_context_pack.catalysts if x.text][:2])
                _ctx_lines.extend([f"Risk: {x.text}" for x in market_context_pack.risks if x.text][:2])
                if _ctx_lines:
                    if not [str(t).strip() for t in (mkt.key_trends or []) if str(t).strip()]:
                        mkt.key_trends = _ctx_lines
                    else:
                        mkt.key_trends = list(dict.fromkeys([*(mkt.key_trends or []), *_ctx_lines]))[:6]
                if str(mkt.source_quality or "").lower() in {"", "low"}:
                    mkt.source_quality = "medium"
                if str(mkt.data_status or "").upper() == "FAILED":
                    mkt.data_status = "PARTIAL"
            if market_context_pack.source_count:
                sources.add_once(
                    "Market Context Sources",
                    f"{int(market_context_pack.relevant_source_count or market_context_pack.source_count)} relevant / "
                    f"{int(market_context_pack.fetched_source_count or market_context_pack.source_count)} fetched",
                    ("market_context_source_backed" if market_context_pack.source_backed else "market_context_fallback"),
                    ("verified" if market_context_pack.source_backed else "estimated"),
                )
            for i, it in enumerate((market_context_pack.trends or [])[:2], start=1):
                sources.add_once(
                    f"Market Trend {i}",
                    it.text,
                    it.source,
                    it.confidence,
                    it.url or "",
                    as_of_date=it.date or "",
                )
            for i, it in enumerate((market_context_pack.catalysts or [])[:2], start=1):
                sources.add_once(
                    f"Market Catalyst {i}",
                    it.text,
                    it.source,
                    it.confidence,
                    it.url or "",
                    as_of_date=it.date or "",
                )
            for i, it in enumerate((market_context_pack.risks or [])[:2], start=1):
                sources.add_once(
                    f"Market Risk {i}",
                    it.text,
                    it.source,
                    it.confidence,
                    it.url or "",
                    as_of_date=it.date or "",
                )
            if market_context_pack.fallback_used:
                sources.add_once(
                    "Market Context Mode",
                    "Fallback Market Context — sector profile only, not source-backed; not used in valuation.",
                    "sector_profile_fallback",
                    "inferred",
                )
    _market_context_backed = bool(
        market_context_pack is not None
        and bool(market_context_pack.source_backed)
        and int(market_context_pack.relevant_source_count or 0) >= 2
    )
    if company_type == "private":
        # Private runs should not be marked source-backed from public-style market URLs.
        _market_source_backed = _market_context_backed
    else:
        _market_source_backed = bool(
            _has_source_backed_market_data(mkt)
            or _market_context_backed
        )
    if (not quick_mode) and market_status == "OK" and (not _market_source_backed):
        market_status = "DEGRADED"
    if market_status == "SKIPPED_QUICK_MODE":
        _research_source = "skipped"
        _research_depth = "none"
    else:
        _research_source = "source_backed" if _market_source_backed else "fallback"
        _research_depth = "full" if _research_source == "source_backed" else "limited"

    def _fmt_elapsed_for_status(value: object) -> str | None:
        try:
            _v = float(value)
        except Exception:
            return None
        if _v < 0:
            return None
        return f"{_v:.2f}s"

    if (not quick_mode) and _research_source == "fallback":
        _ma_t = log.step_times.get("market_analysis")
        _ma_txt = _fmt_elapsed_for_status(_ma_t)
        if _ma_txt:
            console.print(
                f"  [dim]Market analysis attempted: {_ma_txt}; source-backed context unavailable, fallback used.[/dim]"
            )

    # Post-process peer results (yfinance calls — sequential is fine)
    _peer_post_t0 = time.time()
    # target_sector comes from Fundamentals agent output for sector validation
    _target_sector = fund.sector or "" if fund else ""
    _target_industry = ""
    if market_data and isinstance(market_data.additional_metadata, dict):
        _target_industry = str(market_data.additional_metadata.get("industry") or "")
    if company_type == "private":
        _arch = detect_company_archetype(
            company=company,
            ticker=(market_data.ticker if market_data else ""),
            sector=_target_sector,
            industry=_target_industry,
        )
        _arch_sector = _archetype_sector_display(
            archetype=_arch,
            profile_label=_target_sector,
            sector=_target_industry,
        )
        _arch_segment = _archetype_market_segment(_arch)
        if _arch_sector and _arch_sector.lower() != "default fallback":
            _target_sector = _arch_sector
        if not _target_industry and _arch_segment:
            _target_industry = _arch_segment
    peer_comps_table: PeerCompsTable | None = None
    peer_multiples: PeerMultiples | None = None
    _missing_consumer_ecosystem_bucket = False
    # Always start from deterministic peer engine; full-mode LLM results can only enrich.
    try:
        _deterministic_base = find_peers_deterministic_quick(
            target_md=market_data,
            target_sector=_target_sector,
            target_industry=_target_industry,
            target_peers=16,
        )
    except Exception:
        _deterministic_base = []
    if company_type == "private" and not _deterministic_base:
        _private_arch = detect_company_archetype(
            company=company,
            ticker=(market_data.ticker if market_data else ""),
            sector=fund.sector or "",
            industry=_target_industry,
        )
        _deterministic_base = _private_archetype_peer_hints(_private_arch)
        if _deterministic_base:
            console.print(
                "  [dim]Private peer hint set from archetype "
                f"({_private_arch}): {', '.join(_deterministic_base[:6])}[/dim]"
            )

    if _peers_raw or _deterministic_base:
        try:
            peer_tickers_seed: list[str] = []
            if isinstance(_peers_raw, dict) and _peers_raw.get("mode") == "quick_deterministic":
                peer_tickers_seed = [str(t).upper() for t in (_peers_raw.get("tickers") or [])]
            elif _peers_raw:
                peer_list = parse_peer_agent_output(_peers_raw)
                peer_tickers_seed = resolve_peer_tickers(peer_list)
            _is_mega_tech = bool(
                market_data
                and market_data.market_cap
                and market_data.market_cap > _mega_cap_usd_m
                and any(tok in (fund.sector or "").lower() for tok in ("technology", "tech", "software", "semiconductor"))
            )
            _self_ticker = (market_data.ticker or "").upper() if market_data else ""
            _aapl_reserve_core = ["MSFT", "ORCL", "CSCO", "NVDA", "AVGO", "MU"]
            _aapl_reserve_full = _aapl_reserve_core + ["INTC"]

            # Core peer set from deterministic engine for BOTH quick and full.
            peer_tickers = [t for t in _deterministic_base if t and t != _self_ticker]
            if _self_ticker == "AAPL":
                # Keep quick/full Apple fallback stable across runs.
                _base_reserve = _aapl_reserve_core if quick_mode else _aapl_reserve_full
                peer_tickers = _base_reserve + [t for t in peer_tickers if t not in _base_reserve]
            # Optional full-mode enrichment: merge validated LLM candidates (no overwrite).
            if (not quick_mode) and peer_tickers_seed:
                peer_tickers = list(dict.fromkeys(peer_tickers + [t for t in peer_tickers_seed if t and t != _self_ticker]))
            if _self_ticker == "AAPL" and len(peer_tickers) < 5:
                # Peer-set stability guard: force-add reserve peers before valuation.
                peer_tickers = list(dict.fromkeys(peer_tickers + (_aapl_reserve_full if not quick_mode else _aapl_reserve_core)))
            if quick_mode:
                # Keep quick mode fast and stable.
                if _self_ticker == "AAPL":
                    peer_tickers = [t for t in _aapl_reserve_core if t != _self_ticker]
                else:
                    peer_tickers = peer_tickers[:6]

            sources.add_once(
                "Peer Selection Policy",
                "Deterministic core peers (sector/industry/size) + optional full-mode LLM enrichment",
                "peer_policy",
                "verified",
            )
            if peer_tickers:
                peer_multiples = build_peer_multiples(
                    peer_tickers,
                    target_sector=_target_sector,
                    target_industry=_target_industry,
                    target_market_cap=(market_data.market_cap if market_data else None),
                    target_ticker=(market_data.ticker if market_data else ""),
                    target_company_name=(market_data.company_name if market_data else ""),
                    target_country=(
                        str(market_data.additional_metadata.get("country") or "")
                        if (market_data and isinstance(market_data.additional_metadata, dict))
                        else ""
                    ),
                    target_primary_listing=(
                        str(market_data.additional_metadata.get("primary_listing_symbol") or "")
                        if (market_data and isinstance(market_data.additional_metadata, dict))
                        else ""
                    ),
                    target_underlying_symbol=(
                        str(market_data.additional_metadata.get("underlying_symbol") or "")
                        if (market_data and isinstance(market_data.additional_metadata, dict))
                        else ""
                    ),
                    min_similarity=(0.40 if _is_mega_tech else 0.0),
                    target_ebitda_margin=(market_data.ebitda_margin if market_data else None),
                    target_growth=(market_data.forward_revenue_growth if market_data else None),
                    min_market_cap_ratio=(0.05 if _is_mega_tech else 0.0),
                    min_valuation_peers=((5 if quick_mode and _self_ticker == "AAPL" else (3 if quick_mode else 5)) if _is_mega_tech else 3),
                    max_return_peers=(8 if quick_mode else 10),
                )
                if _self_ticker == "AAPL" and peer_multiples.n_valuation_peers < 5:
                    _expanded = list(dict.fromkeys(peer_tickers + _aapl_reserve_full))
                    peer_multiples = build_peer_multiples(
                        _expanded,
                        target_sector=_target_sector,
                        target_industry=_target_industry,
                        target_market_cap=(market_data.market_cap if market_data else None),
                        target_ticker=(market_data.ticker if market_data else ""),
                        target_company_name=(market_data.company_name if market_data else ""),
                        target_country=(
                            str(market_data.additional_metadata.get("country") or "")
                            if (market_data and isinstance(market_data.additional_metadata, dict))
                            else ""
                        ),
                        target_primary_listing=(
                            str(market_data.additional_metadata.get("primary_listing_symbol") or "")
                            if (market_data and isinstance(market_data.additional_metadata, dict))
                            else ""
                        ),
                        target_underlying_symbol=(
                            str(market_data.additional_metadata.get("underlying_symbol") or "")
                            if (market_data and isinstance(market_data.additional_metadata, dict))
                            else ""
                        ),
                        min_similarity=0.30,
                        target_ebitda_margin=(market_data.ebitda_margin if market_data else None),
                        target_growth=(market_data.forward_revenue_growth if market_data else None),
                        min_market_cap_ratio=(0.05 if _is_mega_tech else 0.0),
                        min_valuation_peers=((5 if quick_mode and _self_ticker == "AAPL" else (3 if quick_mode else 5)) if _is_mega_tech else 3),
                        max_return_peers=(10 if quick_mode else 12),
                    )
                # Log validation summary
                drops: list[str] = []
                if peer_multiples.n_dropped_no_data:
                    drops.append(f"{peer_multiples.n_dropped_no_data} not found")
                if peer_multiples.n_dropped_sector:
                    drops.append(f"{peer_multiples.n_dropped_sector} wrong sector")
                if peer_multiples.n_dropped_sanity:
                    drops.append(f"{peer_multiples.n_dropped_sanity} bad multiples")
                if peer_multiples.n_dropped_scale:
                    drops.append(f"{peer_multiples.n_dropped_scale} too small for scale")
                if peer_multiples.n_dropped_bucket:
                    drops.append(f"{peer_multiples.n_dropped_bucket} dropped by bucket balance")
                if peer_multiples.n_dropped_same_issuer:
                    drops.append(f"{peer_multiples.n_dropped_same_issuer} alternate listings dropped")
                drop_note = f"  [dim](dropped: {', '.join(drops)})[/dim]" if drops else ""

                if peer_multiples.n_valuation_peers > 0:
                    _eff_req = 3.0 if quick_mode else 5.0
                    if peers_status in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}:
                        peers_status = "DEGRADED"
                    if peers_status == "OK" and peer_multiples.n_valuation_peers < 5:
                        peers_status = "DEGRADED"
                    if peers_status == "OK" and peer_multiples.effective_peer_count and peer_multiples.effective_peer_count < _eff_req:
                        peers_status = "DEGRADED"
                    if _is_mega_tech and peer_multiples.n_valuation_peers < 5:
                        console.print(
                            f"  [yellow]Peer set expanded (adjacent/global stages used): "
                            f"{peer_multiples.n_valuation_peers} valuation peers "
                            f"({peer_multiples.n_qualitative_peers} qualitative); confidence reduced.[/yellow]"
                        )
                    if peer_multiples.effective_peer_count:
                        console.print(
                            f"  [dim]Effective peer count: {peer_multiples.effective_peer_count:.2f}"
                            f" (target ≥{_eff_req:.0f} for {'quick' if quick_mode else 'full'} mode)[/dim]"
                        )
                    _show_n = min(6, peer_multiples.n_peers)
                    _shown = ", ".join(p.ticker for p in peer_multiples.peers[:_show_n])
                    if peer_multiples.n_peers > _show_n:
                        console.print(
                            f"  [cyan]{peer_multiples.n_peers} validated peers: "
                            f"{peer_multiples.n_valuation_peers} valuation peers, "
                            f"{peer_multiples.n_qualitative_peers} qualitative peer(s) "
                            f"(top {_show_n} shown)[/cyan] "
                            + _shown + drop_note
                        )
                    else:
                        console.print(
                            f"  [cyan]{peer_multiples.n_peers} validated peers: "
                            f"{peer_multiples.n_valuation_peers} valuation peers, "
                            f"{peer_multiples.n_qualitative_peers} qualitative peer(s)[/cyan] "
                            + _shown + drop_note
                        )
                    _bucket_counts: dict[str, int] = {}
                    for _p in peer_multiples.peers:
                        _b = _p.bucket or "other"
                        _bucket_counts[_b] = _bucket_counts.get(_b, 0) + 1
                    if _bucket_counts:
                        _mix = ", ".join(f"{k}={v}" for k, v in sorted(_bucket_counts.items(), key=lambda kv: kv[0]))
                        console.print(f"  [dim]Peer mix by business model bucket: {_mix}[/dim]")
                        console.print(
                            f"  [dim]Pure peer weight: {float(peer_multiples.pure_peer_weight_share or 0.0):.1%} | "
                            f"Adjacent peer weight: {float(peer_multiples.adjacent_peer_weight_share or 0.0):.1%}[/dim]"
                        )
                        _consumer_cnt = _bucket_counts.get("consumer_hardware_ecosystem", 0)
                        if _is_mega_tech and (
                            _consumer_cnt < 1 or float(peer_multiples.pure_peer_weight_share or 0.0) <= 0.001
                        ):
                            _missing_consumer_ecosystem_bucket = True
                            console.print(
                                "  [yellow]Apple-like mega-cap consumer-hardware peers are limited; "
                                "trading comps are adjacent reference multiples, not direct comparable valuation.[/yellow]"
                            )
                            sources.add_once(
                                "Peer Bucket Coverage",
                                "Consumer-hardware ecosystem peers limited at mega-cap scale; adjacent reference peers used",
                                "peer_policy",
                                "inferred",
                            )
                        if detect_sector_profile(_target_sector, _target_industry) == "consumer_staples_tobacco":
                            console.print(
                                "  [yellow]Tobacco valuation uses core nicotine peers plus adjacent consumer-staples references; "
                                "adjacent weights are capped to preserve peer purity.[/yellow]"
                            )
                    if debug and peer_multiples.excluded_details:
                        console.print("  [dim]Excluded peers (debug):[/dim]")
                        for _ex in peer_multiples.excluded_details[:12]:
                            console.print(f"  [dim]- {_ex}[/dim]")
                    for _p in peer_multiples.peers[:8]:
                        _sim = float(_p.similarity or 0.0)
                        if debug:
                            _mcap_b = (_p.market_cap / 1000.0) if _p.market_cap else None
                            _mcap_txt = f"{_mcap_b:.1f}B" if _mcap_b is not None else "N/A"
                            console.print(
                                f"  [dim]Peer {_p.ticker}: EV/EBITDA={_p.ev_ebitda:.1f}x "
                                f"MCap={_mcap_txt} (similarity {_sim:.2f}, "
                                f"business {float(_p.business_similarity or 0.0):.2f}, "
                                f"scale {float(_p.scale_similarity or 0.0):.2f}, "
                                f"role={_p.role or 'n/a'})[/dim]"
                                if _p.ev_ebitda
                                else (
                                    f"  [dim]Peer {_p.ticker}: MCap={_mcap_txt} similarity {_sim:.2f} "
                                    f"business {float(_p.business_similarity or 0.0):.2f} "
                                    f"scale {float(_p.scale_similarity or 0.0):.2f} "
                                    f"role={_p.role or 'n/a'}[/dim]"
                                )
                            )
                        sources.add_once(
                            f"Peer {_p.ticker} Similarity",
                            f"{_sim:.2f}",
                            "peer_similarity_model",
                            "inferred",
                        )
                    if peer_multiples.ev_ebitda_median:
                        _raw = peer_multiples.ev_ebitda_raw_median
                        _wtd = peer_multiples.ev_ebitda_weighted
                        _app = peer_multiples.ev_ebitda_median
                        _line = "  EV/EBITDA anchors: "
                        if _raw is not None:
                            _line += f"raw median {_raw:.1f}x; "
                        if _wtd is not None:
                            _line += f"weighted {_wtd:.1f}x; "
                        _line += f"applied {_app:.1f}x"
                        if peer_multiples.ev_revenue_median:
                            _line += f"  | EV/Rev {peer_multiples.ev_revenue_median:.1f}x"
                        console.print(_line)
                        if _raw is not None:
                            sources.add_once(
                                "Peer EV/EBITDA raw median",
                                f"{_raw:.1f}x",
                                "yfinance_peers",
                                "verified",
                            )
                        if _wtd is not None:
                            sources.add_once(
                                "Peer EV/EBITDA weighted",
                                f"{_wtd:.1f}x",
                                "yfinance_peers",
                                "verified",
                            )
                        sources.add_once(
                            "Peer EV/EBITDA applied",
                            f"{_app:.1f}x",
                            "weighted_validated_peers",
                            "verified",
                        )
                    for _p in peer_multiples.peers[:8]:
                        if _p.ev_ebitda:
                            sources.add_once(
                                f"Peer {_p.ticker} EV/EBITDA",
                                f"{_p.ev_ebitda:.1f}x",
                                "yfinance",
                                "verified",
                            )
                    sources.add_once(
                        "Peer set timestamp",
                        date.today().isoformat(),
                        "system_clock",
                        "verified",
                    )
                    _peer_quote_ccy = str(
                        normalization_audit.get("quote_currency")
                        or getattr(market_data_valuation, "quote_currency", None)
                        or getattr(market_data_valuation, "currency", None)
                        or getattr(market_data, "quote_currency", None)
                        or getattr(market_data, "currency", None)
                        or "USD"
                    ).upper()
                    if not _re.match(r"^[A-Z]{3}$", _peer_quote_ccy):
                        _peer_quote_ccy = "USD"
                    peer_comps_table = PeerCompsTable(
                        peers=[
                            PeerComp(
                                name=p.name,
                                ticker=p.ticker,
                                bucket=p.bucket,
                                role=p.role,
                                market_cap=(
                                    _fmt_money_millions(
                                        float(p.market_cap),
                                        (
                                            p.quote_currency
                                            if getattr(p, "quote_currency", None)
                                            else _peer_quote_ccy
                                        ),
                                    )
                                    if p.market_cap
                                    else None
                                ),
                                ev_ebitda=f"{p.ev_ebitda:.1f}x" if p.ev_ebitda else None,
                                ev_revenue=f"{p.ev_revenue:.1f}x" if p.ev_revenue else None,
                                ebitda_margin=f"{p.ebitda_margin:.1%}" if p.ebitda_margin else None,
                                revenue_growth=f"{p.revenue_growth:+.1%}" if p.revenue_growth else None,
                                similarity=f"{(p.similarity or 0):.2f}",
                                business_similarity=f"{(p.business_similarity or 0):.2f}",
                                scale_similarity=f"{(p.scale_similarity or 0):.2f}",
                                weight=f"{(p.weight or 0):.2f}",
                                include_reason=p.include_reason,
                            )
                            for p in peer_multiples.peers
                        ],
                        median_ev_ebitda=(
                            f"{peer_multiples.ev_ebitda_median:.1f}x"
                            if peer_multiples.ev_ebitda_median else None
                        ),
                        median_ev_revenue=(
                            f"{peer_multiples.ev_revenue_median:.1f}x"
                            if peer_multiples.ev_revenue_median else None
                        ),
                        median_ebitda_margin=(
                            f"{peer_multiples.ebitda_margin_median:.1%}"
                            if peer_multiples.ebitda_margin_median else None
                        ),
                        n_peers=peer_multiples.n_peers,
                    )
                else:
                    peers_status = "FAILED"
                    console.print(
                        f"  [yellow]Peer comps: no usable valuation peers{drop_note} "
                        f"— confidence reduced[/yellow]"
                    )
                    if _is_mega_tech and peer_multiples and peer_multiples.n_valuation_peers < 5:
                        console.print(
                            f"  [yellow]Insufficient comps for mega-cap policy: "
                            f"{peer_multiples.n_valuation_peers} valuation peers (<5).[/yellow]"
                        )
        except Exception as e:
            peers_status = "FAILED"
            console.print(f"  [yellow]Peer post-processing skipped: {e}[/yellow]")
    elif _peers_err:
        if isinstance(_peers_err, APICapacityError) or is_api_capacity_error(_peers_err):
            peers_status = "DEGRADED_API_CAPACITY"
            console.print("  [yellow]PeerFinder LLM unavailable; using deterministic peer fallback.[/yellow]")
        else:
            peers_status = "FAILED"
            console.print(f"  [yellow]Peer finder skipped: {_peers_err}[/yellow]")
    if _missing_consumer_ecosystem_bucket and peers_status == "OK":
        peers_status = "OK_ADJACENT"
    if company_type == "private":
        if peer_multiples and (peer_multiples.n_valuation_peers or 0) >= 3:
            _private_peers_state = "OK"
        elif peer_multiples and (peer_multiples.n_peers or 0) > 0:
            _private_peers_state = "WEAK"
        else:
            _private_peers_state = "FAILED"
    _peer_post_elapsed = round(time.time() - _peer_post_t0, 2)
    _done("Peer Validation", _peer_post_t0)
    try:
        _peer_sel = float(log.step_times.get("peer_selection") or 0.0)
        log.step_times["peer_validation"] = _peer_post_elapsed
        log.step_times["peers"] = round(_peer_sel + _peer_post_elapsed, 2)
    except Exception:
        pass

    # Post-process transaction comps — cache + extract medians
    _tx_medians: dict = {}
    _tx_source_verified = False
    if _tx_raw:
        try:
            new_comps = parse_tx_output(_tx_raw, fund.sector or "")
            if new_comps:
                all_comps = add_comps(new_comps)
                _tx_medians = sector_medians(all_comps, fund.sector or "")
                _tx_source_verified = bool((_tx_medians.get("n_deals") or 0) >= 3)
                _retained = int(_tx_medians.get("n_deals") or 0)
                _median_txt = (
                    f"EV/EBITDA median {_tx_medians['ev_ebitda_median']:.1f}x"
                    if _tx_medians.get("ev_ebitda_median")
                    else ""
                )
                console.print(
                    f"  [cyan]Transaction comps:[/cyan] {len(new_comps)} new deals found; "
                    f"{_retained} retained after sector filter"
                    + (f"; {_median_txt}" if _median_txt else "")
                )
                if not _tx_source_verified and _retained > 0:
                    console.print(
                        "  [dim]Transaction comps used as reference only; "
                        "not included in blended valuation.[/dim]"
                    )
            else:
                # Use cached comps for the sector even if agent returned nothing usable
                _tx_medians = sector_medians(load_cache(), fund.sector or "")
                _tx_source_verified = bool((_tx_medians.get("n_deals") or 0) >= 3)
                if _tx_medians.get("n_deals"):
                    _n_cached = int(_tx_medians["n_deals"])
                    _deal_word = "deal" if _n_cached == 1 else "deals"
                    console.print(
                        f"  [dim]Transaction comps: {_n_cached} sector-relevant cached {_deal_word} "
                        "used as reference only; not included in blended valuation.[/dim]"
                    )
        except Exception as e:
            console.print(f"  [yellow]Transaction comps post-processing skipped: {e}[/yellow]")
    elif _tx_err:
        if isinstance(_tx_err, APICapacityError) or is_api_capacity_error(_tx_err):
            console.print("  [yellow]Transaction comps LLM unavailable; using cached fallback.[/yellow]")
        else:
            console.print(f"  [yellow]Transaction comps agent skipped: {_tx_err}[/yellow]")
        _tx_medians = sector_medians(load_cache(), fund.sector or "")
        _tx_source_verified = bool((_tx_medians.get("n_deals") or 0) >= 3)

    if _tx_source_verified:
        sources.add_once(
            "Transaction Comps Data Quality",
            f"verified deals={int(_tx_medians.get('n_deals') or 0)}",
            "transaction_comps_cache",
            "verified",
        )
    else:
        sources.add_once(
            "Transaction Comps Data Quality",
            "source not logged or insufficient verified deals",
            "transaction_comps_cache",
            "inferred",
        )

    # ── 4. ASSUMPTIONS ────────────────────────────────────────────────────
    # Policy: valuation assumptions used by the deterministic engine must be reproducible.
    # We therefore avoid LLM-generated numeric assumptions by default (WACC/TG/weights).
    # The LLM remains free to produce qualitative rationales in the thesis layer.
    assumptions = ValuationAssumptions()

    # Archetype-aware market-segment backfill so known business models do not
    # fail market-segment checks purely due sparse market-analysis text.
    _archetype_for_market = detect_company_archetype(
        company=company,
        ticker=(market_data.ticker if market_data else ""),
        sector=fund.sector or "",
        industry=_target_industry,
    )
    if _text_missing(mkt.market_segment):
        _seg = _archetype_market_segment(_archetype_for_market)
        if _seg:
            mkt.market_segment = _seg
            if not mkt.market_segments:
                mkt.market_segments = [_seg]
            mkt = _ensure_market_analysis_contract(mkt)

    # ── 5. VALUATION ENGINE ───────────────────────────────────────────────
    t0 = _step("Valuation Engine")
    _dq_profile = detect_sector_profile(fund.sector or "", _target_industry)
    _market_context_optional = _dq_profile in {
        "consumer_staples_tobacco",
        "consumer_staples_beverages",
        "consumer_staples_household",
        "financials_banks",
        "financials_insurance",
        "utilities",
        "energy_oil_gas",
        "materials_chemicals_mining",
        "real_estate_reit",
    }
    quality = assess_data_quality(
        company_type=company_type,
        market_data=market_data,
        financials=fin.model_dump(),
        market_analysis=mkt.model_dump(),
        market_context_optional=_market_context_optional,
        proxy_growth_used=bool(
            market_data
            and market_data.forward_revenue_growth is not None
            and market_data.forward_revenue_1y is None
        ),
        peer_count=(peer_multiples.n_valuation_peers if peer_multiples else 0),
        market_analysis_failed=market_analysis_failed,
        market_analysis_degraded=(market_status == "DEGRADED"),
        market_analysis_skipped_quick=(market_status == "SKIPPED_QUICK_MODE"),
    )
    _is_mega_cap_quality = bool(
        company_type == "public"
        and market_data
        and market_data.market_cap
        and market_data.market_cap > _mega_cap_usd_m
    )
    _peer_count = peer_multiples.n_valuation_peers if peer_multiples else 0
    _effective_peer_count = float(peer_multiples.effective_peer_count) if peer_multiples else 0.0
    if _is_mega_cap_quality and _peer_count < 1:
        if quality.score > 75:
            quality.score = 75
            quality.tier = "B"
        quality.warnings.append(
            "Public mega-cap without usable comps — quality score capped."
        )
    elif _is_mega_cap_quality and _peer_count < 3:
        if quality.score > 68:
            quality.score = 68
            quality.tier = "C"
        quality.warnings.append(
            "Public mega-cap peer set below core threshold (<3) — confidence reduced."
        )
    elif _is_mega_cap_quality and _peer_count < 5:
        if quality.score > 70:
            quality.score = 70
            quality.tier = "C"
        quality.warnings.append(
            "Public mega-cap peer set is weak (<5 validated peers) — confidence reduced."
        )
    if _is_mega_cap_quality and _effective_peer_count > 0 and _effective_peer_count < (3.0 if quick_mode else 5.0):
        if quality.score > 66:
            quality.score = 66
            quality.tier = "C"
        quality.warnings.append(
            f"Low effective peer diversification ({_effective_peer_count:.2f}) — confidence reduced."
        )
    if normalization_blocked:
        if quality.score > 40:
            quality.score = 40
        quality.tier = "D"
        quality.warnings.append(
            "Data normalization failed (currency/share basis mismatch); valuation recommendation suppressed."
        )
    if (not quick_mode) and str(_research_source) == "fallback":
        if quality.score > 79:
            quality.score = 79
        quality.tier = _quality_tier(quality.score)
        if quality.tier == "A":
            quality.tier = "B"
        if "Source-backed market context unavailable; valuation input quality capped." not in quality.warnings:
            quality.warnings.append("Source-backed market context unavailable; valuation input quality capped.")
    _core_quality_score = quality.score
    console.print(
        f"  [bold]Valuation input quality:[/bold] {quality.score}/100 (Tier {quality.tier})"
        + (" [yellow]limited-confidence mode[/yellow]" if quality.is_blocked else "")
    )
    for _warn in quality.warnings[:3]:
        console.print(f"  [yellow]• {_warn}[/yellow]")
    if quality.blockers:
        for _blk in quality.blockers:
            console.print(f"  [red]• {_blk}[/red]")
    sources.add(
        "Data Quality Score",
        f"{quality.score}/100 (Tier {quality.tier})",
        "quality_gate",
        "verified",
    )

    assumptions_dict = assumptions.model_dump()
    assumptions_dict["_assumption_source"] = "system"
    assumptions_dict["normalization_blocked"] = bool(normalization_blocked)
    assumptions_dict["normalization_status"] = str(normalization_audit.get("status") or "UNKNOWN")
    assumptions_dict["normalization_reason"] = str(normalization_audit.get("reason") or "")
    assumptions_dict["normalization_quote_currency"] = str(normalization_audit.get("quote_currency") or "unknown")
    assumptions_dict["normalization_financial_currency"] = str(normalization_audit.get("financial_statement_currency") or "unknown")
    assumptions_dict["normalization_adr_detected"] = bool(normalization_audit.get("adr_detected"))
    assumptions_dict["normalization_adr_ratio"] = normalization_audit.get("adr_ratio")
    _is_mega_cap = bool(
        company_type == "public"
        and market_data
        and market_data.market_cap
        and market_data.market_cap > _mega_cap_usd_m
    )
    assumptions_dict["peer_count"] = _peer_count
    _eff_peer_count = float(peer_multiples.effective_peer_count) if peer_multiples else 0.0
    assumptions_dict["effective_peer_count"] = _eff_peer_count
    assumptions_dict["quick_mode"] = bool(quick_mode)
    assumptions_dict["insufficient_comps"] = bool(
        _is_mega_cap and _peer_count < 1
    )
    assumptions_dict["low_confidence_comps"] = bool(
        _is_mega_cap and peer_multiples and (
            (3 <= peer_multiples.n_valuation_peers < 5)
            or (peer_multiples.effective_peer_count > 0 and peer_multiples.effective_peer_count < (3.0 if quick_mode else 5.0))
            or _missing_consumer_ecosystem_bucket
        )
    )
    assumptions_dict["mega_cap_tech"] = bool(
        _is_mega_cap and any(tok in (fund.sector or "").lower() for tok in ("technology", "tech", "software", "semiconductor"))
    )
    assumptions_dict["missing_consumer_ecosystem_bucket"] = bool(_missing_consumer_ecosystem_bucket)
    assumptions_dict["transaction_comps_verified"] = bool(_tx_source_verified)
    assumptions_dict["transaction_comps_n_deals"] = int(_tx_medians.get("n_deals") or 0)
    _peer_quality = "weak"
    if peer_multiples and peer_multiples.peers:
        _valuation_only = [p for p in peer_multiples.peers if p.ev_ebitda is not None and (p.weight or 0.0) > 0.0]
        _sim_vals = [float(p.similarity or 0.0) for p in _valuation_only]
        _avg_sim = (sum(_sim_vals) / len(_sim_vals)) if _sim_vals else 0.0
        _total_w = sum(float(p.weight or 0.0) for p in _valuation_only)
        _semi_w = sum(
            float(p.weight or 0.0)
            for p in _valuation_only
            if (p.bucket or "") in {"semiconductors", "semiconductor_equipment"}
        )
        _semi_share = (_semi_w / _total_w) if _total_w > 0 else 0.0
        _pure_share = float(peer_multiples.pure_peer_weight_share or 0.0)
        if peer_multiples.n_valuation_peers >= 8 and _avg_sim >= 0.75 and _semi_share <= 0.20 and _pure_share >= 0.60 and _eff_peer_count >= (3.0 if quick_mode else 5.0):
            _peer_quality = "strong"
        elif peer_multiples.n_valuation_peers >= 5 and _avg_sim >= 0.65 and _semi_share <= 0.30 and _pure_share >= 0.25 and _eff_peer_count >= (3.0 if quick_mode else 5.0):
            _peer_quality = "normal"
        elif peer_multiples.n_valuation_peers >= 3:
            _peer_quality = "mixed"
        else:
            _peer_quality = "weak"
        assumptions_dict["peer_avg_similarity"] = _avg_sim
        assumptions_dict["peer_semi_weight_share"] = _semi_share
        assumptions_dict["pure_peer_weight_share"] = _pure_share
        assumptions_dict["adjacent_peer_weight_share"] = float(peer_multiples.adjacent_peer_weight_share or (1.0 - _pure_share))
        assumptions_dict["effective_peer_count"] = _eff_peer_count
        sources.add_once("Peer Quality", _peer_quality, "peer_quality_model", "inferred")
        sources.add_once("Peer Avg Similarity", f"{_avg_sim:.2f}", "peer_quality_model", "inferred")
        sources.add_once("Peer Semi Weight Share", f"{_semi_share:.1%}", "peer_quality_model", "inferred")
        sources.add_once("Pure Peer Weight", f"{_pure_share:.1%}", "peer_quality_model", "inferred")
        sources.add_once(
            "Adjacent Peer Weight",
            f"{float(peer_multiples.adjacent_peer_weight_share or (1.0 - _pure_share)):.1%}",
            "peer_quality_model",
            "inferred",
        )
        if _eff_peer_count > 0:
            sources.add_once("Effective Peer Count", f"{_eff_peer_count:.2f}", "peer_quality_model", "inferred")
    assumptions_dict["peer_quality"] = _peer_quality
    if peer_multiples and peer_multiples.ev_ebitda_low and peer_multiples.ev_ebitda_high:
        assumptions_dict["ev_ebitda_range"] = [
            peer_multiples.ev_ebitda_low,
            peer_multiples.ev_ebitda_high,
        ]
        if peer_multiples.ev_ebitda_median:
            assumptions_dict["ev_ebitda_median"] = peer_multiples.ev_ebitda_median
        if peer_multiples.ev_ebitda_raw_median is not None:
            assumptions_dict["ev_ebitda_raw_median"] = peer_multiples.ev_ebitda_raw_median
        if peer_multiples.ev_ebitda_weighted is not None:
            assumptions_dict["ev_ebitda_weighted"] = peer_multiples.ev_ebitda_weighted
        console.print(
            f"  [cyan]Comps from {peer_multiples.n_valuation_peers} valuation peers "
            f"({peer_multiples.n_qualitative_peers} qualitative) (P25–P75): "
            f"{peer_multiples.ev_ebitda_low:.1f}x–{peer_multiples.ev_ebitda_high:.1f}x EV/EBITDA[/cyan]"
        )
        if _is_mega_cap and peer_multiples.n_valuation_peers < 5:
            console.print("  [yellow]⚠ Peer set expanded beyond core comparables; valuation confidence reduced.[/yellow]")
    elif assumptions_dict.get("insufficient_comps"):
        console.print(
            "  [yellow]Comps unavailable: no validated peers after expansion.[/yellow]"
        )
    elif _is_mega_cap and peer_multiples and 3 <= peer_multiples.n_valuation_peers < 5:
        console.print(
            f"  [yellow]Low-confidence comps: {peer_multiples.n_valuation_peers} valuation peers (target 5–7).[/yellow]"
        )

    # Policy: do not anchor valuation inputs to LLM-sourced transaction comps.
    # Use sector benchmarks (deterministic) unless a verified deal database is integrated.

    _md_val = market_data_valuation or market_data
    _valuation_financials = fin.model_dump()
    _valuation_md = _md_val
    if company_type == "private" and _private_screen_only:
        # Hard gate for private weak-data runs: keep output screen-only and avoid
        # presenting LLM/triangulated estimates as valuation-grade anchors.
        if _valuation_md is not None:
            _valuation_md = _copy.deepcopy(_valuation_md)
            _valuation_md.revenue_ttm = None
            _valuation_md.revenue_history = []
            _valuation_md.ebitda_ttm = None
            _valuation_md.fcf_ttm = None
            _valuation_md.ebitda_margin = None
            _valuation_md.net_margin = None
            _valuation_md.gross_margin = None
        _valuation_financials["revenue_current"] = None
        _valuation_financials["revenue_series"] = []
        _valuation_financials["revenue_growth"] = None
        console.print(
            "  [yellow]Private valuation gated:[/yellow] run is screen-only until identity and "
            "revenue quality gates are satisfied."
        )
    result = svc.run_full_valuation(
        financials=_valuation_financials,
        assumptions=assumptions_dict,
        market_data=_valuation_md,
        sector=fund.sector or "",
        company_type=company_type,
    )
    _method_values: list[float] = []
    if result.dcf and result.dcf.enterprise_value and result.dcf.enterprise_value > 0:
        _method_values.append(float(result.dcf.enterprise_value))
    if result.comps and result.comps.mid and result.comps.mid > 0:
        _method_values.append(float(result.comps.mid))
    if result.transactions and result.transactions.implied_value and result.transactions.implied_value > 0:
        _method_values.append(float(result.transactions.implied_value))
    _method_dispersion_ratio = 1.0
    if len(_method_values) >= 2 and min(_method_values) > 0:
        _method_dispersion_ratio = max(_method_values) / min(_method_values)
    if _method_dispersion_ratio >= 2.0:
        _method_dispersion_level = "High"
    elif _method_dispersion_ratio >= 1.4:
        _method_dispersion_level = "Medium"
    else:
        _method_dispersion_level = "Low"
    sources.add_once(
        "Method Dispersion",
        f"{_method_dispersion_level} ({_method_dispersion_ratio:.2f}x spread)",
        "valuation_engine",
        "inferred",
    )
    blended_ev = result.blended.blended if result.blended else None
    rec = result.recommendation
    _dcf_sanity_fail = any(
        any(tok in str(n) for tok in (
            "DCF likely miscalibrated",
            "DCF sanity flag: implied exit multiple appears low",
            "DCF conservative vs peer floor",
        ))
        for n in (result.notes or [])
    )
    valuation_failed = False
    if _dcf_sanity_fail:
        quality.score = max(0, quality.score - 12)
        quality.warnings.append("DCF sanity check failed")
        quality.checks["dcf_sanity"] = "failed"
    if _peer_count == 0 and _dcf_sanity_fail:
        console.print("  [red]❌ Valuation failed: peer comps unavailable and DCF sanity check failed.[/red]")
        rec.recommendation = "INCONCLUSIVE"
        rec.intrinsic_price = None
        rec.upside_pct = None
        valuation_failed = True
        valuation_status = "FAILED"
        result.field_sources.pop("Fair Value Range", None)
        result.notes.append("Valuation status: FAILED — no peers + DCF sanity failure.")
        if quality.score > 40:
            quality.score = 40
        quality.tier = "D"
        console.print(f"  [red]Adjusted data quality after valuation failure: {quality.score}/100 (Tier {quality.tier})[/red]")
    elif _dcf_sanity_fail and _peer_count > 0:
        valuation_status = "DEGRADED"
    sources.add(
        "Data Quality Score",
        f"{quality.score}/100 (Tier {quality.tier})",
        "quality_gate",
        "verified",
    )

    if normalization_blocked:
        console.print(
            "  [red]⚠ Valuation blocked:[/red] currency/share normalization checks failed. "
            "Run is screen-only until data normalization is resolved."
        )
    elif not result.has_revenue:
        console.print(
            "  [yellow]⚠ No revenue data — quantitative valuation skipped. "
            "Peer multiples shown for reference only.[/yellow]"
        )

    _w = result.weights_used
    _w_dcf  = round(_w.get("dcf", 0.5) * 100)
    _w_comp = round(_w.get("comps", 0.3) * 100)
    _w_tx   = round(_w.get("transactions", 0.2) * 100)

    _methods: list = []
    if result.has_revenue and result.dcf and _w_dcf > 0:
        _methods.append(ValuationMethod(
            name="DCF", mid=str(round(result.dcf.enterprise_value, 1)), weight=_w_dcf
        ))
    if result.has_revenue and result.comps and _w_comp > 0:
        _methods.append(ValuationMethod(
            name=(
                "Trading Comps (Applied Weighted EV/EBITDA)"
                if result.valuation_path.upper() == "EV_EBITDA"
                else f"Trading Comps ({result.valuation_path.upper()})"
            ),
            low=str(round(result.comps.low, 1)),
            mid=str(round(result.comps.mid, 1)),
            high=str(round(result.comps.high, 1)),
            weight=_w_comp,
        ))
    if result.has_revenue and result.transactions and _w_tx > 0:
        tx_label = "Transaction Comps"
        tx_n = _tx_medians.get("n_deals", 0)
        if tx_n:
            tx_label = f"Transaction Comps ({tx_n} deals)"
        _methods.append(ValuationMethod(
            name=tx_label,
            mid=str(round(result.transactions.implied_value, 1)),
            weight=_w_tx,
        ))

    # ── 5a. SOTP (conglomerates / multi-segment) ──────────────────────────
    _quote_ccy = str(normalization_audit.get("quote_currency") or "USD")
    _CONGLOMERATE_KEYWORDS = {"segment", "division", "business unit", "portfolio", "subsidiaries"}
    _desc_lower = (fund.description + " " + fund.business_model).lower()
    if any(kw in _desc_lower for kw in _CONGLOMERATE_KEYWORDS) and result.has_revenue and fin.revenue_current:
        try:
            import json as _j, re as _r
            sotp_prompt = (
                f'Company: "{company}". Sector: {fund.sector}. '
                f'Revenue: {_quote_ccy} {fin.revenue_current}M total. '
                f'Description: {fund.description[:300]}. '
                "List the 2–4 main business segments as a JSON array: "
                '[{"name":"...", "revenue_pct": 0.X, "ebitda_margin": 0.X, "sector": "..."}]. '
                "revenue_pct values must sum to 1.0. Return ONLY the JSON array."
            )
            seg_resp = client.complete(
                messages=[{"role": "user", "content": sotp_prompt}],
                model=client.resolve_model("small"),
                max_tokens=300,
            )
            raw_seg = _r.sub(r"```[a-z]*\n?|\n?```", "", seg_resp.content.strip())
            seg_data = _j.loads(raw_seg)
            if seg_data and float(fin.revenue_current or 0) > 0:
                from goldroger.finance.valuation.sotp import Segment, compute_sotp as _compute_sotp
                _rev = float(fin.revenue_current)
                _segments = [
                    Segment(
                        name=s["name"],
                        revenue=_rev * float(s.get("revenue_pct", 0.25)),
                        ebitda_margin=float(s.get("ebitda_margin", 0.15)),
                        sector=s.get("sector") or fund.sector or "Technology",
                    )
                    for s in seg_data
                ]
                _sotp = _compute_sotp(_segments)
                _methods.append(
                    ValuationMethod(name="SOTP (reference only)", mid=str(round(_sotp.net_ev, 1)), weight=None)
                )
                console.print(
                    f"  [cyan]SOTP ({len(_segments)} segments): Net EV {_fmt_money_millions(_sotp.net_ev, _quote_ccy)} "
                    f"(reference only; unverified segment assumptions)[/cyan]"
                )
                sources.add_once(
                    "SOTP",
                    f"{_fmt_money_millions(_sotp.net_ev, _quote_ccy)} (reference only)",
                    "analysis_output_unverified_segments",
                    "inferred",
                )
        except Exception as _sotp_err:
            console.print(f"  [dim]SOTP skipped: {_sotp_err}[/dim]")

    # Pre-resolve sector multiples and revenue float — used by recommendation + IC scoring
    from goldroger.data.sector_multiples import get_sector_multiples as _get_sm
    _sm = _get_sm(fund.sector or "")
    _, _ev_rev_mid, _ev_rev_high = _sm.ev_revenue
    _rev_float = (
        float(fin.revenue_current)
        if fin.revenue_current and fin.revenue_current.replace(".", "").isdigit()
        else None
    )
    _ebitda_margin = svc._resolve_ebitda_margin(
        fin.model_dump(), market_data, [], sector=fund.sector or ""
    )[0]

    _ev_str = _fmt_ev_human(blended_ev, _quote_ccy) if blended_ev else "N/A"
    _target_price = _fmt_price(rec.intrinsic_price, _quote_ccy, decimals=2) if rec.intrinsic_price else None
    _raw_rec = rec.recommendation if (result.has_revenue or normalization_blocked) else "N/A"
    _model_signal = _raw_rec
    _raw_signal_label = "Neutral valuation signal"
    if result.has_revenue and rec.upside_pct is not None:
        if rec.upside_pct <= -0.30:
            _model_signal = "SELL / NEGATIVE VALUATION SIGNAL"
            _raw_signal_label = "Negative valuation signal"
        elif -0.30 < rec.upside_pct <= -0.15:
            _model_signal = "HOLD / NEGATIVE BIAS"
            _raw_signal_label = "Negative valuation signal"
        elif -0.15 < rec.upside_pct < 0.15:
            _model_signal = "HOLD"
            _raw_signal_label = "Neutral valuation signal"
        elif 0.15 <= rec.upside_pct < 0.30:
            _model_signal = "BUY / POSITIVE BIAS"
            _raw_signal_label = "Positive valuation signal"
        elif rec.upside_pct >= 0.30:
            _model_signal = "BUY / POSITIVE VALUATION SIGNAL"
            _raw_signal_label = "Positive valuation signal"
    _hard_suppression_reasons: list[str] = []
    _model_equity_val = None
    _mcap_cmp_upside = None
    _profile_key_for_guard = detect_sector_profile(
        fund.sector or "",
        _target_industry if "_target_industry" in locals() else "",
    )
    _is_cyclical_profile_for_guard = _profile_key_for_guard in {
        "materials_chemicals_mining",
        "energy_oil_gas",
        "industrials",
    }
    _cyclical_review_required = False
    _normalized_ebitda_supported = False
    _normalized_ebitda_proxy = None
    _avg_rev_3y = None
    _avg_rev_5y = None
    if _md_val and blended_ev is not None:
        try:
            _model_equity_val = float(blended_ev) - float(_md_val.net_debt or 0.0)
            if _md_val.market_cap and _md_val.market_cap > 0:
                _mcap_cmp_upside = (_model_equity_val - float(_md_val.market_cap)) / float(_md_val.market_cap)
        except Exception:
            _model_equity_val = None
            _mcap_cmp_upside = None
    if normalization_blocked:
        _hard_suppression_reasons.append(
            f"data normalization failed: {normalization_audit.get('reason')}"
        )
    if company_type == "public" and _is_cyclical_profile_for_guard and _md_val:
        _rev_hist = [float(x) for x in (_md_val.revenue_history or []) if x and x > 0]
        _rev_now = float(_md_val.revenue_ttm or 0.0)
        _ebitda_now = float(_md_val.ebitda_ttm or 0.0)
        if len(_rev_hist) >= 3:
            _avg_rev_3y = sum(_rev_hist[-3:]) / 3.0
        if len(_rev_hist) >= 5:
            _avg_rev_5y = sum(_rev_hist[-5:]) / 5.0
        _rev_norm = _avg_rev_5y or _avg_rev_3y
        if _rev_norm and _rev_now > 0 and _ebitda_now > 0:
            _curr_margin = _ebitda_now / _rev_now
            _normalized_ebitda_proxy = _rev_norm * _curr_margin
            if _normalized_ebitda_proxy and _normalized_ebitda_proxy > 0:
                _ratio = _ebitda_now / _normalized_ebitda_proxy
                _normalized_ebitda_supported = 0.70 <= _ratio <= 1.30
        else:
            _cyclical_review_required = True
        if _normalized_ebitda_proxy is None:
            _cyclical_review_required = True
        elif not _normalized_ebitda_supported:
            _cyclical_review_required = True
    if (
        company_type == "public"
        and _md_val
        and _md_val.market_cap
        and _md_val.market_cap >= 10000
        and rec.upside_pct is not None
        and rec.upside_pct >= 3.0
    ):
        _basis_up = _mcap_cmp_upside if _mcap_cmp_upside is not None else rec.upside_pct
        _basis_txt = "market-cap comparison" if _mcap_cmp_upside is not None else "per-share comparison"
        _hard_suppression_reasons.append(
            f"normalized equity value implies {_basis_up:+.1%} upside vs market cap (basis: {_basis_txt})"
        )
    if (
        company_type == "public"
        and _md_val
        and _md_val.market_cap
        and _md_val.market_cap >= 10000
        and rec.upside_pct is not None
        and rec.upside_pct <= -0.80
    ):
        _basis_dn = _mcap_cmp_upside if _mcap_cmp_upside is not None else rec.upside_pct
        _basis_txt = "market-cap comparison" if _mcap_cmp_upside is not None else "per-share comparison"
        _hard_suppression_reasons.append(
            f"normalized equity value implies {_basis_dn:+.1%} downside vs market cap (basis: {_basis_txt})"
        )
    if (
        company_type == "public"
        and _md_val
        and _md_val.market_cap
        and _md_val.market_cap >= 1000
        and _md_val.ev_ebitda_market is not None
        and _md_val.ev_ebitda_market < 2.0
    ):
        _hard_suppression_reasons.append(
            f"live EV/EBITDA unusually low ({_md_val.ev_ebitda_market:.1f}x) — possible currency/unit mismatch"
        )
    if (
        company_type == "public"
        and _md_val
        and _md_val.market_cap
        and _md_val.revenue_ttm
        and _md_val.market_cap >= 10000
        and _md_val.revenue_ttm > 0
        and (_md_val.market_cap / _md_val.revenue_ttm) < 0.15
    ):
        _hard_suppression_reasons.append(
            "market-cap-to-revenue ratio is extremely low; verify currency and revenue units"
        )
    _mature_company = bool(
        company_type == "public"
        and _md_val
        and _md_val.market_cap
        and _md_val.market_cap >= 10000
        and (_md_val.ebitda_ttm or 0) > 0
        and (
            _md_val.forward_revenue_growth is None
            or float(_md_val.forward_revenue_growth) <= 0.15
        )
    )
    _extreme_signal_review = False
    _extreme_signal_corroboration = 0
    _extreme_signal_missing: list[str] = []
    _extreme_signal_anchor_labels: list[str] = []
    _extreme_signal_cap_required = False
    _market_context_fallback_pre = bool(market_context_pack.fallback_used) if market_context_pack is not None else False
    _source_backed_quant_market_inputs_pre = bool(
        _market_source_backed
        and ((not _text_missing(mkt.market_growth)) or (not _text_missing(mkt.market_size)))
    )
    _forward_rev_analyst_available = bool(
        _md_val is not None
        and (
            (_md_val.forward_revenue_1y is not None and float(_md_val.forward_revenue_1y or 0.0) > 0.0)
            or (
                isinstance(_md_val.additional_metadata, dict)
                and (_md_val.additional_metadata.get("forward_revenue_estimate") is not None)
            )
        )
    )
    _peer_purity_share = float(peer_multiples.pure_peer_weight_share or 0.0) if peer_multiples else 0.0
    _peer_purity_ok = _peer_purity_share >= 0.75
    _dispersion_ok = _method_dispersion_ratio < 2.0
    _norm_clean_ok = str(normalization_audit.get("status") or "").upper() == "OK"
    _has_recent_company_catalyst = _has_recent_company_specific_catalyst(market_context_pack)
    _extreme_upside_threshold = 0.50 if _is_cyclical_profile_for_guard else 0.75
    _extreme_positive_signal = bool(
        _md_val is not None
        and
        rec.upside_pct is not None
        and rec.upside_pct >= _extreme_upside_threshold
        and (_mature_company or _is_cyclical_profile_for_guard)
    )
    _extreme_negative_signal = bool(
        _mature_company
        and rec.upside_pct is not None
        and rec.upside_pct <= -0.60
    )
    if _extreme_positive_signal or _extreme_negative_signal:
        _extreme_signal_review = True
        # Anchor 1: DCF and comps imply same directional sign.
        _dcf_up = None
        _comps_up = None
        if _md_val.market_cap and _md_val.market_cap > 0 and result.dcf and result.comps:
            try:
                _dcf_eq = float(result.dcf.enterprise_value) - float(_md_val.net_debt or 0.0)
                _comps_eq = float(result.comps.mid) - float(_md_val.net_debt or 0.0)
                _dcf_up = (_dcf_eq - float(_md_val.market_cap)) / float(_md_val.market_cap)
                _comps_up = (_comps_eq - float(_md_val.market_cap)) / float(_md_val.market_cap)
                if ((_dcf_up > 0 and _comps_up > 0 and rec.upside_pct > 0) or (_dcf_up < 0 and _comps_up < 0 and rec.upside_pct < 0)):
                    _extreme_signal_corroboration += 1
                    _extreme_signal_anchor_labels.append("DCF and comps direction align")
            except Exception:
                pass
        # Anchor 2: Source-backed quantitative market context.
        if _source_backed_quant_market_inputs_pre:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("source-backed quantitative market inputs")
        else:
            _extreme_signal_missing.append("source-backed quantitative market inputs")
        # Anchor 3: Analyst forward revenue/EBITDA estimates (not earnings proxy only).
        if _forward_rev_analyst_available:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("analyst forward revenue/EBITDA support")
        else:
            _extreme_signal_missing.append("analyst forward revenue/EBITDA estimate")
        # Anchor 4: High-purity peer set.
        if _peer_purity_ok:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("peer purity >= 75%")
        else:
            _extreme_signal_missing.append("peer purity >= 75%")
        # Anchor 5: Acceptable method dispersion.
        if _dispersion_ok:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("DCF/comps dispersion < 2.0x")
        else:
            _extreme_signal_missing.append("DCF/comps dispersion < 2.0x")
        # Anchor 6: Clean normalization status.
        if _norm_clean_ok:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("normalization status OK")
        else:
            _extreme_signal_missing.append("clean normalization status")
        # Anchor 7: No market-context fallback.
        if (not _market_context_fallback_pre) and _market_source_backed:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("no fallback market context")
        else:
            _extreme_signal_missing.append("non-fallback market context")
        # Anchor 8: Company-specific catalyst support.
        if _has_recent_company_catalyst:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("company-specific catalyst (<=12m)")
        else:
            _extreme_signal_missing.append("company-specific catalyst within 12 months")
        # Anchor 2: FCF yield support for upside calls.
        if rec.upside_pct > 0 and _md_val.fcf_ttm and _md_val.market_cap and _md_val.market_cap > 0:
            try:
                _fcf_yield = float(_md_val.fcf_ttm) / float(_md_val.market_cap)
                if _fcf_yield >= 0.05:
                    _extreme_signal_corroboration += 1
                    _extreme_signal_anchor_labels.append("FCF yield support")
            except Exception:
                pass
        # Anchor 3: Dividend yield support for mature cash-return sectors.
        _div_yld = None
        if isinstance(_md_val.additional_metadata, dict):
            _div_yld = _normalize_dividend_yield(_md_val.additional_metadata.get("dividend_yield"))
        if rec.upside_pct > 0 and _div_yld is not None and _div_yld >= 0.04:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("dividend yield support")
        # Anchor 4: Cyclical normalization corroboration.
        if _is_cyclical_profile_for_guard and _normalized_ebitda_supported:
            _extreme_signal_corroboration += 1
            _extreme_signal_anchor_labels.append("mid-cycle normalization support")
        elif _is_cyclical_profile_for_guard:
            _extreme_signal_missing.append("mid-cycle normalization support")
        _quant_corroboration_ok = bool(_source_backed_quant_market_inputs_pre or _forward_rev_analyst_available)
        _required_extreme_anchors = 2 if _extreme_negative_signal else 3
        if (not _quant_corroboration_ok) and _extreme_positive_signal:
            _extreme_signal_cap_required = True
        if _extreme_signal_corroboration < _required_extreme_anchors:
            _extreme_signal_cap_required = True
    if _hard_suppression_reasons:
        _eq_ctx = ""
        if _model_equity_val is not None and _md_val and _md_val.market_cap is not None:
            _eq_ctx = (
                f" Market cap: {_fmt_money_millions(float(_md_val.market_cap), _quote_ccy)}; "
                f"Model equity value: {_fmt_money_millions(float(_model_equity_val), _quote_ccy)}."
            )
        console.print(
            "  [red]Sanity breaker triggered:[/red] "
            + "; ".join(_hard_suppression_reasons)
            + ". Recommendation suppressed."
            + _eq_ctx
        )
        _model_signal = "DATA CHECK REQUIRED"
        _raw_signal_label = "Data check required"
        valuation_status = "FAILED"
        _target_price = None
        rec.upside_pct = None
        rec.intrinsic_price = None
        rec.recommendation = "INCONCLUSIVE"
        if quality.score > 40:
            quality.score = 40
        quality.tier = "D"
        quality.warnings.append("Sanity breaker triggered; valuation recommendation suppressed.")
        for _sr in _hard_suppression_reasons:
            quality.warnings.append(f"Sanity breaker: {_sr}")
    _suppressed_no_rating = bool(_hard_suppression_reasons)
    _low_conviction = any("dispersion" in str(n).lower() or "high uncertainty" in str(n).lower() for n in (result.notes or []))
    if _suppressed_no_rating:
        _low_conviction = True
    if peer_multiples and peer_multiples.n_valuation_peers < 3:
        _low_conviction = True
    if peer_multiples and peer_multiples.effective_peer_count > 0 and peer_multiples.effective_peer_count < (3.0 if quick_mode else 5.0):
        _low_conviction = True
    if _is_mega_cap and peer_multiples and 3 <= peer_multiples.n_valuation_peers < 5:
        _low_conviction = True
    if _model_signal.startswith("SELL") and (_raw_rec or "").upper() == "HOLD":
        _low_conviction = True
    _private_cap_reason = ""
    _model_signal_for_text = _model_signal
    if _low_conviction and _model_signal_for_text.startswith("SELL /"):
        _model_signal_for_text = "Negative valuation signal"
    if company_type == "private" and result.has_revenue and blended_ev and _rev_float and _rev_float > 0:
        _entry_ev_rev = blended_ev / _rev_float
        _, _sm_mid, _sm_high = _sm.ev_revenue
        if _entry_ev_rev <= _sm_mid * 0.80:
            _rec = "ATTRACTIVE ENTRY"
        elif _entry_ev_rev <= _sm_mid * 1.25:
            _rec = "CONDITIONAL GO"
        elif _entry_ev_rev <= _sm_high * 0.90:
            _rec = "SELECTIVE BUY"
        else:
            _rec = "FULL PRICE"
    elif company_type == "private" and result.has_revenue:
        _rec = "NEUTRAL"
    else:
        _rec = _raw_rec
    if company_type == "private":
        _private_conf_raw = str((market_data.confidence if market_data else "") or "").strip().lower()
        _private_revenue_unverified = _private_revenue_quality not in {"VERIFIED", "HIGH_CONFIDENCE_ESTIMATE", "MANUAL"}
        if _private_identity_status != "RESOLVED" and not _private_manual_identity_override:
            _low_conviction = True
            if not _private_cap_reason:
                _private_cap_reason = "private identity not fully resolved from strong registry sources"
            if "Private identity resolution is weak; valuation is indicative only." not in quality.warnings:
                quality.warnings.append("Private identity resolution is weak; valuation is indicative only.")
        if _private_screen_only or (not result.has_revenue) or _private_revenue_quality in {"UNAVAILABLE", "LOW_CONFIDENCE_ESTIMATE"}:
            _rec = "INCONCLUSIVE"
            _target_price = None
            _ev_str = "N/A"
            rec.upside_pct = None
            rec.intrinsic_price = None
            valuation_status = "FAILED" if _private_revenue_quality == "UNAVAILABLE" else "DEGRADED"
            if not _private_cap_reason:
                _private_cap_reason = (
                    "private revenue is not verified/high-confidence; run is screen-only and recommendation is suppressed"
                )
            if quality.score > 55:
                quality.score = 55
            quality.tier = _quality_tier(quality.score)
            if "Private run is screen-only due to weak identity/revenue confidence; recommendation suppressed (INCONCLUSIVE)." not in quality.warnings:
                quality.warnings.append(
                    "Private run is screen-only due to weak identity/revenue confidence; recommendation suppressed (INCONCLUSIVE)."
                )
        elif _private_revenue_unverified or _private_conf_raw in {"estimated", "inferred", "manual"}:
            _low_conviction = True
            if _rec in {"ATTRACTIVE ENTRY", "CONDITIONAL GO", "SELECTIVE BUY", "FULL PRICE", "NEUTRAL"}:
                _rec = f"{_rec} / LOW CONVICTION"
            if valuation_status == "OK":
                valuation_status = "DEGRADED"
            if not _private_cap_reason:
                _private_cap_reason = (
                    "private valuation relies on manual/estimated revenue (triangulated/provider estimate)"
                )
            if quality.score > 69:
                quality.score = 69
            quality.tier = _quality_tier(quality.score)
            if "Private revenue is manual/estimated; valuation is indicative only." not in quality.warnings:
                quality.warnings.append("Private revenue is manual/estimated; valuation is indicative only.")
        if _private_revenue_quality == "MANUAL":
            if quality.score > (72 if _private_identity_status == "RESOLVED" else 68):
                quality.score = 72 if _private_identity_status == "RESOLVED" else 68
            quality.tier = _quality_tier(quality.score)
            _low_conviction = True
            if not _private_cap_reason:
                _private_cap_reason = "valuation confidence capped due to manual user-provided revenue input"
            if "Valuation confidence capped due to manual user-provided revenue input." not in quality.warnings:
                quality.warnings.append("Valuation confidence capped due to manual user-provided revenue input.")
    if _suppressed_no_rating:
        _rec = "INCONCLUSIVE"
        _target_price = None
        _ev_str = "N/A"
    if _low_conviction and _rec in {"BUY", "SELL", "HOLD"}:
        if _rec == "SELL":
            _rec = "HOLD / LOW CONVICTION"
        elif _rec == "BUY":
            _rec = "BUY / LOW CONVICTION"
        else:
            _rec = "HOLD / LOW CONVICTION"
    if (not valuation_failed) and _low_conviction and valuation_status == "OK":
        valuation_status = "DEGRADED"
    if valuation_failed and not _suppressed_no_rating:
        _rec = "INCONCLUSIVE"
        _target_price = None
        _ev_str = "N/A"
    if company_type == "private":
        if _private_screen_only or _rec == "INCONCLUSIVE":
            _private_valuation_mode = "SCREEN_ONLY"
        elif valuation_status == "FAILED":
            _private_valuation_mode = "FAILED"
        else:
            _private_valuation_mode = "VALUATION_GRADE"
        if _private_valuation_mode == "FAILED":
            _private_state = "VALUATION_FAILED"
        elif _private_valuation_mode == "VALUATION_GRADE":
            _private_state = "VALUATION_READY"
        elif _private_state not in {"IDENTITY_UNRESOLVED", "IDENTITY_RESOLVED_NO_REVENUE"}:
            _private_state = "SCREEN_ONLY"
        sources.add_once(
            "Private Valuation Mode",
            _private_valuation_mode,
            "private_valuation_gate",
            "inferred",
        )
        sources.add_once(
            "Private State",
            _private_state,
            "private_valuation_gate",
            "inferred",
        )
        if _private_screen_only_reasons:
            for _idx, _why in enumerate(list(dict.fromkeys(_private_screen_only_reasons)), start=1):
                sources.add_once(
                    f"Private Screen-Only Reason {_idx}",
                    _why,
                    "private_valuation_gate",
                    "inferred",
                )

    # Dump all per-field provenance from the valuation engine.
    # add_once deduplicates by metric name — equity.py may have already logged
    # revenue/margins from the market data fetch above.
    for _metric, (_val, _src, _conf) in result.field_sources.items():
        sources.add_once(_metric, _val, _src, _conf)

    _wacc_conf = result.field_sources.get("WACC", (None, None, "inferred"))[2]
    sources.add_once("WACC", f"{result.wacc_used:.2%}", "capm_model", _wacc_conf)
    sources.add_once("Terminal Growth", f"{result.terminal_growth_used:.2%}", "sector_default", "inferred")
    sources.add("Implied EV", _ev_str, "valuation_engine", "inferred")
    if peer_multiples and peer_multiples.ev_ebitda_median:
        sources.add(
            "Peer EV/EBITDA median",
            f"{peer_multiples.ev_ebitda_median:.1f}x ({peer_multiples.n_valuation_peers} valuation peers)",
            "yfinance_peers", "verified",
        )

    val = Valuation(
        current_price=(f"{_quote_ccy} {rec.current_price:.2f}" if rec.current_price else None),
        currency=_quote_ccy,
        implied_value=_ev_str,
        target_price=_target_price,
        upside_downside=(f"{rec.upside_pct:+.1%}" if rec.upside_pct is not None else "N/A"),
        recommendation=_rec,
        dcf_assumptions=DCFAssumptions(
            wacc=f"{result.wacc_used:.2%}",
            terminal_growth=f"{result.terminal_growth_used:.2%}",
            projection_years="5",
        ),
        methods=_methods,
        sources=[result.data_confidence],
    )
    sources.add_once("Target Price", _target_price or "N/A", "valuation_engine", "inferred")
    sources.add_once("Upside/Downside", val.upside_downside or "N/A", "valuation_engine", "inferred")

    if result.lbo and not (company_type == "private" and _private_screen_only):
        lbo = result.lbo
        console.print(
            f"  LBO: {'✓ FEASIBLE' if lbo.is_feasible else '✗ INFEASIBLE'} — "
            f"IRR {lbo.irr:.1%} / {lbo.moic:.1f}x MOIC / "
            f"{lbo.leverage_at_entry:.1f}x entry leverage"
        )
    elif result.lbo and company_type == "private" and _private_screen_only:
        console.print(
            "  [dim]LBO diagnostic suppressed: private run is screen-only (identity/revenue gate not satisfied).[/dim]"
        )
    if result.sensitivity and result.sensitivity.ev_matrix:
        try:
            _sr = result.sensitivity
            _wi = min(range(len(_sr.wacc_range)), key=lambda i: abs(_sr.wacc_range[i] - result.wacc_used))
            _ti = min(range(len(_sr.tg_range)), key=lambda i: abs(_sr.tg_range[i] - result.terminal_growth_used))
            _i_up = min(_wi + 1, len(_sr.wacc_range) - 1)
            _i_dn = max(_wi - 1, 0)
            _ev_up = _sr.ev_matrix[_i_up][_ti]
            _ev_dn = _sr.ev_matrix[_i_dn][_ti]
            console.print(
                f"  [dim]Sensitivity (WACC ±100bps): {_fmt_ev_human(_ev_dn, _quote_ccy)} to {_fmt_ev_human(_ev_up, _quote_ccy)}[/dim]"
            )
            sources.add_once(
                "Sensitivity (WACC ±100bps)",
                f"{_fmt_ev_human(_ev_dn, _quote_ccy)} to {_fmt_ev_human(_ev_up, _quote_ccy)}",
                "valuation_engine",
                "inferred",
            )
        except Exception:
            pass
    # DCF diagnostics: keep normal output concise; detailed internals in --debug.
    _exit_line = None
    _live_line = None
    _multi_line = None
    for _n in (result.notes or []):
        _txt = str(_n)
        if _txt.startswith("DCF implied exit EV/EBITDA:"):
            _exit_line = _txt
            sources.add_once("DCF Implied Exit EV/EBITDA", _txt.split(":", 1)[1].strip(), "valuation_engine", "inferred")
        elif _txt.startswith("Live EV/EBITDA check:"):
            _live_line = _txt
        elif _txt.startswith("Multiple cross-check:"):
            _multi_line = _txt
        elif debug and _txt.startswith("DCF implied terminal FCF yield:"):
            sources.add_once("DCF Implied Terminal FCF Yield", _txt.split(":", 1)[1].strip(), "valuation_engine", "inferred")
        elif debug and _txt.startswith("Terminal value "):
            sources.add_once("Terminal Value Share of DCF", _txt.replace("Terminal value", "").strip(), "valuation_engine", "inferred")
        elif debug and _txt.startswith("DCF calibration —"):
            sources.add_once("DCF Calibration Snapshot", _txt.replace("DCF calibration —", "").strip(), "valuation_engine", "inferred")
    if _exit_line:
        if _live_line:
            console.print(f"  [dim]{_exit_line}  | {_live_line.replace('Live EV/EBITDA check: ', '')}[/dim]")
        elif _multi_line:
            console.print(f"  [dim]{_exit_line}  | {_multi_line.replace('Multiple cross-check: ', '')}[/dim]")
        else:
            console.print(f"  [dim]{_exit_line}[/dim]")
    if debug:
        _sanity_prefixes = (
            "DCF calibration —",
            "DCF implied terminal FCF yield:",
            "Terminal value ",
            "Multiple cross-check:",
            "Normalized terminal multiple cross-check:",
            "Live EV/EBITDA check:",
        )
        for _n in (result.notes or []):
            _txt = str(_n)
            if _txt.startswith(_sanity_prefixes):
                console.print(f"  [dim]{_txt}[/dim]")
    if debug:
        for note in result.notes:
            console.print(f"  [dim]• {note}[/dim]")

    # ── 5b. BEAR / BASE / BULL SCENARIOS ─────────────────────────────────
    football_field: FootballField | None = None
    ic_summary: ICScoreSummary | None = None
    try:
        if company_type == "private" and _private_screen_only:
            raise ValueError("private screen-only mode: valuation scenarios suppressed (identity/revenue gate)")
        if normalization_blocked:
            raise ValueError("valuation blocked due to failed currency/share normalization")
        if _hard_suppression_reasons:
            raise ValueError("sanity breaker triggered; scenario valuation suppressed")
        revenue_series, _ = svc._build_revenue_series(
            fin.model_dump(), market_data, [], sector=fund.sector or ""
        )
        if not revenue_series or not result.has_revenue:
            raise ValueError("revenue unavailable for scenario generation")
        _last_ebitda = (revenue_series[-1] * _ebitda_margin) if revenue_series else 1.0
        if peer_multiples and peer_multiples.ev_ebitda_low and peer_multiples.ev_ebitda_high:
            _comps_low = peer_multiples.ev_ebitda_low
            _comps_high = peer_multiples.ev_ebitda_high
        elif _last_ebitda > 0 and result.comps:
            _comps_low = result.comps.low / _last_ebitda
            _comps_high = result.comps.high / _last_ebitda
        else:
            _comps_low, _comps_high = 8.0, 14.0
        _y0_rev = None
        if market_data and market_data.revenue_history:
            _y0_rev = market_data.revenue_history[-1]
        elif market_data and market_data.revenue_ttm:
            _y0_rev = market_data.revenue_ttm
        elif fin.revenue_current:
            try:
                _y0_rev = float(fin.revenue_current)
            except (ValueError, TypeError):
                pass

        scenarios_out = run_scenarios(
            base_revenue=revenue_series,
            base_ebitda_margin=_ebitda_margin,
            base_wacc=result.wacc_used,
            base_terminal_growth=result.terminal_growth_used,
            base_comps_low=_comps_low,
            base_comps_high=_comps_high,
            base_tx_multiple=(
                result.transactions.implied_value / revenue_series[-1]
                if result.transactions and revenue_series and revenue_series[-1] > 0
                else 2.0
            ),
            tax_rate=svc._resolve_tax_rate(fin.model_dump(), market_data),
            capex_pct=svc._resolve_capex_pct(
                fin.model_dump(), market_data,
                revenue_series[-1] if revenue_series else 1000,
            ),
            nwc_pct=float(fin.model_dump().get("nwc_pct") or 0.02),
            da_pct=svc._resolve_da_pct(
                market_data, revenue_series[-1] if revenue_series else None
            ),
            weights=result.weights_used,
            y0_revenue=_y0_rev,
        )

        def _ordered(_low: float, _mid: float, _high: float) -> bool:
            return bool(_low <= _mid <= _high)

        if not _ordered(scenarios_out.bear.dcf_ev, scenarios_out.base.dcf_ev, scenarios_out.bull.dcf_ev):
            raise ValueError("scenario ordering failed (DCF low/base/high invariant violated)")
        if not _ordered(
            scenarios_out.bear.comps_ev_mid,
            scenarios_out.base.comps_ev_mid,
            scenarios_out.bull.comps_ev_mid,
        ):
            raise ValueError("scenario ordering failed (comps low/base/high invariant violated)")
        if not _ordered(
            scenarios_out.bear.blended_ev,
            scenarios_out.base.blended_ev,
            scenarios_out.bull.blended_ev,
        ):
            raise ValueError("scenario ordering failed (blended low/base/high invariant violated)")
        _dcf_mid_ref = float(result.dcf.enterprise_value) if result.dcf else float(scenarios_out.base.dcf_ev)
        _comps_mid_ref = float(result.comps.mid) if result.comps else float(scenarios_out.base.comps_ev_mid)
        _blend_mid_ref = float(blended_ev) if blended_ev is not None else float(scenarios_out.base.blended_ev)
        if not _ordered(scenarios_out.bear.dcf_ev, _dcf_mid_ref, scenarios_out.bull.dcf_ev):
            raise ValueError("scenario ordering failed (DCF scenario band does not contain base valuation)")
        if not _ordered(scenarios_out.bear.comps_ev_mid, _comps_mid_ref, scenarios_out.bull.comps_ev_mid):
            raise ValueError("scenario ordering failed (comps scenario band does not contain base valuation)")
        if not _ordered(scenarios_out.bear.blended_ev, _blend_mid_ref, scenarios_out.bull.blended_ev):
            raise ValueError("scenario ordering failed (blended scenario band does not contain base valuation)")

        def _fmt_ev(v: float) -> str:
            return _fmt_money_millions(v, _quote_ccy)

        football_field = FootballField(
            bear=ScenarioSummary(
                name="Bear",
                dcf_ev=_fmt_ev(scenarios_out.bear.dcf_ev),
                comps_ev=_fmt_ev(scenarios_out.bear.comps_ev_mid),
                blended_ev=_fmt_ev(scenarios_out.bear.blended_ev),
                wacc=f"{scenarios_out.bear.wacc_used:.1%}",
                ebitda_margin=f"{scenarios_out.bear.ebitda_margin_used:.1%}",
            ),
            base=ScenarioSummary(
                name="Base",
                dcf_ev=_fmt_ev(scenarios_out.base.dcf_ev),
                comps_ev=_fmt_ev(scenarios_out.base.comps_ev_mid),
                blended_ev=_fmt_ev(scenarios_out.base.blended_ev),
                wacc=f"{scenarios_out.base.wacc_used:.1%}",
                ebitda_margin=f"{scenarios_out.base.ebitda_margin_used:.1%}",
            ),
            bull=ScenarioSummary(
                name="Bull",
                dcf_ev=_fmt_ev(scenarios_out.bull.dcf_ev),
                comps_ev=_fmt_ev(scenarios_out.bull.comps_ev_mid),
                blended_ev=_fmt_ev(scenarios_out.bull.blended_ev),
                wacc=f"{scenarios_out.bull.wacc_used:.1%}",
                ebitda_margin=f"{scenarios_out.bull.ebitda_margin_used:.1%}",
            ),
            dcf_range=(
                f"{_fmt_ev(scenarios_out.bear.dcf_ev)} — {_fmt_ev(scenarios_out.bull.dcf_ev)}"
            ),
            comps_range=(
                f"{_fmt_ev(scenarios_out.bear.comps_ev_mid)} — "
                f"{_fmt_ev(scenarios_out.bull.comps_ev_mid)}"
            ),
            blended_range=(
                f"{_fmt_ev(scenarios_out.bear.blended_ev)} — "
                f"{_fmt_ev(scenarios_out.bull.blended_ev)}"
            ),
        )
        # Improve method-table integrity: provide low/high for DCF and add blended band.
        for _m in val.methods:
            if _m.name == "DCF":
                _m.low = str(round(scenarios_out.bear.dcf_ev, 1))
                _m.high = str(round(scenarios_out.bull.dcf_ev, 1))
            elif _m.name.startswith("Trading Comps"):
                _m.low = str(round(scenarios_out.bear.comps_ev_mid, 1))
                _m.high = str(round(scenarios_out.bull.comps_ev_mid, 1))
        _blend_name = (
            "DCF-only Valuation"
            if (_w_dcf == 100 and _w_comp == 0 and _w_tx == 0)
            else "Blended Valuation"
        )
        if not (_w_dcf == 100 and _w_comp == 0 and _w_tx == 0):
            val.methods.append(
                ValuationMethod(
                    name=_blend_name,
                    low=str(round(scenarios_out.bear.blended_ev, 1)),
                    mid=str(round(blended_ev, 1)) if blended_ev else str(round(scenarios_out.base.blended_ev, 1)),
                    high=str(round(scenarios_out.bull.blended_ev, 1)),
                    weight=100,
                )
            )
        # Ensure base scenario reconciles with current method outputs and blend.
        if football_field.base:
            if result.dcf:
                football_field.base.dcf_ev = _fmt_ev(result.dcf.enterprise_value)
            if result.comps:
                football_field.base.comps_ev = _fmt_ev(result.comps.mid)
            if blended_ev:
                football_field.base.blended_ev = _fmt_ev(blended_ev)
        # Scenario-based fair-value range in price space, must contain point estimate.
        if (
            rec.intrinsic_price is not None
            and market_data
            and market_data.shares_outstanding
            and market_data.shares_outstanding > 0
        ):
            _nd = market_data.net_debt or 0.0
            _px_low = (scenarios_out.bear.blended_ev - _nd) / market_data.shares_outstanding
            _px_high = (scenarios_out.bull.blended_ev - _nd) / market_data.shares_outstanding
            _lo = min(_px_low, _px_high, rec.intrinsic_price)
            _hi = max(_px_low, _px_high, rec.intrinsic_price)
            sources.add(
                "Fair Value Range",
                f"{_fmt_price(_lo, _quote_ccy, decimals=2)}–{_fmt_price(_hi, _quote_ccy, decimals=2)}",
                "scenario_blended",
                "inferred",
            )
            result.field_sources["Fair Value Range"] = (
                f"{_fmt_price(_lo, _quote_ccy, decimals=2)}–{_fmt_price(_hi, _quote_ccy, decimals=2)}",
                "scenario_blended",
                "inferred",
            )
            _mid = ((_lo + _hi) / 2.0) if (_lo is not None and _hi is not None) else None
            if _mid and _mid > 0:
                _width_ratio = (_hi - _lo) / _mid
                if _width_ratio > 0.75:
                    sources.add_once(
                        "Fair Value Range Width",
                        f"{_width_ratio:.0%} of midpoint",
                        "valuation_engine",
                        "inferred",
                    )
                    result.field_sources["Fair Value Range Width"] = (
                        f"{_width_ratio:.0%} of midpoint",
                        "valuation_engine",
                        "inferred",
                    )
                    result.notes.append(
                        f"Fair value range is wide ({_width_ratio:.0%} of midpoint); confidence reduced."
                    )
                    if not valuation_failed and valuation_status == "OK":
                        valuation_status = "DEGRADED"
        console.print(
            f"  [bold]Football field:[/bold] Bear {football_field.bear.blended_ev if football_field.bear else 'N/A'} "
            f"/ Base {football_field.base.blended_ev if football_field.base else 'N/A'} "
            f"/ Bull {football_field.bull.blended_ev if football_field.bull else 'N/A'}"
        )
        if _w_comp > 0 and result.comps and result.comps.mid > 0:
            console.print(
                "  [dim]Interpretation: DCF anchors downside, comps anchor upside; "
                "wide spread implies higher model uncertainty.[/dim]"
            )
        else:
            console.print(
                "  [dim]Interpretation: comps unavailable/low-weight; valuation confidence is reduced.[/dim]"
            )
    except Exception as e:
        _scenario_err = str(e)
        if "sanity breaker triggered" in _scenario_err.lower():
            console.print(
                "  [yellow]Scenarios suppressed:[/yellow] valuation recommendation is INCONCLUSIVE "
                "after sanity-breaker trigger."
            )
        if "private screen-only mode" in _scenario_err.lower():
            console.print(
                "  [yellow]Scenarios suppressed:[/yellow] private run is screen-only until identity and revenue "
                "quality gates are satisfied."
            )
        if "scenario ordering failed" in _scenario_err.lower():
            quality.warnings.append("Scenario ordering failed; football field suppressed.")
            if valuation_status == "OK":
                valuation_status = "DEGRADED"
            console.print(
                "  [yellow]Scenarios suppressed:[/yellow] scenario ordering failed "
                "(low/base/high invariant violated)."
            )
        elif "failed currency/share normalization" in _scenario_err.lower():
            console.print(
                "  [yellow]Scenarios skipped:[/yellow] valuation blocked due to failed currency/share normalization."
            )
        elif "sanity breaker triggered" not in _scenario_err.lower():
            console.print(f"  [yellow]Scenarios skipped:[/yellow] {_scenario_err}")

    # ── 5c. IC SCORING ────────────────────────────────────────────────────
    try:
        from goldroger.ma.scoring import auto_score_from_valuation
        ic_result = auto_score_from_valuation(
            lbo_output=result.lbo,
            upside_pct=rec.upside_pct,
            sector=fund.sector or "",
            company=company,
            blended_ev=blended_ev,
            revenue=_rev_float,
            ebitda_margin=_ebitda_margin,
            ev_rev_sector_mid=_ev_rev_mid,
            ev_rev_sector_high=_ev_rev_high,
        )
        ic_summary = ICScoreSummary(
            ic_score=f"{ic_result.ic_score:.0f}/100",
            recommendation=ic_result.recommendation,
            strategy=f"{ic_result.dimension_scores.get('strategy', 5):.1f}/10",
            synergies=f"{ic_result.dimension_scores.get('synergies', 5):.1f}/10",
            financial=f"{ic_result.dimension_scores.get('financial', 5):.1f}/10",
            lbo=f"{ic_result.dimension_scores.get('lbo', 5):.1f}/10",
            integration=f"{ic_result.dimension_scores.get('integration', 5):.1f}/10",
            risk=f"{ic_result.dimension_scores.get('risk', 5):.1f}/10",
            rationale=ic_result.rationale,
            next_steps=ic_result.next_steps,
        )
        console.print(
            f"  IC Review Status: [bold]{ic_result.ic_score:.0f}/100[/bold] → {ic_result.recommendation} "
            f"[dim](WATCH = signal requires review before reliance)[/dim]"
        )
    except Exception as e:
        console.print(f"  [yellow]IC scoring skipped: {e}[/yellow]")

    log.data_confidence = result.data_confidence
    log.wacc_method = "capm" if result.data_confidence == "verified" else "estimated"
    log.valuation_notes = result.notes
    log.recommendation = rec.recommendation
    log.upside_pct = rec.upside_pct
    log.blended_ev = blended_ev
    log.end_step("valuation", t0)
    _done("Valuation Engine", t0)

    # ── 6. THESIS ─────────────────────────────────────────────────────────
    t0 = _step("Investment Thesis")
    thesis_status = "OK"
    if _report_mode == "quick":
        _report_timeout = _REPORT_WRITER_TIMEOUT_QUICK
    elif _report_mode == "full":
        _report_timeout = _REPORT_WRITER_TIMEOUT_FULL
    else:
        _report_timeout = _REPORT_WRITER_TIMEOUT_STANDARD
    if quick_mode:
        _fv_range = (result.field_sources.get("Fair Value Range") or ("N/A", "", ""))[0]
        _prof = get_sector_profile(
            fund.sector or "",
            _target_industry if "_target_industry" in locals() else "",
        )
        if company_type == "private":
            _private_reason = (
                "archetype-based deterministic fallback (private screen-only mode)"
                if _private_screen_only
                else "archetype-based deterministic fallback"
            )
            thesis = _build_fallback_thesis(
                company=company,
                sector=fund.sector or "",
                recommendation=val.recommendation or "INCONCLUSIVE",
                reason=_private_reason,
                model_signal=_model_signal_for_text,
                industry=_target_industry if "_target_industry" in locals() else "",
                ticker=(market_data.ticker if market_data else ""),
            )
            thesis.catalysts = _sanitize_catalysts(thesis.catalysts)[:3]
        else:
            _drivers = ", ".join(_prof.demand_drivers[:2]) if _prof.demand_drivers else "demand resilience and execution discipline"
            _margins = ", ".join(_prof.margin_drivers[:2]) if _prof.margin_drivers else "mix and operating leverage"
            _risk_list = list(_prof.common_risks[:3]) if _prof.common_risks else [
                "demand-cycle volatility",
                "regulatory/policy pressure",
                "peer-vs-DCF valuation dispersion",
            ]
            _quick_text = (
                "Thesis:\n"
                f"- Sector profile: {_prof.label or (fund.sector or 'Default fallback')}.\n"
                f"- Demand drivers: {_drivers}.\n"
                f"- Margin drivers: {_margins}.\n"
                f"- Valuation signal: {_model_signal_for_text} ({val.upside_downside or 'N/A'}); "
                f"final recommendation {_rec} due to confidence/dispersion guardrails.\n"
                f"- Indicative fair value: {_fv_range} (use range over point estimate when confidence is low).\n"
                "\nRisks:\n"
                + "".join(f"- {r}.\n" for r in _risk_list)
            ).rstrip()
            thesis = InvestmentThesis(
                thesis=_quick_text,
                catalysts=_sanitize_catalysts(
                    _fallback_catalysts(
                        company,
                        fund.sector or "",
                        _target_industry if "_target_industry" in locals() else "",
                        ticker=(market_data.ticker if market_data else ""),
                    )
                )[:3],
                key_questions=[
                    "What metric would move conviction most next quarter?",
                    "How robust is margin durability vs peers?",
                    "What catalyst could change the recommendation?",
                ],
            )
        log.end_step("thesis", t0)
        _done("Investment Thesis", t0)
    elif cli_mode and _research_source != "source_backed":
        thesis_status = "DEGRADED"
        console.print(
            "  [yellow]Research is fallback/partial; using archetype-based deterministic fallback thesis (no unsourced specifics).[/yellow]"
        )
        thesis = _build_fallback_thesis(
            company=company,
            sector=fund.sector or "",
            recommendation=val.recommendation or "HOLD",
            reason="research fallback mode (source-backed market context unavailable)",
            model_signal=_model_signal_for_text,
            industry=_target_industry if "_target_industry" in locals() else "",
            ticker=(market_data.ticker if market_data else ""),
        )
        thesis.catalysts = _sanitize_catalysts(thesis.catalysts)
        log.end_step("thesis", t0)
        _done("Investment Thesis", t0)
    elif (time.time() - _run_started) > _total_budget_s:
        thesis_status = "TIMEOUT"
        console.print(
            f"  [yellow]Global runtime budget exceeded ({_total_budget_s}s) — using short fallback thesis.[/yellow]"
        )
        thesis = _build_fallback_thesis(
            company=company,
            sector=fund.sector or "",
            recommendation=val.recommendation or "HOLD",
            reason="global runtime budget reached",
            model_signal=_model_signal_for_text,
            industry=_target_industry if "_target_industry" in locals() else "",
            ticker=(market_data.ticker if market_data else ""),
        )
        thesis.catalysts = _sanitize_catalysts(thesis.catalysts)
        log.end_step("thesis", t0)
        _done("Investment Thesis", t0)
    else:
        _thesis_ctx = {
            "sector": fund.sector or "",
            "valuation": val.implied_value,
            "recommendation": val.recommendation,
            "upside": val.upside_downside,
            "wacc": result.wacc_used,
            "market": mkt.market_size,
            "verified_revenue": (
                str(market_data.revenue_ttm)
                if market_data and market_data.revenue_ttm
                else fin.revenue_current or "unknown"
            ),
            "revenue_confidence": (
                market_data.confidence if market_data and market_data.revenue_ttm else "estimated"
            ),
            "ebitda_margin": (
                f"{market_data.ebitda_margin:.1%}"
                if market_data and market_data.ebitda_margin is not None
                else fin.ebitda_margin or ""
            ),
            "company_identifier": company_identifier,
            "country_hint": country_hint or "",
            "identity_note": (
                f"Confirmed legal entity: {fund.company_name} "
                f"(Companies House #{company_identifier}, {country_hint or 'unknown country'})"
                if company_identifier else f"Confirmed legal entity: {fund.company_name}"
            ),
            "registry_facts": (
                market_data.additional_metadata if market_data and isinstance(market_data.additional_metadata, dict) else {}
            ),
            "strict_registry_mode": (
                bool(
                    company_type == "private"
                    and company_identifier
                    and market_data
                    and market_data.data_source == "companies_house"
                    and not market_data.revenue_ttm
                )
            ),
            "run_date": date.today().isoformat(),
            "recent_window_months": 6,
            "quick_mode": quick_mode,
            "cli_mode": cli_mode,
            "debug_retries": debug,
            "report_mode": _report_mode,
            "market_context_source_backed": bool(market_context_pack and market_context_pack.source_backed),
            "market_context_trends": [x.text for x in ((market_context_pack.trends if market_context_pack else []) or [])][:4],
            "market_context_catalysts": [x.text for x in ((market_context_pack.catalysts if market_context_pack else []) or [])][:4],
            "market_context_risks": [x.text for x in ((market_context_pack.risks if market_context_pack else []) or [])][:4],
            "filings_source_count": int(filings_pack.source_count) if filings_pack else 0,
            "latest_filing_type": (filings_pack.latest.filing_type if (filings_pack and filings_pack.latest) else ""),
            "latest_filing_date": (filings_pack.latest.filing_date if (filings_pack and filings_pack.latest) else ""),
        }
        _tp = ThreadPoolExecutor(max_workers=1)
        _fut_thesis = _tp.submit(
            lambda: _parse_with_retry(
                thesis_agent,
                company,
                company_type,
                _thesis_ctx,
                InvestmentThesis,
                InvestmentThesis(thesis="N/A"),
                retry_on_fail=(not quick_mode),
                log_raw_errors=debug,
            )
        )
        try:
            thesis = _fut_thesis.result(timeout=_report_timeout)
        except FutureTimeoutError:
            thesis_status = "TIMEOUT"
            _fut_thesis.cancel()
            console.print(f"  [yellow]Investment thesis timeout > {_report_timeout}s — using structured fallback.[/yellow]")
            thesis = _build_fallback_thesis(
                company=company,
                sector=fund.sector or "",
                recommendation=val.recommendation or "HOLD",
                reason="thesis timeout",
                model_signal=_model_signal_for_text,
                ticker=(market_data.ticker if market_data else ""),
            )
        except APICapacityError:
            thesis_status = "DEGRADED_API_CAPACITY"
            console.print("  [yellow]ReportWriter LLM unavailable; using structured fallback.[/yellow]")
            thesis = _build_fallback_thesis(
                company=company,
                sector=fund.sector or "",
                recommendation=val.recommendation or "HOLD",
                reason="report-writer API capacity",
                model_signal=_model_signal_for_text,
                ticker=(market_data.ticker if market_data else ""),
            )
        except Exception as _th_err:
            thesis_status = "FAILED"
            console.print(f"  [yellow]Investment thesis failed: {_th_err}[/yellow]")
            thesis = _build_fallback_thesis(
                company=company,
                sector=fund.sector or "",
                recommendation=val.recommendation or "HOLD",
                reason="thesis generation failure",
                model_signal=_model_signal_for_text,
                ticker=(market_data.ticker if market_data else ""),
            )
        finally:
            _tp.shutdown(wait=False, cancel_futures=True)
        thesis.catalysts = _sanitize_catalysts(thesis.catalysts)
        log.end_step("thesis", t0)
        _done("Investment Thesis", t0)

    if thesis:
        _prof_key = detect_sector_profile(fund.sector or "", _target_industry if '_target_industry' in locals() else "")
        thesis.thesis = _sanitize_thesis_language(thesis.thesis or "")
        thesis.thesis = _enforce_profile_context_guard(thesis.thesis, _prof_key)
        thesis.bull_case = _sanitize_thesis_language(thesis.bull_case or "")
        thesis.base_case = _sanitize_thesis_language(thesis.base_case or "")
        thesis.bear_case = _sanitize_thesis_language(thesis.bear_case or "")
        if market_status in {"DEGRADED", "DEGRADED_API_CAPACITY", "FAILED", "TIMEOUT"}:
            thesis.thesis = _soften_unsourced_scenario_specificity(thesis.thesis or "")
            thesis.bull_case = _soften_unsourced_scenario_specificity(thesis.bull_case or "")
            thesis.base_case = _soften_unsourced_scenario_specificity(thesis.base_case or "")
            thesis.bear_case = _soften_unsourced_scenario_specificity(thesis.bear_case or "")
            thesis.catalysts = [
                _soften_unsourced_scenario_specificity(c)
                for c in (thesis.catalysts or [])
            ]
        # Default public reporting should remain concise unless full report is explicitly requested.
        if _report_mode != "full":
            thesis.bull_case = ""
            thesis.base_case = ""
            thesis.bear_case = ""

    if football_field and thesis and _report_mode == "full":
        if football_field.bear and thesis.bear_case:
            football_field.bear.narrative = thesis.bear_case[:200]
        if football_field.base and thesis.base_case:
            football_field.base.narrative = thesis.base_case[:200]
        if football_field.bull and thesis.bull_case:
            football_field.bull.narrative = thesis.bull_case[:200]

    if thesis and val:
        _fv_src = result.field_sources.get("Fair Value Range")
        _fv_txt = _fv_src[0] if _fv_src and _fv_src[0] else "N/A"
        _pt_txt = val.target_price or "N/A"
        if thesis.thesis:
            if _fv_txt and _fv_txt != "N/A":
                thesis.thesis = _re.sub(
                    r"(?i)\bfair value\s+(?:[A-Z]{3}\s+|\$)?\d[\d,]*(?:\.\d+)?\s*(?:-|–|to)\s*(?:[A-Z]{3}\s+|\$)?\d[\d,]*(?:\.\d+)?",
                    f"fair value {_fv_txt}",
                    thesis.thesis,
                )
            if _pt_txt and _pt_txt != "N/A":
                thesis.thesis = _re.sub(
                    r"(?i)\bpoint estimate\s+(?:[A-Z]{3}\s+|\$)?\d[\d,]*(?:\.\d+)?",
                    f"point estimate {_pt_txt}",
                    thesis.thesis,
                )

    # Final confidence adjustments after enrichment stage completes.
    if valuation_status == "DEGRADED":
        quality.score = max(0, quality.score - 6)
        quality.warnings.append("Valuation degraded (high dispersion or weak method agreement)")
    if thesis_status == "TIMEOUT":
        quality.score = max(0, quality.score - 6)
        quality.warnings.append("Thesis generation timed out")
        quality.checks["thesis"] = "timeout"
    elif thesis_status == "DEGRADED_API_CAPACITY":
        quality.score = max(0, quality.score - 8)
        quality.warnings.append("Thesis generation degraded (API capacity)")
        quality.checks["thesis"] = "degraded_api_capacity"
    elif thesis_status == "FAILED":
        quality.score = max(0, quality.score - 10)
        quality.warnings.append("Thesis generation failed")
        quality.checks["thesis"] = "failed"
    else:
        quality.checks["thesis"] = "ok"
    if market_status in {"DEGRADED", "DEGRADED_API_CAPACITY"}:
        quality.score = max(0, quality.score - 4)
    quality.score = max(0, min(100, quality.score))
    quality.tier = _quality_tier(quality.score)
    if (not quick_mode) and str(_research_source) == "fallback":
        if quality.score > 79:
            quality.score = 79
        quality.tier = _quality_tier(quality.score)
        if quality.tier == "A":
            quality.tier = "B"
        if "Source-backed market context unavailable; valuation input quality capped." not in quality.warnings:
            quality.warnings.append("Source-backed market context unavailable; valuation input quality capped.")
    _fx_src = str(normalization_audit.get("fx_source") or "").lower()
    _share_basis = str(normalization_audit.get("share_count_basis") or "")
    _norm_state = str(normalization_audit.get("status") or "").upper()
    _share_basis_unresolved = _share_basis in {
        "unknown_depositary_ratio",
        "foreign_us_listing_unverified_share_basis",
        "foreign_ordinary_unresolved",
    }
    if _fx_src == "static_fx_table" and quality.score > 79:
        quality.score = 79
        if "Static FX fallback in use; valuation input quality capped." not in quality.warnings:
            quality.warnings.append("Static FX fallback in use; valuation input quality capped.")
    if _share_basis_unresolved and quality.score > 79:
        quality.score = 79
        if "Share-basis normalization unresolved; valuation input quality capped." not in quality.warnings:
            quality.warnings.append("Share-basis normalization unresolved; valuation input quality capped.")
    if _norm_state == "FAILED":
        quality.is_blocked = True
        if "Failed currency/share normalization" not in quality.blockers:
            quality.blockers.append("Failed currency/share normalization")
        if quality.score > 40:
            quality.score = 40
    if _hard_suppression_reasons and quality.score > 40:
        quality.score = 40
    quality.tier = _quality_tier(quality.score)
    if quality.score <= 79 and quality.tier == "A":
        quality.tier = "B"
    sources.add(
        "Data Quality Score",
        f"{quality.score}/100 (Tier {quality.tier})",
        "quality_gate",
        "verified",
    )

    console.rule("[DONE EQUITY]")
    log.flush()

    if market_status == "SKIPPED_QUICK_MODE":
        _research_quality_score = None
        _research_quality_label = "skipped_quick_mode"
    else:
        _research_quality_score = 100
        _market_sources = [str(s) for s in (mkt.sources or []) if str(s).strip()]
        _has_source_backed_market = any(("http://" in s.lower()) or ("https://" in s.lower()) for s in _market_sources)
        _rq_profile = detect_sector_profile(fund.sector or "", _target_industry if '_target_industry' in locals() else "")
        _tam_optional_profiles = {
            "consumer_staples_tobacco",
            "financials_banks",
            "financials_insurance",
            "utilities",
            "energy_oil_gas",
            "materials_chemicals_mining",
        }
        if market_status in {"DEGRADED", "DEGRADED_API_CAPACITY"}:
            _research_quality_score -= 45
        elif market_status in {"FAILED", "TIMEOUT"}:
            _research_quality_score -= 60
        if _text_missing(mkt.market_size):
            _research_quality_score -= (3 if _rq_profile in _tam_optional_profiles else 10)
        if _text_missing(mkt.market_growth):
            _research_quality_score -= (3 if _rq_profile in _tam_optional_profiles else 10)
        _ms_txt = str(mkt.market_size or "").lower()
        _mg_txt = str(mkt.market_growth or "").lower()
        _tam_estimated = any(tok in _ms_txt for tok in ("estimated", "inferred", "proxy", "approx"))
        _growth_estimated = any(tok in _mg_txt for tok in ("estimated", "inferred", "proxy", "approx"))
        if _tam_estimated:
            _research_quality_score -= 8
        if _growth_estimated:
            _research_quality_score -= 8
        if _tam_estimated or _growth_estimated:
            _research_quality_score = min(_research_quality_score, 85)
        if not _has_source_backed_market:
            _research_quality_score -= 10
            _research_quality_score = min(_research_quality_score, 80)
        if thesis_status == "TIMEOUT":
            _research_quality_score -= 20
        elif thesis_status == "DEGRADED_API_CAPACITY":
            _research_quality_score -= 25
        elif thesis_status == "FAILED":
            _research_quality_score -= 30
        _research_quality_score = max(0, min(100, _research_quality_score))
        _research_quality_label = (
            "low" if _research_quality_score < 60 else "medium" if _research_quality_score < 80 else "high"
        )

    if ic_summary and ic_summary.ic_score:
        try:
            _ic_raw = float(str(ic_summary.ic_score).split("/", 1)[0])
            _ic_penalty = 0.0
            if valuation_status == "DEGRADED":
                _ic_penalty += 8
            elif valuation_status == "FAILED":
                _ic_penalty += 20
            if peers_status in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}:
                _ic_penalty += 8
            elif peers_status == "DEGRADED":
                _ic_penalty += 5
            if quality.score < 60:
                _ic_penalty += 6
            if _dcf_sanity_fail:
                _ic_penalty += 6
            _ic_final = max(0.0, _ic_raw - _ic_penalty)
            ic_summary.ic_score = f"{_ic_final:.0f}/100"
            if _ic_final < 45:
                ic_summary.recommendation = "WATCH"
            if ic_summary.rationale:
                ic_summary.rationale += (
                    f" Confidence-adjusted IC score after valuation reliability checks: {ic_summary.ic_score}."
                )
        except Exception:
            pass

    # Canonical peer-status taxonomy for user-facing consistency.
    if peers_status in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}:
        _peers_display_status = "PEERS_FAILED"
    elif not peer_multiples or peer_multiples.n_valuation_peers <= 0:
        _peers_display_status = "PEERS_FAILED"
    else:
        _pure_share = float(peer_multiples.pure_peer_weight_share or 0.0)
        _eff_disp = float(peer_multiples.effective_peer_count or 0.0)
        _low_div = bool(_eff_disp > 0 and _eff_disp < 5.0)
        if _pure_share >= 0.80 and not _low_div:
            _peers_display_status = "PURE_COMPS_OK"
        elif _pure_share >= 0.50:
            _peers_display_status = "MIXED_COMPS_OK"
        elif _pure_share > 0.0 and not _low_div:
            _peers_display_status = "ADJACENT_COMPS"
        elif _low_div:
            _peers_display_status = "ADJACENT_COMPS_LOW_DIVERSITY"
        elif _pure_share > 0.0 or _missing_consumer_ecosystem_bucket:
            _peers_display_status = "ADJACENT_COMPS_OK"
        else:
            _peers_display_status = "NO_PURE_COMPS"
    _peers_is_low_conf = _peers_display_status in {
        "ADJACENT_COMPS_LOW_DIVERSITY",
        "ADJACENT_COMPS",
        "ADJACENT_COMPS_OK",
        "MIXED_COMPS_OK",
        "NO_PURE_COMPS",
        "PEERS_DEGRADED",
        "PEERS_FAILED",
    }

    _peer_quality_score = 100
    if peer_multiples:
        if peer_multiples.n_valuation_peers < (3 if quick_mode else 5):
            _peer_quality_score -= 15
        if peer_multiples.effective_peer_count > 0 and peer_multiples.effective_peer_count < 5.0:
            _peer_quality_score -= 10
        _pure_share = float(peer_multiples.pure_peer_weight_share or 0.0)
        if _pure_share <= 0.0:
            _peer_quality_score = min(_peer_quality_score, 62)
        elif _pure_share < 0.25:
            _peer_quality_score = min(_peer_quality_score, 65)
        elif _pure_share < 0.50:
            _peer_quality_score = min(_peer_quality_score, 75)
        elif _pure_share < 0.80:
            _peer_quality_score = min(_peer_quality_score, 85)
        else:
            _peer_quality_score = min(_peer_quality_score, 100)
    else:
        _peer_quality_score -= 40
    if _missing_consumer_ecosystem_bucket:
        _peer_quality_score -= 10
    if _peers_display_status == "PEERS_FAILED":
        _peer_quality_score = min(_peer_quality_score, 45)
    _peer_quality_score = max(0, min(100, _peer_quality_score))

    _financial_data_quality_score = 100
    if not market_data:
        _financial_data_quality_score -= 40
    else:
        if market_data.revenue_ttm is None:
            _financial_data_quality_score -= 15
        if market_data.ebitda_margin is None:
            _financial_data_quality_score -= 15
        if market_data.current_price is None:
            _financial_data_quality_score -= 10
        if market_data.market_cap is None:
            _financial_data_quality_score -= 10
    _financial_data_quality_score = max(0, min(100, _financial_data_quality_score))

    _confidence_reasons: list[str] = []
    _profile_for_conf = detect_sector_profile(
        fund.sector or "",
        _target_industry if "_target_industry" in locals() else "",
    )
    _is_cyclical_profile = _profile_for_conf in {
        "materials_chemicals_mining",
        "energy_oil_gas",
        "industrials",
    }
    if peer_multiples and peer_multiples.n_valuation_peers < (3 if quick_mode else 5):
        _confidence_reasons.append("weak valuation peer count")
    if peer_multiples and peer_multiples.effective_peer_count > 0 and peer_multiples.effective_peer_count < (3.0 if quick_mode else 5.0):
        _confidence_reasons.append("low effective peer diversification")
    if _missing_consumer_ecosystem_bucket:
        _confidence_reasons.append("consumer-hardware ecosystem peers unavailable")
    if valuation_status == "DEGRADED":
        _confidence_reasons.append("high DCF/comps dispersion or method disagreement")
    if _dcf_sanity_fail:
        _confidence_reasons.append("DCF result is materially below market/comps cross-check")
    if normalization_blocked:
        _confidence_reasons.append("currency/share normalization failed")
    _norm_status = str(normalization_audit.get("status") or "").upper()
    _norm_share_basis = str(normalization_audit.get("share_count_basis") or "")
    if _norm_status == "OK_FX_NORMALIZED":
        _confidence_reasons.append("FX normalization used static conversion table")
    if _norm_share_basis == "foreign_us_listing_unverified_share_basis":
        _confidence_reasons.append("foreign issuer USD listing has unverified share basis")
    if _method_dispersion_ratio >= 2.0:
        _confidence_reasons.append(f"high method dispersion ({_method_dispersion_ratio:.1f}x)")
    if market_status in {"FAILED", "TIMEOUT", "DEGRADED", "DEGRADED_API_CAPACITY"}:
        _confidence_reasons.append("limited market context")
    if thesis_status in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}:
        _confidence_reasons.append("thesis generation degraded")
    if company_type == "private":
        if _private_revenue_quality == "UNAVAILABLE":
            _confidence_reasons.append("private revenue unavailable")
        elif _private_revenue_quality == "LOW_CONFIDENCE_ESTIMATE":
            _confidence_reasons.append("private revenue low-confidence estimate (indicative only)")
        elif _private_revenue_quality == "MANUAL":
            _confidence_reasons.append("manual user-provided revenue input (unverified)")
        if _private_triangulation_used:
            _confidence_reasons.append("triangulated private revenue input")
        if not _private_identity_resolved:
            _confidence_reasons.append("private legal identity not strongly resolved")
    if _hard_suppression_reasons:
        _confidence_reasons.append("sanity breaker triggered (recommendation suppressed)")
    if company_type == "public" and _is_cyclical_profile:
        _confidence_reasons.append(
            "commodity/cyclical caution: valuation may reflect current-cycle margins rather than mid-cycle normalization"
        )
    if company_type == "public" and _is_cyclical_profile and _cyclical_review_required:
        _confidence_reasons.append(
            "mid-cycle EBITDA support missing (cyclical_review_required)"
        )
    if _extreme_signal_review:
        _req = 2 if _extreme_negative_signal else 3
        _missing_txt = ", ".join(sorted(set(_extreme_signal_missing))[:4]) if _extreme_signal_missing else "none"
        _confidence_reasons.append(
            f"extreme_signal_review (corroboration anchors: {_extreme_signal_corroboration}/{_req}; missing: {_missing_txt})"
        )
    _dcf_status = "normal"
    _dcf_src = result.field_sources.get("DCF Status")
    if _dcf_src and _dcf_src[0]:
        _dcf_status = str(_dcf_src[0])
    elif _dcf_sanity_fail:
        _dcf_status = "conservative / degraded"

    _confidence_is_low = bool(
        valuation_status in {"FAILED", "DEGRADED"}
        or _peers_is_low_conf
        or market_status in {"FAILED", "TIMEOUT", "DEGRADED", "DEGRADED_API_CAPACITY"}
        or thesis_status in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}
    )
    _confidence_is_medium = bool(
        (not _confidence_is_low)
        and (
            _method_dispersion_ratio >= 1.4
            or _peers_display_status in {"MIXED_COMPS_OK", "ADJACENT_COMPS", "ADJACENT_COMPS_OK", "NO_PURE_COMPS"}
            or str(_research_source) != "source_backed"
        )
    )
    _confidence_level = "Low" if _confidence_is_low else ("Medium" if _confidence_is_medium else "High")
    if company_type == "private" and _private_revenue_quality == "MANUAL":
        if _private_identity_status == "RESOLVED":
            if _confidence_level == "High":
                _confidence_level = "Medium"
        else:
            _confidence_level = "Low"
    if company_type == "public" and _is_cyclical_profile and _confidence_level == "High":
        _confidence_level = "Medium"
    if company_type == "public" and _is_cyclical_profile:
        _cyclical_warn = (
            "Commodity/cyclical caution: valuation uses current EBITDA margin and may not reflect mid-cycle pricing."
        )
        if _cyclical_warn not in quality.warnings:
            quality.warnings.append(_cyclical_warn)
    if company_type == "public" and _is_cyclical_profile and _cyclical_review_required:
        _cy_warn = (
            "Cyclical review required — mid-cycle EBITDA support missing; valuation may reflect current-cycle margins."
        )
        if _cy_warn not in quality.warnings:
            quality.warnings.append(_cy_warn)
    if _norm_status == "OK_FX_NORMALIZED" and _confidence_level == "High":
        _confidence_level = "Medium"
    if _norm_share_basis == "foreign_us_listing_unverified_share_basis":
        _confidence_level = "Low"
    if (not quick_mode) and str(_research_source) == "fallback":
        _confidence_level = "Low"
    if market_status == "SKIPPED_QUICK_MODE":
        _research_status_enum = "RESEARCH_SKIPPED_QUICK_MODE"
    elif market_status in {"FAILED", "TIMEOUT"} and thesis_status in {"FAILED", "TIMEOUT"}:
        _research_status_enum = "RESEARCH_FAILED"
    elif _research_source == "source_backed" and market_status == "OK" and thesis_status not in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}:
        _research_status_enum = "RESEARCH_OK"
    elif _research_source == "source_backed":
        _research_status_enum = "RESEARCH_PARTIAL_SOURCE_BACKED"
    else:
        _research_status_enum = "RESEARCH_PARTIAL_FALLBACK"

    _recommendation_cap_reason = _private_cap_reason if company_type == "private" else ""
    _missing_corroboration_items = sorted(set(_extreme_signal_missing))
    _raw_vs_final_reason = ""

    # Deterministic recommendation policy: separate raw valuation signal from final recommendation.
    if (
        not valuation_failed
        and company_type == "public"
        and not _suppressed_no_rating
        and valuation_status != "FAILED"
    ):
        if _confidence_level == "Low":
            if _raw_signal_label.startswith("Positive"):
                _rec = "BUY / LOW CONVICTION"
            elif _raw_signal_label.startswith("Negative"):
                _rec = "HOLD / LOW CONVICTION"
            else:
                _rec = "HOLD / LOW CONVICTION"
        elif _confidence_level == "Medium":
            if _raw_signal_label.startswith("Positive"):
                _rec = "BUY / MODERATE CONVICTION"
            elif _raw_signal_label.startswith("Negative"):
                _rec = "SELL / MODERATE CONVICTION"
            else:
                _rec = "HOLD"
        else:
            if _raw_signal_label.startswith("Positive"):
                _rec = "BUY"
            elif _raw_signal_label.startswith("Negative"):
                _rec = "SELL"
            else:
                _rec = "HOLD"
        # Full-mode fallback should never output high-conviction calls.
        if (not quick_mode) and str(_research_source) != "source_backed" and _rec in {"BUY", "SELL", "BUY / MODERATE CONVICTION", "SELL / MODERATE CONVICTION"}:
            _rec = "BUY / LOW CONVICTION" if _raw_signal_label.startswith("Positive") else "HOLD / LOW CONVICTION"
            _recommendation_cap_reason = (
                "source-backed quantitative market inputs unavailable in full mode; conviction capped"
            )
        # Cyclical guardrail: high upside without normalization support is watchlist-only.
        if (
            company_type == "public"
            and _is_cyclical_profile
            and _cyclical_review_required
            and rec.upside_pct is not None
            and rec.upside_pct >= 0.50
        ):
            _rec = "HOLD / LOW CONVICTION"
            _recommendation_cap_reason = (
                "cyclical_review_required: upside exceeds cyclical plausibility threshold without mid-cycle normalization support"
            )
        # Mature-company extreme signal review cap.
        if _extreme_signal_review and _extreme_signal_cap_required:
            if rec.upside_pct is not None and rec.upside_pct > 0:
                _rec = "HOLD / LOW CONVICTION"
            elif rec.upside_pct is not None and rec.upside_pct < 0:
                _rec = "HOLD / LOW CONVICTION"
            if not _recommendation_cap_reason:
                _recommendation_cap_reason = (
                    "extreme_signal_review: implied upside/downside exceeds mature/cyclical plausibility thresholds without sufficient corroboration"
                )
        val.recommendation = _rec
    elif valuation_failed or _suppressed_no_rating or valuation_status == "FAILED":
        _rec = "INCONCLUSIVE"
        val.recommendation = _rec
    if _rec == "INCONCLUSIVE" and _hard_suppression_reasons:
        _recommendation_cap_reason = "integrity warnings triggered sanity-breaker suppression"
    if _model_signal_for_text and _rec and _rec not in {"N/A", ""} and _model_signal_for_text != _rec:
        _raw_vs_final_reason = (
            _recommendation_cap_reason
            or "raw model signal capped by confidence and plausibility guardrails"
        )

    # Final canonical sync after confidence guardrails so every module uses the same recommendation string.
    if thesis and val:
        _fv_src = result.field_sources.get("Fair Value Range")
        _fv_txt = _fv_src[0] if _fv_src and _fv_src[0] else "N/A"
        _pt_txt = val.target_price or "N/A"
        _final_rec = val.recommendation or _rec or "N/A"
        thesis.thesis = _sync_canonical_recommendation_text(
            thesis.thesis or "",
            _fv_txt,
            _pt_txt,
            _final_rec,
        )

    _market_context_latest_date = ""
    if market_context_pack is not None:
        _ctx_dates = [
            str(x.date).strip()
            for x in [
                *(market_context_pack.trends or []),
                *(market_context_pack.catalysts or []),
                *(market_context_pack.risks or []),
            ]
            if str(getattr(x, "date", "")).strip()
        ]
        if _ctx_dates:
            _market_context_latest_date = max(_ctx_dates)
    _qual_context_backed_available = bool(_market_source_backed)
    _qual_context_used_in_thesis = bool(_qual_context_backed_available and thesis_status not in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"})
    _quant_market_inputs_available = bool(
        _qual_context_backed_available
        and (
            (not _text_missing(mkt.market_growth))
            or (not _text_missing(mkt.market_size))
        )
    )
    # Current prototype does not deterministically map qualitative context into valuation assumptions.
    _quant_market_inputs_used_in_valuation = False
    if market_status == "SKIPPED_QUICK_MODE":
        _research_depth = "none"
    elif _research_source != "source_backed":
        _research_depth = "limited"
    elif thesis_status in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}:
        _research_depth = "partial"
    elif not _quant_market_inputs_available:
        _research_depth = "partial"
    else:
        _research_depth = "full"
    if _qual_context_backed_available and (not _quant_market_inputs_available):
        if "Source-backed quantitative market assumptions unavailable." not in quality.warnings:
            quality.warnings.append("Source-backed quantitative market assumptions unavailable.")
        if quality.score > 84:
            quality.score = 84
            quality.tier = _quality_tier(quality.score)
    if market_status == "SKIPPED_QUICK_MODE":
        _research_collection_semantic = "unavailable"
    elif _market_source_backed and market_context_pack is not None and bool(market_context_pack.fallback_used):
        _research_collection_semantic = "mixed"
    elif _market_source_backed:
        _research_collection_semantic = "source-backed"
    elif market_status in {"FAILED", "TIMEOUT"} and market_context_pack is None:
        _research_collection_semantic = "unavailable"
    elif _research_source == "fallback":
        _research_collection_semantic = "fallback"
    else:
        _research_collection_semantic = "mixed"
    if _qual_context_backed_available:
        _qual_context_semantic = "source-backed"
    elif market_status == "SKIPPED_QUICK_MODE":
        _qual_context_semantic = "fallback"
    elif _research_source == "fallback":
        _qual_context_semantic = "fallback"
    else:
        _qual_context_semantic = "unavailable"
    _quant_context_semantic = "available" if _quant_market_inputs_available else "unavailable"
    if thesis_status == "TIMEOUT":
        _thesis_mode_semantic = "timeout fallback"
    elif thesis_status in {"FAILED", "DEGRADED_API_CAPACITY"}:
        _thesis_mode_semantic = "generic fallback"
    elif (_report_mode == "quick") or (_research_source != "source_backed"):
        _thesis_mode_semantic = "deterministic archetype fallback"
    else:
        _thesis_mode_semantic = "source-backed"

    analysis = AnalysisResult(
        company=company,
        company_type=company_type,
        fundamentals=fund,
        market=mkt,
        financials=fin,
        valuation=val,
        thesis=thesis,
        football_field=football_field,
        peer_comps=peer_comps_table,
        ic_score=ic_summary,
        data_quality={
            "score": quality.score,
            "tier": quality.tier,
            "is_blocked": quality.is_blocked,
            "blockers": quality.blockers,
            "warnings": quality.warnings,
            "checks": quality.checks,
            "pipeline_status": {
                "market_analysis": market_status,
                "peers": _peers_display_status,
                "valuation": valuation_status,
                "thesis": thesis_status,
                "core_valuation": (
                    "FAILED"
                    if valuation_status == "FAILED"
                    else (
                        "DEGRADED"
                        if valuation_status == "DEGRADED" or _peers_is_low_conf
                        else "OK"
                    )
                ),
                "research_enrichment": (
                    _research_status_enum
                ),
                "model_signal": _raw_signal_label,
                "model_signal_raw": _model_signal,
                "model_signal_detail": _raw_signal_label,
                "recommendation": _rec,
                "dcf_status": _dcf_status,
                "method_dispersion_ratio": round(_method_dispersion_ratio, 3),
                "method_dispersion_level": (
                    "N/A — valuation scenarios suppressed by sanity breaker"
                    if _suppressed_no_rating
                    else _method_dispersion_level
                ),
                "effective_peer_count": (round(_eff_peer_count, 2) if _eff_peer_count > 0 else None),
                "pure_peer_weight": (round(float(peer_multiples.pure_peer_weight_share or 0.0), 4) if peer_multiples else None),
                "adjacent_peer_weight": (round(float(peer_multiples.adjacent_peer_weight_share or 0.0), 4) if peer_multiples else None),
                "confidence": _confidence_level,
                "confidence_reason": (
                    "; ".join(_confidence_reasons)
                    if _confidence_reasons
                    else "valuation inputs and enrichment are consistent"
                ),
                "cyclical_review_required": bool(_cyclical_review_required),
                "normalized_ebitda_supported": bool(_normalized_ebitda_supported),
                "mid_cycle_ebitda_m": (
                    round(float(_normalized_ebitda_proxy), 2)
                    if (_normalized_ebitda_proxy is not None and _normalized_ebitda_proxy > 0)
                    else None
                ),
                "extreme_signal_review": bool(_extreme_signal_review),
                "extreme_signal_corroboration": int(_extreme_signal_corroboration),
                "extreme_signal_missing_corroboration": sorted(set(_extreme_signal_missing)),
                "extreme_signal_anchor_labels": sorted(set(_extreme_signal_anchor_labels)),
                "recommendation_cap_reason": _recommendation_cap_reason,
                "raw_vs_final_reason": _raw_vs_final_reason,
                "research_source": _research_source,
                "research_depth": _research_depth,
                "research_collection_semantic": _research_collection_semantic,
                "qualitative_context_semantic": _qual_context_semantic,
                "quantitative_market_inputs_semantic": _quant_context_semantic,
                "thesis_mode_semantic": _thesis_mode_semantic,
                "market_data_source_backed": "yes" if _market_source_backed else "no",
                "market_context_source_backed": "yes" if _market_source_backed else "no",
                "source_backed_market_context_available": _qual_context_backed_available,
                "source_backed_market_context_used_in_thesis": _qual_context_used_in_thesis,
                "source_backed_quant_market_inputs_available": _quant_market_inputs_available,
                "source_backed_quant_market_inputs_used_in_valuation": _quant_market_inputs_used_in_valuation,
                "market_context_source_count": (
                    int(market_context_pack.source_count)
                    if market_context_pack is not None
                    else (None if company_type == "private" else 0)
                ),
                "market_context_relevant_source_count": (
                    int(market_context_pack.relevant_source_count)
                    if market_context_pack is not None
                    else (None if company_type == "private" else 0)
                ),
                "market_context_fetched_source_count": (
                    int(market_context_pack.fetched_source_count)
                    if market_context_pack is not None
                    else (None if company_type == "private" else 0)
                ),
                "market_context_latest_source_date": _market_context_latest_date,
                "market_context_fallback_used": (
                    bool(market_context_pack.fallback_used)
                    if market_context_pack is not None
                    else (not _market_source_backed)
                ),
                "filings_source_backed": bool(filings_pack.source_backed) if filings_pack else False,
                "filings_source_count": int(filings_pack.source_count) if filings_pack else 0,
                "filings_latest_type": (
                    str(filings_pack.latest.filing_type)
                    if (filings_pack and filings_pack.latest)
                    else "unavailable"
                ),
                "filings_latest_date": (
                    str(filings_pack.latest.filing_date)
                    if (filings_pack and filings_pack.latest and filings_pack.latest.filing_date)
                    else ""
                ),
                "normalization_status": str(normalization_audit.get("status") or "UNKNOWN"),
                "normalization_reason": str(normalization_audit.get("reason") or ""),
                "quote_currency": str(normalization_audit.get("quote_currency") or "unknown"),
                "financial_statement_currency": str(normalization_audit.get("financial_statement_currency") or "unknown"),
                "market_cap_currency": str(normalization_audit.get("market_cap_currency") or "unknown"),
                "listing_type": str(normalization_audit.get("listing_type") or "unknown"),
                "selected_listing": str(normalization_audit.get("selected_listing") or "unknown"),
                "primary_listing": str(normalization_audit.get("primary_listing") or "unknown"),
                "listing_exchange": str(normalization_audit.get("exchange") or "unknown"),
                "listing_country": str(normalization_audit.get("country") or "unknown"),
                "share_count_basis": str(normalization_audit.get("share_count_basis") or "unknown"),
                "adr_detected": bool(normalization_audit.get("adr_detected")),
                "depository_receipt_detected": bool(normalization_audit.get("depository_receipt_detected")),
                "adr_ratio": normalization_audit.get("adr_ratio"),
                "fx_source": str(normalization_audit.get("fx_source") or "n/a"),
                "fx_confidence": str(normalization_audit.get("fx_confidence") or "n/a"),
                "fx_timestamp": str(normalization_audit.get("fx_timestamp") or "n/a"),
                "sanity_breaker_triggered": bool(_hard_suppression_reasons),
                "peer_quality_score": _peer_quality_score,
                "financial_data_quality_score": _financial_data_quality_score,
                "report_mode": _report_mode,
                "company_type": company_type,
                "private_revenue_status": _private_revenue_status if company_type == "private" else "",
                "private_revenue_quality": _private_revenue_quality if company_type == "private" else "",
                "private_triangulation_used": bool(_private_triangulation_used) if company_type == "private" else False,
                "private_identity_resolved": bool(_private_identity_resolved) if company_type == "private" else True,
                "private_identity_status": _private_identity_status if company_type == "private" else "",
                "private_identity_source_state": _private_identity_source_state if company_type == "private" else "",
                "private_financials_quality": _private_financials_quality if company_type == "private" else "",
                "private_peers_state": _private_peers_state if company_type == "private" else "",
                "private_valuation_mode": _private_valuation_mode if company_type == "private" else "",
                "private_screen_only_reasons": list(dict.fromkeys(_private_screen_only_reasons)) if company_type == "private" else [],
                "private_state": _private_state if company_type == "private" else "",
                "private_provider_state": _private_provider_state if company_type == "private" else "",
                "private_manual_revenue_used": bool(_private_manual_revenue_used) if company_type == "private" else False,
                "private_used_providers": list(_private_used_providers) if company_type == "private" else [],
                "private_skipped_providers": list(_private_skipped_providers) if company_type == "private" else [],
            },
            "timings_s": {
                "market_data": log.step_times.get("market_data"),
                "fundamentals": log.step_times.get("fundamentals"),
                "market_analysis": log.step_times.get("market_analysis"),
                "peer_selection": log.step_times.get("peer_selection"),
                "peer_validation": log.step_times.get("peer_validation"),
                "peers": log.step_times.get("peers"),
                "tx_comps": log.step_times.get("tx_comps"),
                "research_total": round(
                    float(log.step_times.get("market_analysis") or 0.0)
                    + float(log.step_times.get("peer_selection") or 0.0)
                    + float(log.step_times.get("tx_comps") or 0.0),
                    2,
                ),
                "financials": log.step_times.get("financials"),
                "valuation": log.step_times.get("valuation"),
                "thesis": log.step_times.get("thesis"),
                "total": round(time.time() - log.started_at, 2),
            },
            "core_data_quality_score": _core_quality_score,
            "research_enrichment_quality_score": _research_quality_score,
            "research_enrichment_quality_label": _research_quality_label,
            "sourcing": {
                "filings_pack": filings_pack.to_dict() if filings_pack else None,
                "market_context_pack": market_context_pack.to_dict() if market_context_pack else None,
            },
        },
        sources_md=sources.to_markdown(),
    )
    fill_gaps(analysis, fund.sector or "")
    if market_status == "SKIPPED_QUICK_MODE":
        analysis.market.market_size = "Not available in quick mode"
        analysis.market.market_growth = "Not available in quick mode"
        if _text_missing(analysis.market.market_segment):
            analysis.market.market_segment = "Not available in quick mode"
        analysis.market.data_status = "SKIPPED_QUICK_MODE"
        analysis.market.key_trends = []
    elif market_status == "DEGRADED":
        analysis.market = _ensure_market_analysis_contract(analysis.market)
        if _text_missing(analysis.market.market_size):
            analysis.market.market_size = "Not available from current queries"
        if _text_missing(analysis.market.market_growth):
            analysis.market.market_growth = "Not available from current queries"
        _source_backed = _has_source_backed_market_data(analysis.market)
        if not _source_backed:
            analysis.market.market_size = "Not available from current queries"
            analysis.market.market_growth = "Not available from current queries"
        _has_seg = not _text_missing(analysis.market.market_segment)
        _has_comp = bool([c for c in (analysis.market.main_competitors or []) if getattr(c, "name", None)])
        _has_reg = any("regulat" in str(t).lower() or "policy" in str(t).lower() for t in (analysis.market.key_trends or []))
        _missing_txt = ", ".join(analysis.market.missing_fields or []) or "none"
        analysis.market.market_segment = (
            f"Data status: {analysis.market.data_status or 'PARTIAL'} | "
            f"Missing: {_missing_txt} | "
            f"Segment trends: {'available' if _has_seg else 'unavailable'} | "
            f"Competitive landscape: {'available' if _has_comp else 'unavailable'} | "
            f"Regulatory context: {'available' if _has_reg else 'unavailable'}"
        )
        _existing_trends = [str(t) for t in (analysis.market.key_trends or []) if str(t).strip()]
        _usable_trends = [t for t in _existing_trends if not _trend_is_placeholder(t)]
        _prof = get_sector_profile(fund.sector or "", _target_industry if '_target_industry' in locals() else "")
        if not _source_backed:
            _fallback_ctx = list(_prof.fallback_market_context) if _prof.fallback_market_context else [
                "Demand trend: category demand visibility is limited in this run.",
                "Competitive trend: peer positioning should be interpreted with caution.",
                "Regulatory trend: policy and macro conditions remain external variables.",
            ]
            analysis.market.key_trends = list(_fallback_ctx[:4])
            _prof_key = detect_sector_profile(fund.sector or "", _target_industry if '_target_industry' in locals() else "")
            analysis.market.key_trends = [_enforce_profile_context_guard(t, _prof_key) for t in analysis.market.key_trends]
        elif not _usable_trends:
            _fallback_ctx = list(_prof.fallback_market_context) if _prof.fallback_market_context else [
                "Demand trend: category demand visibility is limited in this run.",
                "Competitive trend: peer positioning should be interpreted with caution.",
                "Regulatory trend: policy and macro conditions remain external variables.",
            ]
            analysis.market.key_trends = list(_fallback_ctx[:4])
            _prof_key = detect_sector_profile(fund.sector or "", _target_industry if '_target_industry' in locals() else "")
            analysis.market.key_trends = [_enforce_profile_context_guard(t, _prof_key) for t in analysis.market.key_trends]
        else:
            analysis.market.key_trends = _usable_trends
    elif market_analysis_failed:
        analysis.market.market_size = "Not available"
        analysis.market.market_growth = "Not available"
        analysis.market.market_segment = "Not available"
        analysis.market.data_status = "FAILED"
        analysis.market.key_trends = []
    if market_status != "SKIPPED_QUICK_MODE":
        analysis.market = _ensure_market_analysis_contract(analysis.market)
        _src_backed_market = _has_source_backed_market_data(analysis.market)
        if (not _src_backed_market) and str(analysis.market.data_status or "").upper() != "COMPLETE":
            analysis.market.market_size = "Not available from current queries"
            analysis.market.market_growth = "Not available from current queries"
    _tam_v = analysis.market.market_size or "Not available"
    _mg_v = analysis.market.market_growth or "Not available"
    if market_status == "SKIPPED_QUICK_MODE":
        sources.add_once("TAM", _tam_v, "skipped_quick_mode", "skipped")
        sources.add_once("Market Growth", _mg_v, "skipped_quick_mode", "skipped")
    else:
        _tam_unavail = "not available" in str(_tam_v).lower()
        _mg_unavail = "not available" in str(_mg_v).lower()
        _src_backed = _has_source_backed_market_data(analysis.market)
        _tam_conf = "unavailable" if _tam_unavail else ("verified" if _src_backed else "estimated")
        _mg_conf = "unavailable" if _mg_unavail else ("verified" if _src_backed else "estimated")
        sources.add_once(
            "TAM",
            _tam_v,
            (
                "market_analysis_unavailable"
                if _tam_unavail
                else ("market_analysis_verified" if _tam_conf == "verified" else "market_analysis_estimate")
            ),
            _tam_conf,
        )
        sources.add_once(
            "Market Growth",
            _mg_v,
            (
                "market_analysis_unavailable"
                if _mg_unavail
                else ("market_analysis_verified" if _mg_conf == "verified" else "market_analysis_estimate")
            ),
            _mg_conf,
        )
    analysis.sources_md = sources.to_markdown()
    return analysis
