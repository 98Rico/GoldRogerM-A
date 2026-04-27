"""
ValuationService — institution-grade valuation orchestrator.

Data priority:
  1. Verified (yfinance MarketData)   → tagged "verified"
  2. LLM-extracted financials         → tagged "estimated"
  3. Sector defaults                  → tagged "inferred"

Outputs:
  - DCF enterprise value (CAPM-derived WACC where possible)
  - Trading comps (sector-calibrated EV/EBITDA ranges)
  - Transaction comps (sector EV/Revenue ranges)
  - Blended ValuationResult (50/30/20 weights)
  - BUY / HOLD / SELL recommendation vs current market cap
  - Source confidence per field
  - Sensitivity matrix (WACC × terminal growth)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from goldroger.data.sector_multiples import get_sector_multiples
from goldroger.finance.core.wacc import compute_capm_wacc, capm_cost_of_equity, RISK_FREE_RATE, EQUITY_RISK_PREMIUM
from goldroger.finance.valuation.dcf import DCFInput, DCFOutput, compute_dcf
from goldroger.finance.valuation.comps import CompsInput, CompsOutput, compute_comps
from goldroger.finance.valuation.transactions import TransactionInput, TransactionOutput, compute_transaction
from goldroger.finance.valuation.aggregator import compute_weighted_valuation, ValuationResult


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class RecommendationOutput:
    recommendation: str          # BUY / HOLD / SELL
    upside_pct: Optional[float]  # decimal, e.g. 0.25 = +25%
    intrinsic_price: Optional[float]
    current_price: Optional[float]
    market_cap: Optional[float]
    ev_blended: float


@dataclass
class SensitivityMatrix:
    wacc_range: list[float]
    tg_range: list[float]
    ev_matrix: list[list[float]]   # [wacc_idx][tg_idx]


@dataclass
class FullValuationOutput:
    dcf: DCFOutput
    comps: CompsOutput
    transactions: TransactionOutput
    blended: ValuationResult
    recommendation: RecommendationOutput
    sensitivity: SensitivityMatrix
    wacc_used: float
    terminal_growth_used: float
    data_confidence: str           # "verified" / "estimated" / "inferred"
    sector: str
    notes: list[str] = field(default_factory=list)


# ── ValuationService ──────────────────────────────────────────────────────────

class ValuationService:
    """
    Run DCF + Comps + Transactions and produce a blended, defensible EV.
    Accepts optional MarketData for verified real inputs.
    """

    # Default DCF projection horizon
    PROJECTION_YEARS = 5

    def run_full_valuation(
        self,
        financials: dict,
        assumptions: dict,
        market_data=None,   # Optional[MarketData] — avoid circular import
        sector: str = "",
    ) -> FullValuationOutput:

        sector_m = get_sector_multiples(sector)
        notes: list[str] = []

        # ── 1. Core financial inputs ──────────────────────────────────────
        revenue_series, _ = self._build_revenue_series(
            financials, market_data, sector_m, notes
        )
        revenue_current = revenue_series[-1]  # most recent / projected base

        ebitda_margin, _ = self._resolve_ebitda_margin(
            financials, market_data, notes
        )

        tax_rate = self._resolve_tax_rate(financials, market_data)

        capex_pct = self._resolve_capex_pct(financials, market_data, revenue_current)

        nwc_pct = self._f(financials.get("nwc_pct"), 0.02)

        da_pct = self._resolve_da_pct(market_data, revenue_current)

        # ── 2. WACC ───────────────────────────────────────────────────────
        wacc, _ = self._resolve_wacc(
            financials, assumptions, market_data, sector_m, notes
        )

        # ── 3. Terminal growth ────────────────────────────────────────────
        terminal_growth = self._resolve_terminal_growth(
            assumptions, sector_m, wacc, notes
        )

        # ── 4. DCF ───────────────────────────────────────────────────────
        dcf_input = DCFInput(
            revenue=revenue_series,
            ebitda_margin=ebitda_margin,
            tax_rate=tax_rate,
            capex_pct=capex_pct,
            nwc_pct=nwc_pct,
            wacc=wacc,
            terminal_growth=terminal_growth,
            da_pct=da_pct,
        )
        dcf_output = compute_dcf(dcf_input)

        if dcf_output.terminal_value_pct > 0.85:
            notes.append(
                f"Terminal value is {dcf_output.terminal_value_pct:.0%} of EV — "
                "model is highly sensitive to terminal assumptions."
            )

        # ── 5. Trading comps (EV/EBITDA) ─────────────────────────────────
        ebitda = revenue_current * ebitda_margin

        # Use market-implied EV/EBITDA to anchor mid if available
        ev_ebitda_low, _, ev_ebitda_high = sector_m.ev_ebitda
        if market_data and market_data.ev_ebitda_market:
            implied = market_data.ev_ebitda_market
            ev_ebitda_low = implied * 0.75
            ev_ebitda_high = implied * 1.25
            notes.append(f"Comps anchored to live market EV/EBITDA of {implied:.1f}x.")

        comps_range = assumptions.get("ev_ebitda_range")
        if comps_range and len(comps_range) == 2:
            ev_ebitda_low, ev_ebitda_high = comps_range

        comps_output = compute_comps(
            CompsInput(
                metric_value=ebitda,
                multiple_range=(ev_ebitda_low, ev_ebitda_high),
            )
        )

        # ── 6. Transaction comps (EV/Revenue) ────────────────────────────
        tx_multiple = self._f(assumptions.get("tx_multiple"), sector_m.ev_revenue[1])

        tx_output = compute_transaction(
            TransactionInput(revenue=revenue_current, multiple=tx_multiple)
        )

        # ── 7. Blended valuation ──────────────────────────────────────────
        weights = assumptions.get("weights") or {"dcf": 0.5, "comps": 0.3, "transactions": 0.2}
        blended = compute_weighted_valuation(dcf_output, comps_output, tx_output, weights)

        # ── 8. BUY / HOLD / SELL ─────────────────────────────────────────
        recommendation = self._compute_recommendation(blended, market_data, notes)

        # ── 9. Sensitivity matrix ─────────────────────────────────────────
        sensitivity = self._build_sensitivity(
            dcf_input, wacc, terminal_growth
        )

        # ── 10. Overall data confidence ───────────────────────────────────
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
            recommendation=recommendation,
            sensitivity=sensitivity,
            wacc_used=wacc,
            terminal_growth_used=terminal_growth,
            data_confidence=data_confidence,
            sector=sector or "Unknown",
            notes=notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_revenue_series(self, financials, market_data, sector_m, notes):
        # Priority 1: verified historical revenue from market_data
        if market_data and market_data.revenue_history and len(market_data.revenue_history) >= 2:
            hist = market_data.revenue_history
            # Extend to PROJECTION_YEARS using last CAGR
            growth = self._historical_cagr(hist)
            series = list(hist)
            while len(series) < self.PROJECTION_YEARS:
                series.append(series[-1] * (1 + growth))
            notes.append(f"Revenue projected at {growth:.1%} p.a. (historical CAGR).")
            return series[-self.PROJECTION_YEARS:], "verified"

        # Priority 2: LLM-provided revenue_series
        rs = financials.get("revenue_series")
        if isinstance(rs, list) and len(rs) >= 2:
            parsed = [self._f(x) for x in rs if self._f(x) > 0]
            if len(parsed) >= 2:
                growth = self._historical_cagr(parsed)
                while len(parsed) < self.PROJECTION_YEARS:
                    parsed.append(parsed[-1] * (1 + growth))
                return parsed[-self.PROJECTION_YEARS:], "estimated"

        # Priority 3: single revenue_current + sector-adjusted growth
        base = None
        if market_data and market_data.revenue_ttm:
            base = market_data.revenue_ttm
        else:
            base = self._f(financials.get("revenue_current"), None)

        if base and base > 0:
            growth = self._f(
                financials.get("revenue_growth") or
                (market_data.revenue_growth_yoy if market_data else None),
                0.08,
            )
            growth = max(-0.10, min(growth, 0.40))
            series = [base * (1 + growth) ** i for i in range(self.PROJECTION_YEARS)]
            notes.append(f"Revenue projected at {growth:.1%} p.a. (single-point base).")
            return series, "inferred"

        # Fallback: placeholder 1000 M
        notes.append("WARNING: No revenue data found — using placeholder $1,000M.")
        return [1000.0 * (1.08 ** i) for i in range(self.PROJECTION_YEARS)], "inferred"

    def _resolve_ebitda_margin(self, financials, market_data, notes):
        if market_data and market_data.ebitda_margin is not None:
            m = market_data.ebitda_margin
            notes.append(f"EBITDA margin {m:.1%} sourced from live market data.")
            return max(-0.50, min(m, 0.80)), "verified"

        raw = self._f(financials.get("ebitda_margin"), None)
        if raw is not None:
            # Handle percentage strings like "25" meaning 25%
            if raw > 1.0:
                raw /= 100.0
            return max(-0.50, min(raw, 0.80)), "estimated"

        notes.append("EBITDA margin not found — using sector default 20%.")
        return 0.20, "inferred"

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
            pct = market_data.capex_ttm / revenue_current
            return max(0.0, min(pct, 0.30))
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

    def _resolve_wacc(self, financials, assumptions, market_data, sector_m, notes):
        # Priority 1: Full CAPM from real market data
        if (
            market_data
            and market_data.beta
            and market_data.market_cap
            and market_data.market_cap > 0
        ):
            net_debt = market_data.net_debt or 0.0
            tax_rate = (
                market_data.effective_tax_rate
                if market_data.effective_tax_rate
                else 0.25
            )
            wacc = compute_capm_wacc(
                beta=market_data.beta,
                market_cap=market_data.market_cap,
                net_debt=max(net_debt, 0.0),
                tax_rate=tax_rate,
                interest_expense=market_data.interest_expense,
                total_debt=market_data.total_debt,
            )
            re = capm_cost_of_equity(market_data.beta)
            notes.append(
                f"WACC {wacc:.1%} via CAPM (β={market_data.beta:.2f}, "
                f"Rf={RISK_FREE_RATE:.1%}, ERP={EQUITY_RISK_PREMIUM:.1%}, Re={re:.1%})."
            )
            return wacc, "verified"

        # Priority 2: LLM-supplied WACC assumption
        raw = self._f(assumptions.get("wacc"), None)
        if raw is not None:
            if raw > 1.0:
                raw /= 100.0
            wacc = max(0.05, min(raw, 0.25))
            notes.append(f"WACC {wacc:.1%} from LLM assumptions.")
            return wacc, "estimated"

        # Priority 3: Sector default
        wacc = sector_m.sector_wacc
        notes.append(f"WACC {wacc:.1%} from sector default.")
        return wacc, "inferred"

    def _resolve_terminal_growth(self, assumptions, sector_m, wacc, notes):
        raw = self._f(assumptions.get("terminal_growth"), None)
        if raw is not None:
            if raw > 1.0:
                raw /= 100.0
            tg = max(0.0, min(raw, 0.04))
        else:
            tg = sector_m.terminal_growth

        # Hard constraint: must be < WACC
        if tg >= wacc:
            tg = wacc - 0.01
            notes.append("Terminal growth clamped below WACC.")

        return tg

    def _compute_recommendation(self, blended, market_data, notes):
        ev_blended = blended.blended

        intrinsic_price = None
        current_price = None
        market_cap = None
        upside_pct = None
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
            else:
                rec = "HOLD"

            notes.append(
                f"Intrinsic price ${intrinsic_price:.2f} vs market ${current_price:.2f} "
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

    def _build_sensitivity(self, base_input: DCFInput, wacc: float, tg: float) -> SensitivityMatrix:
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
                )
                row.append(round(compute_dcf(inp).enterprise_value, 1))
            matrix.append(row)

        return SensitivityMatrix(
            wacc_range=wacc_range,
            tg_range=tg_range,
            ev_matrix=matrix,
        )

    @staticmethod
    def _historical_cagr(series: list[float]) -> float:
        """Compute CAGR from a revenue series. Clamp to -10% – 40%."""
        if len(series) < 2 or series[0] <= 0:
            return 0.08
        n = len(series) - 1
        cagr = (series[-1] / series[0]) ** (1 / n) - 1
        return max(-0.10, min(cagr, 0.40))

    @staticmethod
    def _f(v, default=0.0):
        try:
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            return float(str(v).replace("%", "").replace(",", "").strip())
        except Exception:
            return default
