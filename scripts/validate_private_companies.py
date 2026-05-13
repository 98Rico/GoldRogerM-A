#!/usr/bin/env python3
"""
Private-company validation harness for Gold Roger (prototype).

Runs deterministic private-company quick screens for a fixed regression basket,
then reports trust/provenance outcomes without requiring premium providers.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass

from goldroger.cli import _parse_sources_md
from goldroger.orchestrator import run_analysis


@dataclass
class PrivateValidationRow:
    input_query: str
    country_hint: str
    resolved_name: str
    legal_identifier: str
    revenue_value: str
    revenue_currency: str
    revenue_source: str
    revenue_confidence: str
    revenue_status: str
    triangulation_used: bool
    valuation_status: str
    recommendation: str
    confidence: str
    suppressed_or_degraded: bool
    exports_safe: bool
    provenance_complete: bool
    notes: str = ""


COMPANIES: list[tuple[str, str]] = [
    ("Doctolib", "FR"),
    ("Sézane", "FR"),
    ("Alan", "FR"),
    ("Contentsquare", "FR"),
    ("Revolut", "GB"),
    ("Monzo", "GB"),
    ("Gymshark", "GB"),
    ("Personio", "DE"),
    ("Picnic", "NL"),
    ("Glovo", "ES"),
]


def _extract_currency(value: str) -> str:
    txt = str(value or "").strip()
    m = re.match(r"^([A-Z]{3})\s+", txt)
    if m:
        return m.group(1)
    if txt.startswith("$"):
        return "USD"
    return "unknown"


def summarize(company: str, country_hint: str, result) -> PrivateValidationRow:
    ps = ((result.data_quality or {}).get("pipeline_status") or {}) if isinstance(result.data_quality, dict) else {}
    src = _parse_sources_md(getattr(result, "sources_md", ""))
    rev_entry = src.get("Revenue TTM", {})
    rev_value = str(rev_entry.get("value", "") or "")
    rev_conf = str(rev_entry.get("confidence", "unknown") or "unknown").lower()
    rev_source = str(rev_entry.get("source", "unknown") or "unknown")
    rev_status = str(ps.get("private_revenue_status", rev_conf or "unknown") or "unknown").lower()
    rec = str(getattr(result.valuation, "recommendation", "") or "")
    val_status = str(ps.get("valuation", "UNKNOWN") or "UNKNOWN")
    confidence = str(ps.get("confidence", "unknown") or "unknown")
    legal_id = (
        (src.get("Company Number (GB)", {}) or {}).get("value")
        or (src.get("SIREN", {}) or {}).get("value")
        or ""
    )
    provenance_complete = bool("Data Quality Score" in src and "Private Revenue Status" in src)
    suppressed_or_degraded = bool(rec.upper().startswith("INCONCLUSIVE") or val_status in {"FAILED", "DEGRADED"})
    exports_safe = not rec.upper().startswith("INCONCLUSIVE")
    notes: list[str] = []
    if rev_status in {"estimated", "inferred", "unavailable"} and ("LOW CONVICTION" not in rec and not rec.upper().startswith("INCONCLUSIVE")):
        notes.append("weak revenue confidence without capped recommendation")
    if rec.startswith(("BUY", "SELL", "HOLD")):
        notes.append("public-style recommendation label used for private company")

    return PrivateValidationRow(
        input_query=company,
        country_hint=country_hint,
        resolved_name=str(getattr(result.fundamentals, "company_name", "") or ""),
        legal_identifier=str(legal_id),
        revenue_value=rev_value or "N/A",
        revenue_currency=_extract_currency(rev_value),
        revenue_source=rev_source,
        revenue_confidence=rev_conf,
        revenue_status=rev_status,
        triangulation_used=bool(ps.get("private_triangulation_used")),
        valuation_status=val_status,
        recommendation=rec,
        confidence=confidence,
        suppressed_or_degraded=suppressed_or_degraded,
        exports_safe=exports_safe,
        provenance_complete=provenance_complete,
        notes="; ".join(notes),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate private-company screening behavior.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    rows: list[PrivateValidationRow] = []
    for company, country in COMPANIES:
        try:
            result = run_analysis(
                company=company,
                company_type="private",
                country_hint=country,
                quick_mode=True,
                cli_mode=False,
            )
            rows.append(summarize(company, country, result))
        except Exception as exc:
            rows.append(
                PrivateValidationRow(
                    input_query=company,
                    country_hint=country,
                    resolved_name="",
                    legal_identifier="",
                    revenue_value="N/A",
                    revenue_currency="unknown",
                    revenue_source="error",
                    revenue_confidence="unknown",
                    revenue_status="unavailable",
                    triangulation_used=False,
                    valuation_status="FAILED",
                    recommendation="INCONCLUSIVE",
                    confidence="Low",
                    suppressed_or_degraded=True,
                    exports_safe=False,
                    provenance_complete=False,
                    notes=f"run_error: {exc}",
                )
            )

    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2, ensure_ascii=False))
    else:
        print("# Private Company Validation Harness")
        print()
        for r in rows:
            print(
                f"- {r.input_query} ({r.country_hint}) | rec={r.recommendation} | "
                f"rev={r.revenue_value} [{r.revenue_confidence}] | "
                f"status={r.valuation_status} | provenance={'ok' if r.provenance_complete else 'missing'}"
            )
            if r.notes:
                print(f"  notes: {r.notes}")

    # Non-zero only for trust violations, not missing premium data.
    trust_violations = [r for r in rows if r.notes and "run_error:" not in r.notes]
    return 1 if trust_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
