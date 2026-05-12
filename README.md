# Gold Roger — Institutional Valuation Engine

## Architecture

### Overview

```
CLI / FastAPI
     │
     ▼
orchestrator.py  ←── single entry point
     │
     ├── 1. DATA LAYER
     │       ├── yfinance (public: price, beta, margins, EV, forward estimates)
     │       ├── Pappers  (🇫🇷 private: revenue, filings — PAPPERS_API_KEY ~€30/mo)
     │       ├── recherche-entreprises.api.gouv.fr (🇫🇷 free: SIREN, NAF sector)
     │       ├── Companies House (🇬🇧 free with key: SIC, XBRL revenue)
     │       ├── Handelsregister (🇩🇪 free: revenue best-effort HTML)
     │       ├── Registro Mercantil / BORME (🇪🇸 free: existence only)
     │       ├── KVK (🇳🇱 free with key: SBI/sector)
     │       ├── SEC EDGAR (🇺🇸 free: annual revenue 10-K — ticker + name lookup)
     │       ├── Private Triangulation (Wikipedia NLP + DuckDuckGo + headcount signals)
     │       ├── Crunchbase (freemium: funding, headcount — key required)
     │       └── Bloomberg / Capital IQ / Refinitiv (premium — stubs ready)
     │
     ├── 2. LLM LAYER  (qualitative only — never produces valuation numbers)
     │       ├── Step 1 [sequential]: Fundamentals
     │       ├── Steps 2+3+4 [parallel — ThreadPoolExecutor]:
     │       │       Market Analysis · Peer Finder · Financial Modeler
     │       ├── Step 4b [parallel]: Transaction Comps
     │       ├── Step 5 [sequential]: Valuation Assumptions
     │       └── Step 6 [sequential]: Thesis / Narrative
     │
     ├── 3. VALUATION ENGINE  (pure Python, deterministic)
     │       ├── Path A Standard  → DCF + EV/EBITDA comps + EV/Revenue tx comps
     │       ├── Path B Financial → P/E + P/B  (banks, insurers, asset managers)
     │       └── Path C SOTP      → segments × sector multiple
     │           Weights: sector-aware (private high-growth: DCF 20% / Comps 35% / Tx 45%)
     │           Inputs: CAPM WACC, forward estimates, real peer multiples
     │           Outputs: Bear/Base/Bull × {DCF, comps, blended} + sensitivity 5×5
     │
     └── 4. EXPORT LAYER
             ├── PowerPoint 10 slides (Title, Overview, Market, Financials,
             │   Valuation, Football Field, Peer Comps, IC Score, Thesis, Risks)
             ├── Excel (Dashboard, DCF, Sensitivity, Comparables, Financials)
             └── Markdown sources.md  (data room — full traceability)
```

### Key Modules

| Module | File | Role |
|--------|------|------|
| Orchestrator | `orchestrator.py` | 3-mode coordination |
| LLM Agents | `agents/specialists.py` | 12 specialized agents |
| Valuation Engine | `finance/core/valuation_service.py` | DCF + comps + LBO + weight routing |
| Weight Router | `finance/core/valuation_service.py` | `compute_valuation_weights()` — sector/type-aware |
| DCF engine | `finance/valuation/dcf.py` | FCFF + terminal value |
| LBO engine | `finance/valuation/lbo.py` | IRR + MOIC |
| Scenarios | `finance/core/scenarios.py` | Bear/Base/Bull (uses engine weights) |
| Market Fetcher | `data/fetcher.py` | yfinance + 1h cache |
| Peer Comparables | `data/comparables.py` | Real peer multiples via yfinance |
| Sector Multiples | `data/sector_multiples.py` | 25 sectors — EV/EBITDA, EV/Rev, WACC, growth rates |
| IC Scoring | `ma/scoring.py` | 6 dimensions, pure numeric (no sector imports) |
| Private Triangulation | `data/private_triangulation.py` | 5-signal revenue estimator |
| Source Selector | `data/source_selector.py` | Interactive + CLI source picker |
| Excel Exporter | `exporters/excel.py` | DCF workbook |
| PPT Exporter | `exporters/pptx.py` | 10-slide deck |

### Core Rule

> **The LLM never produces valuation numbers.**
> Source hierarchy for EV, WACC, multiples:
> `Bloomberg/CapIQ > yfinance/SEC EDGAR > EU Registries > Crunchbase > Triangulation`
> Every source tagged `[verified]` / `[estimated]` / `[inferred]` in outputs.

