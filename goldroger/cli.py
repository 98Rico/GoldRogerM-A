#!/usr/bin/env python3
"""
Gold Roger CLI — run a full equity analysis from the command line.

Usage:
    uv run python -m goldroger.cli --company "Longchamp" --type private
    uv run python -m goldroger.cli --company "LVMH" --type public
    uv run python -m goldroger.cli --company "NVIDIA"
"""
import argparse
import html
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .data.fetcher import resolve_ticker
from .data.registry import DEFAULT_REGISTRY
from .data.source_selector import provider_table
from .exporters import generate_excel, generate_pptx
from .orchestrator import run_analysis, run_ma_analysis, run_pipeline
from .utils.money import (
    format_money_millions as _fmt_money_human,
    format_price as _fmt_price_human,
    normalize_currency_code as _normalize_ccy_code,
    parse_monetary_to_millions as _parse_money_millions,
)

console = Console()
load_dotenv()


def _prompt_country_hint(current: str = "") -> str:
    allowed = {"FR", "GB", "DE", "NL", "ES", "US", ""}
    if current and current.upper() in allowed:
        return current.upper()
    console.print("\n[bold]Country hint[/bold] (helps choose the right registry/provider)")
    console.print("  Options: FR, GB, DE, NL, ES, US, or leave blank for unknown")
    raw = console.input("Enter country hint: ").strip().upper()
    return raw if raw in allowed else ""


def _fetch_company_suggestions(query: str, company_type: str, country_hint: str = "") -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        with httpx.Client(timeout=12, follow_redirects=True) as client:
            resp = client.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "quotesCount": 7, "newsCount": 0},
            )
            quotes = resp.json().get("quotes", [])
            out: list[dict] = []
            for item in quotes:
                symbol = item.get("symbol") or ""
                name = item.get("longname") or item.get("shortname") or item.get("name") or q
                qtype = item.get("quoteType") or ""
                exchange = item.get("exchDisp") or item.get("exchange") or ""
                region = item.get("region") or ""
                if company_type == "public" and qtype not in ("EQUITY", "ETF"):
                    continue
                out.append(
                    {
                        "display_name": name,
                        "symbol": symbol,
                        "quote_type": qtype,
                        "exchange": exchange,
                        "region": region,
                        "source": "yahoo_search",
                        "country_hint": country_hint or "",
                    }
                )
            if out:
                return out[:7]
    except Exception:
        pass
    # Public fallback: use internal ticker resolver and direct ticker heuristic.
    if company_type == "public":
        rows: list[dict] = []
        try:
            t = resolve_ticker(q)
            if t:
                rows.append(
                    {
                        "display_name": q.upper(),
                        "symbol": t.upper(),
                        "quote_type": "EQUITY",
                        "exchange": "",
                        "region": "",
                        "source": "resolve_ticker_fallback",
                        "country_hint": country_hint or "",
                        "identifier": "",
                    }
                )
        except Exception:
            pass
        # If input already looks like a ticker, offer it directly.
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,9}", q):
            sym = q.upper()
            if not any((r.get("symbol") or "").upper() == sym for r in rows):
                rows.append(
                    {
                        "display_name": sym,
                        "symbol": sym,
                        "quote_type": "EQUITY",
                        "exchange": "",
                        "region": "",
                        "source": "ticker_input_fallback",
                        "country_hint": country_hint or "",
                        "identifier": "",
                    }
                )
        if rows:
            return rows

    out = [{
        "display_name": q,
        "symbol": "",
        "quote_type": "UNKNOWN",
        "exchange": "",
        "region": "",
        "source": "name_input",
        "country_hint": country_hint or "",
        "identifier": "",
    }]
    # Country-specific private registry candidates.
    if company_type == "private" and (country_hint or "").upper() == "GB":
        def _public_ch_search_rows(name_query: str) -> list[dict]:
            try:
                with httpx.Client(timeout=12, follow_redirects=True) as client:
                    page = client.get(
                        "https://find-and-update.company-information.service.gov.uk/search/companies",
                        params={"q": name_query},
                    )
                    if page.status_code != 200:
                        return []
                    # Extract links like /company/16655420 and their visible titles.
                    rows: list[dict] = []
                    for m in re.finditer(
                        r'href="/company/([A-Z0-9]+)".{0,300}?>([^<]+)</a>',
                        page.text,
                        re.IGNORECASE | re.DOTALL,
                    ):
                        company_number = (m.group(1) or "").strip()
                        title = html.unescape((m.group(2) or "").strip())
                        if not company_number or not title:
                            continue
                        rows.append({
                            "display_name": title,
                            "symbol": "",
                            "quote_type": "PRIVATE",
                            "exchange": "",
                            "region": "GB",
                            "source": "companies_house_public_search",
                            "country_hint": "GB",
                            "identifier": company_number,
                        })
                        if len(rows) >= 5:
                            break
                    return rows
            except Exception:
                return []

        ch_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
        if ch_key:
            try:
                with httpx.Client(timeout=12, follow_redirects=True, auth=(ch_key, "")) as client:
                    resp = client.get(
                        "https://api.company-information.service.gov.uk/search/companies",
                        params={"q": q, "items_per_page": 5},
                        headers={"Accept": "application/json"},
                    )
                    if resp.status_code == 200:
                        items = resp.json().get("items", []) or []
                        ch_rows = []
                        for it in items:
                            ch_rows.append({
                                "display_name": it.get("title") or q,
                                "symbol": "",
                                "quote_type": "PRIVATE",
                                "exchange": "",
                                "region": "GB",
                                "source": "companies_house_search",
                                "country_hint": "GB",
                                "identifier": it.get("company_number") or "",
                            })
                        if ch_rows:
                            return ch_rows + out
                    elif resp.status_code == 401:
                        # Key is present but rejected; fallback to public website search.
                        pub_rows = _public_ch_search_rows(q)
                        if pub_rows:
                            pub_rows.insert(0, {
                                "display_name": "Companies House API key rejected (401); showing public-site matches",
                                "symbol": "",
                                "quote_type": "INFO",
                                "exchange": "",
                                "region": "GB",
                                "source": "companies_house",
                                "country_hint": "GB",
                                "identifier": "",
                            })
                            return pub_rows + out
            except Exception:
                pass
            pub_rows = _public_ch_search_rows(q)
            if pub_rows:
                return pub_rows + out
        else:
            pub_rows = _public_ch_search_rows(q)
            if pub_rows:
                pub_rows.insert(0, {
                    "display_name": "Companies House API key missing; showing public-site matches",
                    "symbol": "",
                    "quote_type": "INFO",
                    "exchange": "",
                    "region": "GB",
                    "source": "companies_house",
                    "country_hint": "GB",
                    "identifier": "",
                })
                return pub_rows + out
            out.insert(0, {
                "display_name": "Companies House candidates unavailable (missing COMPANIES_HOUSE_API_KEY)",
                "symbol": "",
                "quote_type": "INFO",
                "exchange": "",
                "region": "GB",
                "source": "companies_house",
                "country_hint": "GB",
                "identifier": "",
            })
    if company_type == "private":
        try:
            md = DEFAULT_REGISTRY.fetch_by_name(q, country_hint=country_hint or "")
            if md:
                out.insert(0, {
                    "display_name": md.company_name or q,
                    "symbol": "",
                    "quote_type": "PRIVATE",
                    "exchange": "",
                    "region": country_hint or "",
                    "source": md.data_source or "registry",
                    "country_hint": country_hint or "",
                    "identifier": "",
                })
        except Exception:
            pass
    return out


def _confirm_company_or_abort(company: str, company_type: str, country_hint: str = "") -> tuple[str, str, str]:
    ch = country_hint or ""
    if company_type == "private":
        ch = _prompt_country_hint(country_hint)
    suggestions = _fetch_company_suggestions(company, company_type, ch)
    console.print()
    console.rule("[bold cyan]Confirm Company[/]")
    console.print("Select the correct company before analysis:\n")
    t = Table(show_header=True, header_style="bold magenta")
    t.add_column("#", width=3)
    t.add_column("Name")
    t.add_column("Symbol")
    t.add_column("Type")
    t.add_column("Identifier")
    t.add_column("Country/Region")
    t.add_column("Source")
    for idx, s in enumerate(suggestions, start=1):
        t.add_row(
            str(idx),
            s.get("display_name") or company,
            s.get("symbol") or "—",
            s.get("quote_type") or "—",
            s.get("identifier") or "—",
            s.get("region") or s.get("country_hint") or "unknown",
            s.get("source") or "—",
        )
    t.add_row(str(len(suggestions) + 1), "None of these companies", "—", "—", "—", "—", "manual")
    console.print(t)
    if company_type == "private" and not ch:
        console.print("[yellow]Country is unknown. Confirmation quality is lower until country is specified.[/yellow]")

    while True:
        choice = console.input("\nEnter number to confirm: ").strip()
        if not choice.isdigit():
            console.print("[yellow]Please enter a valid number.[/]")
            continue
        n = int(choice)
        if n == len(suggestions) + 1:
            raise ValueError("No company confirmed. Please refine the name and run again.")
        if 1 <= n <= len(suggestions):
            selected = suggestions[n - 1]
            _sym = selected.get("symbol", "").strip()
            if company_type == "public" and _sym:
                console.print(f"[green]Confirmed:[/] {selected['display_name']} ({_sym})")
                return _sym, ch, ""
            console.print(
                f"[green]Confirmed:[/] {selected['display_name']} "
                f"[dim](country: {ch or 'unknown'})[/dim]"
            )
            return selected["display_name"], ch, (selected.get("identifier") or "")
        console.print("[yellow]Selection out of range. Try again.[/]")


