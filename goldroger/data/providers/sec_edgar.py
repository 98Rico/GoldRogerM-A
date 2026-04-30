"""
SEC EDGAR data provider — free, no credentials required.

Covers US public companies only (10-K/10-Q filings).
Uses:
  - https://www.sec.gov/files/company_tickers.json  (ticker → CIK mapping)
  - https://data.sec.gov/api/xbrl/companyfacts/{CIK}.json  (financial facts)

Useful for:
  - Revenue, net income, EPS for US-listed companies
  - Supplements yfinance when data is stale or missing
  - Cross-checking reported numbers against SEC filings
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from .base import DataProvider, ProviderCapabilities

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_HEADERS = {"User-Agent": "GoldRoger Research goldroger@research.ai"}
_TIMEOUT = 15

_ticker_to_cik: dict[str, str] = {}


def _load_cik_map() -> None:
    global _ticker_to_cik
    if _ticker_to_cik:
        return
    try:
        resp = httpx.get(_TICKERS_URL, headers=_HEADERS, timeout=_TIMEOUT)
        data = resp.json()
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                _ticker_to_cik[ticker] = cik
    except Exception:
        pass


def _get_cik(ticker: str) -> Optional[str]:
    _load_cik_map()
    return _ticker_to_cik.get(ticker.upper())


def _search_cik_by_name(company_name: str) -> Optional[str]:
    """Search EDGAR full-text index for a company name → return zero-padded CIK."""
    from goldroger.data.name_resolver import fuzzy_best_match

    for query in [f'"{company_name}"', company_name]:
        try:
            resp = httpx.get(
                _SEARCH_URL,
                params={"q": query, "forms": "10-K", "dateRange": "custom", "startdt": "2019-01-01"},
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                continue
            hits = resp.json().get("hits", {}).get("hits", [])
            if not hits:
                continue
            candidates = [
                (h["_source"]["entity_name"], str(h["_source"]["entity_id"]).zfill(10))
                for h in hits[:10]
                if "_source" in h and "entity_name" in h["_source"] and "entity_id" in h["_source"]
            ]
            if not candidates:
                continue
            best = fuzzy_best_match(company_name, [c[0] for c in candidates], threshold=0.5)
            for name, cik in candidates:
                if name == best:
                    return cik
            return candidates[0][1]  # take top hit if no fuzzy match
        except Exception:
            continue
    return None


def _extract_revenue(facts: dict) -> Optional[float]:
    """Pull most recent annual revenues from XBRL facts (USD)."""
    try:
        revenues = (
            facts.get("us-gaap", {})
            .get("Revenues", {})
            .get("units", {})
            .get("USD", [])
        )
        if not revenues:
            revenues = (
                facts.get("us-gaap", {})
                .get("RevenueFromContractWithCustomerExcludingAssessedTax", {})
                .get("units", {})
                .get("USD", [])
            )
        annual = [r for r in revenues if r.get("form") == "10-K"]
        if not annual:
            return None
        annual.sort(key=lambda x: x.get("end", ""), reverse=True)
        val = annual[0].get("val")
        return float(val) / 1e6 if val else None  # convert to USD millions
    except Exception:
        return None


class SECEdgarProvider(DataProvider):
    name = "sec_edgar"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        cik = _get_cik(ticker)
        if not cik:
            return None
        try:
            resp = httpx.get(
                _FACTS_URL.format(cik=cik),
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            facts = resp.json()
            revenue = _extract_revenue(facts)
            if revenue is None:
                return None
            name = facts.get("entityName", ticker)
            md = MarketData(
                ticker=ticker,
                company_name=name,
                sector="",
                revenue_ttm=revenue,
                confidence="verified",
                data_source="sec_edgar",
            )
            return md
        except Exception:
            return None

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        """Look up a US company by name via EDGAR full-text search → CIK → revenue."""
        cik = _search_cik_by_name(company_name)
        if not cik:
            return None
        try:
            resp = httpx.get(
                _FACTS_URL.format(cik=cik),
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            facts = resp.json()
            revenue = _extract_revenue(facts)
            name = facts.get("entityName", company_name)
            return MarketData(
                ticker=company_name.upper()[:6],
                company_name=name,
                sector="",
                revenue_ttm=revenue,
                confidence="verified" if revenue else "inferred",
                data_source="sec_edgar",
            )
        except Exception:
            return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None  # EDGAR search is slow; yfinance handles this better

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="sec_edgar",
            display_name="SEC EDGAR",
            description="US company 10-K filings — revenue via XBRL; name search for private US filers",
            coverage=["US"],
            company_types=["public", "private"],
            data_fields=["revenue"],
            cost_tier="free",
            requires_key=False,
        )
