#!/usr/bin/env python3
"""
Gold Roger CLI — run a full equity analysis from the command line.

Usage:
    uv run python -m goldroger.cli --company "Longchamp" --type private
    uv run python -m goldroger.cli --company "LVMH" --type public
    uv run python -m goldroger.cli --company "NVIDIA"
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .exporters import generate_excel, generate_pptx
from .orchestrator import run_analysis, run_ma_analysis, run_pipeline

console = Console()


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
    parser.add_argument("--company", "-c", required=True, help="Company name, ticker, or description")
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
    parser.add_argument("--llm", default=None, help="LLM provider: mistral (default), anthropic, openai")
    args = parser.parse_args()

    try:
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
                args.company,
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
            result = run_analysis(args.company, args.type, llm=args.llm)
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

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]Error:[/] {e}")
        raise


if __name__ == "__main__":
    main()
