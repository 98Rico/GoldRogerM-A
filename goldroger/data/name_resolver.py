"""
Company name resolver — maps a commercial name to the correct identifier per data source.

Each source expects a different format:
  - Infogreffe  → legal entity name, NO accents, uppercase (e.g. "SEZANE SAS")
  - Companies House → registered name, no legal suffix (e.g. "SEZANE")
  - Handelsregister → Firma (e.g. "Sézane GmbH")
  - Crunchbase  → slug lowercase, no spaces (e.g. "sezane")
  - yfinance    → ticker resolved separately

Resolution order:
  1. LLM one-shot (fast, no tools) — best quality
  2. Normalization fallback (strip accents, lowercase, remove legal suffixes)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompanyIdentifiers:
    commercial_name: str                   # as entered by user
    infogreffe_query: str = ""             # for Infogreffe LIKE search
    companies_house_query: str = ""        # for Companies House search
    handelsregister_query: str = ""        # for Handelsregister
    crunchbase_slug: str = ""              # for Crunchbase API
    country_hint: str = ""                 # "FR", "GB", "DE", etc.
    legal_suffixes_stripped: str = ""      # clean name, no SAS/Ltd/GmbH
    variants: list[str] = field(default_factory=list)  # all names to try


_LEGAL_SUFFIXES = re.compile(
    r"\b(SAS|SA|SARL|SNC|SCI|SE|NV|BV|GmbH|AG|KG|OHG|Ltd|PLC|LLP|LLC|Inc|Corp|"
    r"Holding|Group|Groupe|International|France|UK|Europe|Global)\b",
    re.IGNORECASE,
)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _normalize(name: str) -> str:
    """Strip accents, legal suffixes, extra whitespace. Uppercase."""
    name = _strip_accents(name)
    name = _LEGAL_SUFFIXES.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.upper()


def _crunchbase_slug(name: str) -> str:
    s = _strip_accents(name.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def resolve(
    company_name: str,
    country_hint: str = "",
    llm_provider=None,
) -> CompanyIdentifiers:
    """
    Resolve company name to per-source identifiers.
    If llm_provider given, uses LLM one-shot for best quality.
    Always falls back to normalization if LLM fails or unavailable.
    """
    ids = CompanyIdentifiers(commercial_name=company_name)
    ids.country_hint = country_hint.upper()

    # Try LLM resolution first
    if llm_provider:
        try:
            ids = _llm_resolve(company_name, country_hint, llm_provider, ids)
            if ids.infogreffe_query:
                return ids
        except Exception:
            pass

    # Normalization fallback
    normalized = _normalize(company_name)
    raw_stripped = _strip_accents(company_name)

    ids.legal_suffixes_stripped = normalized.title()
    ids.infogreffe_query = normalized          # Infogreffe: uppercase, no accents
    ids.companies_house_query = normalized.title()
    ids.handelsregister_query = company_name   # keep original for DE
    ids.crunchbase_slug = _crunchbase_slug(company_name)

    # Build variant list: try all plausible forms
    ids.variants = list(dict.fromkeys([
        company_name,           # original
        raw_stripped,           # no accents, original case
        normalized,             # uppercase, no accents, no suffix
        normalized.title(),     # title case, no accents, no suffix
        _strip_accents(company_name).upper(),
    ]))

    return ids


def _llm_resolve(
    company_name: str,
    country_hint: str,
    llm_provider,
    ids: CompanyIdentifiers,
) -> CompanyIdentifiers:
    import json

    prompt = (
        f'Given company "{company_name}" (country hint: {country_hint or "unknown"}), '
        "return JSON with these keys:\n"
        '{"infogreffe_query": "LEGAL NAME UPPERCASE NO ACCENTS for French registry search", '
        '"companies_house_query": "name for UK Companies House search", '
        '"handelsregister_query": "name for German registry search", '
        '"crunchbase_slug": "lowercase-hyphenated-slug", '
        '"country": "2-letter ISO code or empty string"}\n'
        "Return ONLY the JSON object, no explanation."
    )

    response = llm_provider.complete(
        messages=[{"role": "user", "content": prompt}],
        model=llm_provider.resolve_model("small"),
        max_tokens=200,
    )

    raw = response.content.strip()
    # strip markdown fences if present
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    data = json.loads(raw)

    ids.infogreffe_query = data.get("infogreffe_query", "")
    ids.companies_house_query = data.get("companies_house_query", "")
    ids.handelsregister_query = data.get("handelsregister_query", company_name)
    ids.crunchbase_slug = data.get("crunchbase_slug", "")
    ids.country_hint = data.get("country", country_hint).upper()

    # Also build variants from LLM output
    ids.variants = list(dict.fromkeys(filter(None, [
        company_name,
        ids.infogreffe_query,
        _normalize(company_name),
        _strip_accents(company_name).upper(),
    ])))

    return ids
