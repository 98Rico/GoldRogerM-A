from goldroger.finance.valuation.dcf import DCFInput, compute_dcf
from goldroger.finance.valuation.comps import CompsInput, compute_comps
from goldroger.finance.valuation.transactions import TransactionInput, compute_transaction
from goldroger.finance.valuation.aggregator import compute_weighted_valuation


class ValuationService:
    """
    Orchestrates ALL deterministic valuation engines.
    """

    def run_dcf(self, financials: dict, wacc: float):
        inp = DCFInput(
            revenue=financials["revenue_series"],
            ebitda_margin=financials["ebitda_margin"],
            tax_rate=financials["tax_rate"],
            capex_pct=financials["capex_pct"],
            nwc_pct=financials["nwc_pct"],
            wacc=wacc,
            terminal_growth=0.02,
        )
        return compute_dcf(inp)

    def run_comps(self, financials: dict, multiple_range: tuple):
        return compute_comps(
            CompsInput(
                metric_value=financials["ebitda_latest"],
                multiple_range=multiple_range
            )
        )

    def run_transactions(self, financials: dict, multiple: float):
        return compute_transaction(
            TransactionInput(
                revenue=financials["revenue_latest"],
                multiple=multiple
            )
        )

    def run_full_valuation(self, financials: dict, assumptions: dict):
        """
        FULL PIPELINE ENTRY POINT
        """

        dcf = self.run_dcf(financials, assumptions["wacc"])
        comps = self.run_comps(financials, assumptions["ev_ebitda_range"])
        tx = self.run_transactions(financials, assumptions["tx_multiple"])

        return compute_weighted_valuation(
            dcf,
            comps,
            tx,
            assumptions["weights"]
        )