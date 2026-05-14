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

Pipeline status semantics:
- `Research collection`: source-backed / fallback / mixed / unavailable
- `Qualitative context`: source-backed / fallback / unavailable
- `Quantitative market inputs`: available / unavailable
- `Thesis mode`: source-backed / deterministic archetype fallback / timeout fallback / generic fallback
- `Research used in valuation`: yes/no (qualitative-only context does not count as valuation input)

## Data Sourcing and Reliability

### Source hierarchy (prototype)

- Public market/financial base: `yfinance`.
- FX normalization: free live source -> cache -> static fallback.
- Company metadata: profile source -> cache -> deterministic fallback.
- Filings: SEC/official sources when available; IR fallback otherwise.
- Market context: source-backed links when available; sector fallback otherwise.

### Market-context relevance gate

Source-backed market context is now filtered by deterministic relevance scoring before use:
- each fetched item gets `relevance_score` (0-100),
- low-relevance items are rejected (default threshold: 60),
- direct company/ticker/legal-name/filing matches outrank broad sector mentions,
- generic market headlines are rejected unless tied to the company or core archetype/industry.
- query expansion is deterministic and constrained (ticker, primary/local symbol variants, archetype-aware symbol aliases).
- official filing/IR links can qualify as relevant context when they carry strategic/trend coverage.

At least **2 relevant sources** are required to keep `source_backed=true`.
Otherwise context is downgraded to fallback:

`Fallback Market Context — sector profile only; not source-backed; not used in valuation.`

CLI reports relevance coverage as:
- `Market context sources: <relevant> relevant / <fetched> fetched`

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

## Archetype Fallback System

Fallback thesis/risk/catalyst text uses deterministic company archetypes, not a single generic sector paragraph.

Examples:
- `AAPL` -> `premium_device_platform`
- `BATS.L` -> `tobacco_nicotine_cash_return`
- `NHY.OL` -> `commodity_cyclical_aluminum`

This prevents cross-sector leakage (for example software/cloud wording in Apple hardware fallback or platform-policy wording in tobacco fallback unless explicitly source-backed).

### Archetype to Market-Segment Mapping

When market-segment text is missing, known archetypes map to deterministic segment labels:
- `premium_device_platform` -> `Consumer hardware and services ecosystem`
- `tobacco_nicotine_cash_return` -> `Tobacco and nicotine products`
- `commodity_cyclical_aluminum` -> `Aluminum, recycling, and low-carbon metals`

This prevents avoidable "missing market segment definition" penalties for known company types.

## Cyclical and Extreme-Signal Guardrails

### Cyclical guardrail

For cyclical sectors (materials/mining/energy/industrials), the pipeline now tracks a `cyclical_review_required` flag.
If normalized/mid-cycle support is weak or unavailable, output is explicitly cautionary and conviction is capped:

`Cyclical review required — valuation may reflect current-cycle margins, not mid-cycle earnings.`

For cyclical companies, large upside calls (above +50%) are capped unless mid-cycle support is corroborated.
`mid_cycle_ebitda`/normalization proxy fields are carried as placeholders in pipeline diagnostics until richer mid-cycle datasets are integrated.

### Mature-company extreme-signal guardrail

For mature public companies, extreme signals trigger `extreme_signal_review`:
- upside > +75%, or
- downside < -60%.

Corroboration anchors include deterministic checks such as:
- source-backed quantitative market inputs,
- analyst forward revenue/EBITDA support,
- peer purity (`>=75%`),
- method dispersion (`<2.0x`),
- clean normalization status (`OK`),
- non-fallback market context,
- company-specific catalyst evidence within 12 months,
- cyclical mid-cycle normalization support (for commodity/cyclical names).

If corroboration is insufficient, final recommendation is capped (typically `HOLD / LOW CONVICTION`, or `INCONCLUSIVE` if other integrity warnings are active), while raw model signal is still shown separately.

## Raw Signal vs Final Recommendation

Gold Roger always separates:
- **Raw valuation signal** (model output direction), and
- **Final recommendation** (after confidence, plausibility, and integrity guardrails).

Example:
- raw signal: positive valuation signal
- final recommendation: `HOLD / LOW CONVICTION`
- reason: extreme-signal plausibility cap + missing corroboration anchors

### How To Interpret Capped Recommendations

