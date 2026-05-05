"""Equity analysis pipeline — run_analysis()."""
from __future__ import annotations

import re as _re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date

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
from goldroger.data.transaction_comps import (
    add_comps,
    load_cache,
    parse_agent_output as parse_tx_output,
    sector_medians,
)
from goldroger.data.comparables import (
    PeerMultiples,
    build_peer_multiples,
    find_peers_dynamic,
    parse_peer_agent_output,
    resolve_peer_tickers,
)
from goldroger.data.fetcher import MarketData, fetch_market_data, resolve_ticker
from goldroger.data.private_quality import merge_private_market_data
from goldroger.data.quality_gate import assess_data_quality
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
_TX_COMPS_TIMEOUT = 45
_REPORT_WRITER_TIMEOUT = 20

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
            very_old = delta_months > 6
            # hard reject ancient stale catalysts from the catalysts section
            if delta_months > 18:
                continue

        if stale and is_upcoming_label:
            txt = _re.sub(r"\b(upcoming|expected|will|next)\b", "recent", txt, flags=_re.IGNORECASE)
        if very_old:
            txt = f"Historical context: {txt}"
        elif stale and not txt.lower().startswith("recent"):
            txt = f"Recent event context: {txt}"
        out.append(txt)
    return out


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
) -> AnalysisResult:
    log = new_run(company, company_type)
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
        log.end_step("market_data", t0)
        _done("Market Data", t0)

    # ── 1. FUNDAMENTALS ───────────────────────────────────────────────────
    t0 = _step("Fundamentals")
    fund = _parse_with_retry(
        data_agent, company, company_type, {}, Fundamentals, _fund_fallback(company)
    )
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

    def _do_market():
        _t = _step("Market Analysis")
        if quick_mode:
            console.print("  [dim]Quick mode: skipping deep market analysis.[/dim]")
            log.end_step("market_analysis", _t)
            _done("Market Analysis", _t)
            return MarketAnalysis(), "skipped"
        try:
            result = _parse_with_retry(
                market_agent, company, company_type,
                {
                    "sector": fund.sector or "",
                    "description": fund.description,
                    "run_date": date.today().isoformat(),
                    "current_year": date.today().year,
                    "quick_mode": quick_mode,
                    "max_queries": 5,
                    "max_results": 3,
                },
                MarketAnalysis, MarketAnalysis(),
                fatal_on_fail=True,
            )
            status = "ok"
        except Exception as e:
            console.print(f"  [red]Market analysis failed: {e}[/red]")
            result = MarketAnalysis()
            status = "failed"
        log.end_step("market_analysis", _t)
        _done("Market Analysis", _t)
        return result, status

    def _do_peers():
        _t = _step("Peer Comparables")
        try:
            raw = peer_agent.run(company, company_type, {
                "sector": fund.sector or "",
                "description": fund.description or "",
                "revenue_usd_m": _peer_rev,
                "quick_mode": quick_mode,
                "max_queries": 5,
                "max_results": 3,
            })
            result = (raw, None)
        except Exception as e:
            result = (None, e)
        _done("Peer Comparables", _t)
        return result

    def _do_financials():
        _t = _step("Financials")
        if market_data and market_data.revenue_ttm:
            f = _fin_from_market(market_data)
            console.print(
                f"  [green]Using {market_data.data_source} financials "
                f"(Rev=${market_data.revenue_ttm:.0f}M)[/green]"
            )
        else:
            f = _parse_with_retry(
                fin_agent, company, company_type,
                {"sector": fund.sector or "", "description": fund.description, "quick_mode": quick_mode},
                Financials, _fin_fallback(),
            )
            # Normalise "~$700M", "€700 million", "1.2B" → plain USD-millions string
            f.revenue_current = normalise_revenue_string(f.revenue_current)
        log.end_step("financials", _t)
        _done("Financials", _t)
        return f

    def _do_tx_comps():
        _t = _step("Transaction Comps")
        try:
            import datetime
            raw = tx_agent.run(company, company_type, {
                "sector": fund.sector or "",
                "current_year": str(datetime.date.today().year),
                "quick_mode": quick_mode,
                "max_queries": 5,
                "max_results": 3,
            })
            result = (raw, None)
        except Exception as e:
            result = (None, e)
        _done("Transaction Comps", _t)
        return result

    market_analysis_failed = False
    peer_timeout_or_fail = False
    with ThreadPoolExecutor(max_workers=_cfg.agent.parallel_workers) as _pool:
        _fut_mkt = _pool.submit(_do_market)
        _fut_peers = _pool.submit(_do_peers)
        _fut_fin = _pool.submit(_do_financials)
        _fut_tx = None if _skip_tx_comps else _pool.submit(_do_tx_comps)
        try:
            mkt, _mkt_status = _fut_mkt.result(timeout=_MARKET_ANALYSIS_TIMEOUT)
            market_analysis_failed = (_mkt_status == "failed")
        except FutureTimeoutError:
            market_analysis_failed = True
            mkt = MarketAnalysis()
            console.print(f"  [red]Market analysis failed: timeout > {_MARKET_ANALYSIS_TIMEOUT}s[/red]")
        try:
            _peers_raw, _peers_err = _fut_peers.result(timeout=_PEER_COMPS_TIMEOUT)
            peer_timeout_or_fail = bool(_peers_err)
        except FutureTimeoutError:
            _peers_raw, _peers_err = None, TimeoutError("peer timeout")
            peer_timeout_or_fail = True
            console.print(f"  [red]Peer comparables failed: timeout > {_PEER_COMPS_TIMEOUT}s[/red]")
        fin = _fut_fin.result()
        if _fut_tx is not None:
            try:
                _tx_raw, _tx_err = _fut_tx.result(timeout=_TX_COMPS_TIMEOUT)
            except FutureTimeoutError:
                _tx_raw, _tx_err = None, TimeoutError("tx comps timeout")
                console.print(f"  [yellow]Transaction comps timeout > {_TX_COMPS_TIMEOUT}s — skipped.[/yellow]")
        else:
            _tx_raw, _tx_err = None, None
            console.rule("[bold cyan]Transaction Comps")
            console.print("  [dim]Skipped for mega-cap public company (tx weight forced to 0%).[/dim]")
            _done("Transaction Comps", time.time())

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
    console.print(
        f"  [dim]Parallel agents: {_parallel_elapsed:.1f}s (≈3× faster than sequential)[/dim]"
    )

    # Post-process peer results (yfinance calls — sequential is fine)
    # target_sector comes from Fundamentals agent output for sector validation
    _target_sector = fund.sector or "" if fund else ""
    peer_comps_table: PeerCompsTable | None = None
    peer_multiples: PeerMultiples | None = None
    if _peers_raw:
        try:
            peer_list = parse_peer_agent_output(_peers_raw)
            peer_tickers_seed = resolve_peer_tickers(peer_list)
            _is_mega_tech = bool(
                market_data
                and market_data.market_cap
                and market_data.market_cap > _mega_cap_usd_m
                and any(tok in (fund.sector or "").lower() for tok in ("technology", "tech", "software", "semiconductor"))
            )
            peer_tickers = find_peers_dynamic(
                company_name=company,
                target_sector=_target_sector,
                target_market_cap=(market_data.market_cap if market_data else None),
                seed_tickers=peer_tickers_seed,
            )
            _self_ticker = (market_data.ticker or "").upper() if market_data else ""
            peer_tickers = [t for t in peer_tickers if t and t != _self_ticker]
            sources.add_once(
                "Peer Selection Policy",
                "Dynamic staged search (industry -> sector+size -> adjacent -> global), ranked by similarity",
                "peer_policy",
                "verified",
            )
            if peer_tickers:
                peer_multiples = build_peer_multiples(
                    peer_tickers,
                    target_sector=_target_sector,
                    target_market_cap=(market_data.market_cap if market_data else None),
                    min_similarity=(0.5 if _is_mega_tech else 0.0),
                    target_ebitda_margin=(market_data.ebitda_margin if market_data else None),
                    target_growth=(market_data.forward_revenue_growth if market_data else None),
                )
                # Log validation summary
                drops: list[str] = []
                if peer_multiples.n_dropped_no_data:
                    drops.append(f"{peer_multiples.n_dropped_no_data} not found")
                if peer_multiples.n_dropped_sector:
                    drops.append(f"{peer_multiples.n_dropped_sector} wrong sector")
                if peer_multiples.n_dropped_sanity:
                    drops.append(f"{peer_multiples.n_dropped_sanity} bad multiples")
                drop_note = f"  [dim](dropped: {', '.join(drops)})[/dim]" if drops else ""

                if peer_multiples.n_peers > 0:
                    if _is_mega_tech and peer_multiples.n_peers < 5:
                        console.print(
                            f"  [yellow]Peer set expanded (adjacent/global stages used): "
                            f"{peer_multiples.n_peers} validated peers; confidence reduced.[/yellow]"
                        )
                    console.print(
                        f"  [cyan]{peer_multiples.n_peers} validated peers:[/cyan] "
                        + ", ".join(p.ticker for p in peer_multiples.peers[:6])
                        + drop_note
                    )
                    for _p in peer_multiples.peers[:8]:
                        _sim = _peer_similarity_score(
                            market_data.market_cap if market_data else None,
                            _p.market_cap,
                            _target_sector,
                            _p.sector or "",
                        )
                        console.print(
                            f"  [dim]Peer {_p.ticker}: EV/EBITDA={_p.ev_ebitda:.1f}x "
                            f"(similarity {_sim:.2f})[/dim]"
                            if _p.ev_ebitda
                            else f"  [dim]Peer {_p.ticker}: similarity {_sim:.2f}[/dim]"
                        )
                        sources.add_once(
                            f"Peer {_p.ticker} Similarity",
                            f"{_sim:.2f}",
                            "peer_similarity_model",
                            "inferred",
                        )
                    if peer_multiples.ev_ebitda_median:
                        console.print(
                            f"  Median EV/EBITDA: {peer_multiples.ev_ebitda_median:.1f}x"
                            + (
                                f"  EV/Rev: {peer_multiples.ev_revenue_median:.1f}x"
                                if peer_multiples.ev_revenue_median else ""
                            )
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
                                ev_ebitda=f"{p.ev_ebitda:.1f}x" if p.ev_ebitda else None,
                                ev_revenue=f"{p.ev_revenue:.1f}x" if p.ev_revenue else None,
                                ebitda_margin=f"{p.ebitda_margin:.1%}" if p.ebitda_margin else None,
                                revenue_growth=f"{p.revenue_growth:+.1%}" if p.revenue_growth else None,
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
                    console.print(
                        f"  [yellow]Peer comps: fewer than 3 validated peers{drop_note} "
                        f"— confidence reduced[/yellow]"
                    )
                    if _is_mega_tech and peer_multiples and peer_multiples.n_peers < 5:
                        console.print(
                            f"  [yellow]Insufficient comps for mega-cap policy: "
                            f"{peer_multiples.n_peers} validated peers (<5).[/yellow]"
                        )
        except Exception as e:
            console.print(f"  [yellow]Peer post-processing skipped: {e}[/yellow]")
    elif _peers_err:
        console.print(f"  [yellow]Peer finder skipped: {_peers_err}[/yellow]")

    # Post-process transaction comps — cache + extract medians
    _tx_medians: dict = {}
    if _tx_raw:
        try:
            new_comps = parse_tx_output(_tx_raw, fund.sector or "")
            if new_comps:
                all_comps = add_comps(new_comps)
                _tx_medians = sector_medians(all_comps, fund.sector or "")
                console.print(
                    f"  [cyan]Transaction comps:[/cyan] {len(new_comps)} new deals cached "
                    f"({_tx_medians.get('n_deals', 0)} total in sector) "
                    + (f"EV/EBITDA median {_tx_medians['ev_ebitda_median']:.1f}x"
                       if _tx_medians.get("ev_ebitda_median") else "")
                )
            else:
                # Use cached comps for the sector even if agent returned nothing usable
                _tx_medians = sector_medians(load_cache(), fund.sector or "")
                if _tx_medians.get("n_deals"):
                    console.print(
                        f"  [dim]Transaction comps: using {_tx_medians['n_deals']} "
                        f"cached deals for sector[/dim]"
                    )
        except Exception as e:
            console.print(f"  [yellow]Transaction comps post-processing skipped: {e}[/yellow]")
    elif _tx_err:
        console.print(f"  [yellow]Transaction comps agent skipped: {_tx_err}[/yellow]")
        _tx_medians = sector_medians(load_cache(), fund.sector or "")

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
        peer_count=(peer_multiples.n_peers if peer_multiples else 0),
        market_analysis_failed=market_analysis_failed,
    )
    _is_mega_cap_quality = bool(
        company_type == "public"
        and market_data
        and market_data.market_cap
        and market_data.market_cap > _mega_cap_usd_m
    )
    _peer_count = peer_multiples.n_peers if peer_multiples else 0
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
    assumptions_dict["insufficient_comps"] = bool(
        _is_mega_cap and _peer_count < 1
    )
    assumptions_dict["low_confidence_comps"] = bool(
        _is_mega_cap and peer_multiples and 3 <= peer_multiples.n_peers < 5
    )
    assumptions_dict["mega_cap_tech"] = bool(
        _is_mega_cap and any(tok in (fund.sector or "").lower() for tok in ("technology", "tech", "software", "semiconductor"))
    )
    if peer_multiples and peer_multiples.ev_ebitda_low and peer_multiples.ev_ebitda_high:
        assumptions_dict["ev_ebitda_range"] = [
            peer_multiples.ev_ebitda_low,
            peer_multiples.ev_ebitda_high,
        ]
        if peer_multiples.ev_ebitda_median:
            assumptions_dict["ev_ebitda_median"] = peer_multiples.ev_ebitda_median
        console.print(
            f"  [cyan]Comps from {peer_multiples.n_peers} real peers (P25–P75): "
            f"{peer_multiples.ev_ebitda_low:.1f}x–{peer_multiples.ev_ebitda_high:.1f}x EV/EBITDA[/cyan]"
        )
        if _is_mega_cap and peer_multiples.n_peers < 5:
            console.print("  [yellow]⚠ Peer set expanded beyond core comparables; valuation confidence reduced.[/yellow]")
    elif assumptions_dict.get("insufficient_comps"):
        console.print(
            "  [yellow]Comps unavailable: no validated peers after expansion.[/yellow]"
        )
    elif _is_mega_cap and peer_multiples and 3 <= peer_multiples.n_peers < 5:
        console.print(
            f"  [yellow]Low-confidence comps: {peer_multiples.n_peers} validated peers (target 5–7).[/yellow]"
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
    blended_ev = result.blended.blended if result.blended else None
    rec = result.recommendation
    _dcf_sanity_fail = any("DCF likely miscalibrated" in str(n) for n in (result.notes or []))
    valuation_failed = False
    if _peer_count == 0 and _dcf_sanity_fail:
        console.print("  [red]❌ Valuation failed: peer comps unavailable and DCF sanity check failed.[/red]")
        rec.recommendation = "INCONCLUSIVE"
        rec.intrinsic_price = None
        rec.upside_pct = None
        valuation_failed = True
        result.field_sources.pop("Fair Value Range", None)
        result.notes.append("Valuation status: FAILED — no peers + DCF sanity failure.")

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
            name=f"Trading Comps ({result.valuation_path.upper()})",
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
    _low_conviction = any("dispersion" in str(n).lower() for n in (result.notes or []))
    if peer_multiples and peer_multiples.n_peers < 3:
        _low_conviction = True
    if _is_mega_cap and peer_multiples and 3 <= peer_multiples.n_peers < 5:
        _low_conviction = True
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
        _rec = f"{_rec} / LOW CONVICTION"
    if valuation_failed:
        _rec = "INCONCLUSIVE"
        _target_price = None

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
            f"{peer_multiples.ev_ebitda_median:.1f}x ({peer_multiples.n_peers} peers)",
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
    }
    try:
        with ThreadPoolExecutor(max_workers=1) as _tp:
            _fut_thesis = _tp.submit(
                _parse_with_retry,
                thesis_agent, company, company_type, _thesis_ctx,
                InvestmentThesis, InvestmentThesis(thesis="N/A"),
            )
            thesis = _fut_thesis.result(timeout=_REPORT_WRITER_TIMEOUT)
    except FutureTimeoutError:
        console.print(f"  [yellow]Investment thesis timeout > {_REPORT_WRITER_TIMEOUT}s — using short fallback.[/yellow]")
        thesis = InvestmentThesis(
            thesis=f"{company}: quick summary only. Full thesis unavailable due to timeout.",
            bull_case="Upside depends on execution and market conditions.",
            base_case="Base case assumes stable operations and gradual growth.",
            bear_case="Downside risk from execution, demand, or pricing pressure.",
            catalysts=["Next earnings/filing update", "Product/strategy update", "Macro/regulatory change"],
            key_questions=["What is verified growth?", "How durable are margins?", "What valuation anchor is most reliable?"],
        )
    thesis.catalysts = _sanitize_catalysts(thesis.catalysts)
    log.end_step("thesis", t0)
    _done("Investment Thesis", t0)

    if football_field and thesis:
        if football_field.bear and thesis.bear_case:
            football_field.bear.narrative = thesis.bear_case[:200]
        if football_field.base and thesis.base_case:
            football_field.base.narrative = thesis.base_case[:200]
        if football_field.bull and thesis.bull_case:
            football_field.bull.narrative = thesis.bull_case[:200]

    console.rule("[DONE EQUITY]")
    log.flush()

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
                "market_analysis": "FAILED" if market_analysis_failed else "OK",
                "peers": "FAILED" if (_peer_count == 0 or peer_timeout_or_fail) else "OK",
                "valuation": "FAILED" if valuation_failed else "OK",
                "recommendation": _rec,
            },
            "timings_s": {
                "market_data": log.step_times.get("market_data"),
                "fundamentals": log.step_times.get("fundamentals"),
                "market_analysis": log.step_times.get("market_analysis"),
                "financials": log.step_times.get("financials"),
                "valuation": log.step_times.get("valuation"),
                "thesis": log.step_times.get("thesis"),
                "total": round(time.time() - log.started_at, 2),
            },
        },
        sources_md=sources.to_markdown(),
    )
    fill_gaps(analysis, fund.sector or "")
    if market_analysis_failed:
        analysis.market.market_size = "Not available"
        analysis.market.market_growth = "Not available"
        analysis.market.market_segment = "Not available"
        analysis.market.key_trends = []
    _tam_v = analysis.market.market_size or "Not available"
    _mg_v = analysis.market.market_growth or "Not available"
    _tam_conf = "inferred" if "not available" in str(_tam_v).lower() else "estimated"
    _mg_conf = "inferred" if "not available" in str(_mg_v).lower() else "estimated"
    sources.add_once("TAM", _tam_v, "market_analysis", _tam_conf)
    sources.add_once("Market Growth", _mg_v, "market_analysis", _mg_conf)
    analysis.sources_md = sources.to_markdown()
    return analysis
