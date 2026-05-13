"""
Interactive terminal data-source selector for private company analysis.

Usage:
    result = run_source_selection(company="Doctolib", country_hint="FR", console=console)

Returns a SourceSelectionResult with the list of providers to query and an optional
manual_revenue_usd_m override entered by the user.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.table import Table

from goldroger.data.registry import DEFAULT_REGISTRY


@dataclass
class _ProviderDef:
    name: str            # import key
    display: str         # shown to user
    countries: list[str] # ISO-2 or ["GLOBAL"]
    free: bool           # no API key needed
    env_var: str = ""    # env var that activates it


_ALL_PROVIDERS: list[_ProviderDef] = [
    _ProviderDef("infogreffe",       "Infogreffe (FR gov)",    ["FR"],      True),
    _ProviderDef("pappers",          "Pappers",                ["FR"],      False, "PAPPERS_API_KEY"),
    _ProviderDef("companies_house",  "Companies House (UK)",   ["GB"],      False, "COMPANIES_HOUSE_API_KEY"),
    _ProviderDef("handelsregister",  "Handelsregister (DE)",   ["DE"],      True),
    _ProviderDef("kvk",              "KvK (NL)",               ["NL"],      False, "KVK_API_KEY"),
    _ProviderDef("registro_mercantil","Registro Mercantil (ES)",["ES"],     True),
    _ProviderDef("sec_edgar",        "SEC EDGAR (US)",         ["US"],      True),
    _ProviderDef("crunchbase",       "Crunchbase",             ["GLOBAL"],  False, "CRUNCHBASE_API_KEY"),
    _ProviderDef("bloomberg",        "Bloomberg Terminal",     ["GLOBAL"],  False, "BLOOMBERG_API_KEY"),
    _ProviderDef("capitaliq",        "Capital IQ / Refinitiv", ["GLOBAL"],  False, "CAPITALIQ_USERNAME"),
]

_COUNTRY_HINTS: dict[str, str] = {
    "france": "FR", "french": "FR", "fr": "FR",
    "uk": "GB", "britain": "GB", "england": "GB", "gb": "GB",
    "germany": "DE", "german": "DE", "de": "DE",
    "netherlands": "NL", "dutch": "NL", "nl": "NL",
    "spain": "ES", "spanish": "ES", "es": "ES",
    "us": "US", "usa": "US", "united states": "US", "american": "US",
}


def _normalise_country(hint: str) -> Optional[str]:
    return _COUNTRY_HINTS.get(hint.strip().lower())


def _key_status(p: _ProviderDef) -> tuple[str, str]:
    """Return (status_label, rich_color)."""
    if p.free:
        return "free", "green"
    val = os.getenv(p.env_var, "")
    if val:
        return "key set ✓", "green"
    return "no key — will skip", "red"


def _relevant_providers(country_iso: Optional[str]) -> list[_ProviderDef]:
    """Return country-specific providers first, then globals."""
    if not country_iso:
        return list(_ALL_PROVIDERS)

    local: list[_ProviderDef] = []
    global_: list[_ProviderDef] = []
    for p in _ALL_PROVIDERS:
        if "GLOBAL" in p.countries:
            global_.append(p)
        elif country_iso and country_iso in p.countries:
            local.append(p)
    return local + global_


@dataclass
class SourceSelectionResult:
    selected_providers: list[str] = field(default_factory=list)  # provider names
    manual_revenue_usd_m: Optional[float] = None
    country_iso: Optional[str] = None
    unknown_sources: list[str] = field(default_factory=list)
    skipped_missing_credentials: list[str] = field(default_factory=list)
    requested_sources: list[str] = field(default_factory=list)


def provider_names() -> list[str]:
    """Return all canonical provider names."""
    return [p.name for p in _ALL_PROVIDERS]


def provider_table(country_hint: str = "", company_type: str = "") -> list[dict]:
    """Structured provider status for CLI/UI display."""
    country_iso = _normalise_country(country_hint) if country_hint else None
    providers = _relevant_providers(country_iso)
    ctype = (company_type or "").strip().lower()
    _caps_map = {c.name: c for c in DEFAULT_REGISTRY.list_providers()}
    rows: list[dict] = []
    for p in providers:
        caps = _caps_map.get(p.name)
        if ctype and caps and ctype not in {x.lower() for x in (caps.company_types or [])}:
            continue
        status, _ = _key_status(p)
        data_fields = {str(x).strip().lower() for x in ((caps.data_fields if caps else []) or [])}
        support_identity = bool(
            data_fields.intersection({"sector", "employees", "company_number", "siren", "registration_number"})
            or p.name in {"infogreffe", "pappers", "companies_house", "handelsregister", "kvk", "registro_mercantil"}
        )
        support_revenue = bool(
            data_fields.intersection({"revenue", "net_income"})
            or p.name in {"pappers", "companies_house", "handelsregister", "sec_edgar", "crunchbase", "yfinance"}
        )
        support_filings = bool(
            ("filing" in " ".join((caps.data_fields if caps else [])).lower())
            or p.name in {"companies_house", "sec_edgar", "pappers", "handelsregister"}
        )
        limitations = list(caps.limitations or []) if caps else []
        if not limitations:
            if p.name == "infogreffe":
                limitations = ["Identity/sector only; no revenue in free endpoint"]
            elif p.name == "companies_house":
                limitations = ["Revenue extraction is best-effort from filing docs/XBRL"]
            elif p.name == "handelsregister":
                limitations = ["Best-effort HTML parsing; coverage can be sparse"]
            elif p.name == "sec_edgar":
                limitations = ["US filer coverage only"]
            elif p.name == "kvk":
                limitations = ["Sector-focused; no financial statement revenue"]
            elif p.name == "registro_mercantil":
                limitations = ["Existence/registry context only"]
            elif p.name == "crunchbase":
                limitations = ["Estimated ranges, not filing-verified financials"]
        rows.append(
            {
                "name": p.name,
                "display": p.display,
                "coverage": p.countries,
                "status": status,
                "requires_key": not p.free,
                "env_var": p.env_var,
                "supports_identity": support_identity,
                "supports_revenue": support_revenue,
                "supports_filings": support_filings,
                "limitations": "; ".join(limitations[:2]) if limitations else "",
            }
        )
    return rows


def resolve_source_selection(
    requested: Optional[list[str]] = None,
    country_hint: str = "",
) -> SourceSelectionResult:
    """
    Resolve source list for non-interactive usage.

    Modes:
      - requested None / [] / ["auto"]  -> relevant-by-country + global
      - requested includes "all"        -> all sources
      - requested explicit names         -> exact names
    """
    country_iso = _normalise_country(country_hint) if country_hint else None
    relevant = _relevant_providers(country_iso)
    all_by_name = {p.name: p for p in _ALL_PROVIDERS}

    req = [r.strip().lower() for r in (requested or []) if r and r.strip()]
    if not req or req == ["auto"]:
        # "auto" intentionally excludes premium stubs unless explicitly requested.
        candidates = [p.name for p in relevant if p.name not in {"bloomberg", "capitaliq"}]
    elif "all" in req:
        candidates = [p.name for p in _ALL_PROVIDERS]
    else:
        candidates = req

    selected: list[str] = []
    unknown: list[str] = []
    skipped_no_key: list[str] = []

    for name in candidates:
        p = all_by_name.get(name)
        if p is None:
            unknown.append(name)
            continue
        if not p.free and not os.getenv(p.env_var, ""):
            skipped_no_key.append(name)
            continue
        selected.append(name)

    return SourceSelectionResult(
        selected_providers=selected,
        country_iso=country_iso,
        unknown_sources=unknown,
        skipped_missing_credentials=skipped_no_key,
        requested_sources=candidates,
    )


def run_source_selection(
    company: str,
    country_hint: str = "",
    console: Optional[Console] = None,
) -> SourceSelectionResult:
    """
    Interactively ask the user which data sources to use for a private company.
    Prints a table with credential status and prompts Y/N for each provider.
    Returns a SourceSelectionResult.
    """
    if console is None:
        console = Console()

    country_iso = _normalise_country(country_hint) if country_hint else None
    providers = _relevant_providers(country_iso)

    console.print()
    console.rule(f"[bold cyan]Data Source Selection — {company}[/]")
    if country_iso:
        console.print(f"  Detected country: [bold]{country_iso}[/] — showing relevant registries first\n")
    else:
        console.print("  No country detected — showing all providers\n")

    # Display availability table
    tbl = Table(show_header=True, header_style="bold magenta", box=None)
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Provider", min_width=26)
    tbl.add_column("Coverage")
    tbl.add_column("Status")
    for i, p in enumerate(providers, 1):
        cov = ", ".join(p.countries)
        status, color = _key_status(p)
        tbl.add_row(str(i), p.display, cov, f"[{color}]{status}[/]")
    console.print(tbl)
    console.print()

    selected: list[str] = []
    for p in providers:
        status, _ = _key_status(p)
        skip_no_key = not p.free and not os.getenv(p.env_var, "")
        if skip_no_key:
            default_hint = " [yellow](no key — default N)[/]"
        else:
            default_hint = " [green](default Y)[/]" if p.free else " [cyan](key available)[/]"

        try:
            answer = console.input(f"  Use [bold]{p.display}[/]{default_hint}? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        if skip_no_key:
            use = answer in ("y", "yes")
        else:
            use = answer not in ("n", "no")

        if use:
            selected.append(p.name)
            console.print(f"    [green]✓[/] {p.display} selected")
        else:
            console.print(f"    [dim]– {p.display} skipped[/]")

    # Manual revenue override
    console.print()
    manual_rev: Optional[float] = None
    try:
        rev_input = console.input(
            "  Enter [bold]revenue manually[/] in USD millions (leave blank to skip): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        rev_input = ""

    if rev_input:
        try:
            manual_rev = float(rev_input.replace(",", "").replace("$", "").replace("M", "").replace("m", ""))
            console.print(f"    [green]✓[/] Manual revenue: ${manual_rev:.0f}M USD")
        except ValueError:
            console.print("    [yellow]Could not parse revenue — skipped[/]")

    console.print()
    if selected:
        console.print(f"  [bold]Selected sources:[/] {', '.join(selected)}")
    else:
        console.print("  [yellow]No data sources selected — pipeline will rely on LLM agents only[/]")

    return SourceSelectionResult(
        selected_providers=selected,
        manual_revenue_usd_m=manual_rev,
        country_iso=country_iso,
        requested_sources=[p.name for p in providers],
    )
