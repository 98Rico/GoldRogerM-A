"""
ValuationService — institution-grade valuation orchestrator.

Data priority:
  1. Verified (yfinance MarketData)   → tagged "verified"
  2. LLM-extracted financials         → tagged "estimated"
  3. Sector defaults                  → tagged "inferred"

Valuation paths:
  A. Standard (EV/EBITDA)  — all sectors except financials
  B. Financial (P/E + P/B) — banks, insurers, asset managers
  C. LBO                   — always computed alongside A/B

Outputs:
  - DCF enterprise value (CAPM WACC)
  - Trading comps (sector-calibrated EV/EBITDA or P/E ranges)
  - Transaction comps (EV/Revenue)
  - Blended ValuationResult (50/30/20 weights)
  - LBOOutput (always attached, may be infeasible)
  - BUY / HOLD / SELL vs current market cap
  - Source confidence per field
  - Sensitivity matrix (WACC × terminal growth)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from goldroger.config import DEFAULT_CONFIG as _cfg
from goldroger.data.sector_multiples import get_sector_multiples, is_financial_sector
from goldroger.finance.core.wacc import (
    compute_capm_wacc,
    capm_cost_of_equity,
    RISK_FREE_RATE,
    EQUITY_RISK_PREMIUM,
)
from goldroger.finance.valuation.dcf import DCFInput, DCFOutput, compute_dcf
from goldroger.finance.valuation.comps import CompsInput, CompsOutput, compute_comps
from goldroger.finance.valuation.transactions import (
    TransactionInput,
    TransactionOutput,
    compute_transaction,
)
from goldroger.finance.valuation.aggregator import compute_weighted_valuation, ValuationResult
from goldroger.finance.valuation.lbo import LBOOutput, lbo_from_valuation


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class RecommendationOutput:
    recommendation: str           # BUY / HOLD / SELL
    upside_pct: Optional[float]
    intrinsic_price: Optional[float]
    current_price: Optional[float]
    market_cap: Optional[float]
    ev_blended: float


@dataclass
class SensitivityMatrix:
    wacc_range: list[float]
    tg_range: list[float]
    ev_matrix: list[list[float]]  # [wacc_idx][tg_idx]


@dataclass
class FullValuationOutput:
    dcf: Optional[DCFOutput]
    comps: Optional[CompsOutput]
    transactions: Optional[TransactionOutput]
    blended: Optional[ValuationResult]
    lbo: Optional[LBOOutput]
    recommendation: RecommendationOutput
    sensitivity: Optional[SensitivityMatrix]
    wacc_used: float
    terminal_growth_used: float
    data_confidence: str           # "verified" / "estimated" / "inferred" / "missing"
    sector: str
    valuation_path: str            # "ev_ebitda" or "pe_pb"
    has_revenue: bool = True       # False → all quantitative methods skipped
    weights_used: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Per-field provenance: metric → (value_str, source, confidence)
    field_sources: dict = field(default_factory=dict)


# ── Weight resolution ─────────────────────────────────────────────────────────

def compute_valuation_weights(
    sector: str,
    company_type: str = "public",
    market_data=None,
) -> dict:
    """
    Return blended valuation weights calibrated to sector and company type.

    Private high-growth (SaaS, HealthTech, Biotech, e-commerce — sector rev CAGR > 12%):
      DCF 20% / Trading comps 35% / Transaction comps 45%
      Rationale: no public beta → WACC estimated; DCF entirely dependent on
      estimated growth; precedent M&A transactions are the strongest pricing signal.

    Public / private mature: DCF 50% / Trading comps 30% / Tx comps 20%
    Financial sector:        DCF 0%  / Trading comps 60% / Tx comps 40%
    Mega-caps (>$500B):      DCF 60% / Trading comps 40% / Tx comps 0%
    """
    from goldroger.data.sector_multiples import get_sector_rev_growth
    _mega_cap_usd_m = _cfg.lbo.mega_cap_skip_usd_bn * 1000
    if market_data and market_data.market_cap and market_data.market_cap > _mega_cap_usd_m:
        return {"dcf": 0.6, "comps": 0.4, "transactions": 0.0}
    if is_financial_sector(sector):
        return {"dcf": 0.0, "comps": 0.6, "transactions": 0.4}
    if company_type == "private" and get_sector_rev_growth(sector) > 0.12:
        return {"dcf": 0.20, "comps": 0.35, "transactions": 0.45}
    return {"dcf": 0.50, "comps": 0.30, "transactions": 0.20}


# ── ValuationService ──────────────────────────────────────────────────────────

class ValuationService:
    PROJECTION_YEARS = 5

    def run_full_valuation(
        self,
        financials: dict,
        assumptions: dict,
        market_data=None,
        sector: str = "",
        company_type: str = "public",
    ) -> FullValuationOutput:

        sector_m = get_sector_multiples(sector)
        notes: list[str] = []
        use_financial_path = is_financial_sector(sector)

        # ── 1. Core inputs ────────────────────────────────────────────────
        field_sources: dict = {}
        revenue_series, rev_confidence = self._build_revenue_series(
            financials, market_data, notes, sector=sector
        )

        if revenue_series is None:
            wacc, wacc_confidence = self._resolve_wacc(assumptions, market_data, sector_m, notes)
            tg = self._resolve_terminal_growth(assumptions, sector_m, wacc, notes)
            rec = self._compute_recommendation(
                ValuationResult(low=0, mid=0, high=0, blended=0), market_data, notes
            )
            _w0 = compute_valuation_weights(sector, company_type, market_data)
            return FullValuationOutput(
                dcf=None, comps=None, transactions=None,
                blended=None, lbo=None,
                recommendation=rec,
                sensitivity=None,
                wacc_used=wacc, terminal_growth_used=tg,
                data_confidence="missing",
                sector=sector or "Unknown",
                valuation_path="ev_ebitda",
                has_revenue=False,
                weights_used=_w0,
                notes=notes,
                field_sources={"WACC": (f"{wacc:.2%}", "capm_model", wacc_confidence)},
            )

        revenue_current = revenue_series[-1]

        # Actual year-0 revenue for correct year-1 NWC delta in DCF
        base_revenue_y0 = None
        if market_data and market_data.revenue_history and market_data.revenue_history:
            base_revenue_y0 = market_data.revenue_history[-1]
        elif market_data and market_data.revenue_ttm:
            base_revenue_y0 = market_data.revenue_ttm
        else:
            base_revenue_y0 = self._f(financials.get("revenue_current"), None)

        ebitda_margin, ebitda_confidence = self._resolve_ebitda_margin(
            financials, market_data, notes, sector=sector
        )
        tax_rate = self._resolve_tax_rate(financials, market_data)
        capex_pct = self._resolve_capex_pct(financials, market_data, revenue_current)
        nwc_pct = self._f(financials.get("nwc_pct"), 0.02)
        da_pct = self._resolve_da_pct(market_data, revenue_current)

        # ── 2. WACC ───────────────────────────────────────────────────────
        wacc, wacc_confidence = self._resolve_wacc(
            assumptions, market_data, sector_m, notes
        )
        terminal_growth = self._resolve_terminal_growth(assumptions, sector_m, wacc, notes)

        # ── Populate field_sources ────────────────────────────────────────
        _rev_src = market_data.data_source if market_data else "llm"
        _rev_val = market_data.revenue_ttm if market_data and market_data.revenue_ttm else revenue_current
        field_sources["Revenue TTM"] = (f"${_rev_val:.0f}M", _rev_src, rev_confidence)
        field_sources["EBITDA Margin"] = (f"{ebitda_margin:.1%}", _rev_src, ebitda_confidence)
        field_sources["WACC"] = (f"{wacc:.2%}", "capm_model", wacc_confidence)
        field_sources["Terminal Growth"] = (f"{terminal_growth:.2%}", "sector_default", "inferred")
        if base_revenue_y0 and base_revenue_y0 > 0 and revenue_series:
            _modeled_g = (revenue_series[0] / base_revenue_y0) - 1.0
            field_sources["Modeled Revenue Growth"] = (
                f"{_modeled_g:+.1%}",
                "valuation_model",
                "inferred",
            )
        if market_data and market_data.beta:
            field_sources["Beta (β)"] = (f"{market_data.beta:.3f}", _rev_src, "verified")
        if market_data and market_data.forward_revenue_growth is not None:
            _fg_src = (
                "yfinance_analyst_revenue"
                if market_data.forward_revenue_1y is not None
                else "yfinance_earnings_proxy"
            )
            _fg_conf = "verified" if market_data.forward_revenue_1y is not None else "estimated"
            field_sources["Forward Revenue Growth"] = (
                f"{market_data.forward_revenue_growth:+.1%}", _fg_src, _fg_conf
            )
        if market_data and market_data.market_cap:
            field_sources["Market Cap"] = (f"${market_data.market_cap:.0f}M", _rev_src, "verified")
        if market_data and market_data.ev_ebitda_market:
            field_sources["EV/EBITDA (market)"] = (
                f"{market_data.ev_ebitda_market:.1f}x", _rev_src, "verified"
            )
        if market_data and market_data.net_debt is not None:
            field_sources["Net Debt"] = (f"${market_data.net_debt:.0f}M", _rev_src, "verified")

        # ── 3. DCF ───────────────────────────────────────────────────────
        dcf_input = DCFInput(
            revenue=revenue_series,
            ebitda_margin=ebitda_margin,
            tax_rate=tax_rate,
            capex_pct=capex_pct,
            nwc_pct=nwc_pct,
            wacc=wacc,
            terminal_growth=terminal_growth,
            da_pct=da_pct,
            base_revenue=base_revenue_y0,
        )
        dcf_output = compute_dcf(dcf_input)

        if dcf_output.terminal_value_pct > 0.85:
            notes.append(
                f"Terminal value {dcf_output.terminal_value_pct:.0%} of EV — "
                "highly sensitive to terminal assumptions."
            )

        # ── 4. Comps & transactions ───────────────────────────────────────
        ebitda = revenue_current * ebitda_margin

        comps_field_sources: dict = {}
        insufficient_comps = bool(assumptions.get("insufficient_comps"))
        if use_financial_path:
            comps_output, tx_output = self._financial_comps(
                market_data, sector_m, notes
            )
            valuation_path = "pe_pb"
        elif insufficient_comps:
            comps_output = CompsOutput(low=0.0, mid=0.0, high=0.0)
            tx_output = TransactionOutput(implied_value=0.0)
            comps_field_sources = {
                "EV/EBITDA (peer range)": ("insufficient peers (<3)", "validated_peers", "inferred"),
            }
            notes.append("Comps disabled: insufficient validated peer set (<3 peers).")
            valuation_path = "ev_ebitda"
        else:
            comps_output, tx_output, comps_field_sources = self._standard_comps(
                revenue_current, ebitda, market_data, sector_m, assumptions, notes, sector
            )
            valuation_path = "ev_ebitda"
        field_sources.update(comps_field_sources)

        # ── 5. Blended EV ─────────────────────────────────────────────────
        weights = assumptions.get("weights") or compute_valuation_weights(
            sector=sector,
            company_type=company_type,
            market_data=market_data,
        )
        _mega_cap_usd_m = _cfg.lbo.mega_cap_skip_usd_bn * 1000
        if market_data and market_data.market_cap and market_data.market_cap > _mega_cap_usd_m:
            if insufficient_comps:
                weights = {"dcf": 1.0, "comps": 0.0, "transactions": 0.0}
                notes.append(
                    f"Mega-cap (>${_cfg.lbo.mega_cap_skip_usd_bn:.0f}B MCap) with insufficient peers (<3): "
                    "DCF-only valuation mode (low reliability)."
                )
            else:
                weights = {"dcf": 0.6, "comps": 0.4, "transactions": 0.0}
                notes.append(
                    f"Mega-cap (>${_cfg.lbo.mega_cap_skip_usd_bn:.0f}B MCap): "
                    "tx comps excluded — weights DCF 60% / Comps 40%."
                )
        w_pct = {k: f"{v:.0%}" for k, v in weights.items()}
        notes.append(f"Blend weights: DCF {w_pct['dcf']} / Comps {w_pct['comps']} / Tx {w_pct['transactions']}.")
        blended = compute_weighted_valuation(dcf_output, comps_output, tx_output, weights)

        # ── 6. LBO (always run, may be infeasible; skipped for mega-caps) ──
        lbo = self._run_lbo(
            blended.blended, ebitda, revenue_series, ebitda_margin, capex_pct, tax_rate, market_data
        )

        # ── 7. BUY / HOLD / SELL ─────────────────────────────────────────
        recommendation = self._compute_recommendation(blended, market_data, notes)
        recommendation = self._apply_recommendation_guardrails(
            recommendation=recommendation,
            dcf_output=dcf_output,
            comps_output=comps_output,
            market_data=market_data,
            notes=notes,
            ebitda_margin=ebitda_margin,
        )

        if (
            market_data
            and market_data.ev_ebitda_market
            and comps_field_sources.get("EV/EBITDA (peer median)", ("", "", ""))[1] == "validated_peers"
        ):
            _peer_median = None
            _pm = comps_field_sources.get("EV/EBITDA (peer median)")
            if _pm:
                _nums = __import__("re").findall(r"([0-9]+(?:\.[0-9]+)?)x", str(_pm[0]))
                if _nums:
                    _peer_median = float(_nums[0])
            if _peer_median and _peer_median > 0:
                _delta = ((market_data.ev_ebitda_market / _peer_median) - 1.0) * 100.0
                _stance = "premium" if _delta >= 0 else "discount"
                notes.append(
                    f"Implied vs peer median: {market_data.ev_ebitda_market:.1f}x vs {_peer_median:.1f}x "
                    f"→ {abs(_delta):.1f}% {_stance}."
                )
                if _delta >= 0:
                    notes.append(
                        "Premium rationale to test: margin durability, FCF conversion, and balance-sheet quality."
                    )
                else:
                    notes.append(
                        "Discount rationale to test: growth deceleration, innovation exposure, and cyclicality."
                    )
        field_sources["Enterprise Value (blended)"] = (
            f"${blended.blended:.0f}M",
            "valuation_engine",
            "inferred",
        )
        if (
            market_data
            and market_data.shares_outstanding
            and market_data.shares_outstanding > 0
            and recommendation.intrinsic_price is not None
        ):
            net_debt = market_data.net_debt or 0.0
            equity_value = blended.blended - net_debt
            field_sources["Equity Value"] = (
                f"${equity_value:.0f}M",
                "valuation_bridge",
                "verified",
            )
            field_sources["Shares Outstanding"] = (
                f"{market_data.shares_outstanding:.0f}M",
                market_data.data_source or "yfinance",
                "verified",
            )
            field_sources["Implied Target Price"] = (
                f"${recommendation.intrinsic_price:.2f}",
                "valuation_bridge",
                "verified",
            )
        if recommendation.upside_pct is not None:
            field_sources["Upside/Downside"] = (
                f"{recommendation.upside_pct:+.1%}",
                "valuation_engine",
                "inferred",
            )
        if dcf_output and comps_output and dcf_output.enterprise_value > 0 and comps_output.mid > 0:
            _disp = max(dcf_output.enterprise_value, comps_output.mid) / min(dcf_output.enterprise_value, comps_output.mid)
            if _disp > 2.0:
                field_sources["Valuation Uncertainty"] = (
                    f"High dispersion ({_disp:.1f}x DCF vs comps)",
                    "valuation_engine",
                    "inferred",
                )
                notes.append(f"High dispersion / low confidence: DCF vs comps gap {_disp:.1f}x.")

        # ── 8. Sensitivity matrix ─────────────────────────────────────────
        sensitivity = self._build_sensitivity(dcf_input, wacc, terminal_growth)
        if (
            sensitivity
            and market_data
            and market_data.shares_outstanding
            and market_data.shares_outstanding > 0
        ):
            try:
                _all = [v for row in sensitivity.ev_matrix for v in row]
                if _all:
                    _ev_lo = min(_all)
                    _ev_hi = max(_all)
                    _nd = market_data.net_debt or 0.0
                    _px_lo = (_ev_lo - _nd) / market_data.shares_outstanding
                    _px_hi = (_ev_hi - _nd) / market_data.shares_outstanding
                    field_sources["Fair Value Range"] = (
                        f"${min(_px_lo, _px_hi):.2f}–${max(_px_lo, _px_hi):.2f}",
                        "valuation_sensitivity",
                        "inferred",
                    )
            except Exception:
                pass

        # ── 9. Data confidence ────────────────────────────────────────────
        if market_data:
            data_confidence = "verified"
        elif financials.get("revenue_current"):
            data_confidence = "estimated"
        else:
            data_confidence = "inferred"

        return FullValuationOutput(
            dcf=dcf_output,
            comps=comps_output,
            transactions=tx_output,
            blended=blended,
            lbo=lbo,
            recommendation=recommendation,
            sensitivity=sensitivity,
            wacc_used=wacc,
            terminal_growth_used=terminal_growth,
            data_confidence=data_confidence,
            sector=sector or "Unknown",
            valuation_path=valuation_path,
            field_sources=field_sources,
            weights_used=weights,
            notes=notes,
        )

    def _apply_recommendation_guardrails(
        self,
        recommendation: RecommendationOutput,
        dcf_output: Optional[DCFOutput],
        comps_output: Optional[CompsOutput],
        market_data,
        notes: list[str],
        ebitda_margin: float,
    ) -> RecommendationOutput:
        if recommendation.recommendation != "SELL":
            return recommendation

        rec = recommendation.recommendation
        if dcf_output and comps_output and dcf_output.enterprise_value > 0 and comps_output.mid > 0:
            ratio = max(dcf_output.enterprise_value, comps_output.mid) / min(dcf_output.enterprise_value, comps_output.mid)
            if ratio > 2.0:
                notes.append(
                    f"Recommendation guardrail: high valuation dispersion ({ratio:.1f}x DCF/comps) "
                    "→ downgraded SELL to HOLD."
                )
                rec = "HOLD"

        if rec == "SELL" and ebitda_margin >= 0.25:
            _g = market_data.forward_revenue_growth if market_data else None
            if _g is None or _g > -0.02:
                notes.append(
                    "Recommendation guardrail: resilient margins and no structural growth collapse "
                    "→ capped at HOLD."
                )
                rec = "HOLD"

        recommendation.recommendation = rec
        return recommendation

    # ── Comps paths ───────────────────────────────────────────────────────────

    def _standard_comps(self, revenue, ebitda, market_data, sector_m, assumptions, notes, sector=""):
        ev_ebitda_low, ev_ebitda_mid, ev_ebitda_high = sector_m.ev_ebitda
        comps_field_sources: dict = {}
        comps_range = assumptions.get("ev_ebitda_range")
        peer_median = self._f(assumptions.get("ev_ebitda_median"), None)
        is_mega_cap = bool(
            market_data and market_data.market_cap
            and market_data.market_cap > (_cfg.lbo.mega_cap_skip_usd_bn * 1000)
        )

        if comps_range and len(comps_range) == 2:
            ev_ebitda_low, ev_ebitda_high = sorted((float(comps_range[0]), float(comps_range[1])))
            if peer_median is None:
                peer_median = (ev_ebitda_low + ev_ebitda_high) / 2.0
            # Mega-cap tech quality gate:
            # reject low-multiple peer bands (e.g. 8x–12x) that are not credible
            # for Apple/Microsoft/NVIDIA-scale companies.
            if self._reject_peer_range_for_mega_cap_tech(ev_ebitda_low, ev_ebitda_high, market_data, sector):
                if is_mega_cap:
                    notes.append(
                        "Peer range rejected by mega-cap tech gate and no sector fallback allowed; "
                        "marking comps insufficient."
                    )
                    comps_field_sources["EV/EBITDA (peer range)"] = (
                        "rejected peer range (insufficient comps)",
                        "peer_quality_gate",
                        "inferred",
                    )
                    comps = CompsOutput(low=0.0, mid=0.0, high=0.0)
                    tx = compute_transaction(
                        TransactionInput(revenue=revenue, multiple=0.0)
                    )
                    return comps, tx, comps_field_sources
                ev_ebitda_low, ev_ebitda_mid, ev_ebitda_high = sector_m.ev_ebitda
                notes.append(
                    "Peer range rejected by quality gate; using sector-table fallback "
                    f"{ev_ebitda_low:.1f}x/{ev_ebitda_mid:.1f}x/{ev_ebitda_high:.1f}x."
                )
                comps_field_sources["EV/EBITDA (peer range)"] = (
                    f"{ev_ebitda_low:.1f}x–{ev_ebitda_high:.1f}x (fallback)",
                    "peer_quality_gate",
                    "inferred",
                )
                comps_field_sources["EV/EBITDA (peer median)"] = (
                    f"{ev_ebitda_mid:.1f}x",
                    "sector_table",
                    "inferred",
                )
            else:
                ev_ebitda_mid = float(peer_median)
                comps_field_sources["EV/EBITDA (peer range)"] = (
                    f"{ev_ebitda_low:.1f}x–{ev_ebitda_high:.1f}x",
                    "validated_peers",
                    "verified",
                )
                comps_field_sources["EV/EBITDA (peer median)"] = (
                    f"{ev_ebitda_mid:.1f}x",
                    "validated_peers",
                    "verified",
                )
                notes.append(
                    f"Comps from validated peers: P25 {ev_ebitda_low:.1f}x / "
                    f"Median {ev_ebitda_mid:.1f}x / P75 {ev_ebitda_high:.1f}x EV/EBITDA."
                )
        else:
            if is_mega_cap:
                notes.append("No validated peer range for mega-cap; comps marked insufficient.")
                comps_field_sources["EV/EBITDA (peer range)"] = (
                    "missing validated peer range",
                    "validated_peers",
                    "inferred",
                )
                comps = CompsOutput(low=0.0, mid=0.0, high=0.0)
                tx = compute_transaction(
                    TransactionInput(revenue=revenue, multiple=0.0)
                )
                return comps, tx, comps_field_sources
            # last-resort deterministic sector table
            ev_ebitda_low, ev_ebitda_mid, ev_ebitda_high = sector_m.ev_ebitda
            comps_field_sources["EV/EBITDA (peer range)"] = (
                f"{ev_ebitda_low:.1f}x–{ev_ebitda_high:.1f}x",
                "sector_table",
                "inferred",
            )
            comps_field_sources["EV/EBITDA (peer median)"] = (
                f"{ev_ebitda_mid:.1f}x",
                "sector_table",
                "inferred",
            )
            notes.append(
                f"Comps fallback: sector-table EV/EBITDA {ev_ebitda_low:.1f}x/{ev_ebitda_mid:.1f}x/{ev_ebitda_high:.1f}x."
            )

        comps = CompsOutput(
            low=ebitda * ev_ebitda_low,
            mid=ebitda * ev_ebitda_mid,
            high=ebitda * ev_ebitda_high,
        )
        # Cap tx_multiple to 1.5× the sector's high-end EV/Revenue bound.
        # LLM-sourced M&A data can return outlier multiples (e.g. 22x for a single
        # blockbuster deal) that would dominate the blended valuation at 20% weight.
        _, ev_rev_mid, ev_rev_high = sector_m.ev_revenue
        raw_tx = self._f(assumptions.get("tx_multiple"), ev_rev_mid)
        tx_multiple = min(raw_tx, ev_rev_high * 1.5)
        if raw_tx > tx_multiple:
            notes.append(
                f"tx_multiple capped at {tx_multiple:.1f}x "
                f"(LLM proposed {raw_tx:.1f}x, sector high = {ev_rev_high:.1f}x)."
            )
        tx = compute_transaction(
            TransactionInput(revenue=revenue, multiple=tx_multiple)
        )
        return comps, tx, comps_field_sources

    def _reject_peer_range_for_mega_cap_tech(self, peer_low, peer_high, market_data, sector: str) -> bool:
        if not market_data or not market_data.market_cap:
            return False
        _mega_cap_usd_m = _cfg.lbo.mega_cap_skip_usd_bn * 1000
        if market_data.market_cap <= _mega_cap_usd_m:
            return False

        s = f"{sector or ''} {market_data.sector or ''}".lower()
        tech_tokens = (
            "technology", "tech", "software", "semiconductor", "information technology"
        )
        is_tech = any(tok in s for tok in tech_tokens)
        if not is_tech:
            return False

        # Apple-class peers should not be valued on low-teens EV/EBITDA bands.
        return float(peer_high) < 15.0

    def _financial_comps(self, market_data, sector_m, notes):
        """P/E and P/B based equity valuation for banks/insurers."""
        notes.append("Financial sector detected — using P/E + P/B valuation path.")

        pe_low, _, pe_high = sector_m.pe_range or (10.0, 13.0, 17.0)
        _, pb_mid, _ = sector_m.pb_range or (0.8, 1.2, 1.8)

        # P/E based EV (equity value proxy)
        if market_data and market_data.forward_eps and market_data.shares_outstanding:
            fwd_earnings = market_data.forward_eps * market_data.shares_outstanding
            pe_ev_low = fwd_earnings * pe_low
            pe_ev_high = fwd_earnings * pe_high
            notes.append(
                f"P/E comps: fwd earnings ${fwd_earnings:.0f}M × "
                f"{pe_low:.0f}x–{pe_high:.0f}x P/E."
            )
        elif market_data and market_data.net_income_ttm:
            pe_ev_low = market_data.net_income_ttm * pe_low
            pe_ev_high = market_data.net_income_ttm * pe_high
        else:
            # Fallback: use market cap as base
            mc = (market_data.market_cap if market_data and market_data.market_cap else 1000.0)
            pe_ev_low = mc * 0.70
            pe_ev_high = mc * 1.30

        comps = CompsOutput(
            low=round(pe_ev_low, 1),
            mid=round((pe_ev_low + pe_ev_high) / 2, 1),
            high=round(pe_ev_high, 1),
        )

        # P/B based EV (book value × P/B multiple)
        if market_data and market_data.total_equity:
            pb_ev = market_data.total_equity * pb_mid
            notes.append(
                f"P/B comps: book equity ${market_data.total_equity:.0f}M × {pb_mid:.1f}x P/B."
            )
        else:
            pb_ev = comps.mid

        tx = TransactionOutput(implied_value=round(pb_ev, 1))
        return comps, tx

    # ── Revenue & assumption helpers ──────────────────────────────────────────

    def _build_revenue_series(self, financials, market_data, notes, sector: str = ""):
        # Priority 1: verified yfinance — project FORWARD from most recent year
        if market_data and market_data.revenue_history and len(market_data.revenue_history) >= 2:
            hist = market_data.revenue_history
            base = hist[-1]  # most recent annual revenue (never use hist as forward projections)

            if market_data.forward_revenue_growth is not None:
                fwd = market_data.forward_revenue_growth
                # Mature mega-cap normalization:
                # analyst 1Y growth can spike with cycle effects and should not be directly
                # extrapolated for 5-year cash-flow projections.
                _mega_cap_usd_m = _cfg.lbo.mega_cap_skip_usd_bn * 1000
                if (
                    market_data.market_cap
                    and market_data.market_cap > _mega_cap_usd_m
                    and fwd > 0.15
                ):
                    hist_cagr = self._historical_cagr(hist)
                    norm_fwd = min(max((fwd * 0.45) + (hist_cagr * 0.55), 0.08), 0.12)
                    notes.append(
                        f"Forward growth normalised for mega-cap maturity: {fwd:.1%}→{norm_fwd:.1%} "
                        f"(blend of analyst 1Y and historical CAGR)."
                    )
                    fwd = norm_fwd
                # Multi-stage fade: near-term growth gradually converges to sustainable rate.
                long_run = min(max(fwd * 0.35, 0.03), 0.08)  # clamp long-run to 3–8%
                growth_rates = [
                    fwd,
                    max(fwd * 0.70, long_run * 1.80),
                    max(fwd * 0.45, long_run * 1.40),
                    long_run * 1.15,
                    long_run,
                ]
                series = []
                rev = base
                for g in growth_rates:
                    rev = rev * (1 + g)
                    series.append(rev)
                y1, y5 = growth_rates[0], growth_rates[-1]
                notes.append(f"Revenue fade: {y1:.1%}→{y5:.1%} (multi-stage convergence).")
            else:
                growth = min(self._historical_cagr(hist), 0.35)
                series = [base * (1 + growth) ** i for i in range(1, self.PROJECTION_YEARS + 1)]
                notes.append(f"Revenue at {growth:.1%} p.a. (historical CAGR).")
            return series, "verified"

        # Priority 2: LLM revenue_series — project FORWARD from most recent value
        rs = financials.get("revenue_series")
        if isinstance(rs, list) and len(rs) >= 2:
            parsed = [self._f(x) for x in rs if self._f(x) > 0]
            if len(parsed) >= 2:
                base = parsed[-1]
                growth = min(self._historical_cagr(parsed), 0.35)
                series = [base * (1 + growth) ** i for i in range(1, self.PROJECTION_YEARS + 1)]
                notes.append(f"Revenue at {growth:.1%} p.a. (LLM series CAGR).")
                return series, "estimated"

        # Priority 3: single base + growth rate — project FORWARD
        base = None
        if market_data and market_data.revenue_ttm:
            base = market_data.revenue_ttm
        else:
            base = self._f(financials.get("revenue_current"), None)

        if base and base > 0:
            _fwd = market_data.forward_revenue_growth if market_data else None
            _llm = self._f(financials.get("revenue_growth"), None)
            if _fwd is not None:
                raw_growth = _fwd
            elif _llm is not None:
                raw_growth = _llm
            else:
                from goldroger.data.sector_multiples import get_sector_rev_growth
                raw_growth = get_sector_rev_growth(sector)
                notes.append(f"Revenue growth from sector benchmark ({raw_growth:.0%}) — no verified data.")
            growth = max(-0.10, min(float(raw_growth), 0.35))
            series = [base * (1 + growth) ** i for i in range(1, self.PROJECTION_YEARS + 1)]
            notes.append(f"Revenue at {growth:.1%} p.a. (single-point base).")
            return series, "inferred"

        notes.append("REVENUE_MISSING: No revenue data — DCF and comps omitted.")
        return None, "missing"

    def _resolve_ebitda_margin(self, financials, market_data, notes, sector: str = ""):
        if market_data and market_data.ebitda_margin is not None:
            m = market_data.ebitda_margin
            notes.append(f"EBITDA margin {m:.1%} from live market data.")
            return max(-0.50, min(m, 0.80)), "verified"
        raw = self._f(financials.get("ebitda_margin"), None)
        if raw is not None:
            if raw > 1.0:
                raw /= 100.0
            return max(-0.50, min(raw, 0.80)), "estimated"
        from goldroger.data.sector_multiples import get_sector_ebitda_margin
        margin = get_sector_ebitda_margin(sector) if sector else 0.18
        notes.append(f"EBITDA margin not found — using sector default {margin:.0%}.")
        return margin, "inferred"

    def _resolve_tax_rate(self, financials, market_data):
        if market_data and market_data.effective_tax_rate is not None:
            return market_data.effective_tax_rate
        raw = self._f(financials.get("tax_rate"), None)
        if raw is not None:
            if raw > 1.0:
                raw /= 100.0
            return max(0.0, min(raw, 0.50))
        return 0.25

    def _resolve_capex_pct(self, financials, market_data, revenue_current):
        if market_data and market_data.capex_ttm and revenue_current and revenue_current > 0:
            return max(0.0, min(market_data.capex_ttm / revenue_current, 0.30))
        raw = self._f(financials.get("capex_pct"), None)
        if raw is not None:
            if raw > 1.0:
                raw /= 100.0
            return max(0.0, min(raw, 0.30))
        return 0.04

    def _resolve_da_pct(self, market_data, revenue_current):
        if market_data and market_data.da_ttm and revenue_current and revenue_current > 0:
            return max(0.0, min(market_data.da_ttm / revenue_current, 0.20))
        return None

    def _resolve_wacc(self, assumptions, market_data, sector_m, notes):
        if (
            market_data
            and market_data.beta
            and market_data.market_cap
            and market_data.market_cap > 0
        ):
            net_debt = market_data.net_debt or 0.0
            tax = market_data.effective_tax_rate or 0.25
            # For net-cash companies (net_debt < 0), D=0 in WACC formula
            # (excess cash is treated as unlevered balance sheet, not negative leverage)
            if net_debt < 0:
                notes.append(
                    f"Net cash position (net debt ${net_debt:.0f}M) — "
                    "WACC computed as unlevered (D=0 in weights)."
                )
            wacc = compute_capm_wacc(
                beta=market_data.beta,
                market_cap=market_data.market_cap,
                net_debt=max(net_debt, 0.0),
                tax_rate=tax,
                interest_expense=market_data.interest_expense,
                total_debt=market_data.total_debt,
            )
            re = capm_cost_of_equity(market_data.beta)
            notes.append(
                f"WACC {wacc:.1%} via CAPM (β={market_data.beta:.2f}, "
                f"Rf={RISK_FREE_RATE:.1%}, ERP={EQUITY_RISK_PREMIUM:.1%}, Re={re:.1%})."
            )
            return wacc, "verified"

        # Policy: do not take numeric WACC from LLM by default.
        # Only accept a WACC override if the orchestrator explicitly marks it as user-provided.
        # (LLM can still propose assumption ranges in narrative, but valuation remains reproducible.)
        raw = self._f(assumptions.get("wacc"), None)
        if raw is not None and assumptions.get("_assumption_source") == "user":
            if raw > 1.0:
                raw /= 100.0
            wacc = max(0.05, min(raw, 0.25))
            notes.append(f"WACC {wacc:.1%} from user override.")
            return wacc, "estimated"

        wacc = sector_m.sector_wacc
        notes.append(f"WACC {wacc:.1%} from sector default.")
        return wacc, "inferred"

    def _resolve_terminal_growth(self, assumptions, sector_m, wacc, notes):
        raw = self._f(assumptions.get("terminal_growth"), None)
        if raw is not None and assumptions.get("_assumption_source") == "user":
            if raw > 1.0:
                raw /= 100.0
            tg = max(0.0, min(raw, 0.04))
            notes.append(f"Terminal growth {tg:.1%} from user override.")
        else:
            tg = sector_m.terminal_growth
        if tg >= wacc:
            tg = wacc - 0.01
            notes.append("Terminal growth clamped below WACC.")
        return tg

    def _run_lbo(self, blended_ev, ebitda, revenue_series, ebitda_margin, capex_pct, tax_rate, market_data=None):
        if ebitda <= 0 or blended_ev <= 0:
            return None
        # LBO is not applicable to mega-caps
        _mega_cap_usd_m = _cfg.lbo.mega_cap_skip_usd_bn * 1000
        if market_data and market_data.market_cap and market_data.market_cap > _mega_cap_usd_m:
            return None
        try:
            growth = self._historical_cagr(revenue_series)
            return lbo_from_valuation(
                blended_ev=blended_ev,
                ebitda=ebitda,
                revenue_growth=growth,
                ebitda_margin=ebitda_margin,
                capex_pct=capex_pct,
                tax_rate=tax_rate,
            )
        except Exception:
            return None

    def _compute_recommendation(self, blended, market_data, notes):
        ev_blended = blended.blended
        intrinsic_price = current_price = market_cap = upside_pct = None
        rec = "HOLD"

        if (
            market_data
            and market_data.current_price
            and market_data.shares_outstanding
            and market_data.shares_outstanding > 0
            and market_data.market_cap
        ):
            net_debt = market_data.net_debt or 0.0
            equity_value = ev_blended - net_debt
            intrinsic_price = equity_value / market_data.shares_outstanding
            current_price = market_data.current_price
            market_cap = market_data.market_cap
            upside_pct = (intrinsic_price - current_price) / current_price

            if upside_pct > 0.15:
                rec = "BUY"
            elif upside_pct < -0.15:
                rec = "SELL"

            notes.append(
                f"Intrinsic ${intrinsic_price:.2f} vs market ${current_price:.2f} "
                f"→ {upside_pct:+.1%} → {rec}."
            )
        else:
            notes.append("Recommendation defaulted to HOLD (no live price data).")

        return RecommendationOutput(
            recommendation=rec,
            upside_pct=upside_pct,
            intrinsic_price=intrinsic_price,
            current_price=current_price,
            market_cap=market_cap,
            ev_blended=ev_blended,
        )

    def _build_sensitivity(self, base_input, wacc, tg):
        wacc_steps = [-0.02, -0.01, 0.0, 0.01, 0.02]
        tg_steps = [-0.01, -0.005, 0.0, 0.005, 0.01]
        wacc_range = [round(wacc + d, 4) for d in wacc_steps]
        tg_range = [round(tg + d, 4) for d in tg_steps]

        matrix: list[list[float]] = []
        for w in wacc_range:
            row: list[float] = []
            for g in tg_range:
                w_c = max(0.05, min(w, 0.25))
                g_c = max(0.0, min(g, w_c - 0.005))
                inp = DCFInput(
                    revenue=base_input.revenue,
                    ebitda_margin=base_input.ebitda_margin,
                    tax_rate=base_input.tax_rate,
                    capex_pct=base_input.capex_pct,
                    nwc_pct=base_input.nwc_pct,
                    wacc=w_c,
                    terminal_growth=g_c,
                    da_pct=base_input.da_pct,
                    base_revenue=base_input.base_revenue,
                )
                row.append(round(compute_dcf(inp).enterprise_value, 1))
            matrix.append(row)

        return SensitivityMatrix(wacc_range=wacc_range, tg_range=tg_range, ev_matrix=matrix)

    @staticmethod
    def _historical_cagr(series: list[float]) -> float:
        if len(series) < 2 or series[0] <= 0:
            return 0.08
        n = len(series) - 1
        return max(-0.10, min((series[-1] / series[0]) ** (1 / n) - 1, 0.40))

    _fx_cache: dict = {}

    @staticmethod
    def _live_fx() -> dict:
        """Fetch live FX rates from yfinance; fall back to hardcoded if unavailable."""
        if ValuationService._fx_cache:
            return ValuationService._fx_cache
        _HARDCODED = {"€": 1.08, "eur": 1.08, "gbp": 1.26, "£": 1.26, "chf": 1.11, "cad": 0.74}
        try:
            import yfinance as yf
            pairs = {"eur": "EURUSD=X", "gbp": "GBPUSD=X", "chf": "CHFUSD=X", "cad": "CADUSD=X"}
            rates = {}
            for sym, ticker in pairs.items():
                try:
                    info = yf.Ticker(ticker).fast_info
                    price = getattr(info, "last_price", None) or getattr(info, "lastPrice", None)
                    if price:
                        rates[sym] = float(price)
                except Exception:
                    pass
            if len(rates) >= 2:
                result = {
                    "€": rates.get("eur", _HARDCODED["eur"]),
                    "eur": rates.get("eur", _HARDCODED["eur"]),
                    "gbp": rates.get("gbp", _HARDCODED["gbp"]),
                    "£": rates.get("gbp", _HARDCODED["gbp"]),
                    "chf": rates.get("chf", _HARDCODED["chf"]),
                    "cad": rates.get("cad", _HARDCODED["cad"]),
                }
                ValuationService._fx_cache = result
                return result
        except Exception:
            pass
        return _HARDCODED

    @staticmethod
    def _f(v, default=0.0):
        _FX = ValuationService._live_fx()
        try:
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).strip()
            fx = 1.0
            s_lower = s.lower()
            for sym, rate in _FX.items():
                if sym in s_lower:
                    fx = rate
                    s = s_lower.replace(sym, "").strip()
                    break
            # Strip suffix multipliers like "650m" or "1.2b"
            if s.lower().endswith("b"):
                return float(s[:-1].replace(",", "")) * 1000.0 * fx
            if s.lower().endswith("m"):
                return float(s[:-1].replace(",", "")) * fx
            return float(s.replace("%", "").replace(",", "").strip()) * fx
        except Exception:
            return default
