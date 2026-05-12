"""Minimal filings/annual-report sourcing pack for public-company context."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urljoin

import httpx

from goldroger.data.fetcher import MarketData
from goldroger.utils.cache import filings_meta_cache

_TIMEOUT = 10.0
_HTTP = httpx.Client(
    timeout=_TIMEOUT,
    headers={"User-Agent": "GoldRoger Filings/1.0"},
    follow_redirects=True,
)
_SEC_HEADERS = {"User-Agent": "GoldRoger Research goldroger@research.ai"}
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_TICKER_CIK_CACHE: dict[str, str] = {}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _build_sec_filing_url(cik: str, accession: str, primary_document: str) -> str:
    try:
        cik_int = str(int(str(cik)))
    except Exception:
        cik_int = str(cik).strip()
    acc_plain = str(accession or "").replace("-", "")
    doc = str(primary_document or "").strip()
    if not cik_int or not acc_plain or not doc:
        return ""
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_plain}/{doc}"


def _load_ticker_cik_map() -> None:
    if _TICKER_CIK_CACHE:
        return
    try:
        resp = _HTTP.get(_SEC_TICKERS_URL, headers=_SEC_HEADERS)
        if resp.status_code != 200:
            return
        payload = resp.json()
        if not isinstance(payload, dict):
            return
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            ticker = _safe_str(row.get("ticker")).upper()
            cik = _safe_str(row.get("cik_str")).zfill(10)
            if ticker and cik:
                _TICKER_CIK_CACHE[ticker] = cik
    except Exception:
        return


def _sec_cik_for_ticker(ticker: str) -> str:
    _load_ticker_cik_map()
    return _TICKER_CIK_CACHE.get(str(ticker or "").upper(), "")


@dataclass
class FilingRecord:
    filing_type: str
    fiscal_period: str = ""
    filing_date: str = ""
    accession_number: str = ""
    source_url: str = ""
    source_name: str = "unknown"
    parser_status: str = "ok"
    confidence: str = "verified"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "FilingRecord":
        return FilingRecord(
            filing_type=_safe_str(d.get("filing_type")) or "unknown",
            fiscal_period=_safe_str(d.get("fiscal_period")),
            filing_date=_safe_str(d.get("filing_date")),
            accession_number=_safe_str(d.get("accession_number")),
            source_url=_safe_str(d.get("source_url")),
            source_name=_safe_str(d.get("source_name")) or "unknown",
            parser_status=_safe_str(d.get("parser_status")) or "ok",
            confidence=_safe_str(d.get("confidence")) or "verified",
        )


@dataclass
class FilingsPack:
    company: str
    ticker: str
    source_backed: bool
    source_count: int
    records: list[FilingRecord] = field(default_factory=list)
    fallback_used: bool = False
    note: str = ""
    retrieved_at: str = field(default_factory=_utc_iso)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["records"] = [r.to_dict() for r in self.records]
        return out

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "FilingsPack":
        rows = d.get("records") if isinstance(d.get("records"), list) else []
        return FilingsPack(
            company=_safe_str(d.get("company")),
            ticker=_safe_str(d.get("ticker")).upper(),
            source_backed=bool(d.get("source_backed")),
            source_count=int(d.get("source_count") or 0),
            records=[FilingRecord.from_dict(x) for x in rows if isinstance(x, dict)],
            fallback_used=bool(d.get("fallback_used")),
            note=_safe_str(d.get("note")),
            retrieved_at=_safe_str(d.get("retrieved_at")) or _utc_iso(),
        )

    @property
    def latest(self) -> FilingRecord | None:
        return self.records[0] if self.records else None


def _fetch_sec_recent_filings(ticker: str) -> list[FilingRecord]:
    cik = _sec_cik_for_ticker(ticker)
    if not cik:
        return []
    try:
        resp = _HTTP.get(_SEC_SUBMISSIONS_URL.format(cik=cik), headers=_SEC_HEADERS)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        recent = (payload.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        accessions = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        out: list[FilingRecord] = []
        for i, form in enumerate(forms):
            form_s = _safe_str(form).upper()
            if form_s not in {"10-K", "10-Q", "8-K", "20-F", "6-K"}:
                continue
            filing_date = _safe_str(filing_dates[i] if i < len(filing_dates) else "")
            report_date = _safe_str(report_dates[i] if i < len(report_dates) else "")
            accession = _safe_str(accessions[i] if i < len(accessions) else "")
            primary_doc = _safe_str(docs[i] if i < len(docs) else "")
            out.append(
                FilingRecord(
                    filing_type=form_s,
                    fiscal_period=report_date,
                    filing_date=filing_date,
                    accession_number=accession,
                    source_url=_build_sec_filing_url(cik, accession, primary_doc),
                    source_name="sec_edgar_submissions",
                    parser_status="ok",
                    confidence="verified",
                )
            )
            if len(out) >= 5:
                break
        return out
    except Exception:
        return []


def _website_from_market_data(market_data: MarketData | None) -> str:
    if not market_data or not isinstance(market_data.additional_metadata, dict):
        return ""
    return _safe_str(
        market_data.additional_metadata.get("website")
        or market_data.additional_metadata.get("company_website")
    )


def _guess_ir_url(website: str) -> str:
    base = _safe_str(website)
    if not base:
        return ""
    if not (base.startswith("http://") or base.startswith("https://")):
        base = f"https://{base}"
    base = base.rstrip("/")
    candidates = [
        f"{base}/investors",
        f"{base}/investor-relations",
        f"{base}/investors/annual-report",
        base,
    ]
    for u in candidates:
        try:
            r = _HTTP.get(u)
            if r.status_code < 400:
                return str(r.url)
        except Exception:
            continue
    return base


def _extract_report_links(landing_url: str, html: str) -> list[str]:
    """
    Extract likely annual-report/filing URLs from an IR page.
    Keeps deterministic, small heuristic surface for prototype reliability.
    """
    if not landing_url or not html:
        return []
    hrefs = re.findall(r"""href=["']([^"']+)["']""", html, flags=re.IGNORECASE)
    out: list[str] = []
    seen: set[str] = set()
    keywords = (
        "annual-report",
        "annual_report",
        "annual report",
        "results",
        "financial-report",
        "financial report",
        "20-f",
        "10-k",
    )
    for href in hrefs:
        h = _safe_str(href)
        if not h:
            continue
        abs_url = urljoin(landing_url, h)
        lowered = abs_url.lower()
        if lowered in seen:
            continue
        if not lowered.startswith(("http://", "https://")):
            continue
        if any(k in lowered for k in keywords) or lowered.endswith(".pdf"):
            seen.add(lowered)
            out.append(abs_url)
        if len(out) >= 3:
            break
    return out


def _discover_annual_report_records(ir_url: str) -> list[FilingRecord]:
    if not ir_url:
        return []
    try:
        resp = _HTTP.get(ir_url)
        if resp.status_code >= 400:
            return []
        text = resp.text or ""
    except Exception:
        return []
    links = _extract_report_links(ir_url, text)
    out: list[FilingRecord] = []
    for u in links:
        out.append(
            FilingRecord(
                filing_type="ANNUAL_REPORT_IR",
                fiscal_period="",
                filing_date="",
                accession_number="",
                source_url=u,
                source_name="company_ir_annual_report",
                parser_status="ok",
                confidence="estimated",
            )
        )
    return out


def _fallback_website_record(market_data: MarketData | None) -> list[FilingRecord]:
    site = _website_from_market_data(market_data)
    if not site:
        return []
    ir_url = _guess_ir_url(site)
    if not ir_url:
        return []
    records = [
        FilingRecord(
            filing_type="IR_PROFILE",
            fiscal_period="",
            filing_date="",
            accession_number="",
            source_url=ir_url,
            source_name="company_ir_website",
            parser_status="ok",
            confidence="estimated",
        )
    ]
    records.extend(_discover_annual_report_records(ir_url))
    return records


def build_filings_pack(
    *,
    company: str,
    ticker: str,
    market_data: MarketData | None = None,
) -> FilingsPack:
    """
    Build a minimal filings/source pack.

    Priority:
      1) SEC submissions (US tickers where CIK exists)
      2) company IR website fallback
      3) explicit unavailable pack
    """
    tkr = _safe_str(ticker).upper()
    cache_key = f"filings_pack:{tkr or company.lower()}"
    cached = filings_meta_cache.get(cache_key)
    if isinstance(cached, dict):
        try:
            return FilingsPack.from_dict(cached)
        except Exception:
            pass

    records = _fetch_sec_recent_filings(tkr) if tkr else []
    note = ""
    fallback_used = False
    if not records:
        records = _fallback_website_record(market_data)
        fallback_used = bool(records)
        if fallback_used:
            _has_report = any((r.filing_type or "").upper() == "ANNUAL_REPORT_IR" for r in records)
            if _has_report:
                note = "SEC filings unavailable; using company IR profile + annual-report link fallback."
            else:
                note = "SEC filings unavailable; using company IR profile fallback."
        else:
            note = "No filings/IR sources resolved."

    source_urls = {r.source_url for r in records if _safe_str(r.source_url).startswith("http")}
    pack = FilingsPack(
        company=_safe_str(company) or tkr,
        ticker=tkr,
        source_backed=bool(source_urls),
        source_count=len(source_urls),
        records=records,
        fallback_used=fallback_used,
        note=note,
    )
    filings_meta_cache.set(cache_key, pack.to_dict())
    return pack
