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
- Trading comps are deterministic when market comps exist: if `market_data.ev_ebitda_market` is available, it is the fixed EV/EBITDA mid-anchor; peer inputs can only adjust spread (capped at ±25%).

### Data Quality Gate

Before valuation, Gold Roger now computes a deterministic data quality report:
- `score` (0-100), `tier` (A/B/C/D), `blockers`, `warnings`, `checks`
- Included in `AnalysisResult.data_quality`
- Logged in `sources.md` as `Data Quality Score`

Blocking policy:
- Missing revenue triggers a blocker and limited-confidence mode warning
- Public checks include: market data, market cap, beta, live EV/EBITDA
- Private checks include: provider record presence and confidence level

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

**FX rates**: EUR/GBP/CHF/CAD fetched live via yfinance (`EURUSD=X` etc.) with hardcoded fallback.

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
| Public equity (NVIDIA) | ~5–10 min |
| Private company (Sézane) | ~1–2 min |
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
