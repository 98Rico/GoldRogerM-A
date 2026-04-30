"""
French company registry provider — gouvernement.fr open data.

Primary source: recherche-entreprises.api.gouv.fr (official, free, no auth)
Returns: SIREN, legal name, NAF/sector, address, headcount category.
Revenue is NOT available from this source — falls through to web-search fallback.

Note: the Infogreffe `comptes-sociaux-des-societes-commerciales` dataset was removed
from opendata.infogreffe.fr in 2025. The domain also no longer resolves.
"""
from __future__ import annotations

import difflib
from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from goldroger.data.name_resolver import resolve, fuzzy_best_match
from .base import DataProvider

_SEARCH_URL = "https://recherche-entreprises.api.gouv.fr/search"

_NAF_SECTOR: dict[str, str] = {
    "62": "Technology", "63": "Technology",
    "64": "Financials", "65": "Financials", "66": "Financials",
    "46": "Wholesale",
    "47": "Retail",
    "10": "Consumer Staples", "11": "Consumer Staples",
    "14": "Consumer Discretionary", "15": "Consumer Discretionary",
    "45": "Consumer Discretionary",
    "55": "Consumer Discretionary",
    "56": "Consumer Discretionary",
    "72": "Healthcare", "86": "Healthcare", "87": "Healthcare",
    "41": "Real Estate", "68": "Real Estate",
    "25": "Industrials", "28": "Industrials", "49": "Industrials",
    "52": "Industrials", "70": "Industrials", "71": "Industrials",
    "74": "Industrials", "77": "Industrials", "78": "Industrials",
    "82": "Industrials",
    "35": "Energy",
    "01": "Agriculture",
    "85": "Education",
    "59": "Communication Services", "60": "Communication Services",
    "73": "Communication Services",
    "69": "Financials",
}


def _best_match(company_name: str, results: list[dict]) -> tuple[Optional[dict], float]:
    candidate_names = [r.get("nom_complet", "") for r in results]
    matched_name = fuzzy_best_match(company_name, candidate_names, threshold=0.55)
    if not matched_name:
        return None, 0.0
    score = difflib.SequenceMatcher(None, company_name.lower(), matched_name.lower()).ratio()
    record = next((r for r in results if r.get("nom_complet") == matched_name), results[0])
    return record, score


class InfogreffeProvider(DataProvider):
    """French company registry provider using the official gouvernement.fr open data API."""

    name = "infogreffe"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None  # name-based only

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        ids = resolve(company_name)
        queries = list(dict.fromkeys(filter(None, [
            ids.infogreffe_query, *ids.variants, company_name,
        ])))

        best_result: Optional[dict] = None
        best_score = 0.0

        for query in queries:
            try:
                resp = httpx.get(
                    _SEARCH_URL,
                    params={"q": query, "per_page": 5},
                    timeout=10,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    continue
                results = resp.json().get("results", [])
                if not results:
                    continue
                record, score = _best_match(company_name, results)
                if record and score > best_score:
                    best_score = score
                    best_result = record
            except Exception:
                continue

        if not best_result:
            return None

        naf = best_result.get("siege", {}).get("activite_principale", "")
        sector = _NAF_SECTOR.get(naf[:2], "") if naf else ""

        return MarketData(
            ticker=company_name.upper()[:6],
            company_name=best_result.get("nom_complet", company_name),
            sector=sector,
            revenue_ttm=None,
            confidence="inferred",
            data_source="infogreffe",
        )

    def fetch_by_siren(self, siren: str, company_name: str = "") -> Optional[MarketData]:
        """Direct SIREN lookup via recherche-entreprises.api.gouv.fr — sector only, no revenue."""
        try:
            resp = httpx.get(
                _SEARCH_URL,
                params={"q": siren, "per_page": 1},
                timeout=10,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return None
            results = resp.json().get("results", [])
            if not results:
                return None
            r = results[0]
            naf = r.get("siege", {}).get("activite_principale", "")
            sector = _NAF_SECTOR.get(naf[:2], "") if naf else ""
            return MarketData(
                ticker=(company_name or siren).upper()[:6],
                company_name=r.get("nom_complet", company_name or siren),
                sector=sector,
                revenue_ttm=None,
                confidence="inferred",
                data_source="infogreffe",
            )
        except Exception:
            return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None

    def capabilities(self) -> "ProviderCapabilities":
        from .base import ProviderCapabilities
        return ProviderCapabilities(
            name="infogreffe",
            display_name="Infogreffe (FR gov)",
            description="French company registry via recherche-entreprises.api.gouv.fr — sector only, no revenue",
            coverage=["FR"],
            company_types=["public", "private"],
            data_fields=["sector", "employees"],
            cost_tier="free",
            requires_key=False,
        )