def _parse_sources_md(sources_md: Optional[str]) -> dict[str, dict[str, str]]:
    """
    Parse SourcesLog markdown table into:
      metric -> {"value": str, "source": str, "confidence": str, "url": str}
    """
    if not sources_md:
        return {}
    out: dict[str, dict[str, str]] = {}
    for line in sources_md.splitlines():
        if not line.startswith("|"):
            continue
        if line.startswith("| Metric |") or line.startswith("|--------|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        # Expected split shape with leading/trailing pipes:
        # ["", metric, value, source, confidence, ""]
        if len(parts) < 6:
            continue
        metric = parts[1]
        if not metric:
            continue
        value = parts[2]
        source_raw = parts[3]
        conf_raw = parts[4]
        url = ""
        m = re.search(r"\(\[link\]\(([^)]+)\)\)", source_raw)
        if m:
            url = m.group(1).strip()
            source_raw = re.sub(r"\s*\(\[link\]\([^)]+\)\)", "", source_raw).strip()
        confidence = re.sub(r"^[^a-zA-Z]+", "", conf_raw).strip() or "unknown"
        out[metric] = {
            "value": value,
            "source": source_raw or "unknown",
            "confidence": confidence,
            "url": url,
        }
    return out


def _metric_source_keys(metric: str) -> list[str]:
    aliases: dict[str, list[str]] = {
        "Revenue": ["Revenue", "Revenue TTM"],
        "Revenue Growth": ["Revenue Growth", "Forward Revenue Growth"],
        "Modeled Revenue Growth": ["Modeled Revenue Growth"],
        "Gross Margin": ["Gross Margin"],
        "EBITDA Margin": ["EBITDA Margin"],
        "Net Margin": ["Net Margin"],
        "Free Cash Flow": ["Free Cash Flow"],
        "TAM": ["TAM", "Market Size"],
        "Market Growth": ["Market Growth"],
        "Dividend Yield": ["Dividend Yield"],
        "FCF Yield": ["FCF Yield", "FCF Yield on Market Cap"],
        "FCF Yield on Market Cap": ["FCF Yield on Market Cap", "FCF Yield"],
        "Net Debt / EBITDA": ["Net Debt / EBITDA"],
        "Dividend Coverage": ["Dividend Coverage"],
        "Interest Coverage": ["Interest Coverage"],
        "Target": ["Implied Target Price", "Target Price", "Implied EV"],
        "Indicative midpoint": ["Implied Target Price", "Target Price", "Implied EV"],
        "Indicative value per share": ["Implied Target Price", "Target Price", "Implied EV"],
        "Fair Value Range": ["Fair Value Range"],
        "Upside": ["Upside", "Upside/Downside"],
        "WACC": ["WACC"],
        "Terminal Growth": ["Terminal Growth"],
        "Blended Valuation": ["Blended EV Calculation", "Enterprise Value (blended)"],
        "DCF-only Valuation": ["Blended EV Calculation", "Enterprise Value (blended)"],
    }
    return aliases.get(metric, [metric])


def _infer_source_note(metric: str, value: str, src_map: dict[str, dict[str, str]]) -> str:
    if metric == "Fair Value Range":
        entry = src_map.get("Fair Value Range")
        if entry:
            src = entry["source"]
            conf = entry["confidence"]
            if src == "scenario_blended":
                src = "valuation_bridge from blended valuation low/high"
                conf = "inferred"
            note = f"{metric}: {src} ({conf})"
            if entry.get("url"):
                note += f" — {entry['url']}"
            return note
    for key in _metric_source_keys(metric):
        entry = src_map.get(key)
        if entry:
            if metric in {"Indicative midpoint", "Indicative value per share"}:
                src = entry["source"]
                conf = entry["confidence"]
                if src == "valuation_bridge":
                    src = "valuation_bridge from blended valuation mid"
                if conf.lower() == "verified":
                    conf = "verified calculation, low-confidence input"
                else:
                    conf = f"{conf} calculation, low-confidence input"
                note = f"{metric}: {src} ({conf})"
                if entry.get("url"):
                    note += f" — {entry['url']}"
                return note
            note = f"{metric}: {entry['source']} ({entry['confidence']})"
            if entry.get("url"):
                note += f" — {entry['url']}"
            return note
    txt = (value or "").lower()
    if "[sector avg]" in txt or "[sector benchmark]" in txt:
        return f"{metric}: sector benchmarks (inferred)"
    if "[estimated]" in txt:
        return f"{metric}: model estimate (estimated)"
    if "[no verified source]" in txt:
        return f"{metric}: no verified primary source available"
    if value in {"N/A", "—", ""}:
        return f"{metric}: not available"
    return f"{metric}: analysis output (source not logged)"


class _Footnotes:
    def __init__(self) -> None:
        self._idx: dict[str, int] = {}
        self._items: list[str] = []

    def tag(self, note: str) -> str:
        if not note:
            return ""
        if note not in self._idx:
            self._idx[note] = len(self._items) + 1
            self._items.append(note)
        return f" (S{self._idx[note]})"

    def items(self) -> list[str]:
        return self._items


def _split_qualifier(raw: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)(\s*\[[^\]]+\])?$", raw.strip())
    if not m:
        return raw, ""
    return (m.group(1) or "").strip(), (m.group(2) or "")


def _to_float(s: str) -> Optional[float]:
    t = str(s or "").strip().replace(",", "")
    if not t:
        return None
    _parsed = _parse_money_millions(t)
    if _parsed is not None:
        return _parsed
    if t.startswith("$"):
        t = t[1:]
    elif re.match(r"^[A-Z]{3}\s+", t):
        t = re.sub(r"^[A-Z]{3}\s+", "", t)
    mult = 1.0
    if t.endswith("T"):
        mult = 1_000_000.0
        t = t[:-1]
    elif t.endswith("B"):
        mult = 1_000.0
        t = t[:-1]
    elif t.endswith("M"):
        mult = 1.0
        t = t[:-1]
    elif t.endswith("K"):
        mult = 0.001
        t = t[:-1]
    try:
        return float(t) * mult
    except Exception:
        return None


def _fmt_money_m(v_m: float, currency: str = "USD") -> str:
    return _fmt_money_human(v_m, currency)


def _fmt_percentish(raw: str, signed: bool = False) -> str:
    base, q = _split_qualifier(raw)
    if not base:
        return raw
    if base.endswith("%"):
        return f"{base}{q}"
    n = _to_float(base)
    if n is None:
        return raw
    if abs(n) > 1.5:
        n = n / 100.0
    spec = "+.1%" if signed else ".1%"
    return f"{format(n, spec)}{q}"


def _format_metric_value(metric: str, value: str) -> str:
    metric = metric.strip()
    raw = (value or "").strip()
    if not raw:
        return "N/A"
    if metric in {"Revenue", "Free Cash Flow"}:
        base, q = _split_qualifier(raw)
        _ccy = None
        _ccy_rest = base
        _m_ccy = re.match(r"^([A-Z]{3})\s+(.+)$", base)
        if _m_ccy:
            _ccy = _m_ccy.group(1)
            _ccy_rest = _m_ccy.group(2).strip()
        if _ccy:
            _n_ccy = _to_float(_ccy_rest)
            if _n_ccy is not None:
                return f"{_fmt_money_m(_n_ccy, currency=_ccy)}{q}"
        n = _to_float(base)
        if n is not None:
            return f"{_fmt_money_m(n)}{q}"
    if metric in {"Revenue Growth", "Market Growth"}:
        return _fmt_percentish(raw, signed=True)
    if metric in {"Gross Margin", "EBITDA Margin", "Net Margin"}:
        return _fmt_percentish(raw, signed=False)
    if metric in {"Dividend Yield", "FCF Yield", "FCF Yield on Market Cap"}:
        return _fmt_percentish(raw, signed=False)
    return raw


def _format_valuation_cell(value: Optional[str], currency: str = "USD") -> str:
    if not value:
        return "—"
    n = _to_float(value)
    if n is None:
        return value
    return _fmt_money_m(n, currency=currency)


def _fmt_timing_s(v) -> str:
    if v is None:
        return "N/A"
    try:
        if isinstance(v, str) and not v.strip():
            return "N/A"
        n = float(v)
        if not math.isfinite(n):
            return "N/A"
        return f"{n:.2f}s"
    except Exception:
        s = str(v).strip()
        if not s or s.lower() in {"none", "nones", "n/a", "nan"}:
            return "N/A"
        return s