In addition:
- Numeric **valuation assumptions** (e.g. WACC, terminal growth) are **not taken from LLM output** by default.
- They come from verified market data when available, otherwise from `data/sector_multiples.py`.
- If you want to override them, use `--interactive` (manual user override is explicitly tagged and then allowed).
- Trading comps are deterministic when market comps exist, with peer-quality and confidence guardrails applied before final blending.

### Data Quality Gate

Before valuation, Gold Roger now computes a deterministic data quality report:
- `score` (0-100), `tier` (A/B/C/D), `blockers`, `warnings`, `checks`
- Included in `AnalysisResult.data_quality`
- Logged in `sources.md` as `Data Quality Score`

Blocking policy:
- Missing revenue triggers a blocker and limited-confidence mode warning
- Public checks include: market data, market cap, beta, live EV/EBITDA
- Private checks include: provider record presence and confidence level

### Public-Listing Normalization & Safety Gates

Before public-company valuation is allowed, the pipeline runs a normalization audit:
- quote currency vs financial-statement currency
- market-cap currency
- listing/share basis classification
- depositary-receipt status and ratio (if known)
- FX conversion path and confidence

Status examples in output:
- `OK`
- `OK_FX_NORMALIZED`
- `FAILED` (valuation suppressed)

When normalization or sanity checks fail:
- recommendation is forced to `INCONCLUSIVE`
- target/upside are suppressed (`N/A`)
- scenario/football-field output is suppressed when integrity checks fail
- output is explicitly marked as diagnostic/screen-only

Scenario integrity guard:
- low/base/high ordering is enforced for DCF, comps, and blended scenario outputs.
- if any ordering check fails, the scenario section is suppressed and flagged.

### What the tool produces

| Mode | Command | Outputs |
|------|---------|---------|
| **Equity** (public) | `--company NVIDIA` | Implied EV, target price, BUY/HOLD/SELL, football field, PPT+Excel |
| **Equity** (private) | `--company Doctolib --type private` | EV range, CONDITIONAL GO / SELECTIVE BUY / FULL PRICE, PPT+Excel |
| **Equity** (interactive) | `--company Doctolib --type private --interactive` | Same + terminal prompt to choose data sources + manual revenue override |
| **Equity** (SIREN) | `--siren 804398073 --type private` | Same + verified revenue from Pappers/Infogreffe |
| **M&A** | `--mode ma --company Target --acquirer Buyer` | Deal sourcing, strategic fit, DD red flags, LBO, IC score |
| **Pipeline** | `--mode pipeline --buyer LVMH --focus "skincare DTC"` | Screened target list with IC score per target |

---

## Commands

All exports (PPT + Excel) auto-save to a timestamped subfolder:
`outputs/<Company>_<YYYYMMDD_HHMMSS>/`

### Public company

```bash
uv run python -m goldroger.cli --company "NVIDIA" --excel --pptx
```

### Public company — quick screen mode

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --quick
```

Quick mode is deterministic-first and optimized for speed:
- skips deep market analysis and transaction comps
- emphasizes core valuation + peer diagnostics
- clearly labels low-confidence outputs as indicative

### Private company — standard

```bash
uv run python -m goldroger.cli --company "Doctolib" --type private --excel --pptx
```

Default private behavior uses `--sources auto`:
- country-relevant free sources + keyed sources if credentials are available
- premium stubs (`bloomberg`, `capitaliq`) excluded from auto mode
- missing credentials are skipped (no hard failure)
- provider revenues are merged with confidence-weighted scoring + outlier rejection
- manual revenue input (interactive mode) always overrides provider estimates

### Private company — explicit source selection (non-interactive)

```bash
uv run python -m goldroger.cli \
  --company "Doctolib" \
  --type private \
  --sources "infogreffe,pappers,sec_edgar,crunchbase" \
  --excel --pptx
```

Source modes:
- `--sources auto` (default for private)
- `--sources all`
- `--sources "name1,name2,..."`

List all sources + current credential status:

```bash
uv run python -m goldroger.cli --list-sources
```

### Private company — interactive data source selection

```bash
uv run python -m goldroger.cli --company "Doctolib" --type private --interactive
```

Prompts Y/N for each applicable registry (country-filtered) and global data sources.
Allows manual revenue entry as a final override. Useful when:
- Automated registry returns no revenue (French SAS confidentiality)
- You have a reliable internal revenue estimate
- You want to compare Bloomberg vs. triangulation results

### Private company — direct SIREN lookup

```bash
uv run python -m goldroger.cli --company "Sézane" --siren 804398073 --type private --excel --pptx
```

SIREN bypasses name resolution — calls Pappers/Infogreffe directly.
Output includes `sources.md` — data room with every metric traced.

### Pipeline sourcing

```bash
# Standard (with web search, ~5 min)
uv run python -m goldroger.cli \
  --mode pipeline \
  --buyer "Carlyle Group" \
  --focus "European B2B SaaS, ARR €5M–€50M, founder-led" \
  --pptx

