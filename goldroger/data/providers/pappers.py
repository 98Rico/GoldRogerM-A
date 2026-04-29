"""
Pappers.fr provider — French company financials.

Free tier: 100 calls/month with PAPPERS_API_KEY.
Returns: declared revenue, net income, headcount, NAF/sector, funding.

This is the primary source for verified French private company financials.
The Infogreffe open-data financials dataset was removed in 2025; Pappers
aggregates the same RNCS/INPI filing data with a clean API.

Get a free key at: https://www.pappers.fr/api
Set PAPPERS_API_KEY in .env to activate.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from goldroger.data.name_resolver import resolve, fuzzy_best_match
from .base import DataProvider

_BASE = "https://api.pappers.fr/v2"

_NAF_SECTOR: dict[str, str] = {
    "62": "Technology", "63": "Technology",
    "64": "Financials", "65": "Financials", "66": "Financials",
    "46": "Wholesale", "47": "Retail",
    "10": "Consumer Staples", "11": "Consumer Staples",
    "14": "Consumer Discretionary", "15": "Consumer Discretionary",
    "45": "Consumer Discretionary", "55": "Consumer Discretionary",
    "56": "Consumer Discretionary",
    "72": "Healthcare", "86": "Healthcare", "87": "Healthcare",
    "41": "Real Estate", "68": "Real Estate",
    "25": "Industrials", "28": "Industrials", "49": "Industrials",
    "52": "Industrials", "70": "Industrials",
    "35": "Energy",
    "59": "Communication Services", "60": "Communication Services",
    "73": "Communication Services",
}

# Pappers revenue growth code → approximate multiplier
_EFF_HEADCOUNT: dict[str, int] = {
    "00": 0, "01": 1, "02": 3, "03": 6, "11": 10, "12": 20,
    "21": 35, "22": 75, "31": 150, "32": 350, "41": 750,
    "42": 1500, "51": 3500, "52": 7500, "53": 15000,
}


class PappersProvider(DataProvider):
    """French company financials via Pappers API (100 free calls/month)."""

    name = "pappers"
    requires_credentials = True

    def is_available(self) -> bool:
        return bool(os.getenv("PAPPERS_API_KEY", ""))

    def _token(self) -> str:
        return os.getenv("PAPPERS_API_KEY", "")

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None  # name-based only

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        ids = resolve(company_name)
        queries = list(dict.fromkeys(filter(None, [
            ids.infogreffe_query, *ids.variants, company_name,
        ])))

        best_siren: Optional[str] = None
        best_name: Optional[str] = None
        best_score = 0.0

        for query in queries:
            try:
                resp = httpx.get(
                    f"{_BASE}/recherche",
                    params={"q": query, "per_page": 5, "api_token": self._token()},
                    timeout=12,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    continue
                results = resp.json().get("resultats", [])
                if not results:
                    continue

                candidate_names = [r.get("nom_entreprise", "") for r in results]
                matched = fuzzy_best_match(company_name, candidate_names, threshold=0.55)
                if not matched:
                    continue

                import difflib
                score = difflib.SequenceMatcher(None, company_name.lower(), matched.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best = next(r for r in results if r.get("nom_entreprise") == matched)
                    best_siren = best.get("siren")
                    best_name = matched
            except Exception:
                continue

        if not best_siren:
            return None

        return self._fetch_details(best_siren, best_name or company_name)

    def _fetch_details(self, siren: str, company_name: str) -> Optional[MarketData]:
        """Fetch full financials by SIREN."""
        try:
            resp = httpx.get(
                f"{_BASE}/entreprise",
                params={"siren": siren, "api_token": self._token()},
                timeout=12,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception:
            return None

        # --- Revenue ---
        # Pappers returns finances as a list sorted most-recent-first
        finances = data.get("finances", [])
        revenue_eur: Optional[float] = None
        net_income_eur: Optional[float] = None
        ebitda_margin: Optional[float] = None

        if finances:
            latest = finances[0]
            ca = latest.get("chiffre_affaires")       # € (full value, not thousands)
            ni = latest.get("resultat_net")
            ebitda_raw = latest.get("excedent_brut_exploitation")  # EBE ≈ EBITDA
            if ca and float(ca) > 0:
                revenue_eur = float(ca)
            if ni:
                net_income_eur = float(ni)
            if ebitda_raw and revenue_eur and revenue_eur > 0:
                ebitda_margin = float(ebitda_raw) / revenue_eur

        # Revenue: € → M$ (approximate EUR/USD = 1.08)
        revenue_usd_m = revenue_eur / 1_000_000 * 1.08 if revenue_eur else None
        net_income_usd_m = net_income_eur / 1_000_000 * 1.08 if net_income_eur else None

        # --- Sector ---
        naf = data.get("code_naf", "")
        sector = _NAF_SECTOR.get(naf[:2], "") if naf else ""

        # --- Headcount ---
        eff_code = data.get("tranche_effectif", "")
        headcount = _EFF_HEADCOUNT.get(eff_code)

        return MarketData(
            ticker=company_name.upper()[:6],
            company_name=data.get("nom_entreprise", company_name),
            sector=sector,
            revenue_ttm=revenue_usd_m,
            net_income_ttm=net_income_usd_m,
            ebitda_margin=ebitda_margin,
            confidence="verified" if revenue_usd_m else "inferred",
            data_source="pappers",
        )

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None
