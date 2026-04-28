"""
Registro Mercantil — Spanish company registry.
Public search via BORME (Boletín Oficial del Registro Mercantil) API.
No API key required. Provides: company name, registered address, status.
Revenue not available in public API.
"""
from __future__ import annotations
from typing import Optional
import httpx
from goldroger.data.fetcher import MarketData
from .base import DataProvider

_BASE = "https://www.registradores.org/actualidad/servicios-registrales"
_BORME_API = "https://boe.es/diario_borme/xml.php"


class RegistroMercantilProvider(DataProvider):
    name = "registro_mercantil"
    requires_credentials = False

    def is_available(self) -> bool:
        return True

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        from goldroger.data.name_resolver import resolve
        ids = resolve(company_name)
        # Build queries: strip accents + uppercase, try all variants
        queries = list(dict.fromkeys(filter(None, [
            ids.legal_suffixes_stripped,
            company_name,
        ] + ids.variants)))

        for variant in queries:
            if not variant:
                continue
            try:
                resp = httpx.get(
                    "https://api.cif.es/api/v1/search",
                    params={"name": variant, "limit": 5},
                    timeout=10,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 200:
                    items = resp.json().get("data", [])
                    if items:
                        best = items[0]
                        return MarketData(
                            ticker=company_name.upper()[:6],
                            company_name=best.get("name", company_name),
                            sector=None,
                            revenue_ttm=None,
                            confidence="inferred",
                            data_source="registro_mercantil",
                        )
            except Exception:
                continue

            # Fallback: BORME XML search (very limited)
            try:
                resp2 = httpx.get(
                    _BORME_API,
                    params={"id": variant},
                    timeout=8,
                )
                if resp2.status_code == 200 and variant.upper() in resp2.text.upper():
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