# Fast for demos (no web search, ~1 min)
uv run python -m goldroger.cli \
  --mode pipeline \
  --buyer "Carlyle Group" \
  --focus "European B2B SaaS, ARR €5M–€50M, founder-led" \
  --pptx --quick
```

### Other examples

```bash
# Bank (P/E + P/B path, automatic)
uv run python -m goldroger.cli --company "JPM" --excel --pptx

# M&A (specific acquirer → target)
uv run python -m goldroger.cli --company "Figma" --mode ma --acquirer "Adobe" --pptx
```

---

## Valuation Logic

### Sector-aware blend weights

Weights are computed by `compute_valuation_weights()` — not hardcoded:

| Company type | Sector growth | DCF | Trading Comps | Tx Comps | Rationale |
|---|---|---|---|---|---|
| Public | Any | 50% | 30% | 20% | Market price anchors DCF; transactions secondary |
| Private | < 12% CAGR | 50% | 30% | 20% | Standard; DCF reliable for stable businesses |
| Private | > 12% CAGR | 20% | 35% | 45% | No public beta → WACC estimated; precedent M&A most informative |
| Financial | Any | 0% | 60% | 40% | P/E + P/B path; DCF not applicable |
| Mega-cap (>$500B) | Any | 60% | 40% | 0% | No acquirer at this scale; transactions excluded |

High-growth private sectors (sector rev CAGR > 12%): SaaS, HealthTech, Biotech, e-commerce.

### Sector-calibrated fallbacks

When live data is unavailable, fallbacks use sector benchmarks from `sector_multiples.py`, not global constants:

| Field | Old fallback | New fallback |
|---|---|---|
| Revenue growth | 8% (hardcoded) | `get_sector_rev_growth(sector)` — e.g. 22% for HealthTech |
| EBITDA margin | 20% (hardcoded) | `get_sector_ebitda_margin(sector)` — e.g. 15% for HealthTech |

### Private company recommendation labels

Based on blended EV/Revenue vs sector benchmark — no live price needed:

| EV/Revenue vs sector | Label |
|---|---|
| ≤ sector_mid × 0.80 | **ATTRACTIVE ENTRY** |
| ≤ sector_mid × 1.25 | **CONDITIONAL GO** |
| ≤ sector_high × 0.90 | **SELECTIVE BUY** |
| > sector_high × 0.90 | **FULL PRICE** |

### IC Scoring architecture

`scoring.py` is pure numeric — it accepts pre-resolved inputs, imports no sector data:

```python
auto_score_from_valuation(
    lbo_output=result.lbo,
    blended_ev=2400.0,
    revenue=380.0,
    ebitda_margin=0.20,
    ev_rev_sector_mid=12.0,   # resolved by equity.py, passed in
    ev_rev_sector_high=20.0,  # resolved by equity.py, passed in
)
```

LBO growth-equity thresholds (`growth_equity_ev_rev: 12.0`, `growth_equity_ev_ebitda: 25.0`) live in `config.py`.

---

## Data Sources

| Source | Country | Revenue | Auth |
|--------|---------|---------|------|
| **yfinance** | Global | ✅ Verified (public) | None |
| **SEC EDGAR** | 🇺🇸 | ✅ 10-K XBRL (ticker + name lookup) | None |
| **Pappers** | 🇫🇷 | ✅ RNCS verified | ~€30/mo |
| **recherche-entreprises** | 🇫🇷 | ❌ Sector only | None |
| **Companies House** | 🇬🇧 | ⚠️ Best-effort XBRL | Free key |
| **Handelsregister** | 🇩🇪 | ⚠️ Best-effort HTML | None |
| **BORME** | 🇪🇸 | ❌ Existence only | None |
| **KVK** | 🇳🇱 | ❌ Sector only | Free key |
| **Crunchbase** | Global | ⚠️ Range estimate | Enterprise key |
| **Private Triangulation** | Any | ⚠️ Multi-signal estimate | None |
| **Bloomberg** | Global | ✅ Everything | License — stub ready |
| **Capital IQ** | Global | ✅ Everything + deals | License — stub ready |

### Credential Setup in UI/API

The API/UI now support entering missing provider keys directly:
- `GET /data-sources` → provider status/capabilities
- `POST /settings/credentials` → set keyed provider env vars at runtime
- Optional persistence to `.env` via `persist_to_env_file=true`

The `/ui` page includes a **Data Source Credentials** panel for this workflow.

CLI note:
- CLI now loads `.env` at startup, so provider keys in `.env` are available during confirmation and sourcing checks.

### Company Confirmation (Always-On)

For non-pipeline workflows, both UI and CLI now enforce an explicit company confirmation step:
- `GET /resolve-company?query=...&company_type=...` returns suggested matches
- User must confirm one suggestion before analysis runs
- User can choose **None of these companies** to stop and refine input
- CLI private flow asks for optional country hint (FR/GB/DE/NL/ES/US) and shows source/context in the confirmation table
- For `GB` private confirmation, CLI now lists Companies House search candidates (with company number) when `COMPANIES_HOUSE_API_KEY` is configured
- If the Companies House API key is rejected or missing, CLI falls back to public Companies House search and still shows candidate company numbers for confirmation
- Confirmed company identifiers (e.g., Companies House company number) now flow into pipeline context to reduce same-name confusion in downstream narrative agents
- For GB entities, Companies House metadata enrichment now includes SIC details, active director/officer counts, incorporation date, and recent filing-history summaries to ground fundamentals and thesis text
- GB provider now paginates through filing history (bounded), indexes document metadata/resources across filings, and surfaces filing/document coverage counts in `sources.md`
- GB incorporation PDFs are now parsed (best effort) for statement-of-capital fields (share class, total shares, nominal value, unpaid capital, rights summary), and these are logged in provenance
- For confirmed GB private entities with limited verified facts, thesis generation now runs in strict registry mode: no named competitor/product/TAM speculation unless explicitly verified in context
- Agent retry path hardened: `DataCollectorAgent` now supports strict JSON retry mode kwargs to avoid crashes during transient provider/API instability

API guardrail:
- `POST /analyze` rejects non-pipeline requests unless `confirmed_company=true`

**FX/normalization note**:
- cross-currency public valuation uses a deterministic normalization audit first.
- when FX conversion is required, the engine records source/confidence in output (for example `static_fx_table`, low confidence).
- low-confidence FX/share-basis states are surfaced and can suppress recommendation.

### Interactive source selector (`--interactive`)

When running a private company analysis with `--interactive`, the CLI prompts:

```
Data Source Selection — Doctolib
  Detected country: FR — showing relevant registries first

  #   Provider                Coverage   Status
  1   Infogreffe (FR gov)     FR         free
  2   Pappers                 FR         key set ✓
  3   Companies House (UK)    GB         free
  4   Crunchbase              GLOBAL     no key — will skip
  5   Bloomberg Terminal      GLOBAL     no key — will skip

  Use Infogreffe (FR gov) (default Y)? [Y/n]
  Use Pappers (key available)? [Y/n]
  ...
  Enter revenue manually in USD millions (leave blank to skip):