def _short_description(text: str, max_chars: int = 420) -> str:
    s = str(text or "").strip()
    if len(s) <= max_chars:
        return s
    clipped = s[: max_chars - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped + "…"


def _extract_first_number(raw: str) -> Optional[float]:
    txt = str(raw or "").strip()
    if not txt:
        return None
    m = re.search(r"(-?\d[\d,]*\.?\d*)", txt)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def _extract_two_numbers(raw: str) -> Optional[tuple[float, float]]:
    txt = str(raw or "").strip()
    nums = re.findall(r"(-?\d[\d,]*\.?\d*)", txt)
    if len(nums) < 2:
        return None
    try:
        return float(nums[0].replace(",", "")), float(nums[1].replace(",", ""))
    except Exception:
        return None


def _run_currency(pipeline_status: dict, src_map: dict[str, dict[str, str]]) -> str:
    ccy = str((pipeline_status or {}).get("quote_currency") or "").upper().strip()
    if ccy:
        norm, _, _ = _normalize_ccy_code(ccy)
        if norm:
            return norm
    mcap_val = str((src_map.get("Market Cap") or {}).get("value") or "").strip()
    m = re.match(r"^([A-Z]{3})\s+", mcap_val)
    if m:
        norm, _, _ = _normalize_ccy_code(m.group(1))
        if norm:
            return norm
    return "USD"


def _normalize_sector_label(sector: str, industry: str | None = None) -> str:
    s = (sector or "").strip()
    i = (industry or "").strip()
    _sl = s.lower()
    if _sl in {"consumer staples", "consumer staples - tobacco", "consumer goods - tobacco"}:
        s = "Consumer Staples"
    if i.lower() in {"consumer goods - tobacco", "tobacco products", "tobacco"}:
        i = "Tobacco"
    if s.lower().endswith("/ tobacco") or s.lower().endswith("- tobacco"):
        s = "Consumer Staples"
        if not i:
            i = "Tobacco"
    if i and i.lower() not in {"none", "n/a", "unknown"}:
        return f"{s} / {i}" if s else i
    return s or "Unknown"


def _peer_table_headers(debug: bool = False) -> list[str]:
    if debug:
        return [
            "Ticker", "Name", "Bucket", "Role", "MCap", "EV/EBITDA",
            "Similarity", "Business Sim", "Scale Sim", "Weight", "Include Reason",
        ]
    return ["Ticker", "Role", "Bucket", "MCap", "EV/EBITDA", "Weight"]


def _normalize_research_status(raw: str) -> str:
    s = (raw or "").upper()
    if s in {"RESEARCH_SKIPPED_QUICK_MODE", "SKIPPED_QUICK_MODE"}:
        return "SKIPPED_QUICK_MODE"
    if s in {"RESEARCH_PARTIAL_FALLBACK", "PARTIAL_FALLBACK"}:
        return "PARTIAL_FALLBACK"
    if s in {"RESEARCH_PARTIAL_SOURCE_BACKED", "PARTIAL_SOURCE_BACKED"}:
        return "PARTIAL_SOURCE_BACKED"
    if s in {"RESEARCH_OK", "OK"}:
        return "OK"
    if s in {"RESEARCH_FAILED", "FAILED", "TIMEOUT"}:
        return "FAILED"
    if s in {"FAILED", "TIMEOUT"}:
        return "FAILED"
    return "PARTIAL_FALLBACK"


def _normalize_valuation_status(raw_status: str, confidence: str) -> str:
    s = (raw_status or "").upper()
    c = (confidence or "").lower()
    if s == "FAILED":
        return "FAILED"
    if s in {"DEGRADED", "DEGRADED_API_CAPACITY"} or c == "low":
        return "LOW_CONFIDENCE"
    return "OK"


def _render_pipeline_status_block(pipeline_status: dict) -> tuple[str, str]:
    _company_type = str(pipeline_status.get("company_type", "") or "").strip().lower()
    _is_private = _company_type == "private" or bool(pipeline_status.get("private_revenue_status"))
    market_data_state = "N/A (private providers)" if _is_private else "OK"
    research_state = _normalize_research_status(str(pipeline_status.get("research_enrichment", "OK")))
    peers_state = str(pipeline_status.get("peers", "N/A")).upper()
    if peers_state == "DEGRADED_API_CAPACITY":
        peers_state = "PEERS_FAILED"
    if peers_state in {"FAILED", "TIMEOUT", "DEGRADED"}:
        peers_state = "PEERS_DEGRADED"
    if peers_state == "ADJACENT_REFERENCE_SET":
        peers_state = "ADJACENT_COMPS_OK"
    valuation_state = _normalize_valuation_status(
        str(pipeline_status.get("valuation", "N/A")),
        str(pipeline_status.get("confidence", "")),
    )
    if _is_private:
        _p_val_mode = str(pipeline_status.get("private_valuation_mode", "") or "").strip().upper()
        if _p_val_mode in {"VALUATION_GRADE", "SCREEN_ONLY", "FAILED"}:
            valuation_state = _p_val_mode
    rec_state = str(pipeline_status.get("recommendation", "N/A"))
    _qual_backed_avail = pipeline_status.get("source_backed_market_context_available")
    _qual_backed_used = pipeline_status.get("source_backed_market_context_used_in_thesis")
    _quant_backed_avail = pipeline_status.get("source_backed_quant_market_inputs_available")
    _quant_backed_used = pipeline_status.get("source_backed_quant_market_inputs_used_in_valuation")
    _research_collection_sem = str(pipeline_status.get("research_collection_semantic", "") or "").strip().lower()
    _qual_context_sem = str(pipeline_status.get("qualitative_context_semantic", "") or "").strip().lower()
    _quant_context_sem = str(pipeline_status.get("quantitative_market_inputs_semantic", "") or "").strip().lower()
    _thesis_mode_sem = str(pipeline_status.get("thesis_mode_semantic", "") or "").strip().lower()
    if isinstance(_quant_backed_used, bool):
        _used_in_valuation = "yes" if _quant_backed_used else "no — qualitative context only"
    else:
        if _quant_context_sem == "unavailable" or isinstance(_quant_backed_avail, bool) and (not _quant_backed_avail):
            _used_in_valuation = "no — qualitative context only"
        else:
            _used_in_valuation = "no" if research_state in {"SKIPPED_QUICK_MODE", "PARTIAL_FALLBACK", "FAILED"} else "yes"
    if isinstance(_qual_backed_used, bool):
        if _qual_backed_used:
            _used_in_thesis = "yes"
        else:
            if _thesis_mode_sem == "deterministic archetype fallback":
                _used_in_thesis = "archetype-based deterministic fallback"
            elif _thesis_mode_sem == "timeout fallback":
                _used_in_thesis = "timeout fallback"
            elif _thesis_mode_sem == "generic fallback":
                _used_in_thesis = "generic fallback"
            else:
                _used_in_thesis = "fallback"
    else:
        if research_state in {"PARTIAL_FALLBACK", "FAILED", "SKIPPED_QUICK_MODE"}:
            _used_in_thesis = (
                "archetype-based deterministic fallback"
                if _thesis_mode_sem in {"", "deterministic archetype fallback"}
                else _thesis_mode_sem
            )
        else:
            _used_in_thesis = "yes"
    _research_source_raw = str(pipeline_status.get("research_source", "") or "").strip().lower()
    if _research_collection_sem in {"source-backed", "fallback", "mixed", "unavailable"}:
        _research_collection = _research_collection_sem
    elif research_state == "FAILED":
        _research_collection = "unavailable"
    elif research_state == "SKIPPED_QUICK_MODE":
        _research_collection = "unavailable"
    elif _research_source_raw == "source_backed":
        _research_collection = "source-backed"
    elif _research_source_raw == "fallback":
        _research_collection = "fallback"
    else:
        _research_collection = "mixed"
    if _qual_context_sem in {"source-backed", "fallback", "unavailable"}:
        _qual_context_state = _qual_context_sem
    else:
        _qual_context_state = "source-backed" if bool(_qual_backed_avail) else ("fallback" if research_state in {"PARTIAL_FALLBACK", "SKIPPED_QUICK_MODE"} else "unavailable")
    if _quant_context_sem in {"available", "unavailable"}:
        _quant_context_state = _quant_context_sem
    else:
        _quant_context_state = "available" if bool(_quant_backed_avail) else "unavailable"
    _thesis_stage = str(pipeline_status.get("thesis", "") or "").strip().upper()
    if _thesis_mode_sem in {"source-backed", "deterministic archetype fallback", "timeout fallback", "generic fallback"}:
        _thesis_mode = _thesis_mode_sem
    elif _thesis_stage == "TIMEOUT":
        _thesis_mode = "timeout fallback"
    elif _thesis_stage == "DEGRADED_API_CAPACITY":
        _thesis_mode = "generic fallback"
    elif _thesis_stage == "FAILED":
        _thesis_mode = "generic fallback"
    elif research_state in {"PARTIAL_FALLBACK", "SKIPPED_QUICK_MODE"}:
        _thesis_mode = "deterministic archetype fallback"
    elif _research_collection == "source-backed":
        _thesis_mode = "source-backed"
    else:
        _thesis_mode = "generic fallback"
    block = (
        "[bold]Pipeline status:[/bold]\n"
        f"  Market data: {market_data_state}\n"
        f"  Research: {research_state}\n"
        f"  Peers: {peers_state}\n"
        f"  Valuation: {valuation_state}\n"
        f"  Recommendation: {rec_state}"
    )
    _report_mode = str(pipeline_status.get("report_mode", "") or "").strip().upper()
    if _report_mode:
        block += f"\n  Report mode: {_report_mode}"
    _private_rev_status = str(pipeline_status.get("private_revenue_status", "") or "").strip()
    if _private_rev_status:
        _private_identity_status = str(pipeline_status.get("private_identity_status", "") or "").strip().upper() or (
            "RESOLVED" if bool(pipeline_status.get("private_identity_resolved")) else "UNRESOLVED"
        )
        _private_identity_source_state = str(
            pipeline_status.get("private_identity_source_state", "") or ""
        ).strip().lower()
        _private_rev_quality = str(pipeline_status.get("private_revenue_quality", "") or "").strip().upper()
        _private_fin_q = str(pipeline_status.get("private_financials_quality", "") or "").strip().upper()
        _private_peers = str(pipeline_status.get("private_peers_state", "") or "").strip().upper()
        _private_val_mode = str(pipeline_status.get("private_valuation_mode", "") or "").strip().upper()
        _private_state = str(pipeline_status.get("private_state", "") or "").strip().upper()
        _private_provider_state = str(pipeline_status.get("private_provider_state", "") or "").strip().upper()
        _private_manual = bool(pipeline_status.get("private_manual_revenue_used"))
        _private_used = pipeline_status.get("private_used_providers") or []
        _private_skipped = pipeline_status.get("private_skipped_providers") or []
        _private_tri = bool(pipeline_status.get("private_triangulation_used"))
        _private_id_ok = bool(pipeline_status.get("private_identity_resolved"))
        _private_screen_reasons = pipeline_status.get("private_screen_only_reasons") or []
        _private_screen_txt = ""
        if isinstance(_private_screen_reasons, list) and _private_screen_reasons:
            _private_screen_txt = ", ".join(str(x) for x in _private_screen_reasons if str(x).strip())
        block += (
            f"\n  Private revenue status: {_private_rev_status}"
            + (" | triangulation used" if _private_tri else "")
            + (" | identity resolved" if _private_id_ok else " | identity unresolved")
        )
        block += (
            f"\n  Identity: {_private_identity_status}"
            f"\n  Identity sources: {_private_identity_source_state or 'unavailable'}"
            f"\n  Revenue: {_private_rev_quality or _private_rev_status.upper()}"
            f"\n  Financials: {_private_fin_q or 'UNAVAILABLE'}"
            f"\n  Private peers: {_private_peers or 'FAILED'}"
            f"\n  Private providers: {_private_provider_state or 'FAILED'}"
            f"\n  Private state: {_private_state or 'SCREEN_ONLY'}"
            f"\n  Private valuation mode: {_private_val_mode or 'SCREEN_ONLY'}"
        )
        if isinstance(_private_used, list) and _private_used:
            block += "\n  Used providers: " + ", ".join(str(x) for x in _private_used if str(x).strip())
        if isinstance(_private_skipped, list) and _private_skipped:
            block += "\n  Skipped providers: " + ", ".join(str(x) for x in _private_skipped if str(x).strip())
        if _private_manual:
            block += "\n  Manual revenue override: yes (user-provided, unverified)"
        if _private_screen_txt:
            block += f"\n  Screen-only reasons: {_private_screen_txt}"
            _unlock: list[str] = []
            if "verified revenue unavailable" in _private_screen_txt.lower():
                _unlock.append("provide verified revenue source (for example Pappers/Companies House accounts) or pass --manual-revenue")
            if "legal identity unresolved" in _private_screen_txt.lower():
                _unlock.append("provide legal identifier (for example SIREN/company number) or pass --manual-identity-confirmed with manual revenue")
            if not _unlock:
                _unlock.append("improve private identity and revenue source quality")
            block += "\n  What would unlock valuation:"
            for _u in _unlock[:3]:
                block += f"\n    - {_u}"
    block += (
        f"\n  Research collection: {_research_collection}"
        f" | Qualitative context: {_qual_context_state}"
        f" | Quantitative market inputs: {_quant_context_state}"
    )
    if isinstance(_qual_backed_avail, bool) or isinstance(_quant_backed_avail, bool):
        block += (
            "\n  Qualitative source-backed context available: "
            + ("yes" if bool(_qual_backed_avail) else "no")
            + " | Quantitative market inputs available: "
            + ("yes" if bool(_quant_backed_avail) else "no")
        )
    _ctx_count = pipeline_status.get("market_context_source_count")
    _ctx_rel = pipeline_status.get("market_context_relevant_source_count")
    _ctx_fetch = pipeline_status.get("market_context_fetched_source_count")
    _ctx_fallback = bool(pipeline_status.get("market_context_fallback_used"))
    _ctx_date = str(pipeline_status.get("market_context_latest_source_date", "") or "").strip()
    if _ctx_count is not None or _ctx_rel is not None:
        if _ctx_rel is not None and _ctx_fetch is not None:
            try:
                _ctx_txt = f"{int(_ctx_rel)} relevant / {int(_ctx_fetch)} fetched"
            except Exception:
                _ctx_txt = f"{_ctx_rel} relevant / {_ctx_fetch} fetched"
        else:
            try:
                _ctx_txt = f"{int(_ctx_count)}"
            except Exception:
                _ctx_txt = str(_ctx_count)
        block += (
            f"\n  Market context sources: {_ctx_txt}"
            + (" (fallback-only context)" if _ctx_fallback else "")
            + (f" | latest source date: {_ctx_date}" if _ctx_date else "")
        )
    _filings_count = pipeline_status.get("filings_source_count")
    _filings_state = "unavailable"
    if _filings_count is not None:
        try:
            _f_cnt_int = int(_filings_count)
        except Exception:
            _f_cnt_int = 0
        _f_backed = bool(pipeline_status.get("filings_source_backed"))
        if _f_cnt_int > 0:
            _filings_state = "source-backed" if _f_backed else "fallback"
    if _filings_count is not None:
        _f_type = str(pipeline_status.get("filings_latest_type", "") or "").strip()
        _f_date = str(pipeline_status.get("filings_latest_date", "") or "").strip()
        _filing_desc = (
            f"{_f_type}" + (f" ({_f_date})" if _f_date else "")
            if _f_type and _f_type.lower() != "unavailable"
            else "unavailable"
        )
        block += (
            f"\n  Filing sources: {int(_filings_count) if str(_filings_count).isdigit() else _filings_count}"
            f" | Latest filing: {_filing_desc}"
            + (" | source-backed" if _filings_state == "source-backed" else " | fallback/unavailable")
        )
    _market_context_state = _qual_context_state
    if _ctx_fallback:
        _market_context_state = "fallback"
    elif _ctx_count is not None:
        try:
            if int(_ctx_count) <= 0:
                _market_context_state = "unavailable"
            elif int(_ctx_count) > 0 and not _ctx_fallback and _market_context_state in {"fallback", "unavailable"}:
                _market_context_state = "source-backed"
        except Exception:
            pass
    _valuation_inputs_state = (
        "market data + verified quantitative context"
        if str(_used_in_valuation).strip().lower() == "yes"
        else ("private provider financials only" if _is_private else "market data only")
    )
    block += (
        f"\n  Filings: {_filings_state}"
        f"\n  Market context: {_market_context_state}"
        f"\n  Quantitative market inputs: {_quant_context_state}"
        f"\n  Thesis mode: {_thesis_mode}"
        f"\n  Valuation inputs: {_valuation_inputs_state}"
    )
    block += (
        "\n  Research used in valuation: " + _used_in_valuation
        + " | Research used in thesis: " + _used_in_thesis
    )
    if research_state == "PARTIAL_FALLBACK":
        block += (
            "\n  Full research unavailable; report generated from verified market data, "
            "deterministic peer set, and archetype-based deterministic fallback thesis."
        )
    elif research_state == "SKIPPED_QUICK_MODE":
        block += (
            "\n  Quick mode: deep research skipped by design; output is indicative, not full research-backed."
        )
    _disp_level = str(pipeline_status.get("method_dispersion_level", "") or "").strip()
    _disp_ratio = pipeline_status.get("method_dispersion_ratio")
    if _disp_level:
        try:
            _disp_txt = f"{_disp_level} — valuation range high/low = {float(_disp_ratio):.2f}x"
        except Exception:
            _disp_txt = _disp_level
        block += f"\n  Method dispersion: {_disp_txt}"
        if str(pipeline_status.get("confidence", "")).lower() == "low" and str(_disp_level).lower() == "high":
            block += "\n  Use range over midpoint due to low confidence and high method dispersion."
    _pure_w = pipeline_status.get("pure_peer_weight")
    _adj_w = pipeline_status.get("adjacent_peer_weight")
    if _pure_w is not None and _adj_w is not None:
        try:
            block += (
                f"\n  Pure peer weight: {float(_pure_w):.1%} | "
                f"Adjacent peer weight: {float(_adj_w):.1%}"
            )
        except Exception:
            pass
    _norm_status = str(pipeline_status.get("normalization_status", "") or "").strip()
    if (not _is_private) and _norm_status:
        _q_ccy = str(pipeline_status.get("quote_currency", "") or "unknown")
        _f_ccy = str(pipeline_status.get("financial_statement_currency", "") or "unknown")
        _m_ccy = str(pipeline_status.get("market_cap_currency", "") or "unknown")
        _listing_type = str(pipeline_status.get("listing_type", "") or "unknown")
        _selected_listing = str(pipeline_status.get("selected_listing", "") or "unknown")
        _primary_listing = str(pipeline_status.get("primary_listing", "") or "unknown")
        _listing_exchange = str(pipeline_status.get("listing_exchange", "") or "unknown")
        _listing_country = str(pipeline_status.get("listing_country", "") or "unknown")
        _share_basis = str(pipeline_status.get("share_count_basis", "") or "unknown")
        _adr = bool(pipeline_status.get("adr_detected", False))
        _dr = bool(pipeline_status.get("depository_receipt_detected", False))
        _adr_ratio = pipeline_status.get("adr_ratio")
        _fx_source = str(pipeline_status.get("fx_source", "") or "n/a")
        _fx_conf = str(pipeline_status.get("fx_confidence", "") or "n/a")
        _fx_ts = str(pipeline_status.get("fx_timestamp", "") or "n/a")
        _norm_reason = str(pipeline_status.get("normalization_reason", "") or "").strip()
        _dr_state = "yes" if _dr else "no"
        if _share_basis in {
            "unknown_depositary_ratio",
            "foreign_us_listing_unverified_share_basis",
            "foreign_ordinary_unresolved",
        }:
            _dr_state = "unresolved / not confirmed"
        block += (
            f"\n  Data normalization: {_norm_status}"
            f"\n    Quote/market cap currency: {_q_ccy}/{_m_ccy}"
            f"\n    Financial statement currency: {_f_ccy}"
            f"\n    Listing type: {_listing_type}"
            f"\n    Selected/primary listing: {_selected_listing} / {_primary_listing}"
            f"\n    Listing exchange/country: {_listing_exchange} / {_listing_country}"
            f"\n    Share basis: {_share_basis}"
            f"\n    Depositary receipt status: {_dr_state}"
            + (f" (ratio: {_adr_ratio})" if (_adr or _dr) and _adr_ratio else "")
            + f"\n    FX source/confidence: {_fx_source} / {_fx_conf} ({_fx_ts})"
        )
        if _norm_reason:
            block += f"\n    Normalization note: {_norm_reason}"
    if pipeline_status.get("sanity_breaker_triggered"):
        block += "\n  Recommendation suppressed by sanity breaker: data check required."
    reason = str(pipeline_status.get("confidence_reason", "") or "").strip()
    return block, reason


def _confidence_improvement_actions(
    sector: str,
    confidence_reason: str,
    research_state: str,
    peers_state: str,
    company_type: str = "public",
) -> list[str]:
    sec = (sector or "").lower()
    rs = (research_state or "").upper()
    ps = (peers_state or "").upper()
    reasons = (confidence_reason or "").lower()
    ctype = (company_type or "").lower()
    tips: list[str] = []
    if ctype == "private":
        if rs in {"PARTIAL_FALLBACK", "FAILED", "SKIPPED_QUICK_MODE"}:
            tips.append("add source-backed private-company context (registry filings, verified revenue, and legal identifiers)")
        if "identity" in reasons:
            tips.append("resolve legal identity using a strong registry identifier (SIREN/company number) before valuation")
        if "revenue" in reasons:
            tips.append("add verified or high-confidence revenue input; triangulated/inferred revenue should remain indicative")
        if ps in {"PEERS_FAILED", "NO_PURE_COMPS", "ADJACENT_COMPS_LOW_DIVERSITY"}:
            tips.append("improve private-peer set quality with closer business-model comparables")
        if not tips:
            tips.append("collect verified private financial inputs and strengthen legal-identity provenance")
        return tips[:4]
    if rs in {"PARTIAL_FALLBACK", "FAILED"}:
        tips.append("add source-backed market context (trends/catalysts) instead of fallback-only research")
    if ps in {"ADJACENT_COMPS_LOW_DIVERSITY", "ADJACENT_COMPS", "ADJACENT_COMPS_OK", "NO_PURE_COMPS", "PEERS_DEGRADED"}:
        tips.append("improve peer purity/diversification (more core peers, less adjacent concentration)")
    if "dispersion" in reasons:
        tips.append("reduce method dispersion by stress-testing DCF and comps assumptions")
    if "dcf" in reasons:
        tips.append("recalibrate DCF (terminal assumptions, growth fade, and discount-rate sensitivity)")
    if "tobacco" in sec:
        tips.append("add tobacco cash-return inputs (FCF yield, dividend coverage, leverage trends)")
    if "technology" in sec or "consumer electronics" in sec:
        tips.append("add segment-level hardware/services assumptions and Apple-like peer context")
    if not tips:
        tips.append("collect higher-quality source-backed inputs for market context and peers")
    # Keep concise and deterministic.
    return tips[:4]


def print_result(result, debug: bool = False):
    f = result.fundamentals
    m = result.market
    fin = result.financials
    v = result.valuation
    t = result.thesis
    src_map = _parse_sources_md(getattr(result, "sources_md", None))
    footnotes = _Footnotes()

    def _value_with_source(metric: str, value: Optional[str]) -> str:
        shown_raw = value or "N/A"
        note = _infer_source_note(metric, shown_raw, src_map)
        shown = _format_metric_value(metric, shown_raw)
        if metric in {"TAM", "Market Growth"}:
            _conf = None
            for key in _metric_source_keys(metric):
                entry = src_map.get(key)
                if entry:
                    _conf = (entry.get("confidence") or "").strip().lower()
                    break
            if _conf in {"estimated", "inferred"} and "[estimated]" not in shown.lower():
                shown = f"{shown} [estimated]"
        return f"{shown}{footnotes.tag(note)}"

    def _source_value(metric: str) -> Optional[str]:
        for key in _metric_source_keys(metric):
            entry = src_map.get(key)
            if entry and entry.get("value"):
                return str(entry["value"])
        return None

    # Header
    _is_inconclusive = (v.recommendation or "").upper().startswith("INCONCLUSIVE")
    rec_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow", "INCONCLUSIVE": "magenta"}.get((v.recommendation or "").split(" ")[0], "white")
    _pipeline_status = (getattr(result, "data_quality", {}) or {}).get("pipeline_status", {})
    _extreme_signal_review = bool(_pipeline_status.get("extreme_signal_review"))
    _cap_reason = str(_pipeline_status.get("recommendation_cap_reason", "") or "").strip()
    _extreme_capped = bool((not _is_inconclusive) and _extreme_signal_review and _cap_reason)
    _run_ccy = _run_currency(_pipeline_status, src_map)
    console.print()
    _target_display = "N/A" if _is_inconclusive else (v.target_price or v.implied_value)
    _confidence = str(_pipeline_status.get("confidence", "")).lower()
    if (not _is_inconclusive) and _confidence == "low" and isinstance(_target_display, str):
        _pt = _extract_first_number(_target_display)
        if _pt is not None:
            _target_display = f"~{_fmt_price_human(_pt, _run_ccy, decimals=0)}"
    _fv_range = _source_value("Fair Value Range")
    if _fv_range and _confidence == "low":
        _rng = _extract_two_numbers(str(_fv_range))
        if _rng:
            _lo = round(_rng[0])
            _hi = round(_rng[1])
            _fv_range = f"{_fmt_price_human(_lo, _run_ccy, decimals=0)}–{_fmt_price_human(_hi, _run_ccy, decimals=0)}"
    _fv_width = _source_value("Fair Value Range Width")
    _ev_display = (
        f" | Implied EV: {v.implied_value}"
        if (v.target_price and not _is_inconclusive)
        else ""
    )
    _fv_label = _value_with_source("Fair Value Range", _fv_range)
    if _fv_width:
        _fv_label = f"{_fv_label} (wide; low confidence)"
    _pt_label = "Indicative Value" if ((not _is_inconclusive) and _confidence == "low") else "Point Estimate"
    _is_low_conf = ((not _is_inconclusive) and _confidence == "low")
    if _is_low_conf and _fv_range and v.target_price:
        _range_label = "Midpoint reference"
        try:
            _rng = _extract_two_numbers(str(_fv_range))
            _t_n = _extract_first_number(str(_target_display))
            if _rng and _t_n is not None:
                _lo_n, _hi_n = _rng
                _mid_n = (_lo_n + _hi_n) / 2.0
                if _mid_n > 0 and abs(_t_n - _mid_n) / _mid_n > 0.03:
                    _range_label = "Base case"
        except Exception:
            _range_label = "Base case"
        _target_line = (
            f"Indicative Range: {_fv_label} | "
            f"{_range_label}: {_value_with_source('Indicative midpoint', _target_display)} | "
            f"Model-implied upside/downside: {_value_with_source('Upside', v.upside_downside)}"
        )
    else:
        _target_line = (
            f"Fair Value Range: {_fv_label} | "
            f"{_pt_label}: {_value_with_source('Target', _target_display)}"
            if _fv_range and v.target_price and not _is_inconclusive
            else f"Target: {_value_with_source('Target', _target_display)}{_ev_display}"
        )
    if (not _is_inconclusive) and _pipeline_status.get("confidence"):
        _target_line += f" | Valuation reliability: {_pipeline_status.get('confidence')}"
    if _extreme_capped:
        if _is_low_conf and _fv_range and v.target_price:
            _target_line = (
                f"Diagnostic Model Range: {_fv_label} | "
                f"Diagnostic model value: {_value_with_source('Indicative midpoint', _target_display)} | "
                f"Model-implied upside/downside: {_value_with_source('Upside', v.upside_downside)}"
            )
        elif _fv_range and v.target_price:
            _target_line = (
                f"Diagnostic Model Range: {_fv_label} | "
                f"Diagnostic model value: {_value_with_source('Target', _target_display)}"
            )
        else:
            _target_line = f"Diagnostic model value: {_value_with_source('Target', _target_display)}{_ev_display}"
        if (not _is_inconclusive) and _pipeline_status.get("confidence"):
            _target_line += f" | Valuation reliability: {_pipeline_status.get('confidence')}"
        _target_line += " | diagnostic; final recommendation capped pending corroboration"
    _headline_tail = "" if _is_low_conf else f" | Upside: {_value_with_source('Upside', v.upside_downside)}"
    if _extreme_capped:
        _headline_tail = ""
    _industry = (src_map.get("Industry", {}) or {}).get("value") if src_map else None
    _sector_label = _normalize_sector_label(f.sector or "Unknown", _industry)
    _header_parts = [f"[bold]{f.company_name}[/]", _sector_label]
    _hq = str(f.headquarters or "").strip()
    if _hq and _hq.lower() not in {"none", "n/a", "unknown"}:
        _header_parts.append(_hq)
    _header_line = " | ".join(_header_parts)
    _short_desc = _short_description(str(f.description or ""))
    console.print(Panel(
        f"{_header_line}\n"
        f"{_short_desc}\n\n"
        f"Recommendation: [{rec_color}]{v.recommendation}[/] | "
        f"{_target_line}{_headline_tail}",
        title=f"[bold cyan]{result.company}[/]",
        border_style="cyan",
    ))
    _dq = (getattr(result, "data_quality", {}) or {})
    _sourcing = _dq.get("sourcing", {}) if isinstance(_dq, dict) else {}
    _ctx_pack = _sourcing.get("market_context_pack", {}) if isinstance(_sourcing, dict) else {}
    _filings_pack = _sourcing.get("filings_pack", {}) if isinstance(_sourcing, dict) else {}
    if _pipeline_status:
        _status_block, _status_reason = _render_pipeline_status_block(_pipeline_status)
        console.print(_status_block)
        if _status_reason:
            console.print(f"[dim]Why low confidence: {_status_reason}[/dim]")
        _research_state_for_tips = _normalize_research_status(str(_pipeline_status.get("research_enrichment", "OK")))
        _peers_state_for_tips = str(_pipeline_status.get("peers", "N/A"))
        _tips = _confidence_improvement_actions(
            sector=f.sector or "",
            confidence_reason=_status_reason,
            research_state=_research_state_for_tips,
            peers_state=_peers_state_for_tips,
            company_type=getattr(result, "company_type", "public"),
        )
        if _tips and str(_pipeline_status.get("confidence", "")).lower() == "low":
            console.print("[bold]What Would Improve Confidence:[/bold]")
            for _tip in _tips:
                console.print(f"  • {_tip}")
        _ms_plain = str(_pipeline_status.get("model_signal_detail", _pipeline_status.get("model_signal", "N/A")) or "N/A")
        _fr_plain = str(_pipeline_status.get("recommendation", "N/A") or "N/A")
        _conf_plain = str(_pipeline_status.get("confidence", "")).strip()
        _cap_reason = str(_pipeline_status.get("recommendation_cap_reason", "") or "").strip()
        _missing_corr = _pipeline_status.get("extreme_signal_missing_corroboration")
        _missing_corr_txt = ""
        if isinstance(_missing_corr, list) and _missing_corr:
            _missing_corr_txt = ", ".join(str(x) for x in _missing_corr[:4])
        if _ms_plain not in {"N/A", ""} and _fr_plain not in {"N/A", ""} and _ms_plain != _fr_plain:
            _reason_txt = "confidence guardrails are applied."
            if _conf_plain.lower() == "low":
                _reason_txt = "the raw model signal is capped due to low valuation confidence."
            if _cap_reason:
                _reason_txt = _cap_reason
            if _missing_corr_txt:
                _reason_txt += f" Missing corroboration: {_missing_corr_txt}."
            console.print(
                f"[dim]Raw valuation signal: {_ms_plain}. Final recommendation: {_fr_plain}. "
                f"Reason: {_reason_txt}[/dim]"
            )
        if debug:
            console.print(
                "[dim]Diagnostics:\n"
                f"  Model signal: {_pipeline_status.get('model_signal_detail', _pipeline_status.get('model_signal', 'N/A'))}\n"
                f"  DCF status: {_pipeline_status.get('dcf_status', 'N/A')}\n"
                f"  Effective peers: {_pipeline_status.get('effective_peer_count', 'N/A')}\n"
                f"  Thesis status: {_pipeline_status.get('thesis', 'N/A')}[/dim]"
            )
        _ms = str(_pipeline_status.get("model_signal_detail", _pipeline_status.get("model_signal", "N/A")))
        _fr = str(_pipeline_status.get("recommendation", "N/A"))
        if debug and _ms not in {"N/A", ""} and _fr not in {"N/A", ""} and _ms != _fr:
            console.print(f"[dim]Model signal vs final recommendation: {_ms} → {_fr} (guardrails/confidence applied).[/dim]")
    _core_q = _dq.get("core_data_quality_score")
    _r_q = _dq.get("research_enrichment_quality_score")
    _r_lbl = _dq.get("research_enrichment_quality_label")
    _peer_q = _pipeline_status.get("peer_quality_score") if _pipeline_status else None
    _fin_q = _pipeline_status.get("financial_data_quality_score") if _pipeline_status else None
    _norm_q = _pipeline_status.get("normalization_status") if _pipeline_status else None
    _is_private_run = str(getattr(result, "company_type", "")).lower() == "private"
    if (_core_q is not None) or (_r_q is not None):
        if _is_private_run and _pipeline_status:
            console.print(
                "[bold]Private Data Quality:[/bold]\n"
                f"  Identity: {_pipeline_status.get('private_identity_status', 'UNRESOLVED')}\n"
                f"  Revenue: {_pipeline_status.get('private_revenue_quality', 'UNAVAILABLE')}\n"
                f"  Financials: {_pipeline_status.get('private_financials_quality', 'UNAVAILABLE')}\n"
                f"  Peers: {_pipeline_status.get('private_peers_state', 'FAILED')}\n"
                f"  Valuation mode: {_pipeline_status.get('private_valuation_mode', 'SCREEN_ONLY')}\n"
                f"  Valuation confidence: {_pipeline_status.get('confidence', 'N/A')}"
            )
        else:
            console.print(
                "[bold]Quality Split:[/bold]\n"
                f"  Raw financial data availability: {(_fin_q if _fin_q is not None else _core_q if _core_q is not None else 'N/A')}/100\n"
                f"  Normalization quality: {_norm_q or 'N/A'}\n"
                f"  Peer quality: {(_peer_q if _peer_q is not None else 'N/A')}/100\n"
                f"  Research enrichment quality: {(_r_q if _r_q is not None else _r_lbl or 'N/A')}\n"
                f"  Valuation confidence: {_pipeline_status.get('confidence', 'N/A') if _pipeline_status else 'N/A'}"
            )
    _timings = (getattr(result, "data_quality", {}) or {}).get("timings_s", {})
    if _timings:
        _known = 0.0
        _known_keys = [
            "market_data",
            "fundamentals",
            "market_analysis",
            "peer_selection",
            "peer_validation",
            "tx_comps",
            "financials",
            "valuation",
            "thesis",
        ]
        if (
            _timings.get("peer_selection") in {None, "N/A"}
            and _timings.get("peer_validation") in {None, "N/A"}
        ):
            _known_keys.append("peers")
        for _k in _known_keys:
            try:
                _known += float(_timings.get(_k) or 0.0)
            except Exception:
                pass
        _total = 0.0
        try:
            _total = float(_timings.get("total") or 0.0)
        except Exception:
            _total = 0.0
        _hidden = round(max(0.0, _total - _known), 2)
        console.print(
            "[bold]Timing:[/bold]\n"
            f"  Market data: {_fmt_timing_s(_timings.get('market_data', 'N/A'))}\n"
            f"  Fundamentals: {_fmt_timing_s(_timings.get('fundamentals', 'N/A'))}\n"
            f"  Market analysis: {_fmt_timing_s(_timings.get('market_analysis', 'N/A'))}\n"
            f"  Peer selection: {_fmt_timing_s(_timings.get('peer_selection', 'N/A'))}\n"
            f"  Peer validation/data fetch: {_fmt_timing_s(_timings.get('peer_validation', 'N/A'))}\n"
            f"  Peer total: {_fmt_timing_s(_timings.get('peers', 'N/A'))}\n"
            + (
                f"  Research agent time sum (parallel; may exceed wall-clock): {_fmt_timing_s(_timings.get('research_total', 'N/A'))}\n"
                if debug else
                f"  Research agent compute time: {_fmt_timing_s(_timings.get('research_total', 'N/A'))}\n"
            )
            + f"  Financials: {_fmt_timing_s(_timings.get('financials', 'N/A'))}\n"
            f"  Valuation: {_fmt_timing_s(_timings.get('valuation', 'N/A'))}\n"
            f"  Report: {_fmt_timing_s(_timings.get('thesis', 'N/A'))}\n"
            f"  Startup/orchestration: {_hidden}s\n"
            f"  Total: {_fmt_timing_s(_timings.get('total', 'N/A'))}"
        )
    _ev_bridge = src_map.get("Enterprise Value (blended)", {}).get("value")
    _eq_bridge = src_map.get("Equity Value", {}).get("value")
    _nd_bridge = src_map.get("Net Debt", {}).get("value")
    _nd_bridge_orig = src_map.get("Net Debt (original currency)", {}).get("value") or _nd_bridge
    _nd_bridge_val = src_map.get("Net Debt (valuation currency)", {}).get("value")
    _sh_bridge = src_map.get("Shares Outstanding", {}).get("value")
    _tp_bridge = src_map.get("Implied Target Price", {}).get("value")
    if (not _is_inconclusive) and _ev_bridge and _eq_bridge and _sh_bridge and _tp_bridge:
        _tag_ev = footnotes.tag(_infer_source_note("Enterprise Value (blended)", _ev_bridge, src_map))
        _tag_eq = footnotes.tag(_infer_source_note("Equity Value", _eq_bridge, src_map))
        _tag_sh = footnotes.tag(_infer_source_note("Shares Outstanding", _sh_bridge, src_map))
        _tp_metric = "Indicative value per share" if _is_low_conf else "Implied Target Price"
        _tag_tp = footnotes.tag(_infer_source_note(_tp_metric, _tp_bridge, src_map))
        _nd_txt = ""
        if _nd_bridge_val or _nd_bridge_orig:
            _nd_metric = "Net Debt (valuation currency)" if _nd_bridge_val else "Net Debt"
            _nd_note = _infer_source_note(_nd_metric, _nd_bridge_val or _nd_bridge_orig or "", src_map)
            _tag_nd = footnotes.tag(_nd_note)
            _nd_val_disp = _format_metric_value("Revenue", _nd_bridge_val or _nd_bridge_orig or "N/A")
            _nd_orig_disp = _format_metric_value("Revenue", _nd_bridge_orig or "")
            _nd_suffix = ""
            if _nd_bridge_val and _nd_bridge_orig and str(_nd_bridge_val) != str(_nd_bridge_orig):
                _fx_hint = ""
                _norm_reason = str(_pipeline_status.get("normalization_reason", "") or "")
                _m_fx = re.search(r"rate=([0-9]*\.?[0-9]+)", _norm_reason)
                if _m_fx:
                    _fx_hint = f" @ {float(_m_fx.group(1)):.4f}"
                _nd_suffix = f" ({_nd_orig_disp}{_fx_hint})"
            _nd_txt = f" - Net Debt {_nd_val_disp}{_nd_suffix}{_tag_nd}"
        _tp_bridge_label = "Target"
        _tp_bridge_show = _tp_bridge
        if _is_low_conf:
            _tp_bridge_label = "Indicative value"
            _v = _extract_first_number(str(_tp_bridge))
            if _v is not None:
                _tp_bridge_show = f"~{_fmt_price_human(_v, _run_ccy, decimals=0)}"
        console.print(
            f"[dim]Bridge:[/] EV {_ev_bridge}{_tag_ev}{_nd_txt} = Equity {_eq_bridge}{_tag_eq} "
            f"→ / Shares {_sh_bridge}{_tag_sh} = {_tp_bridge_label} {_tp_bridge_show}{_tag_tp}"
        )
    _mcap_val = src_map.get("Market Cap", {}).get("value")
    _valuation_failed = str(_pipeline_status.get("valuation", "")).upper() == "FAILED"
    _sanity_suppressed = bool(_pipeline_status.get("sanity_breaker_triggered"))
    if _mcap_val and _eq_bridge:
        _interp = "not available — insufficient upside/downside signal"
        _updn_txt = str(v.upside_downside or "N/A")
        try:
            _u_txt = _updn_txt.replace("%", "")
            _u_val = float(_u_txt)
            if _u_val <= -10:
                _interp = "market appears overvalued vs model"
            elif _u_val >= 10:
                _interp = "market appears undervalued vs model"
            else:
                _interp = "fairly valued"
        except Exception:
            if _is_inconclusive or _valuation_failed or _sanity_suppressed or str(_updn_txt).upper() in {"N/A", ""}:
                _interp = "not available — valuation suppressed by sanity breaker"
        if _valuation_failed or _sanity_suppressed:
            _interp = "not available — valuation suppressed by sanity breaker"
            console.print(
                f"[dim]Diagnostic market-cap check: Market cap {_mcap_val} | "
                f"Diagnostic model equity value {_eq_bridge} | "
                f"Model-implied upside/downside: {_updn_txt or 'N/A'} | "
                f"Interpretation: {_interp}[/dim]"
            )
        elif _extreme_capped:
            _interp = "diagnostic only — final recommendation capped pending corroboration"
            console.print(
                f"[dim]Diagnostic market-cap check: Market cap {_mcap_val} | "
                f"Diagnostic model equity value {_eq_bridge} | "
                f"Model-implied upside/downside: {_updn_txt or 'N/A'} | "
                f"Interpretation: {_interp}[/dim]"
            )
        else:
            console.print(
                f"[dim]Market cap: {_mcap_val} | Model equity value: {_eq_bridge} | "
                f"Model-implied upside/downside: {_updn_txt or 'N/A'} | "
                f"Interpretation: {_interp}[/dim]"
            )

    # KPIs
    kpi_table = Table(title="Key Financials", show_header=True, header_style="bold magenta")
    kpi_table.add_column("Metric")
    kpi_table.add_column("Value")
    _private_val_mode = str((_pipeline_status or {}).get("private_valuation_mode", "") or "").strip().upper()
    _private_screen_only_mode = (_private_val_mode == "SCREEN_ONLY")
    if str(getattr(result, "company_type", "")).lower() == "private" and _private_screen_only_mode:
        _private_rev_quality = str((_pipeline_status or {}).get("private_revenue_quality", "") or "").strip().upper()
        _rev_screen = _source_value("Revenue TTM") or "N/A"
        _rev_screen_l = str(_rev_screen).strip().lower()
        if _rev_screen_l.startswith("unknown ") or _rev_screen_l in {"unknown", "n/a", ""}:
            _rev_screen = "N/A"
        if _private_rev_quality not in {"VERIFIED", "HIGH_CONFIDENCE_ESTIMATE", "MANUAL"}:
            _rev_screen = "N/A"
        _private_rows = [
            ("Revenue", _rev_screen),
            ("Revenue Growth", "N/A [screen-only: non-valuation-grade]"),
            ("Modeled Revenue Growth", "N/A [screen-only: non-valuation-grade]"),
            ("Gross Margin", "N/A [screen-only: non-valuation-grade]"),
            ("EBITDA Margin", "N/A [screen-only: non-valuation-grade]"),
            ("Net Margin", "N/A [screen-only: non-valuation-grade]"),
            ("Free Cash Flow", "N/A [screen-only: non-valuation-grade]"),
            ("TAM", m.market_size),
            ("Market Growth", m.market_growth),
            ("Terminal Growth", _source_value("Terminal Growth")),
        ]
        for row in _private_rows:
            kpi_table.add_row(row[0], _value_with_source(row[0], row[1]))
    else:
        for row in [
            ("Revenue", _source_value("Revenue") or _source_value("Revenue TTM") or fin.revenue_current),
            ("Revenue Growth", fin.revenue_growth),
            ("Modeled Revenue Growth", _source_value("Modeled Revenue Growth")),
            ("Gross Margin", fin.gross_margin),
            ("EBITDA Margin", fin.ebitda_margin),
            ("Net Margin", fin.net_margin),
            ("Free Cash Flow", _source_value("Free Cash Flow") or fin.free_cash_flow),
            ("TAM", m.market_size),
            ("Market Growth", m.market_growth),
            ("Terminal Growth", _source_value("Terminal Growth")),
        ]:
            kpi_table.add_row(row[0], _value_with_source(row[0], row[1]))
    console.print(kpi_table)
    if str(getattr(result, "company_type", "")).lower() == "private" and _private_screen_only_mode:
        _private_rev_quality = str((_pipeline_status or {}).get("private_revenue_quality", "") or "").strip().upper()
        if _private_rev_quality in {"LOW_CONFIDENCE_ESTIMATE", "UNAVAILABLE"}:
            _rev_hint = _source_value("Revenue TTM")
            if _rev_hint and str(_rev_hint).strip().upper() not in {"", "N/A", "UNKNOWN"}:
                console.print("\n[bold]Triangulation / Unverified Clues[/]")
                console.print(
                    "  "
                    + _format_metric_value("Revenue", str(_rev_hint))
                    + " [excluded from valuation]"
                )
    _sector_ind = f"{f.sector or ''} {(_source_value('Industry') or '')}".lower()
    if any(tok in _sector_ind for tok in ("tobacco", "nicotine")):
        cash_table = Table(title="Cash Return & Leverage Metrics", show_header=True, header_style="bold magenta")
        cash_table.add_column("Metric")
        cash_table.add_column("Value")
        _cash_rows = [
            ("Dividend Yield", _source_value("Dividend Yield")),
            ("FCF Yield on Market Cap", _source_value("FCF Yield on Market Cap")),
            ("Net Debt / EBITDA", _source_value("Net Debt / EBITDA")),
            ("Dividend Coverage", _source_value("Dividend Coverage")),
            ("Interest Coverage", _source_value("Interest Coverage")),
        ]
        _has_cash_rows = False
        for _m, _v in _cash_rows:
            if _v:
                _has_cash_rows = True
                cash_table.add_row(_m, _value_with_source(_m, _v))
        if _has_cash_rows:
            console.print(cash_table)
    _has_market_context = bool(m.key_trends) or bool(_ctx_pack)
    if _has_market_context:
        console.print("\n[bold]Market Context[/]")
        _fallback_banner = "Fallback Market Context — sector profile only; not source-backed; not used in valuation."
        _ctx_source_backed = bool(_ctx_pack.get("source_backed")) if isinstance(_ctx_pack, dict) else False
        _ctx_fallback_used = bool(_ctx_pack.get("fallback_used")) if isinstance(_ctx_pack, dict) else False
        _ctx_source_count = 0
        if isinstance(_ctx_pack, dict):
            try:
                _ctx_source_count = int(_ctx_pack.get("source_count") or 0)
            except Exception:
                _ctx_source_count = 0
        _ctx_latest = ""
        if isinstance(_ctx_pack, dict):
            _dates = []
            for _k in ("trends", "catalysts", "risks"):
                _rows = _ctx_pack.get(_k)
                if not isinstance(_rows, list):
                    continue
                for _row in _rows:
                    if isinstance(_row, dict):
                        _d = str(_row.get("date") or "").strip()
                        if _d:
                            _dates.append(_d)
            if _dates:
                _ctx_latest = max(_dates)
        if _ctx_source_backed:
            _ctx_header = f"Source-backed Market Context — {_ctx_source_count} sources"
            if _ctx_latest:
                _ctx_header += f"; latest source date {_ctx_latest}"
            console.print(f"  [dim]{_ctx_header}.[/dim]")
        elif _ctx_fallback_used or not _ctx_source_backed:
            console.print(f"  [dim]{_fallback_banner}[/dim]")
        _trend_rows: list[str] = []
        for trend in (m.key_trends or [])[:8]:
            _txt = str(trend or "").strip()
            if not _txt:
                continue
            if _txt.lower().startswith("fallback market context — sector profile only"):
                continue
            _trend_rows.append(_txt)
        for trend in _trend_rows[:4]:
            if str(trend or "").strip():
                console.print(f"  • {trend}")
        if debug and isinstance(_ctx_pack, dict) and _ctx_source_backed:
            _extra_rows: list[str] = []
            for _label, _k in (("Catalyst", "catalysts"), ("Risk", "risks")):
                _rows = _ctx_pack.get(_k)
                if not isinstance(_rows, list):
                    continue
                for _row in _rows[:2]:
                    if not isinstance(_row, dict):
                        continue
                    _txt = str(_row.get("text") or "").strip()
                    if not _txt:
                        continue
                    _src = str(_row.get("source") or "unknown").strip()
                    _d = str(_row.get("date") or "").strip()
                    _extra_rows.append(
                        f"  • {_label}: {_txt} [source: {_src}" + (f", date: {_d}]" if _d else "]")
                    )
            for _line in _extra_rows[:4]:
                console.print(_line)

    if isinstance(_filings_pack, dict) and _filings_pack.get("records"):
        _records = _filings_pack.get("records")
        if isinstance(_records, list) and _records:
            console.print("\n[bold]Filing Sources[/]")
            _sb = bool(_filings_pack.get("source_backed"))
            _sc = _filings_pack.get("source_count")
            console.print(
                f"  [dim]{'Source-backed' if _sb else 'Fallback'} filing pack — "
                f"{_sc if _sc is not None else 0} source link(s).[/dim]"
            )
            for _row in _records[:3]:
                if not isinstance(_row, dict):
                    continue
                _ft = str(_row.get("filing_type") or "unknown").strip()
                _fd = str(_row.get("filing_date") or "").strip()
                _fs = str(_row.get("source_name") or "unknown").strip()
                _fu = str(_row.get("source_url") or "").strip()
                _line = f"  • {_ft}" + (f" ({_fd})" if _fd else "") + f" — {_fs}"
                if _fu:
                    _line += f" — {_fu}"
                console.print(_line)

    # Peer table (auditability)
    if result.peer_comps and result.peer_comps.peers:
        peer_table = Table(title="Peer Set (Top Validated Peers)", show_header=True, header_style="bold cyan")
        for _col in _peer_table_headers(debug=debug):
            peer_table.add_column(_col)
        _role_rank = {
            "core valuation peer": 0,
            "adjacent valuation peer": 1,
            "qualitative peer only": 2,
            "excluded": 3,
        }
        _sorted_peers = sorted(
            list(result.peer_comps.peers),
            key=lambda p: (
                _role_rank.get(str(getattr(p, "role", "")).strip().lower(), 9),
                -float(_to_float(getattr(p, "weight", None)) or 0.0),
            ),
        )
        for p in _sorted_peers:
            if debug:
                peer_table.add_row(
                    p.ticker or "—",
                    p.name or "—",
                    p.bucket or "—",
                    p.role or "—",
                    p.market_cap or "—",
                    p.ev_ebitda or "—",
                    p.similarity or "—",
                    p.business_similarity or "—",
                    p.scale_similarity or "—",
                    p.weight or "—",
                    p.include_reason or "—",
                )
            else:
                peer_table.add_row(
                    p.ticker or "—",
                    p.role or "—",
                    p.bucket or "—",
                    p.market_cap or "—",
                    p.ev_ebitda or "—",
                    p.weight or "—",
                )
        console.print(peer_table)
        console.print(
            "[dim]Peer role legend: core peer = same business model; adjacent peer = related/capped weight; "
            "qualitative peer = context only (0% valuation weight).[/dim]"
        )

    # Valuation methods
    if v.methods and not _is_inconclusive:
        val_table = Table(title="Valuation Football Field", show_header=True, header_style="bold yellow")
        val_table.add_column("Method")
        val_table.add_column("Low")
        val_table.add_column("Mid")
        val_table.add_column("High")
        val_table.add_column("Weight")
        for method in v.methods:
            if method.name.startswith("Trading Comps"):
                source_metric = "EV/EBITDA (peer applied)"
            elif method.name.startswith("Transaction Comps"):
                source_metric = "Transaction Comps"
            elif method.name == "DCF":
                source_metric = "WACC"
            else:
                source_metric = method.name
            method_note = footnotes.tag(_infer_source_note(source_metric, method.mid or "", src_map))
            _low = _format_valuation_cell(method.low, _run_ccy)
            _mid = _format_valuation_cell(method.mid, _run_ccy)
            _high = _format_valuation_cell(method.high, _run_ccy)
            val_table.add_row(
                method.name,
                f"{_low}{method_note if method.low else ''}",
                f"{_mid}{method_note if method.mid else ''}",
                f"{_high}{method_note if method.high else ''}",
                (f"{method.weight}%{method_note}" if method.weight is not None else "—"),
            )
        console.print(val_table)

    if footnotes.items():
        console.print("\n[bold]Value Sources[/]")
        for i, note in enumerate(footnotes.items(), start=1):
            console.print(f"  (S{i}) {note}")

    # Thesis
    if t.thesis:
        console.print(Panel(t.thesis, title="Investment Thesis", border_style="green"))

    # Scenarios
    if t.bull_case or t.base_case or t.bear_case:
        console.print("\n[bold]Scenarios:[/]")
        console.print("  [dim]Scenarios are model-generated watch cases, source-informed, and not individually source-verified.[/dim]")
        if t.bull_case:
            console.print(f"  [green]Bull:[/] {t.bull_case}")
        if t.base_case:
            console.print(f"  [yellow]Base:[/] {t.base_case}")
        if t.bear_case:
            console.print(f"  [red]Bear:[/] {t.bear_case}")

    # Catalysts
    if t.catalysts:
        console.print("\n[bold]Catalysts:[/]")
        for c in t.catalysts:
            console.print(f"  → {c}")
    console.print("\n[dim]This is a model-based valuation screen, not investment advice.[/dim]")


def main():
    # Signal interactive CLI execution so agent retry behavior can prefer
    # immediate fallbacks over long capacity backoffs.
    os.environ["GOLDROGER_RUN_MODE"] = "cli"

    parser = argparse.ArgumentParser(description="Gold Roger — AI-powered equity analysis")
    parser.add_argument("--company", "-c", required=False, help="Company name, ticker, or description")
    parser.add_argument("--siren", help="French SIREN — bypasses name resolution, calls Pappers directly")
    parser.add_argument("--type", "-t", choices=["public", "private"], default="public",
                        help="public (listed) or private company")
    parser.add_argument("--mode", choices=["equity", "ma", "pipeline"], default="equity",
                        help="equity (5-agent analysis), ma (M&A workflow), or pipeline (target list + valuations)")
    parser.add_argument("--acquirer", help="Acquirer name (M&A mode)")
    parser.add_argument("--objective", help="Acquirer objective (M&A mode)")
    parser.add_argument("--buyer", help="Buyer name (pipeline mode)")
    parser.add_argument("--focus", help="Pipeline focus/thesis (pipeline mode)")
    parser.add_argument("--output", "-o", help="Save JSON result to file")
    parser.add_argument("--excel", action="store_true", help="Generate Excel DCF workbook")
    parser.add_argument("--pptx", action="store_true", help="Generate PowerPoint deck")
    parser.add_argument("--outdir", default="outputs", help="Output directory for files")
    parser.add_argument("--quick", action="store_true", help="Fast bounded pipeline (deterministic peers + short report; skips deep market research)")
    parser.add_argument(
        "--full-report",
        action="store_true",
        help="Generate full narrative report (thesis + scenarios + catalysts). Default mode is standard concise report.",
    )
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Interactively select data sources before analysis (private companies)")
    parser.add_argument(
        "--sources",
        default=None,
        help=(
            "Comma-separated data sources for private analysis "
            "(e.g. infogreffe,pappers,sec_edgar,crunchbase | auto | all). "
            "Unavailable sources (missing credentials) are skipped."
        ),
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List available data sources and credential status, then exit.",
    )
    parser.add_argument("--llm", default=None, help="LLM provider: mistral (default), anthropic, openai")
    parser.add_argument("--country-hint", default="", help="Optional ISO-2 country hint for private company resolution (FR/GB/DE/NL/ES/US)")
    parser.add_argument(
        "--manual-revenue",
        type=float,
        default=None,
        help="Manual private-company revenue override in millions (for prototype valuation unlock).",
    )
    parser.add_argument(
        "--manual-revenue-currency",
        default="USD",
        help="Currency code for --manual-revenue (default: USD).",
    )
    parser.add_argument(
        "--manual-revenue-year",
        type=int,
        default=None,
        help="Fiscal year for --manual-revenue (for provenance only).",
    )
    parser.add_argument(
        "--manual-revenue-source-note",
        default="",
        help="Short provenance note for manual revenue input.",
    )
    parser.add_argument(
        "--manual-identity-confirmed",
        action="store_true",
        help="Allow private manual-revenue valuation when legal identity remains unresolved (prototype guardrail).",
    )
    parser.add_argument("--debug", action="store_true", help="Show verbose diagnostics (JSON parse/raw search details, full notes)")
    args = parser.parse_args()
    if args.quick and args.full_report:
        parser.error("--quick and --full-report cannot be used together")

    if args.list_sources:
        rows = provider_table(country_hint=args.country_hint or "", company_type=args.type or "")
        t = Table(title="Data Sources", show_header=True, header_style="bold magenta")
        t.add_column("Name")
        t.add_column("Display")
        t.add_column("Coverage")
        t.add_column("Status")
        t.add_column("Identity")
        t.add_column("Revenue")
        t.add_column("Filings")
        t.add_column("Limitations")
        for r in rows:
            cov = ", ".join(r["coverage"])
            t.add_row(
                r["name"],
                r["display"],
                cov,
                r["status"],
                "yes" if r.get("supports_identity") else "no",
                "yes" if r.get("supports_revenue") else "no",
                "yes" if r.get("supports_filings") else "no",
                str(r.get("limitations") or "—"),
            )
        console.print(t)
        return

    if args.mode != "pipeline" and not args.company:
        parser.error("--company is required unless --mode pipeline or --list-sources is used")
    if args.mode == "pipeline" and not args.company:
        args.company = "pipeline"

    selected_sources: list[str] | None = None
    if args.sources is not None:
        selected_sources = [
            s.strip().lower() for s in args.sources.split(",") if s.strip()
        ]

    try:
        confirmed_company = args.company
        country_hint = args.country_hint.strip().upper() if args.country_hint else ""
        company_identifier = ""
        if args.mode != "pipeline":
            confirmed_company, country_hint, company_identifier = _confirm_company_or_abort(
                args.company, args.type, country_hint
            )

        if args.mode == "pipeline":
            buyer = args.buyer or "Global consumer goods group"
            focus = args.focus or (
                "Premium beauty and wellness; high-growth founder-led private companies in Europe; "
                "skincare, wellness, premium personal care; younger consumers; DTC"
            )
            result = run_pipeline(buyer=buyer, focus=focus, quick=args.quick, llm=args.llm)
            console.print(Panel(
                f"[bold]{buyer}[/]\n{focus}\n\nTargets: {len(result.targets)}",
                title="[bold cyan]Pipeline Summary[/]",
                border_style="cyan",
            ))
        elif args.mode == "ma":
            result = run_ma_analysis(
                confirmed_company,
                args.type,
                acquirer=args.acquirer,
                objective=args.objective,
                llm=args.llm,
            )
            console.print(Panel(
                f"[bold]{result.company}[/] ({result.company_type})\n"
                f"Acquirer: {result.acquirer or 'N/A'}\n\n"
                f"Opportunities: {len(result.deal_sourcing.opportunities)} | "
                f"Fit: {result.strategic_fit.fit_score or 'N/A'} | "
                f"Red flags: {len(result.due_diligence.red_flags)}",
                title="[bold cyan]M&A Summary[/]",
                border_style="cyan",
            ))
        else:
            result = run_analysis(confirmed_company, args.type, llm=args.llm, siren=args.siren,
                                   interactive=args.interactive, data_sources=selected_sources,
                                   country_hint=country_hint, company_identifier=company_identifier,
                                   manual_revenue=args.manual_revenue,
                                   manual_revenue_currency=args.manual_revenue_currency,
                                   manual_revenue_year=args.manual_revenue_year,
                                   manual_revenue_source_note=args.manual_revenue_source_note,
                                   manual_identity_confirmed=bool(args.manual_identity_confirmed),
                                   quick_mode=args.quick, full_report=args.full_report,
                                   debug=args.debug, cli_mode=True)
            print_result(result, debug=args.debug)

        if args.output:
            path = Path(args.output)
            path.write_text(result.model_dump_json(indent=2))
            console.print(f"\n[green]✓[/] Saved to {path}")

        if args.excel or args.pptx:
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", args.company).strip("_")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            outdir = Path(args.outdir) / f"{slug}_{ts}"
            outdir.mkdir(parents=True, exist_ok=True)

        if args.excel and args.mode == "equity":
            xlsx = outdir / f"{slug}_analysis.xlsx"
            generate_excel(result, str(xlsx))
            console.print(f"\n[green]✓[/] Excel generated: {xlsx}")
        elif args.excel and args.mode != "equity":
            console.print("\n[yellow]Excel export is only available in equity mode for now.[/]")

        if args.pptx:
            deck_name = f"{slug}_analysis.pptx" if args.mode != "pipeline" else "pipeline_deck.pptx"
            deck = outdir / deck_name
            generate_pptx(result, str(deck))
            console.print(f"\n[green]✓[/] PowerPoint generated: {deck}")

        if (args.excel or args.pptx) and hasattr(result, "sources_md") and result.sources_md:
            src = outdir / "sources.md"
            src.write_text(result.sources_md, encoding="utf-8")
            console.print(f"[green]✓[/] Sources log: {src}")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]Error:[/] {e}")
        raise


if __name__ == "__main__":
    main()
