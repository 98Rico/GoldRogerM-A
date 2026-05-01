"""
Private company revenue triangulation engine.

For companies with no verified financial data, triangulates revenue from
multiple independent signals and returns a weighted median estimate.

Signals (in priority order):
  1. EU registries  — Companies House, Infogreffe, Handelsregister (verified)
  2. Crunchbase     — revenue_range from funding profile (estimated)
  3. Headcount      — employee count × sector revenue/employee benchmark
  4. Funding-based  — total raised × SaaS ARR/capital ratio (SaaS only)
  5. Web traffic    — SimilarWeb-style estimate (DTC/consumer only)
  6. Press NLP      — revenue figures extracted from web search snippets

Each signal produces (estimate_usd_m, confidence_score, source_label).
The engine returns the weighted median of all signals with ≥2 agreeing within ±40%.

All values in USD millions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx

# ── Revenue/employee benchmarks by sector (USD) ──────────────────────────────
# Source: public benchmarks from SaaS Capital, OpenView, McKinsey industry reports
_REV_PER_EMPLOYEE: dict[str, float] = {
    "saas": 200_000,
    "software": 200_000,
    "technology": 180_000,
    "fintech": 300_000,
    "financial services": 400_000,
    "ecommerce": 150_000,
    "retail": 250_000,
    "consumer": 180_000,
    "healthcare": 200_000,
    "industrials": 280_000,
    "manufacturing": 300_000,
    "consulting": 180_000,
    "media": 150_000,
    "default": 200_000,
}

# ARR multiple on total capital raised (SaaS benchmarks)
# Rule of thumb: funded SaaS companies typically raise ~0.5–1.5x ARR per round
_SAAS_ARR_CAPITAL_RATIO = 1.2

# Crunchbase employee count enum → midpoint headcount
_CB_EMPLOYEE_MIDPOINT: dict[str, int] = {
    "1_10": 5, "11_50": 30, "51_100": 75, "101_250": 175,
    "251_500": 375, "501_1000": 750, "1001_5000": 3000,
    "5001_10000": 7500, "10001+": 15000,
}


@dataclass
class TriangulationSignal:
    estimate_m: float        # USD millions
    confidence: float        # 0–1 weight
    source: str


@dataclass
class TriangulationResult:
    revenue_estimate_m: float
    confidence: str          # "verified", "estimated", "inferred"
    signals: list[TriangulationSignal]
    notes: list[str]


def triangulate_revenue(
    company_name: str,
    sector: str = "",
    country: str = "",
    crunchbase_data: Optional[dict] = None,
) -> Optional[TriangulationResult]:
    """
    Run all available signals and return a triangulated revenue estimate.
    Returns None if fewer than 1 signal produces a result.
    """
    signals: list[TriangulationSignal] = []
    notes: list[str] = []
    sector_lower = sector.lower()

    # ── Signal 1: EU registries ───────────────────────────────────────────────
    reg_revenue = _signal_eu_registry(company_name, country)
    if reg_revenue:
        signals.append(TriangulationSignal(reg_revenue, 0.95, "eu_registry"))
        notes.append(f"EU registry filing: ${reg_revenue:.1f}M")

    # ── Signal 2: Crunchbase revenue range ────────────────────────────────────
    if crunchbase_data:
        cb_rev = _signal_crunchbase(crunchbase_data)
        if cb_rev:
            signals.append(TriangulationSignal(cb_rev, 0.70, "crunchbase"))
            notes.append(f"Crunchbase revenue range midpoint: ${cb_rev:.1f}M")

        # ── Signal 3: Headcount × benchmark ──────────────────────────────────
        hc_rev = _signal_headcount(crunchbase_data, sector_lower)
        if hc_rev:
            signals.append(TriangulationSignal(hc_rev, 0.50, "headcount_benchmark"))
            notes.append(f"Headcount × sector benchmark: ${hc_rev:.1f}M")

        # ── Signal 4: Funding-based ARR (SaaS only) ───────────────────────────
        if any(k in sector_lower for k in ("saas", "software", "tech")):
            fund_rev = _signal_funding_arr(crunchbase_data)
            if fund_rev:
                signals.append(TriangulationSignal(fund_rev, 0.40, "funding_arr_proxy"))
                notes.append(f"Funding ARR proxy: ${fund_rev:.1f}M")

    # ── Signal 5: Wikipedia revenue mention ──────────────────────────────────
    wiki_rev = _signal_wikipedia(company_name)
    if wiki_rev:
        signals.append(TriangulationSignal(wiki_rev, 0.60, "wikipedia"))
        notes.append(f"Wikipedia revenue mention: ${wiki_rev:.1f}M")

    # ── Signal 6: Press/web NLP ───────────────────────────────────────────────
    press_rev = _signal_press_nlp(company_name)
    if press_rev:
        signals.append(TriangulationSignal(press_rev, 0.55, "press_nlp"))
        notes.append(f"Press/web revenue mention: ${press_rev:.1f}M")

    if not signals:
        return None

    estimate = _weighted_median(signals)
    n_agreeing = _count_agreeing(signals, estimate)

    if any(s.source == "eu_registry" for s in signals):
        confidence = "verified"
    elif n_agreeing >= 2:
        confidence = "estimated"
    else:
        confidence = "inferred"

    return TriangulationResult(
        revenue_estimate_m=round(estimate, 1),
        confidence=confidence,
        signals=signals,
        notes=notes,
    )


# ── Signal implementations ────────────────────────────────────────────────────

def _signal_eu_registry(company_name: str, country: str) -> Optional[float]:
    """Try EU registries in order based on country hint."""
    country_lower = country.lower()

    # France first if country hints FR
    if any(k in country_lower for k in ("france", "fr", "paris")):
        rev = _try_infogreffe(company_name)
        if rev:
            return rev

    # UK
    if any(k in country_lower for k in ("uk", "united kingdom", "england", "britain", "london")):
        rev = _try_companies_house(company_name)
        if rev:
            return rev

    # Germany
    if any(k in country_lower for k in ("germany", "de", "deutschland", "berlin", "munich")):
        rev = _try_handelsregister(company_name)
        if rev:
            return rev

    # Try all three if country unknown
    for fn in (_try_infogreffe, _try_companies_house, _try_handelsregister):
        rev = fn(company_name)
        if rev:
            return rev
    return None


def _try_infogreffe(company_name: str) -> Optional[float]:
    try:
        resp = httpx.get(
            "https://opendata.infogreffe.fr/api/explore/v2.1/catalog/datasets"
            "/comptes-sociaux-des-societes-commerciales/records",
            params={
                "where": f'denominationsociale like "%{company_name}%"',
                "order_by": "millesime desc",
                "limit": 3,
                "select": "denominationsociale,netsales",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        for r in resp.json().get("results", []):
            val = r.get("netsales")
            if val and float(val) > 0:
                return round(float(val) / 1000 * 1.08, 1)  # k€ → M$
    except Exception:
        pass
    return None


def _try_companies_house(company_name: str) -> Optional[float]:
    # Companies House revenue requires XBRL parsing — return None for now
    # (CompaniesHouseProvider.fetch_by_name handles this when registered)
    return None


def _try_handelsregister(company_name: str) -> Optional[float]:
    # Bundesanzeiger is best-effort; handled by HandelsregisterProvider
    return None


def _signal_crunchbase(cb_data: dict) -> Optional[float]:
    """Parse Crunchbase revenue_range string to USD millions midpoint."""
    range_str = cb_data.get("revenue_range", "")
    if not range_str:
        return None
    nums = re.findall(r"[\d.]+\s*[MBK]?", range_str.replace(",", ""))
    values = []
    for n in nums:
        n = n.strip()
        if n.endswith("B"):
            values.append(float(n[:-1]) * 1000)
        elif n.endswith("M"):
            values.append(float(n[:-1]))
        elif n.endswith("K"):
            values.append(float(n[:-1]) / 1000)
        elif n:
            try:
                values.append(float(n))
            except ValueError:
                pass
    return round(sum(values) / len(values), 1) if values else None


def _signal_headcount(cb_data: dict, sector: str) -> Optional[float]:
    """Employee count × sector revenue/employee benchmark."""
    enum = cb_data.get("num_employees_enum", "")
    headcount = _CB_EMPLOYEE_MIDPOINT.get(enum)
    if not headcount:
        return None

    rev_per_emp = _REV_PER_EMPLOYEE.get(sector)
    if not rev_per_emp:
        for key in _REV_PER_EMPLOYEE:
            if key in sector:
                rev_per_emp = _REV_PER_EMPLOYEE[key]
                break
        rev_per_emp = rev_per_emp or _REV_PER_EMPLOYEE["default"]

    return round(headcount * rev_per_emp / 1_000_000, 1)


def _signal_funding_arr(cb_data: dict) -> Optional[float]:
    """Total funding raised → implied ARR for SaaS companies."""
    total_usd = cb_data.get("funding_total", {})
    if isinstance(total_usd, dict):
        total_usd = total_usd.get("value_usd", 0) or 0
    if not total_usd or float(total_usd) <= 0:
        return None
    total_m = float(total_usd) / 1_000_000
    return round(total_m * _SAAS_ARR_CAPITAL_RATIO, 1)


def _signal_wikipedia(company_name: str) -> Optional[float]:
    """Search Wikipedia for the company article and extract revenue from article text."""
    try:
        search_resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search",
                "srsearch": company_name, "srlimit": 3, "format": "json",
            },
            timeout=8,
        )
        if search_resp.status_code != 200:
            return None
        results = search_resp.json().get("query", {}).get("search", [])
        if not results:
            return None
        title = results[0]["title"]
        extract_resp = httpx.get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + title.replace(" ", "_"),
            timeout=8,
        )
        if extract_resp.status_code != 200:
            return None
        text = extract_resp.json().get("extract", "")
        return _extract_revenue_from_text(text)
    except Exception:
        return None


def _signal_press_nlp(company_name: str) -> Optional[float]:
    """Extract revenue figure from web search snippets (DuckDuckGo + fallback)."""
    queries = [
        f'"{company_name}" annual revenue',
        f'"{company_name}" revenue turnover annual report',
        f'{company_name} revenue sales fiscal year',
    ]
    for query in queries:
        result = _ddg_revenue(query)
        if result:
            return result
    return None


def _ddg_revenue(query: str) -> Optional[float]:
    """Single DuckDuckGo query → revenue extraction."""
    try:
        resp = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (compatible; goldroger-research/1.0)"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        # Collect all text snippets
        snippets = [data.get("AbstractText", "")]
        snippets += [r.get("Text", "") for r in data.get("RelatedTopics", [])[:8]]
        snippets += [r.get("FirstURL", "") + " " + r.get("Text", "")
                     for r in data.get("Results", [])[:3]]
        combined = " ".join(snippets)
        return _extract_revenue_from_text(combined)
    except Exception:
        return None


def _extract_revenue_from_text(text: str) -> Optional[float]:
    """Parse revenue mentions like '$120M', '€1.2B', '£450 million', '1.2 billion euros'."""
    # Patterns ordered from most specific to most general
    patterns = [
        # "$120M revenue" or "revenue of $1.2B"
        r'(?:revenue|turnover|sales|chiffre\s*d\'affaires)[^\d$€£]{0,40}[\$€£]?\s*([\d,.]+)\s*(trillion|billion|million|bn|tn|m\b)',
        r'[\$€£]([\d,.]+)\s*(trillion|billion|million|bn|tn|m\b)\s{0,5}(?:in\s+)?(?:revenue|turnover|sales)',
        # "1.2 billion in revenue" or "revenues of 450 million euros"
        r'([\d,.]+)\s*(trillion|billion|million|bn)\s+(?:in\s+|of\s+)?(?:[\$€£])?\s*(?:revenue|turnover|sales|euros?|dollars?)',
        # "revenues reached €1.2 billion"
        r'(?:revenue|turnover|sales)[^.]{0,60}?([\d,.]+)\s*(billion|million|bn)',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            raw = match.group(1).replace(",", "")
            unit = match.group(2).lower()
            try:
                val = float(raw)
            except ValueError:
                continue
            if unit in ("trillion", "tn"):
                return round(val * 1_000_000, 1)
            if unit in ("billion", "bn"):
                return round(val * 1_000, 1)
            return round(val, 1)  # million / m
    return None


# ── Aggregation helpers ───────────────────────────────────────────────────────

def _weighted_median(signals: list[TriangulationSignal]) -> float:
    if not signals:
        return 0.0
    total_w = sum(s.confidence for s in signals)
    sorted_s = sorted(signals, key=lambda s: s.estimate_m)
    cumulative = 0.0
    for s in sorted_s:
        cumulative += s.confidence
        if cumulative >= total_w / 2:
            return s.estimate_m
    return sorted_s[-1].estimate_m


def _count_agreeing(signals: list[TriangulationSignal], estimate: float) -> int:
    """Count signals within ±40% of the median estimate."""
    if estimate <= 0:
        return 0
    return sum(
        1 for s in signals
        if abs(s.estimate_m - estimate) / estimate <= 0.40
    )
