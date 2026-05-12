# Gold Roger — Prototype Valuation Screening Engine

Gold Roger is a **prototype** valuation screening tool for public and private companies.

It is designed to:
- produce deterministic valuation diagnostics (DCF/comps/blends) from sourced data,
- explicitly label confidence and provenance,
- and suppress recommendations when data integrity checks fail.

It is **not** a finished institutional platform and is **not investment advice**.

## Current Reliability Status

Gold Roger is currently a prototype valuation screening engine. It is designed to surface useful valuation diagnostics and suppress recommendations when data integrity is insufficient.

Current maturity:
- Public US large caps: usable for indicative screening.
- Public European primary listings: improving; normalization and source packs are implemented, but currency/share-basis edge cases still require review.
- ADR/OTC foreign listings: diagnostic only unless share basis is verified.
- Private companies: provider architecture exists, but coverage depends heavily on country/provider availability.
- Client-ready Excel/PPT: export layer exists, but final outputs require human review.

## What It Does

- Public/private equity screening from a single CLI.
- Deterministic valuation engine with DCF, trading comps, and optional reference methods.
- Source provenance contracts (`SourceResult`) attached to key values.
- Currency/share-basis normalization audit before trusting valuation outputs.
- Sanity-breaker suppression (`INCONCLUSIVE`, target `N/A`) when integrity checks fail.
- Filing pack and market-context pack surfaced in runtime output.
- Optional Excel/PPT exports.

Core guardrail:

> LLM components do not generate valuation numbers. Numerical valuation is computed by deterministic Python logic.

## Architecture (High Level)

```
CLI/FastAPI
   -> Orchestrator/Pipelines
      -> Data/Sourcing Layer
         -> Normalization Audit (currency/share-basis/FX)
         -> Deterministic Valuation Engine
         -> Report Writer / Exporters
```

Key modules:
- `goldroger/cli.py` - CLI entrypoint and rendering.
- `goldroger/orchestrator.py` - top-level orchestration.
- `goldroger/pipelines/equity.py` - public/private analysis pipeline.
- `goldroger/data/normalization.py` - normalization audit and gate states.
- `goldroger/data/fx.py` - FX hierarchy (live -> cache -> static fallback).
- `goldroger/data/filings.py` - filings/IR source pack + URL classification.
- `goldroger/data/market_context.py` - trends/catalysts/risks source pack.
- `goldroger/data/comparables.py` - peer sourcing, filtering, weighting.
- `goldroger/finance/core/valuation_service.py` - deterministic valuation core.
- `goldroger/finance/core/scenarios.py` - scenario framework.
- `goldroger/utils/money.py` - deterministic currency/price formatting and quote-unit normalization.

## Currency and Share-Basis Handling

Gold Roger separates:
- quote currency,
- quote unit (for example `GBp`/`GBX` pence vs `GBP` pounds),
- market-cap currency,
- financial-statement currency,
- valuation/reporting currency,
- share-count basis.

Normalization states:
- `OK` - no FX conversion required.
- `OK_FX_NORMALIZED` - cross-currency conversion applied with source/confidence.
- `FAILED` - valuation recommendation suppressed.

For London tickers, quote price may be pence while financials/market cap are pounds. Per-share comparisons are normalized before upside/downside and sanity checks.

## Research Usage (Qualitative vs Quantitative)

Gold Roger distinguishes:
- **Qualitative source-backed context** used in thesis/risk framing.
- **Quantitative source-backed assumptions** used in valuation inputs.

Qualitative market context does **not** automatically change valuation assumptions.
Valuation assumptions are only changed when explicit numeric, source-backed inputs are available.

## Data Sourcing and Reliability

### Source hierarchy (prototype)

- Public market/financial base: `yfinance`.
- FX normalization: free live source -> cache -> static fallback.
- Company metadata: profile source -> cache -> deterministic fallback.
- Filings: SEC/official sources when available; IR fallback otherwise.
- Market context: source-backed links when available; sector fallback otherwise.

### Source contracts

- `ProviderCapabilities`: coverage, freshness, confidence, limitations.
- `SourceResult`: value + metadata (`currency`, `unit`, `as_of_date`, `source_name`, `source_url`, `source_confidence`, `is_fallback`, `warning_flags`, etc.).

### Recommendation suppression

When critical integrity checks fail:
- recommendation is forced to `INCONCLUSIVE`,
- target/upside/downside are suppressed (`N/A`),
- scenario/football-field output may be suppressed or marked diagnostic.

## Foreign Listings and Alternate-Listing Peers

- Resolver prefers local-primary listing for foreign issuers where available.
- Explicit input ticker is respected, but unresolved share basis can still suppress recommendations.
- Peer validation excludes same-issuer alternate listings (for example local line vs ADR/OTC mirror) from comparable sets.

## Report Modes

- `--quick`: deterministic screen, bounded runtime, no long narrative.
- default (no flag): standard concise report.
- `--full-report`: extended thesis + scenarios + catalysts + source appendix sections.

`--quick` and `--full-report` are mutually exclusive.

## CLI Usage

### Public company (standard)

```bash
uv run python -m goldroger.cli --company "AAPL" --type public
```

### Public company quick screen

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --quick
```

### Public company full narrative report

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --full-report
```

### Private company

```bash
uv run python -m goldroger.cli --company "Doctolib" --type private
```

### Source inspection

```bash
uv run python -m goldroger.cli --list-sources
```

### Export artifacts

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --excel --pptx
```

## Testing

Run full test suite:

```bash
uv run pytest -q
```

For validation benchmarks and expected invariants, see [docs/VALIDATION.md](docs/VALIDATION.md).

## Stable / Beta / Experimental

### Stable
- Deterministic DCF/comps valuation core.
- Source provenance contracts.
- Currency/share-basis normalization audit.
- Recommendation suppression safety gates.
- Local-primary listing preference logic.
- US filing-link extraction/classification foundation.

### Beta
- European public-company filing discovery.
- Market-context sourcing and labeling.
- Thesis/scenario generation with grounding guardrails.
- Private-company registry enrichment.
- Excel/PPT export consistency across all edge cases.

### Experimental
- Transaction comps as decision input.
- SOTP in production decisioning.
- M&A sourcing pipeline.
- Premium provider stubs.
- Private triangulation-heavy fallback paths.

## Known Limitations

- `yfinance` is useful for prototype public-market data but is not institutional-grade.
- ADR/depositary share ratios may remain unresolved for some foreign listings.
- Market context may be source-backed at link/trend level without deep quantitative extraction.
- Transaction comps are reference-oriented unless source quality is explicitly validated.
- Thesis/scenario text is source-informed and still requires human review.
- Excel/PPT exports are not client-ready without analyst review.
- Static FX fallback is low confidence and should not be treated as production-grade pricing.

## Additional Docs

- Usage guide: [HowToUse.md](HowToUse.md)
- Validation benchmarks: [docs/VALIDATION.md](docs/VALIDATION.md)
- Engineering notes and milestone history: [docs/engineering_notes.md](docs/engineering_notes.md)
- Repository workflow/guardrails: [AGENTS.md](AGENTS.md)
