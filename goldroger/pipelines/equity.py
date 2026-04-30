"""Equity analysis pipeline — run_analysis()."""
from __future__ import annotations

import re as _re
import json as _json
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from goldroger.agents.specialists import (
    DataCollectorAgent,
    FinancialModelerAgent,
    PeerFinderAgent,
    ReportWriterAgent,
    SectorAnalystAgent,
    ValuationEngineAgent,
)
from goldroger.data.comparables import (
    PeerMultiples,
    build_peer_multiples,
    parse_peer_agent_output,
    resolve_peer_tickers,
)
from goldroger.data.fetcher import MarketData, fetch_market_data, resolve_ticker
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

load_dotenv()


def run_analysis(
    company: str,
    company_type: str = "public",
    llm: str | None = None,
    siren: str | None = None,
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

    console.rule(f"[EQUITY] {company}")

    # ── 0. REAL DATA ──────────────────────────────────────────────────────
    market_data: MarketData | None = None
    if company_type == "private":
        t0 = _step("Registry (EU filings)")
        if siren:
            console.print(f"  [dim]SIREN {siren} — direct lookup[/dim]")
            from goldroger.data.providers.pappers import PappersProvider
            from goldroger.data.providers.infogreffe import InfogreffeProvider
            pp = PappersProvider()
            market_data = pp.fetch_by_siren(siren, company) if pp.is_available() else None
            if not market_data:
                market_data = InfogreffeProvider().fetch_by_siren(siren, company)
        else:
            from goldroger.data.name_resolver import resolve as resolve_company_name
            _ids = resolve_company_name(company, llm_provider=client)
            _q = _ids.infogreffe_query or (_ids.variants[0] if _ids.variants else company)
            console.print(f"  [dim]Querying as: {_q}[/dim]")
            market_data = DEFAULT_REGISTRY.fetch_by_name(company)
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
        if market_data:
            f = _fin_from_market(market_data)
            console.print("  [green]Using verified yfinance financials[/green]")
        else:
            f = _parse_with_retry(
                fin_agent, company, company_type,
                {"sector": fund.sector or "", "description": fund.description},
                Financials, _fin_fallback(),
            )
            if not f.revenue_current or f.revenue_current in ("0", "0.0", "null", "None"):
                try:
                    rev_prompt = (
                        f'What is the most recent annual revenue of "{company}"? '
                        "Return ONLY a JSON object: "
                        '{"revenue_usd_m": <number>, "source": "<brief source>"}. '
                        "Convert to USD millions. No markdown."
                    )
                    rev_resp = client.complete(
                        messages=[{"role": "user", "content": rev_prompt}],
                        model=client.resolve_model("large"),
                        max_tokens=100,
                    )
                    raw = _re.sub(r"```[a-z]*\n?|\n?```", "", rev_resp.content.strip())
                    rev_data = _json.loads(raw)
                    rev_val = rev_data.get("revenue_usd_m")
                    if rev_val and float(rev_val) > 0:
                        f.revenue_current = str(float(rev_val))
                        console.print(
                            f"  [cyan]Revenue fallback: ${float(rev_val):.0f}M (estimated)[/cyan]"
                        )
                except Exception:
                    pass
            if company_type == "private" and (
                not f.revenue_current or f.revenue_current in ("0", "0.0", "null", "None")
            ):
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
                        company, sector=fund.sector or "", country="",
                        crunchbase_data=crunchbase_data,
                    )
                    if tri and tri.revenue_estimate_m > 0:
                        f.revenue_current = str(tri.revenue_estimate_m)
                        console.print(
                            f"  [cyan]Triangulation ({tri.confidence}): "
                            f"${tri.revenue_estimate_m:.0f}M "
                            f"from {len(tri.signals)} signal(s)[/cyan]"
                        )
                except Exception:
                    pass
        log.end_step("financials", _t)
        _done("Financials", _t)
        return f

    with ThreadPoolExecutor(max_workers=3) as _pool:
        _fut_mkt = _pool.submit(_do_market)
        _fut_peers = _pool.submit(_do_peers)
        _fut_fin = _pool.submit(_do_financials)
        mkt = _fut_mkt.result()
        _peers_raw, _peers_err = _fut_peers.result()
        fin = _fut_fin.result()

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

    # ── 4. ASSUMPTIONS ────────────────────────────────────────────────────
    t0 = _step("Assumptions")
    assumptions = _parse_with_retry(
        val_agent, company, company_type,
        {
            "sector": fund.sector or "",
            "revenue_current": fin.revenue_current,
            "ebitda_margin": fin.ebitda_margin,
        },
        ValuationAssumptions, ValuationAssumptions(),
    )
    log.end_step("assumptions", t0)
    _done("Assumptions", t0)

    # ── 5. VALUATION ENGINE ───────────────────────────────────────────────
    t0 = _step("Valuation Engine")
    assumptions_dict = assumptions.model_dump()
    if peer_multiples and peer_multiples.ev_ebitda_low and peer_multiples.ev_ebitda_high:
        assumptions_dict["ev_ebitda_range"] = [
            peer_multiples.ev_ebitda_low,
            peer_multiples.ev_ebitda_high,
        ]
        console.print(
            f"  [cyan]Comps anchored to {peer_multiples.n_peers} real peers: "
            f"{peer_multiples.ev_ebitda_low:.1f}x–{peer_multiples.ev_ebitda_high:.1f}x EV/EBITDA[/cyan]"
        )

    result = svc.run_full_valuation(
        financials=fin.model_dump(),
        assumptions=assumptions_dict,
        market_data=market_data,
        sector=fund.sector or "",
    )
    blended_ev = result.blended.blended if result.blended else None
    rec = result.recommendation

    if not result.has_revenue:
        console.print(
            "  [yellow]⚠ No revenue data — quantitative valuation skipped. "
            "Peer multiples shown for reference only.[/yellow]"
        )

    _methods: list = []
    if result.has_revenue and result.dcf:
        _methods.append(
            ValuationMethod(name="DCF", mid=str(round(result.dcf.enterprise_value, 1)), weight=50)
        )
    if result.has_revenue and result.comps:
        _methods.append(ValuationMethod(
            name=f"Trading Comps ({result.valuation_path.upper()})",
            low=str(round(result.comps.low, 1)),
            mid=str(round(result.comps.mid, 1)),
            high=str(round(result.comps.high, 1)),
            weight=30,
        ))
    if result.has_revenue and result.transactions:
        _methods.append(ValuationMethod(
            name="Transaction Comps",
            mid=str(round(result.transactions.implied_value, 1)),
            weight=20,
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

    _ev_str = _fmt_ev_human(blended_ev) if blended_ev else "N/A"
    _target_price = f"${rec.intrinsic_price:.2f}" if rec.intrinsic_price else None
    _raw_rec = rec.recommendation if result.has_revenue else "N/A"
    _rec = (
        {"BUY": "ATTRACTIVE", "HOLD": "NEUTRAL", "SELL": "EXPENSIVE"}.get(_raw_rec, "NEUTRAL")
        if company_type == "private" and result.has_revenue else _raw_rec
    )

    sources.add("WACC", f"{result.wacc_used:.2%}", "capm_model", "inferred")
    sources.add("Terminal growth", f"{result.terminal_growth_used:.2%}", "sector_default", "inferred")
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
        revenue_series, _ = svc._build_revenue_series(fin.model_dump(), market_data, [])
        if not revenue_series or not result.has_revenue:
            raise ValueError("No revenue — skipping football field")
        _ebitda_margin = svc._resolve_ebitda_margin(fin.model_dump(), market_data, [])[0]
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

    return AnalysisResult(
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
        sources_md=sources.to_markdown(),
    )
