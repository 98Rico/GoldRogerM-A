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

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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


def print_result(result):
    f = result.fundamentals
    m = result.market
    fin = result.financials
    v = result.valuation
    t = result.thesis

    # Header
    rec_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(v.recommendation or "", "white")
    console.print()
    _target_display = v.target_price or v.implied_value  # per-share if public, EV if private
    _ev_display = f" | Implied EV: {v.implied_value}" if v.target_price else ""
    console.print(Panel(
        f"[bold]{f.company_name}[/] | {f.sector} | {f.headquarters}\n"
        f"{f.description}\n\n"
        f"Recommendation: [{rec_color}]{v.recommendation}[/] | "
        f"Target: {_target_display}{_ev_display} | "
        f"Upside: {v.upside_downside}",
        title=f"[bold cyan]{result.company}[/]",
        border_style="cyan",
    ))

    # KPIs
    kpi_table = Table(title="Key Financials", show_header=True, header_style="bold magenta")
    kpi_table.add_column("Metric")
    kpi_table.add_column("Value")
    for row in [
        ("Revenue", fin.revenue_current),
        ("Revenue Growth", fin.revenue_growth),
        ("Gross Margin", fin.gross_margin),
        ("EBITDA Margin", fin.ebitda_margin),
        ("Net Margin", fin.net_margin),
        ("Free Cash Flow", fin.free_cash_flow),
        ("TAM", m.market_size),
        ("Market Growth", m.market_growth),
    ]:
        kpi_table.add_row(row[0], row[1] or "N/A")
    console.print(kpi_table)

    # Valuation methods
    if v.methods:
        val_table = Table(title="Valuation Football Field", show_header=True, header_style="bold yellow")
        val_table.add_column("Method")
        val_table.add_column("Low")
        val_table.add_column("Mid")
        val_table.add_column("High")
        val_table.add_column("Weight")
        for method in v.methods:
            val_table.add_row(
                method.name, method.low or "—", method.mid or "—",
                method.high or "—", f"{method.weight}%" if method.weight else "—"
            )
        console.print(val_table)

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
    parser.add_argument("--quick", action="store_true", help="Skip web search in pipeline (faster, uses training knowledge)")
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
                                   country_hint=country_hint, company_identifier=company_identifier)
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
