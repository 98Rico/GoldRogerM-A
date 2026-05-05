"""
Companies House (UK) data provider — free REST API, no key required for basic search.

Provides: company verification, SIC codes (sector), incorporation date, status.
Revenue from filed accounts where available (iXBRL, best-effort).

Register at https://developer.company-information.service.gov.uk for an API key
to avoid anonymous rate limits (600 req/5min vs 50 req/5min unauthenticated).

Set COMPANIES_HOUSE_API_KEY in .env to activate authenticated access.
"""
from __future__ import annotations

import os
import re
from io import BytesIO
from typing import Optional

import httpx
from pypdf import PdfReader

from goldroger.data.fetcher import MarketData
from .base import DataProvider

_BASE = "https://api.company-information.service.gov.uk"

# SIC code → sector name (partial, most common M&A sectors)
_SIC_SECTOR = {
    "62": "Technology",  "63": "Technology",
    "64": "Financial Services", "65": "Financial Services", "66": "Financial Services",
    "47": "Retail", "46": "Wholesale",
    "10": "Consumer Staples", "11": "Consumer Staples",
    "56": "Consumer Discretionary",
    "72": "Healthcare",
    "41": "Real Estate", "68": "Real Estate",
    "49": "Industrials", "52": "Industrials",
    "35": "Energy",
    "85": "Education",
    "86": "Healthcare", "87": "Healthcare",
}

_SIC_DESCRIPTION = {
    "62012": "Business and domestic software development",
    "63120": "Web portals",
}

_MAX_FILING_ITEMS = 200


