from typing import Dict, Any, List

from goldroger.finance.valuation.dcf import DCFInput


def extract_revenue_series(financials: Dict[str, Any]) -> List[float]:
    """
    Converts LLM historical revenue into clean float list
    """
    history = financials.get("revenue_history", [])

    values = []
    for item in history:
        val = item.get("value", "N/A")

        if val == "N/A":
            continue

        # crude cleanup: "$1.2B" → 1.2e9
        if isinstance(val, str):
            val = val.replace("$", "").replace("€", "").strip()

            if "B" in val:
                val = float(val.replace("B", "")) * 1e9
            elif "M" in val:
                val = float(val.replace("M", "")) * 1e6
            else:
                val = float(val)

        values.append(float(val))

    return values


def build_dcf_from_llm(financials: Dict[str, Any], wacc: float) -> DCFInput:
    """
    Converts FinancialModelerAgent output → DCF engine input
    """

    revenue_series = extract_revenue_series(financials)

    # fallback: if no history, use latest as flat proxy
    if len(revenue_series) == 0:
        latest = financials.get("revenue_latest", 0)
        revenue_series = [float(latest)] * 3

    ebitda_margin = financials.get("ebitda_margin", 0.15)
    capex = financials.get("capex", 0.05)
    nwc = financials.get("nwc_pct", 0.02)
    tax = financials.get("tax_rate", 0.25)

    # ensure numeric cleanup
    def clean(x):
        if isinstance(x, str):
            return float(x.replace("%", "")) / 100
        return float(x)

    return DCFInput(
        revenue=revenue_series,
        ebitda_margin=clean(ebitda_margin),
        tax_rate=clean(tax),
        capex_pct=clean(capex),
        nwc_pct=clean(nwc),
        wacc=wacc,
        terminal_growth=0.02,
    )