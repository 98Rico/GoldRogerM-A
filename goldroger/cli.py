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
        "Target": ["Implied Target Price", "Target Price", "Implied EV"],
        "Fair Value Range": ["Fair Value Range"],
        "Upside": ["Upside", "Upside/Downside"],
        "WACC": ["WACC"],
        "Terminal Growth": ["Terminal Growth"],
        "Blended Valuation": ["Blended EV Calculation", "Enterprise Value (blended)"],
        "DCF-only Valuation": ["Blended EV Calculation", "Enterprise Value (blended)"],
    }
    return aliases.get(metric, [metric])


def _infer_source_note(metric: str, value: str, src_map: dict[str, dict[str, str]]) -> str:
    for key in _metric_source_keys(metric):
        entry = src_map.get(key)
        if entry:
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
    t = s.strip().replace(",", "")
    if not t:
        return None
    if t.startswith("$"):
        t = t[1:]
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


def _fmt_money_m(v_m: float) -> str:
    if abs(v_m) >= 1_000_000:
        return f"${v_m / 1_000_000:.2f}T"
    if abs(v_m) >= 1_000:
        return f"${v_m / 1_000:.1f}B"
    return f"${v_m:,.0f}M"


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
        n = _to_float(base)
        if n is not None:
            return f"{_fmt_money_m(n)}{q}"
    if metric in {"Revenue Growth", "Market Growth"}:
        return _fmt_percentish(raw, signed=True)
    if metric in {"Gross Margin", "EBITDA Margin", "Net Margin"}:
        return _fmt_percentish(raw, signed=False)
    return raw


def _format_valuation_cell(value: Optional[str]) -> str:
    if not value:
        return "—"
    n = _to_float(value)
    if n is None:
        return value
    return _fmt_money_m(n)