```

Manual revenue entry overrides all provider data and is tagged `[verified — manual]`.

For non-interactive runs, use `--sources`; providers without credentials are automatically skipped.

### Private data accuracy guardrails

- Revenue is merged deterministically from selected providers (`private_quality.py`), not chosen from one random source.
- Low-quality outliers are automatically dropped before valuation.
- If no provider has revenue, a deterministic triangulation fallback is used (multi-signal estimate, tagged confidence).
- LLM no longer runs a standalone “guess revenue JSON” fallback step.

---

## LLM Providers

Runs on **Mistral (free)** by default — no credit card required. Switch with one flag:

| Provider | Cost | Command |
|----------|------|---------|
| **Mistral** (default) | Free | _(default)_ |
| **Anthropic** | Paid | `uv add --group anthropic anthropic` then `--llm claude` |
| **OpenAI** | Paid | `uv add --group openai openai` then `--llm openai` |

```bash
# Via .env (persistent)
LLM_PROVIDER=mistral

# Via CLI (one run)
uv run python -m goldroger.cli --company "NVIDIA" --llm claude
```

Rate limiting: 3s minimum gap between LLM calls (Mistral free tier). Thread-safe lock prevents race conditions under parallel execution.

---

## Tests

```bash
uv run python -m pytest tests/ -v
```

Covers: WACC CAPM, DCF forward projections, LBO IRR/MOIC/feasibility, Bear/Base/Bull scenarios, IC scoring gates and thresholds, sector multiple resolution, JSON repair.

---

## Adding a Data Provider

Implement `DataProvider`:

```python
from goldroger.data.providers.base import DataProvider
from goldroger.data.fetcher import MarketData

class MyProvider(DataProvider):
    name = "my_source"

    def is_available(self) -> bool:
        return bool(os.getenv("MY_API_KEY"))

    def fetch_by_name(self, company_name: str) -> MarketData | None:
        ...