When `extreme_signal_review` caps a call:
- displayed model value/range is **diagnostic**, not directly actionable,
- headline output is explicitly labeled as capped pending corroboration,
- recommendation should be treated as a watchlist/review state until missing anchors are resolved (for example quantified market inputs or mid-cycle support).

## Status Semantics (Trust Signals)

Pipeline status now separates sourcing trust dimensions:
- `Filings`: `source-backed` / `fallback` / `unavailable`
- `Market context`: `source-backed` / `fallback` / `unavailable`
- `Quantitative market inputs`: `available` / `unavailable`
- `Thesis mode`: `source-backed` / `deterministic archetype fallback` / `timeout fallback` / `generic fallback`
- `Valuation inputs`: `none — valuation gated` / `market data only` / `market data + verified quantitative context`

This avoids overloading one aggregate research label when filings and market-context quality differ.

## Report Modes

- `--quick`: deterministic screen, bounded runtime, no long narrative.
- default (no flag): standard concise report.
- `--full-report`: extended thesis + scenarios + catalysts + source appendix sections.

### ReportWriter timeout behavior

ReportWriter is executed under a hard wall-clock timeout per mode.
On timeout, Gold Roger returns an immediate structured archetype fallback thesis.
Timeout fallback is explicitly labeled in pipeline status (`Thesis mode: timeout fallback`).

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

## Private Company Valuation Pipeline — Current Behavior

This mechanism already exists in the current codebase and is used by `--type private`.

### 1) Entrypoint and routing
- CLI entrypoint: `goldroger/cli.py`
- Orchestration entrypoint: `goldroger/orchestrator.py`
- Main private flow: `goldroger/pipelines/equity.py` inside `run_analysis(..., company_type="private")`

### 2) Source selection and provider flow
- Private runs start with registry/provider discovery, then optional additional providers:
  - SIREN path (FR) when provided.
  - Name-based resolver + `DEFAULT_REGISTRY.fetch_by_name(...)`.
  - Source selection from `goldroger/data/source_selector.py`:
    - `--sources auto` (default): country-relevant + global non-premium providers
    - explicit `--sources ...`
    - `--interactive` for manual provider prompts and optional manual revenue override.
- Provider orchestration registry: `goldroger/data/registry.py`
  - country-priority routing table (`FR`, `GB`, `DE`, `NL`, `ES`, `US`) before global fallbacks.

### 3) Country/provider behavior (prototype)
- FR: `pappers` (verified revenue when key present), `infogreffe` (sector/identity, no revenue)
- GB: `companies_house` (identity + filings metadata, revenue best-effort)
- DE: `handelsregister` (best-effort revenue extraction)
- NL: `kvk` (identity/sector, no revenue)
- ES: `registro_mercantil` (existence/registry context, no revenue)
- US: `sec_edgar` (filer/XBRL revenue where available)
- Global supplemental: `crunchbase` (estimated), `triangulation` fallback

### 4) Revenue discovery and deterministic merge
- Private revenue candidate merge is deterministic (`goldroger/data/private_quality.py`):
  - weighted by source reliability and confidence,
  - verified cohorts preferred,
  - outlier trimming applied,
  - merge notes emitted.
- If providers still have no revenue, deterministic triangulation can run (`goldroger/data/private_triangulation.py`).

### 5) Confidence/provenance and suppression
- Revenue confidence states are explicit (`verified` / `estimated` / `inferred` / unavailable).
- `sources.md` records provenance for key fields and merge notes.
- Private pipeline now surfaces:
  - `Private Revenue Status`
  - `Private Identity Resolution`
  - `private_triangulation_used` (pipeline status)
- Conservative behavior:
  - no revenue or low-confidence inferred/triangulated revenue -> `SCREEN_ONLY` + `INCONCLUSIVE`
  - weak identity resolution -> `SCREEN_ONLY` + `INCONCLUSIVE`
  - only verified/high-confidence revenue with resolved identity can be `VALUATION_GRADE`
  - manual revenue can unlock only `INDICATIVE_MANUAL_REVENUE` with explicit confidence caps

### 5b) Private status semantics

