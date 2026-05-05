"""Equity analysis pipeline — run_analysis()."""
from __future__ import annotations

import re as _re
import time
from concurrent.futures import ThreadPoolExecutor

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
                    console.print(
                        f"  [cyan]Forward growth: {market_data.forward_revenue_growth:+.1%}[/cyan]"
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
                        "yfinance", "verified",
                    )
                if market_data.gross_margin is not None:
                    sources.add("Gross Margin", f"{market_data.gross_margin:.1%}", "yfinance", "verified")
                if market_data.net_debt is not None:
                    sources.add("Net Debt", f"${market_data.net_debt:.0f}M", "yfinance", "verified")
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
    # Identity guardrail: if we only have registry identity (no verified business description),
    # avoid hallucinated business models from similarly named entities.
    if (
        company_type == "private"
        and company_identifier
        and market_data
        and market_data.data_source == "companies_house"
        and not market_data.revenue_ttm
    ):
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

    def _do_market():
        _t = _step("Market Analysis")
        result = _parse_with_retry(
            market_agent, company, company_type,
            {"sector": fund.sector or "", "description": fund.description},
            MarketAnalysis, MarketAnalysis(),
        )
        log.end_step("market_analysis", _t)
        _done("Market Analysis", _t)
        return result

    def _do_peers():
        _t = _step("Peer Comparables")
        try:
            raw = peer_agent.run(company, company_type, {
                "sector": fund.sector or "",
                "description": fund.description or "",
                "revenue_usd_m": _peer_rev,
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
                {"sector": fund.sector or "", "description": fund.description},
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
            })
            result = (raw, None)
        except Exception as e:
            result = (None, e)
        _done("Transaction Comps", _t)
        return result

    with ThreadPoolExecutor(max_workers=_cfg.agent.parallel_workers) as _pool:
        _fut_mkt = _pool.submit(_do_market)
        _fut_peers = _pool.submit(_do_peers)
        _fut_fin = _pool.submit(_do_financials)
        _fut_tx = _pool.submit(_do_tx_comps)
        mkt = _fut_mkt.result()
        _peers_raw, _peers_err = _fut_peers.result()
        fin = _fut_fin.result()
        _tx_raw, _tx_err = _fut_tx.result()

    # Override LLM-derived financials with registry-verified values when available
    fin = _reconcile_financials(fin, market_data, console)

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
            peer_tickers = resolve_peer_tickers(peer_list)
            if peer_tickers:
                peer_multiples = build_peer_multiples(
                    peer_tickers, target_sector=_target_sector
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
                    console.print(
                        f"  [cyan]{peer_multiples.n_peers} validated peers:[/cyan] "
                        + ", ".join(p.ticker for p in peer_multiples.peers[:6])
                        + drop_note
                    )
                    if peer_multiples.ev_ebitda_median:
                        console.print(
                            f"  Median EV/EBITDA: {peer_multiples.ev_ebitda_median:.1f}x"
                            + (
                                f"  EV/Rev: {peer_multiples.ev_revenue_median:.1f}x"
                                if peer_multiples.ev_revenue_median else ""
                            )
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
                        f"— using sector-table multiples[/yellow]"
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
    if peer_multiples and peer_multiples.ev_ebitda_low and peer_multiples.ev_ebitda_high:
        assumptions_dict["ev_ebitda_range"] = [
            peer_multiples.ev_ebitda_low,
            peer_multiples.ev_ebitda_high,
        ]
        console.print(
            f"  [cyan]Comps anchored to {peer_multiples.n_peers} real peers: "
            f"{peer_multiples.ev_ebitda_low:.1f}x–{peer_multiples.ev_ebitda_high:.1f}x EV/EBITDA[/cyan]"
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
    if result.has_revenue and result.dcf:
        _methods.append(ValuationMethod(
            name="DCF", mid=str(round(result.dcf.enterprise_value, 1)), weight=_w_dcf
        ))
    if result.has_revenue and result.comps:
        _methods.append(ValuationMethod(
            name=f"Trading Comps ({result.valuation_path.upper()})",
            low=str(round(result.comps.low, 1)),
            mid=str(round(result.comps.mid, 1)),
            high=str(round(result.comps.high, 1)),
            weight=_w_comp,
        ))
    if result.has_revenue and result.transactions:
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

    if result.lbo:
        lbo = result.lbo
        console.print(
            f"  LBO: {'✓ FEASIBLE' if lbo.is_feasible else '✗ INFEASIBLE'} — "
            f"IRR {lbo.irr:.1%} / {lbo.moic:.1f}x MOIC / "
            f"{lbo.leverage_at_entry:.1f}x entry leverage"
        )
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
        console.print(
            f"  [bold]Football field:[/bold] Bear {_fmt_ev(scenarios_out.bear.blended_ev)} "
            f"/ Base {_fmt_ev(scenarios_out.base.blended_ev)} "
            f"/ Bull {_fmt_ev(scenarios_out.bull.blended_ev)}"
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
    thesis = _parse_with_retry(
        thesis_agent, company, company_type,
        {
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
        },
        InvestmentThesis,
        InvestmentThesis(thesis="N/A"),
    )
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
        },
        sources_md=sources.to_markdown(),
    )
    fill_gaps(analysis, fund.sector or "")
    return analysis