```

Register at the top of the priority stack:
```python
from goldroger.data.registry import DEFAULT_REGISTRY
DEFAULT_REGISTRY.register(MyProvider())
```

---

## Performance

Typical run times (after parallelisation):

| Scenario | Estimated time |
|----------|---------------|
| Public equity `--quick` (deterministic-first) | ~10–35s |
| Public equity full mode (with enrichment attempts) | ~35–90s |
| Private company (source-dependent) | ~1–3 min |
| Pipeline sourcing `--quick` | ~1–2 min |
| Pipeline sourcing standard | ~4–6 min |

Agents with web search (capped at 3 rounds): Fundamentals, Market Analysis, FinancialModeler (private), PeerFinder, TransactionComps.
Agents without web search (direct response): ValuationAssumptions, ReportWriter.

---

## Definition of Done

✔ 0 CLI crashes
✔ Verified financial data (yfinance) for all public companies
✔ CAPM WACC on real data
✔ DCF + LBO stable and defensible
✔ Bear/Base/Bull football field
✔ Real peer comparables (not hardcoded sector table)
✔ IC scoring derived from agents (not neutral 5.0/10)
✔ BUY/HOLD/SELL reliable vs market cap
✔ M&A pipeline complete (sourcing → IC scoring)
✔ PPT 10 slides institutional
✔ Excel + PPT exports reliable
✔ Cache + production logging
✔ Pluggable data architecture (Bloomberg/CapIQ stubs ready)
✔ Crunchbase integrated (freemium, private companies)
✔ 20+ unit tests on valuation engine
✔ LLM-agnostic (Mistral free default, Anthropic/OpenAI optional)
✔ EU registries for private companies (FR, UK, DE, ES, NL)
✔ Name Resolver — correct legal identifiers per source
✔ No placeholder values — honest N/A over fabricated data
✔ Football field for private companies (revenue fallback chain)
✔ SOTP auto-detect for conglomerates
✔ Scenario narratives Bear/Base/Bull
✔ Live FX rates via yfinance
✔ Target price (per-share) separate from Implied EV
✔ Mega-cap: tx comps excluded (weight 0) for MCap >$500B
✔ Revenue lock in thesis agent — no cross-section contradictions
✔ DCF NWC year-1 correct — `base_revenue` anchors NWC delta to actual year-0 revenue
✔ LBO revenue corrected — `entry_ebitda / ebitda_margin`
✔ Scenarios correctly anchored — all 3 scenarios share same y0, only growth rate differs
✔ Aggregator robust — weights auto-normalised, `blended = mid`
✔ Sector multiples word-boundary regex — no sector mismatch
✔ Infogreffe migrated — `recherche-entreprises.api.gouv.fr` (official FR gov, always up)
✔ Pappers integrated — RNCS-verified revenue for French private companies
✔ Peer scale constraint — revenue bracket ×0.25–×4, no mega-cap as SME comparable
✔ Confidence tagging — `[verified]` / `[estimated]` visible in CLI and exported
✔ Full per-field provenance — `sources.md` logs every input (revenue, margins, beta, WACC, growth, EV) with source and confidence; WACC tagged `verified` when CAPM/real beta used, `inferred` for sector default; deduplication via `SourcesLog.add_once()`
✔ HealthTech sector — dedicated multiples (EV/Rev 6–20x, WACC 11.5%, growth 22%)
✔ Sector-calibrated revenue growth fallback (22% HealthTech, not generic 8%)
✔ Sector-calibrated EBITDA margin fallback (sector benchmark, not generic 20%)
✔ Private high-growth weights — DCF 20% / Comps 35% / Tx 45% (sectors with rev CAGR > 12%)
✔ Scenario engine uses actual blend weights — not hardcoded 50/30/20
✔ ValuationMethod display weights reflect actual engine weights
✔ IC scoring decoupled from sector data — `ev_rev_sector_mid/high` passed from caller
✔ LBO growth-equity detection — EV/EBITDA > 25x check (config-driven)
✔ Private recommendation labels — ATTRACTIVE ENTRY / CONDITIONAL GO / SELECTIVE BUY / FULL PRICE
✔ Interactive data source selector (`--interactive`) — country-filtered, credential-aware, manual override
✔ Thread-safe rate limiter — `threading.Lock` prevents 429s under parallel execution
✔ JSON repair for Mistral free tier — trailing commas, None literals, truncated output recovery
✔ Wikipedia revenue signal — NLP signal 5 in private triangulation
✔ Hallucination firewall — no-revenue path in ReportWriterAgent blocks any financial figure generation
✔ EV/EBITDA comps anchor determinism — live market EV/EBITDA locks comps mid; peer ranges only adjust spread (±25% cap), with provenance in `sources.md`
✔ Data quality gate — deterministic score/tier/blockers before valuation, included in result payload and `sources.md`
✔ UI/API credential management — keyed providers can be configured from UI and persisted to `.env`
✔ Mandatory company confirmation — explicit pre-run company selection with a “None of these companies” path
✔ CLI value footnotes — every displayed KPI/valuation value now includes a `(S#)` marker with a source/confidence legend (and URL when available)
✔ Market-context-aware quality scoring — missing TAM/market growth/segment/trend depth now penalizes confidence score
✔ Verified growth precedence — public Key Financials now prefer `yfinance` forward growth when available
✔ Zero-weight valuation cleanup — methods with 0% weight are hidden from the valuation table
✔ Mega-cap tech peer-quality gate — low EV/EBITDA peer bands (e.g. 8x–12x) are rejected for Apple-class companies
✔ Standardized CLI financial formatting — consistent `%`, `$M/$B/$T`, and readable valuation cells
✔ Mega-cap tx-comps skip — transaction comps are no longer executed when tx weight is forced to 0%
✔ EV→Equity bridge transparency — valuation output now logs blended EV, equity value, share count, and implied target price provenance
✔ Mega-cap growth normalization — forward growth is normalized for mature mega-caps with multi-stage fade
✔ Forward growth provenance clarity — distinguishes analyst revenue estimate vs earnings-growth proxy in source tags
✔ Football-field base reconciliation — base scenario now reconciles with current DCF/comps/blended outputs
✔ Time-aware catalyst guardrail — stale “upcoming” catalysts are rewritten as recent-event context
✔ Quality score credibility cap — any estimated/proxy input caps data quality at 90
✔ Comps interpretation layer — output now states implied premium/discount vs peer median with rationale hooks
✔ Recommendation guardrails — high method dispersion or resilient fundamentals can cap SELL to HOLD
✔ Real-peer comps first — removed market-anchor ±25%; comps now use peer P25/Median/P75 when available
✔ Conviction scaling — weak-comps/high-dispersion cases are surfaced as low-conviction recommendations
✔ Catalyst freshness policy — upcoming=future only, recent=<=6 months, older items rewritten as historical context
✔ Fair-value presentation — output now shows fair value range + point estimate
✔ Football-field cleanup — DCF/comps/blended low-mid-high rows are now shown consistently
✔ Peer robustness — mega-cap tech runs with mandatory platform peers and small-set safeguards
✔ Mega-cap comps policy hardening — no sector-table multiples when peer set is insufficient; comps disabled and confidence reduced instead
✔ Range integrity — fair value range is scenario-based and always includes point estimate
✔ Peer transparency — per-peer EV/EBITDA and similarity hints are surfaced in runtime output
✔ Uncertainty signaling — DCF vs comps dispersion >2x is explicitly flagged as high-dispersion / low-confidence
✔ JSON reliability hardening — strict JSON retry path now logs invalid raw snippets and always injects strict-json hints
✔ Agent strict-json compatibility — retry no longer crashes on agents without `_strict_json` kwargs support
✔ Mega-cap comps minimum policy — target 5–7 peers, minimum 3 peers allowed with explicit low-confidence signaling
✔ No mega-cap sector-multiple fallback — rejected/missing peer ranges no longer silently degrade to sector-table comps
✔ Valuation mode naming integrity — 100% DCF runs are labeled `DCF-only Valuation` (not blended)
✔ Public mega-cap quality cap with missing comps — quality score capped at 75 when usable trading comps are unavailable
✔ Tiered Apple-class peers — core platform peers prioritized (MSFT/GOOGL/META), adjacent peers included (AMZN/AVGO), low-fit OEM/legacy-semi names de-prioritized
✔ Similarity-weighted comps — peers are filtered by minimum similarity and weighted in EV/EBITDA central tendency and dispersion
✔ Mega-cap terminal-growth guardrail — terminal growth is constrained to a realistic 3.0–3.5% band with rationale notes
✔ Peer-policy transparency — output now logs peer selection policy and per-peer similarity scores
✔ Weak-peer scoring penalty — mega-cap analyses with weak but usable peer sets are downgraded to lower-confidence quality tiers
✔ Dynamic peer discovery — staged search flow (industry → sector+size → adjacent industries → global similar models), no hardcoded ticker fallback list
✔ Similarity-driven peer ranking — peer selection now uses explicit weighted economic similarity (industry/sector/model/size/margin/growth)
✔ Graceful comps degradation — public mega-cap runs now keep partial comps weights (10% / 25% / 40%) instead of hard disabling when peer count is low
✔ DCF sanity guardrail — >40% implied downside with stable margins is flagged as likely DCF miscalibration
✔ Limitation banner for expanded peers — runs with non-core expanded peer sets now surface a reduced-confidence warning
✔ Fail-fast agent guardrails — critical parallel agents now timeout at 60s to prevent 20+ minute hangs
✔ Fatal JSON mode for critical analysis — market-analysis JSON double-failure now raises hard failure instead of silently degrading
✔ Explicit failed valuation state — when peers are unavailable and DCF sanity fails, recommendation is forced to `INCONCLUSIVE`
✔ No silent contradiction messaging — comps-upside interpretation is suppressed when comps are unavailable/zero-weight
✔ Data-quality reliability penalties — public quality scoring now penalizes missing/weak peers and market-analysis failures
✔ Current-year query guardrails — market-search prompts now enforce latest/current-year scope and sanitize stale 2023/2024 query anchors
✔ Hard agent timeout caps — market analysis and peer comps now fail fast at 30s (instead of long blocking runs)
✔ Pipeline failure status block — final output now reports Market/Peers/Valuation status plus final recommendation state
✔ Failure-safe target suppression — `INCONCLUSIVE` runs hide target/fair-value presentation and avoid false precision
✔ Market-data consistency guardrail — when market analysis fails, market size/growth fields are forced to `Not available` (no contradictory inferred values)
✔ Cleaner final presentation — shorter thesis output and less noisy contradictory messaging in failed/low-confidence runs
✔ Quick-mode speed lane — `--quick` now skips deep market analysis and enforces bounded agent behavior
✔ Bounded tool-search budgets — per-agent query deduplication + caps (`max_queries`, `max_results`) to prevent runaway web-search loops
✔ Retry throttling in quick mode — reduced LLM retries/tool rounds for faster degraded output instead of long retry chains
✔ End-to-end latency controls — explicit 30s caps for market/peer stages and 20s cap for report writer
✔ Timing diagnostics in output — final report now prints per-stage timings and total runtime for regression tracking
✔ Real quick-mode pipeline split — `--quick` now runs a bounded fast path and skips deep market research/TAM workflows
✔ Deterministic quick peers — quick mode now bypasses LLM peer discovery and uses a deterministic sector/industry universe screen
✔ Quick-mode retry suppression — JSON retry loops are curtailed in quick mode to avoid long degraded stalls
✔ Pipeline status semantics — final status now uses explicit `OK/SKIPPED_QUICK_MODE/TIMEOUT/FAILED/DEGRADED` stage outcomes
✔ Failure-safe rendering — `INCONCLUSIVE` outputs suppress target/bridge/fair-value style leakage in final display
✔ Debug-gated diagnostics — noisy raw parse diagnostics and deep peer traces are now behind `--debug`
✔ Shared quick/full valuation core hardening — full mode now starts from deterministic core peers and keeps them even when enrichment fails
✔ Timeout + cancellation tightening — added financial-stage timeout and best-effort future cancellation on timed-out parallel stages
✔ Peer-scale filter fix — tiny out-of-scale peers are now dropped before comps aggregation for mega-cap targets
✔ Pipeline status expansion — output now includes `core_valuation`, `research_enrichment`, and `thesis` stage states
✔ Quick-mode scoring semantics refinement — quick-mode market-context skip applies light penalties instead of failure-like penalties
✔ Quality tier calibration update — tiers are now `A:80+ / B:65+ / C:50+ / D:<50`
✔ Peer-composition hygiene — peer engine now classifies business-model buckets and caps semiconductor overrepresentation for non-semi targets
✔ Similarity-weighted comps refinement — bucket-adjusted similarity weights now drive comps central tendency, with stable small-set median behavior
✔ Peer auditability — CLI now prints a peer table with bucket, market cap, EV/EBITDA, similarity, weight, and include reason
✔ Full-mode enrichment semantics — market analysis can now be marked `DEGRADED` when TAM/growth context is missing
✔ Quick/full thesis fallback overhaul — quick mode now uses deterministic short thesis, and full timeout fallback uses structured thesis/risks/catalysts
✔ DCF conservative guardrail — mega-cap tech DCF now flags low implied exit multiples vs peer floor and can shift blend weight from DCF to comps
✔ Quick-mode LLM hard-skip for slow paths — `--quick` now skips transaction comps entirely and avoids deep financial-model LLM calls when no verified revenue feed is available
✔ Quick-mode crash-proof financial fallback — network/API failures in financial modeling now degrade to deterministic fallback instead of breaking the run
✔ Wide fair-value-range confidence flag — scenario range width >75% of midpoint is now surfaced explicitly and degrades valuation confidence
✔ Apple-like archetype refinement — added `consumer_hardware_ecosystem` classification and controlled peer-relaxation order to avoid over-filtered 2-peer runs
✔ Valuation-peer integrity — peers without EV/EBITDA are marked `qualitative peer only`, excluded from valuation peer minimums, and forced to zero valuation weight
✔ Peer role transparency — runtime peer table now includes explicit role labels (`core valuation`, `adjacent valuation`, `qualitative only`)
✔ Comps-weight guardrails by usable peer count — mega-cap blend now follows strict peer-count buckets (1–2 peers: ~10% comps; 3–4: ~20–25%; 5–7: ~35–40%; 8+: up to 50%)
✔ Live-vs-applied multiple check — valuation notes now report live EV/EBITDA vs applied peer EV/EBITDA premium/discount and label low-confidence references
✔ DCF diagnostics expansion — valuation notes now include terminal value share and projected FCF CAGR, and low implied exit multiples (<12x for mega-cap tech) trigger conservative flags
✔ Unavailable market-context provenance — missing full-mode TAM/market-growth now logs `market_analysis_unavailable` instead of generic inferred tags
✔ Canonical valuation consistency in thesis — thesis now prepends a canonical fair-value/point-estimate reference from the same valuation object used in the headline
✔ Canonical range hardening — thesis fair-value range and point estimate are now regex-normalized to the canonical valuation object to prevent headline/thesis mismatch
✔ Mega-cap degraded-comps guardrail — comps weight is capped at 35% when peer confidence is low or when pre-blend DCF/comps dispersion exceeds 2.0x
✔ Peer-bucket integrity fix — removed stale `semiconductors_infrastructure` dependency in peer-quality scoring; split semiconductor buckets now evaluate correctly
✔ Source-confidence semantics expansion — `sources.md` now renders explicit `unavailable` and `skipped` confidence states with dedicated legend entries
✔ Regression coverage for this pass — added tests for Cisco networking bucket classification and high-dispersion comps-weight capping behavior
✔ Apple archetype strengthening — added `premium_device_platform` profile to better represent hardware+services ecosystem valuation behavior
✔ Hardware/ecosystem peer broadening — deterministic peer discovery now adds consumer-electronics and premium device/platform category expansions for Apple-like mega-caps
✔ Lower semiconductor influence for Apple-like targets — aggregate semiconductor + semiconductor-equipment bucket cap reduced to 20%
✔ Comps provenance clarity — trading-comps method now references `EV/EBITDA (peer applied)` from `weighted_validated_peers`
✔ DCF status surfacing — valuation now logs explicit `DCF Status` (`normal` vs `conservative / degraded`) for downstream pipeline confidence display
✔ Stronger model-signal separation — downside worse than -30% now maps to `SELL / NEGATIVE VALUATION SIGNAL` while final recommendation can remain capped by confidence guardrails
✔ Stale product-cycle phrasing guardrail — thesis/catalyst sanitizer now rewrites version-locked labels (`iPhone N`, `iOS N`, `macOS N`) into current-cycle generic wording when unsourced
✔ Scale-aware mega-cap peer floor — valuation peers for mega-cap targets now require `max($100B, 5% of target market cap)`; subscale names are qualitative-only
✔ Split peer similarity dimensions — peer diagnostics now track both `business_similarity` and `scale_similarity` (not only blended similarity)
✔ Effective peer diversification control — added `effective_peer_count` and weak-diversification guardrails that cap comps influence (down to 15% in weak quick-mode sets)
✔ Tiny-peer dominance prevention — small niche peers can no longer carry valuation weights in Apple-like mega-cap runs even when business tags match
✔ Valuation reliability surfacing — CLI header now prints valuation reliability and effective peer count alongside DCF status/model signal
✔ Additional regressions — tests now cover mega-cap floor qualitative-only behavior and aggressive comps-cap behavior under low effective peer count
✔ Currency/share normalization audit for public listings — explicit `OK` / `OK_FX_NORMALIZED` / `FAILED` states
✔ Sanity-breaker suppression — failed normalization/extreme-signal checks force `INCONCLUSIVE` and suppress target/upside
✔ Scenario ordering integrity checks — low/base/high invariant enforced; invalid football fields are suppressed
✔ FX transparency in output — FX source/confidence and conversion notes rendered in pipeline status and bridge context
✔ Diagnostic-vs-official separation — when valuation is suppressed, output is explicitly marked as diagnostic/screen-only