def print_result(result):
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
    console.print()
    _target_display = "N/A" if _is_inconclusive else (v.target_price or v.implied_value)
    _confidence = str(_pipeline_status.get("confidence", "")).lower()
    if (not _is_inconclusive) and _confidence == "low" and isinstance(_target_display, str):
        _m = re.search(r"\$([0-9][0-9,]*\.?[0-9]*)", _target_display)
        if _m:
            try:
                _pt = float(_m.group(1).replace(",", ""))
                _target_display = f"~${_pt:,.0f}"
            except Exception:
                pass
    _fv_range = _source_value("Fair Value Range")
    if _fv_range and _confidence == "low":
        _m = re.match(r"\$([0-9][0-9,]*\.?[0-9]*)\s*[–-]\s*\$([0-9][0-9,]*\.?[0-9]*)", str(_fv_range))
        if _m:
            try:
                _lo = round(float(_m.group(1).replace(",", "")))
                _hi = round(float(_m.group(2).replace(",", "")))
                _fv_range = f"${_lo:,}–${_hi:,}"
            except Exception:
                pass
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
    _target_line = (
        f"Fair Value Range: {_fv_label} | "
        f"{_pt_label}: {_value_with_source('Target', _target_display)}"
        if _fv_range and v.target_price and not _is_inconclusive
        else f"Target: {_value_with_source('Target', _target_display)}{_ev_display}"
    )
    if (not _is_inconclusive) and _pipeline_status.get("confidence"):
        _target_line += f" | Valuation reliability: {_pipeline_status.get('confidence')}"
    console.print(Panel(
        f"[bold]{f.company_name}[/] | {f.sector} | {f.headquarters}\n"
        f"{f.description}\n\n"
        f"Recommendation: [{rec_color}]{v.recommendation}[/] | "
        f"{_target_line} | "
        f"Upside: {_value_with_source('Upside', v.upside_downside)}",
        title=f"[bold cyan]{result.company}[/]",
        border_style="cyan",
    ))
    _dq = (getattr(result, "data_quality", {}) or {})
    if _pipeline_status:
        console.print(
            "[bold]Pipeline status:[/bold]\n"
            f"  Core valuation: {_pipeline_status.get('core_valuation', 'N/A')}\n"
            f"  Research enrichment: {_pipeline_status.get('research_enrichment', 'N/A')}\n"
            f"  Market analysis: {_pipeline_status.get('market_analysis', 'N/A')}\n"
            f"  Peers: {_pipeline_status.get('peers', 'N/A')}\n"
            f"  Valuation: {_pipeline_status.get('valuation', 'N/A')}\n"
            f"  Thesis: {_pipeline_status.get('thesis', 'N/A')}\n"
            f"  Model signal: {_pipeline_status.get('model_signal_detail', _pipeline_status.get('model_signal', 'N/A'))}\n"
            f"  Recommendation: {_pipeline_status.get('recommendation', 'N/A')}\n"
            f"  DCF status: {_pipeline_status.get('dcf_status', 'N/A')}\n"
            f"  Effective peers: {_pipeline_status.get('effective_peer_count', 'N/A')}\n"
            f"  Confidence: {_pipeline_status.get('confidence', 'N/A')}\n"
            f"  Confidence reason: {_pipeline_status.get('confidence_reason', 'N/A')}"
        )
        _ms = str(_pipeline_status.get("model_signal_detail", _pipeline_status.get("model_signal", "N/A")))
        _fr = str(_pipeline_status.get("recommendation", "N/A"))
        if _ms not in {"N/A", ""} and _fr not in {"N/A", ""} and _ms != _fr:
            console.print(f"[dim]Model signal vs final recommendation: {_ms} → {_fr} (guardrails/confidence applied).[/dim]")
    _core_q = _dq.get("core_data_quality_score")
    _r_q = _dq.get("research_enrichment_quality_score")
    _r_lbl = _dq.get("research_enrichment_quality_label")
    if (_core_q is not None) or (_r_q is not None):
        console.print(
            "[bold]Quality Split:[/bold]\n"
            f"  Core data quality: {(_core_q if _core_q is not None else 'N/A')}/100\n"
            f"  Research enrichment quality: {(_r_q if _r_q is not None else _r_lbl or 'N/A')}\n"
            f"  Valuation confidence: {_pipeline_status.get('confidence', 'N/A') if _pipeline_status else 'N/A'}"
        )
    _timings = (getattr(result, "data_quality", {}) or {}).get("timings_s", {})
    if _timings:
        console.print(
            "[bold]Timing:[/bold]\n"
            f"  Market data: {_timings.get('market_data', 'N/A')}s\n"
            f"  Market analysis: {_timings.get('market_analysis', 'N/A')}s\n"
            f"  Peers/financials: {_timings.get('financials', 'N/A')}s\n"
            f"  Valuation: {_timings.get('valuation', 'N/A')}s\n"
            f"  Report: {_timings.get('thesis', 'N/A')}s\n"
            f"  Total: {_timings.get('total', 'N/A')}s"
        )
    _ev_bridge = src_map.get("Enterprise Value (blended)", {}).get("value")
    _eq_bridge = src_map.get("Equity Value", {}).get("value")
    _nd_bridge = src_map.get("Net Debt", {}).get("value")
    _sh_bridge = src_map.get("Shares Outstanding", {}).get("value")
    _tp_bridge = src_map.get("Implied Target Price", {}).get("value")
    if (not _is_inconclusive) and _ev_bridge and _eq_bridge and _sh_bridge and _tp_bridge:
        _tag_ev = footnotes.tag(_infer_source_note("Enterprise Value (blended)", _ev_bridge, src_map))
        _tag_eq = footnotes.tag(_infer_source_note("Equity Value", _eq_bridge, src_map))
        _tag_sh = footnotes.tag(_infer_source_note("Shares Outstanding", _sh_bridge, src_map))
        _tag_tp = footnotes.tag(_infer_source_note("Implied Target Price", _tp_bridge, src_map))
        _nd_txt = f" - Net Debt {_nd_bridge}" if _nd_bridge else ""
        console.print(
            f"[dim]Bridge:[/] EV {_ev_bridge}{_tag_ev}{_nd_txt} = Equity {_eq_bridge}{_tag_eq} "
            f"→ / Shares {_sh_bridge}{_tag_sh} = Target {_tp_bridge}{_tag_tp}"
        )

    # KPIs
    kpi_table = Table(title="Key Financials", show_header=True, header_style="bold magenta")
    kpi_table.add_column("Metric")
    kpi_table.add_column("Value")
    for row in [
        ("Revenue", fin.revenue_current),
        ("Revenue Growth", fin.revenue_growth),
        ("Modeled Revenue Growth", _source_value("Modeled Revenue Growth")),
        ("Gross Margin", fin.gross_margin),
        ("EBITDA Margin", fin.ebitda_margin),
        ("Net Margin", fin.net_margin),
        ("Free Cash Flow", fin.free_cash_flow),
        ("TAM", m.market_size),
        ("Market Growth", m.market_growth),
        ("Terminal Growth", _source_value("Terminal Growth")),
    ]:
        kpi_table.add_row(row[0], _value_with_source(row[0], row[1]))
    console.print(kpi_table)
    if m.key_trends:
        console.print("\n[bold]Market Context[/]")
        for trend in m.key_trends[:4]:
            if str(trend or "").strip():
                console.print(f"  • {trend}")

    # Peer table (auditability)
    if result.peer_comps and result.peer_comps.peers:
        peer_table = Table(title="Peer Set (Top Validated Peers)", show_header=True, header_style="bold cyan")
        peer_table.add_column("Ticker")
        peer_table.add_column("Name")
        peer_table.add_column("Bucket")
        peer_table.add_column("Role")
        peer_table.add_column("MCap")
        peer_table.add_column("EV/EBITDA")
        peer_table.add_column("Similarity")
        peer_table.add_column("Business Sim")
        peer_table.add_column("Scale Sim")
        peer_table.add_column("Weight")
        peer_table.add_column("Include Reason")
        for p in result.peer_comps.peers:
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
        console.print(peer_table)

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
            _low = _format_valuation_cell(method.low)
            _mid = _format_valuation_cell(method.mid)
            _high = _format_valuation_cell(method.high)
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


def main():
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
    parser.add_argument("--debug", action="store_true", help="Show verbose diagnostics (JSON parse/raw search details, full notes)")
    args = parser.parse_args()

    if args.list_sources:
        rows = provider_table()
        t = Table(title="Data Sources", show_header=True, header_style="bold magenta")
        t.add_column("Name")
        t.add_column("Display")
        t.add_column("Coverage")
        t.add_column("Status")
        for r in rows:
            cov = ", ".join(r["coverage"])
            t.add_row(r["name"], r["display"], cov, r["status"])
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
                                   quick_mode=args.quick, debug=args.debug)
            print_result(result)

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
