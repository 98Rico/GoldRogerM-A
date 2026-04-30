"""
Registro Mercantil — Spanish company registry.

Primary: BORME (Boletín Oficial del Registro Mercantil) via boe.es — official, free.
Note: api.cif.es is DNS-dead as of 2025 and has been removed.

BORME XML provides company existence confirmation but not financial data.
Revenue is not available — falls through to web-search fallback.
"""
from __future__ import annotations

from typing import Optional

import httpx

from goldroger.data.fetcher import MarketData
from goldroger.data.name_resolver import resolve
from .base import DataProvider

_BORME_SEARCH = "https://boe.es/buscar/borme.php"


class RegistroMercantilProvider(DataProvider):
    name = "registro_mercantil"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        ids = resolve(company_name)
        queries = list(dict.fromkeys(filter(None, [
            ids.legal_suffixes_stripped, company_name, *ids.variants,
        ])))

        for variant in queries:
            if not variant:
                continue
            try:
                # BORME search via boe.es full-text search
                resp = httpx.get(
                    _BORME_SEARCH,
                    params={"campo[0]": "DENOMINACION", "dato[0]": variant, "pag": 1},
                    timeout=10,
                    follow_redirects=True,
                    headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
                )
                if resp.status_code == 200 and variant.upper()[:6] in resp.text.upper():
                    return MarketData(
                        ticker=company_name.upper()[:6],
                        company_name=company_name,
                        sector=None,
                        revenue_ttm=None,
                        confidence="inferred",
                        data_source="registro_mercantil",
                    )
            except Exception:
                continue

        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None

    def capabilities(self) -> "ProviderCapabilities":
        from .base import ProviderCapabilities
        return ProviderCapabilities(
            name="registro_mercantil",
            display_name="Registro Mercantil (ES)",
            description="Spanish commercial registry via BORME — company existence only, no financials",
            coverage=["ES"],
            company_types=["public", "private"],
            data_fields=["sector"],
            cost_tier="free",
            requires_key=False,
        )