class CompaniesHouseProvider(DataProvider):
    name = "companies_house"
    requires_credentials = False

    def is_available(self) -> bool:
        # Anonymous access was removed — API key required as of 2024.
        # Register free at developer.company-information.service.gov.uk
        return bool(os.getenv("COMPANIES_HOUSE_API_KEY", ""))

    def _auth(self):
        api_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
        return (api_key, "") if api_key else None

    def _get(self, path: str, params: dict | None = None) -> Optional[dict]:
        try:
            resp = httpx.get(
                f"{_BASE}{path}",
                params=params,
                auth=self._auth(),
                timeout=10,
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def _search(self, company_name: str) -> Optional[str]:
        data = self._get("/search/companies", {"q": company_name, "items_per_page": 5})
        if not data:
            return None
        items = data.get("items", [])
        if not items:
            return None
        from goldroger.data.name_resolver import fuzzy_best_match
        candidate_names = [item.get("title", "") for item in items]
        matched = fuzzy_best_match(company_name, candidate_names, threshold=0.6)
        best = next(
            (item for item in items if item.get("title") == matched),
            items[0],
        )
        return best.get("company_number")

    def _fetch_all_filings(self, company_number: str) -> list[dict]:
        """Fetch filing history pages (bounded) for richer metadata extraction."""
        items: list[dict] = []
        start = 0
        page_size = 100
        while start < _MAX_FILING_ITEMS:
            page = self._get(
                f"/company/{company_number}/filing-history",
                {"items_per_page": page_size, "start_index": start},
            )
            if not page:
                break
            batch = page.get("items", []) or []
            if not batch:
                break
            items.extend(batch)
            total = int(page.get("total_count") or 0)
            start += len(batch)
            if (total and start >= total) or len(batch) < page_size:
                break
        return items[:_MAX_FILING_ITEMS]

    def _summarise_filing_documents(self, filing_items: list[dict]) -> dict:
        """
        Summarise filings and extract lightweight signals from document metadata.
        We inspect all fetched filing items and read document metadata for each item.
        """
        doc_count = 0
        has_accounts = False
        has_confirmation_statement = False
        has_director_changes = False
        notes: list[str] = []
        recent_docs: list[dict] = []

        for it in filing_items:
            desc = (it.get("description") or "").replace("_", " ").strip()
            typ = (it.get("type") or "").strip()
            date = (it.get("date") or "").strip()
            dmeta = it.get("links", {}).get("document_metadata", "")
            if dmeta:
                doc_count += 1
            low = f"{typ} {desc}".lower()
            if "accounts" in low or typ in {"AA", "AA01"}:
                has_accounts = True
            if "confirmation statement" in low or typ == "CS01":
                has_confirmation_statement = True
            if "appointment" in low or "director" in low or typ in {"AP01", "TM01"}:
                has_director_changes = True
            if len(recent_docs) < 12:
                recent_docs.append({"date": date, "type": typ, "description": desc})

            if dmeta and len(notes) < 10:
                try:
                    r = httpx.get(dmeta, auth=self._auth(), timeout=10, headers={"Accept": "application/json"})
                    meta = r.json() if r.status_code == 200 else None
                except Exception:
                    meta = None
                if isinstance(meta, dict):
                    resources = meta.get("resources", {}) or {}
                    mime_list = sorted(resources.keys())
                    if mime_list:
                        notes.append(f"{date} {typ}: available formats {', '.join(mime_list[:3])}")

        return {
            "filing_count_total": len(filing_items),
            "document_count_total": doc_count,
            "has_accounts_filings": has_accounts,
            "has_confirmation_statement": has_confirmation_statement,
            "has_director_changes": has_director_changes,
            "filing_history_recent": recent_docs,
            "filing_notes": notes,
        }

    def _extract_statement_of_capital_from_incorporation(self, filing_items: list[dict]) -> dict:
        """
        Parse incorporation document PDF text to extract statement-of-capital fields.
        Best effort only; returns empty dict if unavailable.
        """
        inc = next((x for x in filing_items if (x.get("type") or "").upper() in {"NEWINC", "IN01"}), None)
        if not inc:
            return {}
        meta_url = inc.get("links", {}).get("document_metadata", "")
        if not meta_url:
            return {}
        try:
            mr = httpx.get(meta_url, auth=self._auth(), timeout=12, headers={"Accept": "application/json"})
            if mr.status_code != 200:
                return {}
            doc_url = (mr.json().get("links", {}) or {}).get("document", "")
            if not doc_url:
                return {}
            dr = httpx.get(doc_url, auth=self._auth(), timeout=20, follow_redirects=True)
            if dr.status_code != 200 or not dr.content:
                return {}
            reader = PdfReader(BytesIO(dr.content))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:30])
            if not text.strip():
                return {}
            out: dict = {}
            up = text.upper()
            if "ORDINARY" in up:
                out["share_class"] = "ORDINARY"
            m_num = re.search(r"NUMBER\s+ALLOTTED\s+([0-9][0-9,]*)", up)
            if m_num:
                out["shares_allotted"] = int(m_num.group(1).replace(",", ""))
            m_nom = re.search(r"AGGREGATE\s+NOMINAL\s+VALUE[:\s]*([A-Z]{3})?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", up)
            if m_nom:
                if m_nom.group(1):
                    out["share_capital_currency"] = m_nom.group(1)
                out["aggregate_nominal_value"] = float(m_nom.group(2).replace(",", ""))
            m_total_shares = re.search(r"TOTAL\s+NUMBER\s+OF\s+SHARES[:\s]*([0-9][0-9,]*)", up)
            if m_total_shares:
                out["total_shares"] = int(m_total_shares.group(1).replace(",", ""))
            m_unpaid = re.search(r"TOTAL\s+AGGREGATE\s+UNPAID[:\s]*([0-9][0-9,]*(?:\.[0-9]+)?)", up)
            if m_unpaid:
                out["aggregate_unpaid"] = float(m_unpaid.group(1).replace(",", ""))
            m_cur = re.search(r"CURRENCY[:\s]*([A-Z]{3})", up)
            if m_cur and "share_capital_currency" not in out:
                out["share_capital_currency"] = m_cur.group(1)
            rights_line = ""
            m_rights = re.search(r"PRESCRIBED\s+PARTICULARS(.{0,220})", up, re.DOTALL)
            if m_rights:
                rights_line = " ".join(m_rights.group(1).split())
            if rights_line:
                out["share_rights_summary"] = rights_line[:180]
            return out
        except Exception:
            return {}

    def fetch(self, ticker: str) -> Optional[MarketData]:
        return None  # Companies House uses company names, not tickers

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        from goldroger.data.name_resolver import resolve
        ids = resolve(company_name)
        # Try each variant to maximise match rate
        company_number = None
        for variant in ([ids.companies_house_query] + ids.variants):
            if variant:
                company_number = self._search(variant)
                if company_number:
                    break
        if not company_number:
            return None

        return self.fetch_by_company_number(company_number, fallback_name=company_name)

    def fetch_by_company_number(self, company_number: str, fallback_name: str = "") -> Optional[MarketData]:
        profile = self._get(f"/company/{company_number}")
        if not profile:
            return None

        status = profile.get("company_status", "")
        if status not in ("active", ""):
            return None

        # Derive sector from SIC codes
        sic_codes = profile.get("sic_codes", [])
        sector = ""
        for sic in sic_codes:
            prefix = sic[:2]
            if prefix in _SIC_SECTOR:
                sector = _SIC_SECTOR[prefix]
                break

        officers = self._get(
            f"/company/{company_number}/officers",
            {"items_per_page": 100},
        ) or {}
        officer_items = officers.get("items", []) or []
        director_count = len([
            x for x in officer_items
            if (x.get("officer_role") or "").lower() == "director"
            and not x.get("resigned_on")
        ])
        officer_count = len([
            x for x in officer_items
            if not x.get("resigned_on")
        ])

        filing_items = self._fetch_all_filings(company_number)
        filing_summary = self._summarise_filing_documents(filing_items)
        capital_summary = self._extract_statement_of_capital_from_incorporation(filing_items)
        recent_filings = filing_summary.get("filing_history_recent", []) or []

        # Try to get revenue from most recent filed accounts (best-effort)
        revenue = self._fetch_revenue(company_number, filing_items=filing_items)

        sic_details = []
        for c in sic_codes:
            sic_details.append({"code": c, "description": _SIC_DESCRIPTION.get(c, "")})

        meta = {
            "registry": "companies_house",
            "company_number": company_number,
            "company_status": profile.get("company_status", ""),
            "date_of_creation": profile.get("date_of_creation", ""),
            "registered_office_address": profile.get("registered_office_address", {}) or {},
            "sic_codes": sic_codes,
            "sic_details": sic_details,
            "officer_count_active": officer_count,
            "director_count_active": director_count,
            "filing_history_recent": recent_filings,
            "last_filing_date": recent_filings[0]["date"] if recent_filings else "",
            "filing_count_total": filing_summary.get("filing_count_total", 0),
            "document_count_total": filing_summary.get("document_count_total", 0),
            "has_accounts_filings": filing_summary.get("has_accounts_filings", False),
            "has_confirmation_statement": filing_summary.get("has_confirmation_statement", False),
            "has_director_changes": filing_summary.get("has_director_changes", False),
            "filing_notes": filing_summary.get("filing_notes", []),
            "statement_of_capital": capital_summary,
        }

        return MarketData(
            ticker=(profile.get("company_name", fallback_name) or fallback_name or company_number).upper()[:6],
            company_name=profile.get("company_name", fallback_name or company_number),
            sector=sector,
            revenue_ttm=revenue,
            confidence="verified" if revenue else "inferred",
            data_source="companies_house",
            additional_metadata=meta,
        )

    def _fetch_revenue(self, company_number: str, filing_items: Optional[list[dict]] = None) -> Optional[float]:
        items = filing_items or self._fetch_all_filings(company_number)
        # Prefer full/small accounts over abbreviated (abbreviated rarely have revenue)
        priority_types = {"AA", "ACCOUNTS TYPE FULL", "ACCOUNTS TYPE SMALL", "AA01"}
        sorted_filings = sorted(
            [x for x in items if "accounts" in (x.get("description") or "").lower() or x.get("type") in priority_types],
            key=lambda f: (0 if f.get("type", "") in priority_types else 1),
        )
        for filing in sorted_filings:
            doc_url = filing.get("links", {}).get("document_metadata", "")
            if doc_url:
                revenue = self._parse_xbrl_revenue(doc_url)
                if revenue:
                    return revenue
        return None

    def _parse_xbrl_revenue(self, metadata_url: str) -> Optional[float]:
        try:
            meta = httpx.get(
                metadata_url,
                auth=self._auth(),
                timeout=10,
                headers={"Accept": "application/json"},
            )
            if meta.status_code != 200:
                return None
            links = meta.json().get("links", {})
            doc_url = links.get("document", "")
            if not doc_url:
                return None
            doc = httpx.get(doc_url, auth=self._auth(), timeout=20)
            if doc.status_code != 200:
                return None
            text = doc.text
            import re

            # Strategy 1: iXBRL inline tags — multiple known revenue concepts
            # UK GAAP / FRS 102 / IFRS concepts in order of reliability
            xbrl_patterns = [
                # FRS 102 / UK GAAP
                r'(?:name|contextRef)="[^"]*(?:Turnover|Revenue|GrossProfit)[^"]*"[^>]*>\s*([£]?[\d,]+)',
                r'ix:nonFraction[^>]*name="[^"]*(?:Turnover|Revenue)[^"]*"[^>]*>\s*([\d,]+)',
                # Core UK taxonomy
                r'uk-core:(?:Turnover|Revenue)[^>]*>\s*([\d,]+)',
                r'bus:(?:Turnover|TotalRevenue)[^>]*>\s*([\d,]+)',
                # Inline XBRL data attributes
                r'data-xbrl-concept="[^"]*(?:turnover|revenue)[^"]*"[^>]*>\s*([£]?[\d,]+)',
            ]
            for pattern in xbrl_patterns:
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    raw = m.group(1).replace(",", "").replace("£", "").strip()
                    try:
                        val = float(raw)
                        if val > 1000:  # raw pence/units below 1000 not plausible revenue
                            gbp_usd = 1.27  # approximate GBP→USD
                            # Values in CH are in GBP (£) — determine scale by magnitude
                            if val < 1_000_000:
                                # Likely in thousands
                                return val * gbp_usd / 1_000
                            else:
                                # Likely in full GBP
                                return val * gbp_usd / 1_000_000
                    except ValueError:
                        continue

            # Strategy 2: plain-text fallback — "Turnover ... £X,XXX,XXX" or "Revenue £X"
            text_patterns = [
                r'(?:Turnover|Revenue|Sales)\D{0,30}£\s*([\d,]+)',
                r'£\s*([\d,]+)\s*(?:turnover|revenue|sales)',
            ]
            for pattern in text_patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    raw = float(m.group(1).replace(",", ""))
                    if raw > 100:
                        gbp_usd = 1.27
                        return raw * gbp_usd / (1 if raw > 1_000_000 else 1_000)
        except Exception:
            pass
        return None

    def resolve_ticker(self, company_name: str) -> Optional[str]:
        return None  # CH companies are private, no tickers

    def capabilities(self) -> "ProviderCapabilities":
        from .base import ProviderCapabilities
        return ProviderCapabilities(
            name="companies_house",
            display_name="Companies House",
            description="UK company registry — revenue from XBRL filings where available",
            coverage=["GB"],
            company_types=["public", "private"],
            data_fields=["revenue", "sector", "employees"],
            cost_tier="free",
            requires_key=True,
            key_env_var="COMPANIES_HOUSE_API_KEY",
            key_signup_url="https://developer.company-information.service.gov.uk/",
        )
