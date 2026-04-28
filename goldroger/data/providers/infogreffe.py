"""
Infogreffe / RNCS open data provider — French companies, free, no API key required.

Provides: declared turnover (chiffre d'affaires), net result, sector (NAF code),
headcount category, registered address.

Data source: opendata.infogreffe.fr — annual accounts filed with French commercial courts.
Coverage: ~2M French companies, data up to ~2 years lag.

No credentials required.
"""
from __future__ import annotations

from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from .base import DataProvider

_BASE = "https://opendata.infogreffe.fr/api/explore/v2.1/catalog/datasets"
_DATASET = "comptes-sociaux-des-societes-commerciales"

# NAF code prefix → sector
_NAF_SECTOR = {
    "62": "Technology", "63": "Technology",
    "64": "Financial Services", "65": "Financial Services", "66": "Financial Services",
    "47": "Retail", "46": "Wholesale",
    "10": "Consumer Staples", "11": "Consumer Staples",
    "56": "Consumer Discretionary",
    "72": "Healthcare", "86": "Healthcare", "87": "Healthcare",
    "41": "Real Estate", "68": "Real Estate",
    "49": "Industrials", "25": "Industrials", "28": "Industrials",
    "35": "Energy",
    "01": "Agriculture",
    "85": "Education",
}


class InfogreffeProvider(DataProvider):
    name = "infogreffe"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None  # Infogreffe uses company names, not tickers

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        from goldroger.data.name_resolver import resolve
        ids = resolve(company_name)
        # Try each variant — Infogreffe needs normalized uppercase, no accents
        queries_to_try = list(dict.fromkeys(
            [ids.infogreffe_query] + ids.variants
        ))

        results = []
        for query in queries_to_try:
            if not query:
                continue
            try:
                resp = httpx.get(
                    f"{_BASE}/{_DATASET}/records",
                    params={
                        "where": f'denominationsociale like "%{query}%"',
                        "order_by": "millesime desc",
                        "limit": 5,
                        "select": (
                            "denominationsociale,millesime,netsales,netincome,"
                            "codeconventionnaf,trancheeffectif,departement"
                        ),
                    },
                    timeout=15,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if results:
                        break
            except Exception:
                continue

        try:
            if not results:
                return None

            # Pick best match by fuzzy name similarity
            from goldroger.data.name_resolver import fuzzy_best_match
            candidate_names = [r.get("denominationsociale", "") for r in results]
            matched_name = fuzzy_best_match(company_name, candidate_names, threshold=0.6)
            best = next(
                (r for r in results if r.get("denominationsociale") == matched_name),
                results[0],
            )

            # Revenue: netsales is in thousands of euros — convert to USD millions
            netsales_k_eur = best.get("netsales")
            revenue_usd_m = None
            if netsales_k_eur and float(netsales_k_eur) > 0:
                revenue_usd_m = float(netsales_k_eur) / 1000 * 1.08  # k€ → M€ → M$

            naf = best.get("codeconventionnaf", "")
            sector = _NAF_SECTOR.get(naf[:2], "") if naf else ""

            return MarketData(
                ticker=company_name.upper()[:6],
                company_name=best.get("denominationsociale", company_name),
                sector=sector,
                revenue_ttm=revenue_usd_m,
                confidence="verified" if revenue_usd_m else "inferred",
                data_source="infogreffe",
            )
        except Exception:
            return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None