Private runs use private-specific trust labels in `Pipeline status`:
- `Identity`: `RESOLVED_STRONG` / `RESOLVED_WEAK` / `UNRESOLVED`
- `Revenue`: `VERIFIED` / `HIGH_CONFIDENCE_ESTIMATE` / `LOW_CONFIDENCE_ESTIMATE` / `UNAVAILABLE`
- `Financials`: `VERIFIED` / `ESTIMATED` / `UNAVAILABLE`
- `Private peers`: `OK` / `WEAK` / `FAILED`
- `Private valuation mode`: `VALUATION_GRADE` / `INDICATIVE_MANUAL_REVENUE` / `SCREEN_ONLY` / `FAILED`

Private state-machine states:
- `IDENTITY_UNRESOLVED`
- `IDENTITY_RESOLVED_STRONG_NO_REVENUE`
- `IDENTITY_RESOLVED_WEAK_NO_REVENUE`
- `SCREEN_ONLY`
- `VALUATION_READY_VERIFIED_REVENUE`
- `VALUATION_READY_MANUAL_REVENUE`
- `VALUATION_FAILED`

When `SCREEN_ONLY` is active:
- recommendation is forced to `INCONCLUSIVE`,
- target/upside stay `N/A`,
- football-field scenarios are suppressed,
- LBO feasibility is treated as diagnostic and not rendered as investable output,
- qualitative peers can still be shown for context (`qualitative peer only`, 0% weight; reference-only, not used in valuation math),
- key financial lines are shown as non-valuation-grade when necessary,
- Value Sources mark unavailable lines as `not available — excluded from valuation` (not model output).

Identity-gate policy:
- `RESOLVED_STRONG` + verified/high-confidence/manual revenue can unlock valuation.
- `RESOLVED_WEAK` + verified/manual revenue can unlock only low-conviction valuation.
- `UNRESOLVED` + manual revenue can unlock `INDICATIVE_MANUAL_REVENUE` only (not valuation-grade), with explicit legal-identity warning and capped confidence.
- `UNRESOLVED` without manual revenue remains `SCREEN_ONLY`.

Private qualitative peer buckets now prefer context-specific taxonomy when available, for example:
- healthtech: `healthtech_platform`, `healthcare_software`, `digital_health`, `healthcare_services_adjacent`
- HR tech: `hrtech_saas`, `hcm_payroll`
- fintech: `fintech_digital_bank`, `fintech_payments`, `fintech_consumer_lending`, `fintech_brokerage`, `fintech_crypto_platform`, `fintech_infrastructure`

### 6) Valuation path and labels
- Valuation math remains deterministic (same engine, private weights are sector/type-aware).
- Private recommendation taxonomy is used (not public BUY/HOLD/SELL labels):
  - `ATTRACTIVE ENTRY`
  - `CONDITIONAL GO`
  - `SELECTIVE BUY`
  - `FULL PRICE`
  - `INCONCLUSIVE` when integrity is insufficient
- If confidence is weak, private labels are explicitly marked `LOW CONVICTION`.
- LLM or triangulated revenue estimates are never treated as verified valuation-grade inputs.

### 7) Exports and provenance
- Excel/PPT exports still run on degraded private cases without crashing.
- `sources.md` is the primary trust artifact for demo/review: it must show whether revenue is verified, estimated, inferred, or unavailable.

## Private Company Validation

### Lightweight regression harness (tests)

```bash
uv run pytest -q tests/test_private_company_validation.py
```

This suite checks that:
- missing/unverified private revenue cannot produce high-conviction recommendations,
- triangulated revenue is clearly tagged and capped,
- private labels remain private-label taxonomy (not public BUY/HOLD/SELL),
- provider merge/outlier behavior is visible in provenance (`sources.md`),
- unresolved private identity yields low-conviction or inconclusive outcomes.

### Optional CLI validation script

```bash
uv run python scripts/validate_private_companies.py
uv run python scripts/validate_private_companies.py --json
```

Target basket:
- Doctolib, Sézane, Alan, Contentsquare
- Revolut, Monzo, Gymshark
- Personio, Picnic, Glovo

The script is designed to degrade gracefully when provider credentials are missing. It flags trust issues (for example uncapped weak-confidence recommendations), not normal source sparsity.

### Private smoke commands

```bash
uv run python -m goldroger.cli --company "Doctolib" --type private --quick
uv run python -m goldroger.cli --company "Revolut" --type private --quick
uv run python -m goldroger.cli --company "Personio" --type private --quick
```

### Source inspection

```bash
uv run python -m goldroger.cli --list-sources
uv run python -m goldroger.cli --list-sources --type private --country-hint FR
uv run python -m goldroger.cli --list-sources --type private --country-hint GB
```

