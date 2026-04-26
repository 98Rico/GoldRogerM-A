from goldroger.finance.valuation.dcf import DCFInput, compute_dcf
from goldroger.finance.valuation.comps import CompsInput, compute_comps
from goldroger.finance.valuation.transactions import TransactionInput, compute_transaction
from goldroger.finance.valuation.aggregator import compute_weighted_valuation


class ValuationService:
    """
    Safe deterministic valuation engine (production-ready)
    """

    def _f(self, v, default=0.0):
        try:
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            return float(str(v).replace("%", "").replace(",", "").strip())
        except:
            return default

    def _revenue_series(self, financials: dict):
        rs = financials.get("revenue_series")
        if isinstance(rs, list) and len(rs) >= 3:
            return [self._f(x) for x in rs]

        base = self._f(financials.get("revenue_current"), 1000.0)
        g = 0.08

        return [
            base,
            base * (1 + g),
            base * (1 + g) ** 2,
            base * (1 + g) ** 3,
            base * (1 + g) ** 4,
        ]

    def run_dcf(self, financials: dict, wacc: float):
        inp = DCFInput(
            revenue=self._revenue_series(financials),
            ebitda_margin=self._f(financials.get("ebitda_margin"), 0.2),
            tax_rate=self._f(financials.get("tax_rate"), 0.25),
            capex_pct=self._f(financials.get("capex_pct"), 0.04),
            nwc_pct=self._f(financials.get("nwc_pct"), 0.02),
            wacc=self._f(wacc, 0.1),
            terminal_growth=0.02,
        )
        return compute_dcf(inp)

    def run_comps(self, financials: dict, multiple_range: tuple):
        revenue = self._f(financials.get("revenue_current"), 1000.0)
        ebitda = revenue * self._f(financials.get("ebitda_margin"), 0.2)

        return compute_comps(
            CompsInput(
                metric_value=ebitda,
                multiple_range=multiple_range or (8, 12),
            )
        )

    def run_transactions(self, financials: dict, multiple: float):
        return compute_transaction(
            TransactionInput(
                revenue=self._f(financials.get("revenue_current"), 1000.0),
                multiple=self._f(multiple, 2.5),
            )
        )

    def run_full_valuation(self, financials: dict, assumptions: dict):
        dcf = self.run_dcf(financials, assumptions.get("wacc", 0.1))
        comps = self.run_comps(financials, assumptions.get("ev_ebitda_range", (8, 12)))
        tx = self.run_transactions(financials, assumptions.get("tx_multiple", 2.5))

        weights = assumptions.get("weights") or {
            "dcf": 0.5,
            "comps": 0.3,
            "transactions": 0.2,
        }

        return compute_weighted_valuation(dcf, comps, tx, weights)