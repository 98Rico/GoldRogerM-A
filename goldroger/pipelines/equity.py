"""Equity analysis pipeline — run_analysis()."""
from __future__ import annotations

import re as _re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date
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
from goldroger.data.fetcher import MarketData, fetch_market_data, resolve_ticker
from goldroger.data.private_quality import merge_private_market_data
from goldroger.data.quality_gate import assess_data_quality
from goldroger.data.sector_profiles import get_sector_profile, detect_sector_profile
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
    return txt


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


def _fallback_catalysts(company: str, sector: str, industry: str = "") -> list[str]:
    prof = get_sector_profile(sector or "", industry or "")
    if prof.fallback_catalysts:
        return [str(x) for x in prof.fallback_catalysts]
    return [
        "Next earnings/filing update: demand, margins, and guidance.",
        "Strategy/product execution update: evidence of growth durability.",
        "Macro/regulatory developments: potential impact on assumptions.",
    ]


def _build_fallback_thesis(
    company: str,
    sector: str,
    recommendation: str,
    reason: str,
    model_signal: str = "N/A",
    industry: str = "",
) -> InvestmentThesis:
    prof = get_sector_profile(sector or "", industry or "")
    cats = _fallback_catalysts(company, sector, industry)
    _drivers = ", ".join(prof.demand_drivers[:3]) if prof.demand_drivers else "demand resilience and execution discipline"
    _margins = ", ".join(prof.margin_drivers[:3]) if prof.margin_drivers else "mix and operating leverage"
    _risks = ", ".join(prof.common_risks[:3]) if prof.common_risks else "competition, regulation, and macro volatility"
    thesis = (
        f"Thesis:\n"
        f"- Sector profile: {prof.label if prof.label else (sector or 'Default fallback')}.\n"
        f"- Demand drivers: {_drivers}.\n"
        f"- Margin drivers: {_margins}.\n"
        f"- Valuation: model signal is {model_signal}, but final recommendation is {recommendation} "
        f"because valuation confidence is low and method dispersion is high ({reason}).\n"
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
    quick_mode: bool = False,
    debug: bool = False,
    cli_mode: bool = False,
) -> AnalysisResult:
    log = new_run(company, company_type)
    _run_started = time.time()
    _total_budget_s = _TOTAL_TIMEOUT_QUICK if quick_mode else _TOTAL_TIMEOUT_FULL
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
            console.print("  [dim]No EU registry data found — revenue via web search fallback[/dim]")
        log.end_step("market_data", t0)

        # ── Data-source selection (interactive or CLI) ───────────────────
        from goldroger.data.source_selector import run_source_selection, resolve_source_selection
        _country_hint = (country_hint or _country_hint_from_market_data(market_data)).upper()
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
            if _sel.selected_providers:
                console.print(
                    "  [dim]Using additional sources:[/] " + ", ".join(_sel.selected_providers)
                )

        # Manual revenue override takes precedence over registry
        if _sel.manual_revenue_usd_m:
            from goldroger.data.fetcher import MarketData as _MD
            if market_data is None:
                market_data = _MD(
                    ticker="",
                    company_name=company,
                    sector="",
                    revenue_ttm=_sel.manual_revenue_usd_m,
                    data_source="manual (user input)",
                    confidence="verified",
                )
            else:
                market_data.revenue_ttm = _sel.manual_revenue_usd_m
                market_data.data_source = "manual (user input)"
                market_data.confidence = "verified"
            console.print(
                f"  [green]Manual revenue set:[/green] ${_sel.manual_revenue_usd_m:.0f}M"
            )
            sources.add(
                "Revenue TTM", f"${_sel.manual_revenue_usd_m:.0f}M",
                "manual (user input)", "verified",
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
        for _note in _merge.notes:
            console.print(f"  [dim]{_note}[/dim]")

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
                    market_data.revenue_ttm = tri.revenue_estimate_m
                    market_data.confidence = tri.confidence
                    market_data.data_source = "triangulation"
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

    if company_type == "public":
        t0 = _step("Market Data (yfinance)")
        ticker = resolve_ticker(company)
        if ticker:
            log.ticker = ticker
            console.print(f"  Resolved ticker: [bold]{ticker}[/bold]")
            market_data = fetch_market_data(ticker)
            if market_data:
                console.print(
                    f"  [green]Verified[/green] Rev=${market_data.revenue_ttm:.0f}M "
                    f"EBITDA={market_data.ebitda_margin:.1%} β={market_data.beta} "
                    f"MCap=${market_data.market_cap:.0f}M"
                )
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
                sources.add("Revenue TTM", f"${market_data.revenue_ttm:.0f}M", "yfinance", "verified")
                sources.add("EBITDA Margin", f"{market_data.ebitda_margin:.1%}", "yfinance", "verified")
                sources.add("Market Cap", f"${market_data.market_cap:.0f}M", "yfinance", "verified")
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
                    sources.add("Free Cash Flow", f"${market_data.fcf_ttm:.0f}M", "yfinance", "verified")
                if market_data.net_debt is not None:
                    sources.add("Net Debt", f"${market_data.net_debt:.0f}M", "yfinance", "verified")
                if market_data.shares_outstanding is not None:
                    sources.add("Shares Outstanding", f"{market_data.shares_outstanding:.0f}M", "yfinance", "verified")
                if market_data.current_price is not None:
                    sources.add("Current Price", f"${market_data.current_price:.2f}", "yfinance", "verified")
                if market_data.sector:
                    sources.add_once("Sector", str(market_data.sector), "yfinance", "verified")
                if isinstance(market_data.additional_metadata, dict):
                    _ind = str(market_data.additional_metadata.get("industry") or "").strip()
                    if _ind:
                        sources.add_once("Industry", _ind, "yfinance", "verified")
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
                console.print(f"  [red]Market analysis failed: {e}[/red]")
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
            if market_data and market_data.revenue_ttm:
                f = _fin_from_market(market_data)
                console.print(
                    f"  [green]Using {market_data.data_source} financials "
                    f"(Rev=${market_data.revenue_ttm:.0f}M)[/green]"
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
            console.print(f"  [red]Market analysis failed: timeout > {_MARKET_ANALYSIS_TIMEOUT}s[/red]")
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
    # Strict provenance policy for confirmed low-data private entities:
    # do not surface unsourced LLM financial metrics as factual numbers.
    _strict_registry_mode = bool(
        company_type == "private"
        and company_identifier
        and market_data
        and market_data.data_source == "companies_house"
        and not market_data.revenue_ttm
    )
    if _strict_registry_mode:
        fin.revenue_growth = "Not available [no verified source]"
        fin.gross_margin = "Not available [no verified source]"
        fin.ebitda_margin = "Not available [no verified source]"
        fin.free_cash_flow = "Not available [no verified source]"
        fin.net_margin = "Not available [no verified source]"

    _parallel_elapsed = time.time() - _parallel_t0
    if debug:
        console.print(
            f"  [dim]Parallel agents: {_parallel_elapsed:.1f}s (≈3× faster than sequential)[/dim]"
        )
    else:
        console.print(f"  [dim]Research agents completed in {_parallel_elapsed:.1f}s[/dim]")
    _market_source_backed = _has_source_backed_market_data(mkt)
    if (not quick_mode) and market_status == "OK" and (not _market_source_backed):
        market_status = "DEGRADED"
    if market_status == "SKIPPED_QUICK_MODE":
        _research_source = "skipped"
        _research_depth = "none"
    else:
        _research_source = "source_backed" if (market_status == "OK" and _market_source_backed) else "fallback"
        _research_depth = "full" if _research_source == "source_backed" else "limited"
    if (not quick_mode) and _research_source == "fallback":
        _ma_t = log.step_times.get("market_analysis")
        if _ma_t is not None:
            try:
                _ma_txt = f"{float(_ma_t):.2f}s"
            except Exception:
                _ma_txt = f"{_ma_t}s"
            console.print(f"  [dim]Market analysis attempted: {_ma_txt}; fallback used.[/dim]")

    # Post-process peer results (yfinance calls — sequential is fine)
    _peer_post_t0 = time.time()
    # target_sector comes from Fundamentals agent output for sector validation
    _target_sector = fund.sector or "" if fund else ""
    _target_industry = ""
    if market_data and isinstance(market_data.additional_metadata, dict):
        _target_industry = str(market_data.additional_metadata.get("industry") or "")
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
                            f"  [cyan]{peer_multiples.n_peers} validated peers, top {_show_n} shown:[/cyan] "
                            + _shown + drop_note
                        )
                    else:
                        console.print(
                            f"  [cyan]{peer_multiples.n_peers} validated peers:[/cyan] "
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
                        if _is_mega_tech and _consumer_cnt < 1:
                            _missing_consumer_ecosystem_bucket = True
                            console.print(
                                "  [yellow]Apple-like mega-cap consumer-hardware peers are limited; "
                                "peer set is an adjacent reference set (platform/infrastructure), "
                                "not a pure comparable set.[/yellow]"
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
                    peer_comps_table = PeerCompsTable(
                        peers=[
                            PeerComp(
                                name=p.name,
                                ticker=p.ticker,
                                bucket=p.bucket,
                                role=p.role,
                                market_cap=(f"${p.market_cap/1000:.1f}B" if p.market_cap else None),
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
                console.print(
                    f"  [cyan]Transaction comps:[/cyan] {len(new_comps)} new deals cached "
                    f"({_tx_medians.get('n_deals', 0)} total in sector) "
                    + (f"EV/EBITDA median {_tx_medians['ev_ebitda_median']:.1f}x"
                       if _tx_medians.get("ev_ebitda_median") else "")
                )
            else:
                # Use cached comps for the sector even if agent returned nothing usable
                _tx_medians = sector_medians(load_cache(), fund.sector or "")
                _tx_source_verified = bool((_tx_medians.get("n_deals") or 0) >= 3)
                if _tx_medians.get("n_deals"):
                    console.print(
                        f"  [dim]Transaction comps: using {_tx_medians['n_deals']} "
                        f"cached deals for sector[/dim]"
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

    # ── 5. VALUATION ENGINE ───────────────────────────────────────────────
    t0 = _step("Valuation Engine")
    quality = assess_data_quality(
        company_type=company_type,
        market_data=market_data,
        financials=fin.model_dump(),
        market_analysis=mkt.model_dump(),
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
    _core_quality_score = quality.score
    console.print(
        f"  [bold]Data quality:[/bold] {quality.score}/100 (Tier {quality.tier})"
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

    result = svc.run_full_valuation(
        financials=fin.model_dump(),
        assumptions=assumptions_dict,
        market_data=market_data,
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

    if not result.has_revenue:
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
    _CONGLOMERATE_KEYWORDS = {"segment", "division", "business unit", "portfolio", "subsidiaries"}
    _desc_lower = (fund.description + " " + fund.business_model).lower()
    if any(kw in _desc_lower for kw in _CONGLOMERATE_KEYWORDS) and result.has_revenue and fin.revenue_current:
        try:
            import json as _j, re as _r
            sotp_prompt = (
                f'Company: "{company}". Sector: {fund.sector}. '
                f'Revenue: ${fin.revenue_current}M total. '
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
                    ValuationMethod(name="SOTP", mid=str(round(_sotp.net_ev, 1)), weight=None)
                )
                console.print(
                    f"  [cyan]SOTP ({len(_segments)} segments): Net EV ${_sotp.net_ev:.0f}M[/cyan]"
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

    _ev_str = _fmt_ev_human(blended_ev) if blended_ev else "N/A"
    _target_price = f"${rec.intrinsic_price:.2f}" if rec.intrinsic_price else None
    _raw_rec = rec.recommendation if result.has_revenue else "N/A"
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
    _low_conviction = any("dispersion" in str(n).lower() or "high uncertainty" in str(n).lower() for n in (result.notes or []))
    if peer_multiples and peer_multiples.n_valuation_peers < 3:
        _low_conviction = True
    if peer_multiples and peer_multiples.effective_peer_count > 0 and peer_multiples.effective_peer_count < (3.0 if quick_mode else 5.0):
        _low_conviction = True
    if _is_mega_cap and peer_multiples and 3 <= peer_multiples.n_valuation_peers < 5:
        _low_conviction = True
    if _model_signal.startswith("SELL") and (_raw_rec or "").upper() == "HOLD":
        _low_conviction = True
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
    if _low_conviction and _rec in {"BUY", "SELL", "HOLD"}:
        if _rec == "SELL":
            _rec = "HOLD / LOW CONVICTION"
        elif _rec == "BUY":
            _rec = "BUY / LOW CONVICTION"
        else:
            _rec = "HOLD / LOW CONVICTION"
    if (not valuation_failed) and _low_conviction and valuation_status == "OK":
        valuation_status = "DEGRADED"
    if valuation_failed:
        _rec = "INCONCLUSIVE"
        _target_price = None
        _ev_str = "N/A"

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
        current_price=f"${rec.current_price:.2f}" if rec.current_price else None,
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

    if result.lbo:
        lbo = result.lbo
        console.print(
            f"  LBO: {'✓ FEASIBLE' if lbo.is_feasible else '✗ INFEASIBLE'} — "
            f"IRR {lbo.irr:.1%} / {lbo.moic:.1f}x MOIC / "
            f"{lbo.leverage_at_entry:.1f}x entry leverage"
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
                f"  [dim]Sensitivity (WACC ±100bps): {_fmt_ev_human(_ev_dn)} to {_fmt_ev_human(_ev_up)}[/dim]"
            )
            sources.add_once(
                "Sensitivity (WACC ±100bps)",
                f"{_fmt_ev_human(_ev_dn)} to {_fmt_ev_human(_ev_up)}",
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
        revenue_series, _ = svc._build_revenue_series(
            fin.model_dump(), market_data, [], sector=fund.sector or ""
        )
        if not revenue_series or not result.has_revenue:
            raise ValueError("No revenue — skipping football field")
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

        def _fmt_ev(v: float) -> str:
            return f"${v/1000:.1f}B" if v >= 1000 else f"${v:.0f}M"

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
                f"${_lo:.2f}–${_hi:.2f}",
                "scenario_blended",
                "inferred",
            )
            result.field_sources["Fair Value Range"] = (
                f"${_lo:.2f}–${_hi:.2f}",
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
        console.print(f"  [yellow]Scenarios skipped: {e}[/yellow]")

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
            f"  IC Score: [bold]{ic_result.ic_score:.0f}/100[/bold] → {ic_result.recommendation}"
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
    _report_timeout = _REPORT_WRITER_TIMEOUT_QUICK if quick_mode else _REPORT_WRITER_TIMEOUT_FULL
    if quick_mode:
        _modeled_growth = (result.field_sources.get("Modeled Revenue Growth") or ("N/A", "", ""))[0]
        _fv_range = (result.field_sources.get("Fair Value Range") or ("N/A", "", ""))[0]
        _prof = get_sector_profile(
            fund.sector or "",
            _target_industry if "_target_industry" in locals() else "",
        )
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
            "  [yellow]Research is fallback/partial; using conservative thesis template (no unsourced specifics).[/yellow]"
        )
        thesis = _build_fallback_thesis(
            company=company,
            sector=fund.sector or "",
            recommendation=val.recommendation or "HOLD",
            reason="research fallback mode (source-backed market context unavailable)",
            model_signal=_model_signal_for_text,
            industry=_target_industry if "_target_industry" in locals() else "",
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
        }
        try:
            with ThreadPoolExecutor(max_workers=1) as _tp:
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
                thesis = _fut_thesis.result(timeout=_report_timeout)
        except FutureTimeoutError:
            thesis_status = "TIMEOUT"
            console.print(f"  [yellow]Investment thesis timeout > {_report_timeout}s — using structured fallback.[/yellow]")
            thesis = _build_fallback_thesis(
                company=company,
                sector=fund.sector or "",
                recommendation=val.recommendation or "HOLD",
                reason="thesis timeout",
                model_signal=_model_signal_for_text,
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
            )
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
            thesis.bull_case = _soften_unsourced_scenario_specificity(thesis.bull_case or "")
            thesis.base_case = _soften_unsourced_scenario_specificity(thesis.base_case or "")
            thesis.bear_case = _soften_unsourced_scenario_specificity(thesis.bear_case or "")

    if football_field and thesis:
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
                    r"(?i)\bfair value\s+\$?\d[\d,]*(?:\.\d+)?\s*(?:-|–|to)\s*\$?\d[\d,]*(?:\.\d+)?",
                    f"fair value {_fv_txt}",
                    thesis.thesis,
                )
            if _pt_txt and _pt_txt != "N/A":
                thesis.thesis = _re.sub(
                    r"(?i)\bpoint estimate\s+\$?\d[\d,]*(?:\.\d+)?",
                    f"point estimate {_pt_txt}",
                    thesis.thesis,
                )
        _canon = (
            f"Valuation reference (canonical): fair value {_fv_txt}; "
            f"point estimate {_pt_txt}; recommendation {val.recommendation or 'N/A'}."
        )
        if _canon not in (thesis.thesis or ""):
            thesis.thesis = f"{_canon}\n\n{thesis.thesis or ''}".strip()

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
        if _pure_share >= 0.65 and not _low_div:
            _peers_display_status = "PURE_COMPS_OK"
        elif _pure_share >= 0.25:
            _peers_display_status = "MIXED_COMPS_OK"
        elif _low_div:
            _peers_display_status = "ADJACENT_COMPS_LOW_DIVERSITY"
        elif _pure_share > 0.0 or _missing_consumer_ecosystem_bucket:
            _peers_display_status = "ADJACENT_COMPS_OK"
        else:
            _peers_display_status = "PEERS_DEGRADED"
    _peers_is_low_conf = _peers_display_status in {
        "ADJACENT_COMPS_LOW_DIVERSITY",
        "ADJACENT_COMPS_OK",
        "MIXED_COMPS_OK",
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
        if _pure_share < 0.15:
            _peer_quality_score -= 10
        elif _pure_share < 0.35:
            _peer_quality_score -= 10
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
    if peer_multiples and peer_multiples.n_valuation_peers < (3 if quick_mode else 5):
        _confidence_reasons.append("weak valuation peer count")
    if peer_multiples and peer_multiples.effective_peer_count > 0 and peer_multiples.effective_peer_count < (3.0 if quick_mode else 5.0):
        _confidence_reasons.append("low effective peer diversification")
    if _missing_consumer_ecosystem_bucket:
        _confidence_reasons.append("consumer-hardware ecosystem peers unavailable")
    if valuation_status == "DEGRADED":
        _confidence_reasons.append("high DCF/comps dispersion or method disagreement")
    if _dcf_sanity_fail:
        _confidence_reasons.append("conservative/degraded DCF sanity")
    if _method_dispersion_ratio >= 2.0:
        _confidence_reasons.append(f"high method dispersion ({_method_dispersion_ratio:.1f}x)")
    if market_status in {"FAILED", "TIMEOUT", "DEGRADED", "DEGRADED_API_CAPACITY"}:
        _confidence_reasons.append("limited market context")
    if thesis_status in {"FAILED", "TIMEOUT", "DEGRADED_API_CAPACITY"}:
        _confidence_reasons.append("thesis generation degraded")
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
            or _peers_display_status in {"MIXED_COMPS_OK", "ADJACENT_COMPS_OK"}
            or str(_research_source) != "source_backed"
        )
    )
    _confidence_level = "Low" if _confidence_is_low else ("Medium" if _confidence_is_medium else "High")
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

    # Deterministic recommendation policy: separate raw valuation signal from final recommendation.
    if not valuation_failed and company_type == "public":
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
        val.recommendation = _rec

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
                "method_dispersion_level": _method_dispersion_level,
                "effective_peer_count": (round(_eff_peer_count, 2) if _eff_peer_count > 0 else None),
                "pure_peer_weight": (round(float(peer_multiples.pure_peer_weight_share or 0.0), 4) if peer_multiples else None),
                "adjacent_peer_weight": (round(float(peer_multiples.adjacent_peer_weight_share or 0.0), 4) if peer_multiples else None),
                "confidence": _confidence_level,
                "confidence_reason": (
                    "; ".join(_confidence_reasons)
                    if _confidence_reasons
                    else "valuation inputs and enrichment are consistent"
                ),
                "research_source": _research_source,
                "research_depth": _research_depth,
                "market_data_source_backed": "yes" if _market_source_backed else "no",
                "peer_quality_score": _peer_quality_score,
                "financial_data_quality_score": _financial_data_quality_score,
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
        },
        sources_md=sources.to_markdown(),
    )
    fill_gaps(analysis, fund.sector or "")
    if market_status == "SKIPPED_QUICK_MODE":
        analysis.market.market_size = "Not available in quick mode"
        analysis.market.market_growth = "Not available in quick mode"
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
            analysis.market.key_trends = [
                "Fallback Market Context — sector profile only, not source-backed. Used for qualitative framing only, not valuation inputs.",
                *_fallback_ctx[:4],
            ]
            _prof_key = detect_sector_profile(fund.sector or "", _target_industry if '_target_industry' in locals() else "")
            analysis.market.key_trends = [_enforce_profile_context_guard(t, _prof_key) for t in analysis.market.key_trends]
        elif not _usable_trends:
            _fallback_ctx = list(_prof.fallback_market_context) if _prof.fallback_market_context else [
                "Demand trend: category demand visibility is limited in this run.",
                "Competitive trend: peer positioning should be interpreted with caution.",
                "Regulatory trend: policy and macro conditions remain external variables.",
            ]
            analysis.market.key_trends = [
                "Fallback Market Context — sector profile only, not source-backed. Used for qualitative framing only, not valuation inputs.",
                *_fallback_ctx[:4],
            ]
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