### Manual revenue override (private prototype unlock)

Use this only when you have a defensible internal estimate and want an indicative valuation:

```bash
uv run python -m goldroger.cli \
  --company "Personio" \
  --type private \
  --country-hint DE \
  --manual-revenue 300 \
  --manual-revenue-currency EUR \
  --manual-revenue-year 2025 \
  --manual-revenue-source-note "prototype user estimate"
```

If legal identity remains unresolved, you can explicitly force a prototype run:

```bash
uv run python -m goldroger.cli \
  --company "Personio" \
  --type private \
  --country-hint DE \
  --manual-revenue 300 \
  --manual-revenue-currency EUR \
  --manual-identity-confirmed
```

Manual-input behavior:
- Revenue is tagged `manual user-provided, unverified`.
- Pipeline status shows `Private valuation mode: INDICATIVE_MANUAL_REVENUE`.
- Confidence is capped; output remains indicative and non-client-ready.

### Typical outcomes (private prototype)

- **Doctolib (FR) without Pappers/manual revenue**
  - typically `RESOLVED_WEAK` + revenue unavailable -> `SCREEN_ONLY`, `INCONCLUSIVE`.
- **Revolut (GB) with Companies House identity but no revenue extraction**
  - typically `RESOLVED_STRONG` + revenue unavailable -> `SCREEN_ONLY`, `INCONCLUSIVE`.
- **Personio (DE) without legal identifier/revenue**
  - typically `UNRESOLVED` + revenue unavailable -> `SCREEN_ONLY`, `INCONCLUSIVE`.
- **Personio (DE) with manual revenue**
  - can unlock `INDICATIVE_MANUAL_REVENUE` with explicit identity/revenue caveats and capped confidence.

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

## Known Private-Company Limitations

- Private coverage quality is country/provider dependent; many registries return sector/identity but no revenue.
- Some private runs rely on estimated or triangulated revenue; these outputs are indicative and conviction-capped.
- Identity confidence can be weak for same-name entities without a strong legal identifier in context.
- Private triangulation uses heuristic signals and should not be treated as filing-grade financial truth.
- Provider schema differences and stale filings can create dispersion in private value ranges.
- Private outputs are suitable for prototype screening and prioritization, not client-ready valuation opinions without analyst verification.
- FR: without `PAPPERS_API_KEY`, verified revenue is commonly unavailable for SAS entities; runs may remain `SCREEN_ONLY`.
- GB: Companies House provides strong identity/filing metadata but revenue extraction is best-effort and can remain unavailable.
- DE: Handelsregister identity/revenue coverage can be sparse; unresolved identity should be treated as `SCREEN_ONLY` until a legal identifier or verified revenue is provided.

### Private Troubleshooting

- Why is recommendation `INCONCLUSIVE`?
  - Check `Private valuation mode` and `Screen-only reasons` in pipeline status.
  - Most common causes: unresolved legal identity or unavailable verified revenue.
- Why is revenue `N/A`?
  - Provider coverage can be identity-only for many private entities/countries.
  - Add a stronger provider (`PAPPERS_API_KEY`, Companies House accounts extraction, etc.) or use manual override for prototype-only runs.
- Why are peers shown but valuation skipped?
  - Peer tables can still be shown for directional context, but valuation remains blocked until identity/revenue gates pass.
- How do I unlock valuation in private mode?
  - Provide a legal identifier (SIREN/company number) and verified/high-confidence revenue, or use manual revenue flags for prototype runs with explicit confidence caps.

## Public Validation Examples

- `AAPL`: expected `USD/USD`, normalization `OK`, adjacent-reference peer set, low-conviction recommendation.
- `BATS.L`: expected `GBP/GBP`, normalization `OK`, no same-issuer `BTI` peer inclusion, tobacco archetype context.
- `NHY.OL`: expected `NOK/NOK`, normalization `OK`, aluminum/cyclical caution and recommendation cap when normalization support is weak.

## Additional Docs

- Usage guide: [HowToUse.md](HowToUse.md)
- Validation benchmarks: [docs/VALIDATION.md](docs/VALIDATION.md)
- Engineering notes and milestone history: [docs/engineering_notes.md](docs/engineering_notes.md)
- Repository workflow/guardrails: [AGENTS.md](AGENTS.md)
