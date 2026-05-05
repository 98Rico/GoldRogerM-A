# NEXT STEPS — GOLD ROGER

---

## PRODUCT VISION

**What this is**: An M&A analysis platform for funds and advisors.
Two modes:
1. **Company analysis** — enter any company (public or private), get a full memo: valuation, football field, scenarios, IC score, investment thesis, slide deck, Excel model.
2. **Deal sourcing** — enter a client investment brief, get a shortlist of real target companies with scoring, rationale, and preliminary valuation.

**Core rule (non-negotiable)**: LLMs produce language, never numbers. Every financial figure — revenue, margin, EV, multiple — must originate from a verified data source or a clearly-tagged deterministic estimate.

**LLM strategy**: Mistral by default (free). Switchable via `--llm` or `LLM_PROVIDER`. Architecture is already LLM-agnostic — no code change to switch providers.

**Data strategy**: Free tier must produce excellent analysis. Architecture is pluggable — Bloomberg, CapIQ, Refinitiv can be connected per client without touching core logic.

**UI**: CLI + `--interactive` + explicit CLI source selection (`--sources`, `--list-sources`) for now. Web interface (Next.js + FastAPI) once data layer is solid.

---

## WHAT WORKS TODAY

| Capability | Status | Notes |
|-----------|--------|-------|
| Full per-field provenance tracking | ✅ Done | `sources.md` logs all inputs — revenue, margins, beta, WACC, growth, EV — with source + confidence; WACC now correctly tagged `verified` when CAPM used |
| EV/EBITDA comps anchor determinism | ✅ Done | `ValuationService` now locks comps mid to live `market_data.ev_ebitda_market`; peer ranges only affect spread (capped ±25%) |
| Data quality gate (public + private) | ✅ Done | Deterministic `data_quality` score/tier with blockers/warnings now returned in analysis output and logged to `sources.md` |
| UI credential setup for missing sources | ✅ Done | API + UI now expose keyed providers and allow runtime key entry with optional `.env` persistence |
| Mandatory company confirmation flow | ✅ Done | UI and CLI now require explicit company confirmation before non-pipeline runs, with a “None of these companies” option, country-hint support, and GB Companies House candidate listing when key is set |
| CLI env/key loading parity | ✅ Done | CLI now loads `.env` at startup, and Companies House is correctly marked as key-required in source status |
| GB confirmation fallback when CH API auth fails | ✅ Done | CLI now falls back to public Companies House search HTML and still shows candidate company numbers |
| Entity identity guardrail in pipeline/thesis | ✅ Done | Confirmed company identifier now flows into analysis, GB private lookups can resolve by company number, and thesis is constrained against similarly named-company hallucinations |
| GB Companies House metadata enrichment | ✅ Done | Pipeline now ingests SIC details, active directors/officers counts, incorporation date, and recent filing-history metadata for fundamentals + thesis grounding |
| GB filing-history document ingestion | ✅ Done | Companies House provider now reads paginated filing history (bounded), indexes document metadata formats, and logs filing/document counts into analysis provenance |
| GB statement-of-capital extraction | ✅ Done | Incorporation filing PDFs are now parsed for share class, total shares, aggregate nominal value, unpaid capital, and rights summary; surfaced in metadata and `sources.md` |
| GB strict thesis grounding mode | ✅ Done | For confirmed GB entities with limited verified detail, thesis output now avoids named competitor/product/TAM speculation and uses registry facts as hard baseline |
| Strict JSON retry compatibility fix | ✅ Done | `DataCollectorAgent.run()` now accepts strict-retry kwargs, preventing crash on retry path during transient API failures |
| Public company valuation (DCF + Comps + LBO) | ✅ Solid | yfinance, CAPM WACC, sector multiples |
| Private company valuation — high-growth | ✅ Improved | DCF 20% / Comps 35% / Tx 45% weights |
| Sector-calibrated growth + margin fallbacks | ✅ Done | `get_sector_rev_growth` / `get_sector_ebitda_margin` |
| HealthTech sector multiples | ✅ Done | EV/Rev 6–20x, WACC 11.5%, growth 22% |
| IC scoring — pure numeric, no sector imports | ✅ Done | Caller passes ev_rev_sector_mid/high |
| LBO growth-equity detection | ✅ Done | EV/Revenue > 12x OR EV/EBITDA > 25x (config-driven) |
| Interactive data source selector | ✅ Done | `--interactive` — Y/N per provider, manual revenue override |
| Non-interactive source selector | ✅ Done | `--sources auto/all/name1,name2` + `--list-sources`; missing credentials auto-skip |
| Private revenue quality merge | ✅ Done | confidence-weighted provider merge + deterministic outlier rejection |
| Private recommendation labels | ✅ Done | ATTRACTIVE ENTRY / CONDITIONAL GO / SELECTIVE BUY / FULL PRICE |
| Scenario weights match engine weights | ✅ Done | `run_scenarios(weights=result.weights_used)` |
| Deterministic assumption guardrail | ✅ Done | WACC / terminal growth no longer sourced from LLM by default |
| LLM standalone revenue fallback removed | ✅ Done | no separate “guess revenue JSON” call |
| Transaction comp acceptance hardening | ✅ Done | stricter EV/year/source filters before cache inclusion |
| Thread-safe rate limiter | ✅ Done | `threading.Lock` — no more 429 races |
| JSON repair (Mistral free tier) | ✅ Done | Trailing commas, None literals, truncated output recovery |
| Wikipedia revenue signal | ✅ Done | Signal 5 in private triangulation |
| Hallucination firewall | ✅ Done | No-revenue path blocks any figure generation in thesis agent |
| EU registries (FR, UK, DE, ES, NL) | ✅ Wired | Revenue quality varies by country |
| Parallel agents | ✅ Fast | ThreadPoolExecutor, 2 workers (Mistral free tier) |
| Bear/Base/Bull scenarios + football field | ✅ Solid | Anchored to actual revenue y0, correct weights |
| PPT 10 slides | ✅ Functional | Tables only, no charts yet |
| Excel DCF | ✅ Functional | Single model, not 3-statement |
| Unit tests | ✅ | Finance engines + agents + providers + scoring |

---

## WHAT IS BROKEN OR INSUFFICIENT

| Problem | Impact | Root cause |
|---------|--------|-----------|
| Private revenue still often missing | Blocks accurate valuation | FR SAS confidentiality law; DE/ES/NL registries expose no revenue |
| Mistral free tier JSON failures | Agent output silently discarded | Token-limit truncation + non-standard JSON; repair catches most but not all |
| Transaction comps coverage still thin | Multiples can be sparse | No paid deal feed yet (Capital IQ / Refinitiv / Mergermarket) |
| PPT is text tables | Not presentable to fund clients | No charts in python-pptx |
| Excel is DCF only | Missing BS + CF | Not a real 3-statement model |
| SEC EDGAR match quality variable | US private coverage uneven | `fetch_by_name()` depends on filing-name quality and aliases |
| Company identity ambiguity from free-text names | Can fetch wrong ticker/company | Name-based resolution can return near-match symbols without user confirmation |
| IC auto-score floor ~54 for private | Requires agent data to reach BUY | Strategy/synergies neutral at 5.0 without agent intelligence |

---

## PRIORITY ROADMAP

### 🔴 PRIORITY 0 — Data quality (before anything else)

#### 0.0 — Fix EV volatility from LLM peer comps  ← **COMPLETED**

**Status**: ✅ completed. Comps anchor is now deterministic when live market EV/EBITDA exists.

**Root cause**: `PeerFinderAgent` returns EV/EBITDA multiples from LLM memory. These vary per run (e.g. 35.8x → 45.1x). At 40% blend weight on NVIDIA's $133B EBITDA, a 10-point multiple swing adds ~$1.3T to the output. The peer multiple is supposed to be anchored to `market_data.ev_ebitda_market` (live yfinance) but LLM-suggested overrides can bypass this.

**Implemented** in `ValuationService._standard_comps()`:
- When `market_data.ev_ebitda_market` is set, use it as the fixed mid; only allow ±25% range from peers
- Log the peer multiple source in `field_sources` so it's visible in `sources.md`
- Added regression test `tests/test_valuation_comps_anchor.py`: two runs with different peer ranges must keep blended EV within 5% (currently 0.00% in deterministic smoke test)

#### 0.1 — Private revenue confidence scoring refinement

**Status**: implemented and active.  
**Remaining work**: add country-specific weighting profiles and confidence calibration by sector.

#### 0.4 — Data quality gate and scorecard  ← **COMPLETED**

**Status**: ✅ completed.

**Implemented**:
- New deterministic gate: `goldroger/data/quality_gate.py`
- Analysis output now includes `data_quality` payload: score, tier, blockers, warnings, checks
- `equity.py` logs the quality score in console and `sources.md`
- Regression tests added: `tests/test_quality_gate.py`

**Current policy**:
- Missing revenue triggers a blocker (`is_blocked=true`) and limited-confidence warning mode
- Public checks include market data presence, market cap, live EV/EBITDA, beta
- Private checks include provider-record availability + confidence level weighting

#### 0.5 — UI/API credential entry for all keyed providers  ← **COMPLETED**

**Status**: ✅ completed.

**Implemented**:
- `GET /data-sources` returns provider capabilities/status (including missing-key status)
- `POST /settings/credentials` accepts provider env vars and values, applies at runtime
- Optional persistence to `.env` (`persist_to_env_file=true`)
- `/ui` now includes a credentials panel to enter/update keys directly

#### 0.6 — Mandatory company confirmation before analysis  ← **COMPLETED**

**Status**: ✅ completed.

**Implemented**:
- New endpoint: `GET /resolve-company?query=...&company_type=...` (returns suggested matches)
- Non-pipeline analysis now requires explicit confirmation (`confirmed_company=true`)
- UI always shows a confirmation step before run
- CLI now prompts confirmation before non-pipeline run
- CLI private confirmation now prompts for country hint and shows source/context columns
- UI and CLI include explicit **“None of these companies”** option to stop and refine safely

#### 0.2 — Crunchbase activation

**Status**: implemented, tested. Set `CRUNCHBASE_API_KEY` in `.env` to activate.
**Note**: Crunchbase removed free tier in 2024 — enterprise subscription required.
Activating it immediately covers most VC-backed private companies globally.

#### 0.3 — Companies House XBRL revenue improvement

**Status**: SIC/sector fetched. Revenue parsing is best-effort; many filings return None.
**Fix**: improve XBRL namespace handling for iXBRL inline filings (post-2020 format).

---

### 🟠 PRIORITY 1 — Transaction comps (real deal data)

**Current**: EV/Revenue multiples come from LLM M&A research — anecdotal, unverified.

**Problem**: a fund building a deal thesis needs multiples from *comparable recent transactions*, not LLM-recalled headlines. A single outlier deal (e.g. Veeva at 25x) can dominate the blended valuation.

**Needed**: a local deal cache populated from structured sources:

```
data/
  transaction_comps.json    ← local cache, updated per run
  providers/
    mergermarket.py         ← stub (requires subscription)
    press_releases.py       ← free: scrape PR Newswire + BusinessWire M&A announcements
```

**Agent behavior refinement**:
1. `TransactionCompsAgent` outputs structured JSON: `{acquirer, target, sector, ev_usd_m, revenue_usd_m, ev_rev_multiple, date}`
2. Cache deduplicated by target name + year
3. Validation: strict EV/multiple bounds + recency window + source-quality threshold
4. `ValuationService` uses median of validated cache, not raw LLM suggestion

---

### 🟠 PRIORITY 2 — Output polish (client-ready)

M&A fund clients expect outputs that look like they came from a bank.

#### 2.1 — PPT: real charts

| Slide | Current | Target |
|-------|---------|--------|
| Football field | Text table | Horizontal bar chart (bear/base/bull ranges per method) |
| DCF | Text | Waterfall: FCF bars + terminal value |
| Peer comps | Text table | Scatter: EV/EBITDA vs EBITDA margin |
| IC Score | Text table | Radar/spider chart (6 dimensions) |

All implementable with `python-pptx` chart API.

#### 2.2 — Excel: 3-statement model

Current: DCF tab only.
Target: 7-tab model:

| Tab | Content |
|-----|---------|
| `Assumptions` | Revenue CAGR, margins, WACC, exit multiple — all editable |
| `P&L` | 5-year projected income statement |
| `Balance Sheet` | Simplified |
| `Cash Flow` | Operating + investing + financing |
| `DCF` | FCF waterfall → EV → equity value |
| `LBO` | Entry/exit, debt schedule, IRR/MOIC |
| `Scenarios` | Bear/Base/Bull sensitivity matrix |

#### 2.3 — Executive summary slide

First slide: company name, recommendation, EV range, 3 key bullets, IC score badge. One page — the slide a partner reads before the full deck.

---

### 🟡 PRIORITY 3 — Deal sourcing (investment brief → target shortlist)

**Current state**: `run_pipeline()` exists but uses LLM-hallucinated company names.

**Target state**:
1. **Real company sourcing**: Crunchbase + EU registries + web search — actual companies matching brief criteria
2. **Scoring against brief**: sector fit, size fit, geography, growth profile, deal complexity, estimated EV
3. **Output**: shortlist PPT (one slide per target: overview, financials, IC score, next step)
4. **Validation**: every target verifiable via at least one data source

**New command**: `goldroger source --brief "SaaS, €50–200M EBITDA, Southern Europe" --n 10`

---

### 🟢 PRIORITY 4 — Premium data connectors

Architecture already has `DataRegistry` + `DataProvider` ABC. Adding a new source = one file in `data/providers/`.

**Priority connectors**:

| Source | Covers | Status |
|--------|--------|--------|
| **Crunchbase Pro** | Global private, VC-backed | Stub active — key required |
| **Bloomberg** | Global, all assets | Stub ready — BDP API not yet implemented |
| **Capital IQ** | Global private + transactions | Stub ready — API not yet implemented |
| **Refinitiv / LSEG** | Global M&A transactions | Not stubbed — best source for real deal comps |
| **Companies House** | 🇬🇧 UK revenue improvement | Active — XBRL parsing needs work |

**Connector SDK**: `data/providers/TEMPLATE.py` already exists. A new provider can be wired in under 2 hours.

---

### 🔵 PRIORITY 5 — Web interface

**When**: after Priority 0 + 1 (data is trusted).
**Stack**: Next.js frontend + existing FastAPI backend (`api.py`).

**MVP screens**:
1. Search bar → analysis in progress → memo page with PPT/Excel download
2. Deal sourcing form → target shortlist with scores
3. Data source config (connect Pappers, Bloomberg, etc.)

---

## DATA PROVIDER STATE

| Source | Country | Revenue | Auth | Status |
|--------|---------|---------|------|--------|
| **yfinance** | Global | ✅ Verified (public) | None | Active |
| **SEC EDGAR** | 🇺🇸 | ✅ 10-K XBRL (ticker + name) | None | Active |
| **Crunchbase** | Global | ⚠️ Range estimate | Enterprise key | Active if key set |
| **recherche-entreprises** | 🇫🇷 | ❌ Sector only | None | Active |
| **Pappers** | 🇫🇷 | ✅ RNCS verified | ~€30/mo | Active if key set |
| **Companies House** | 🇬🇧 | ⚠️ Best-effort XBRL | Free key | Active — parsing improvement needed |
| **Handelsregister** | 🇩🇪 | ⚠️ Best-effort HTML | None | Active |
| **BORME** | 🇪🇸 | ❌ Existence only | None | Active |
| **KVK** | 🇳🇱 | ❌ Sector only | Free key | Active if key set |
| **Bloomberg** | Global | ✅ Everything | License | Stub ready |
| **Capital IQ** | Global | ✅ Everything + deals | License | Stub ready |
| **Refinitiv** | Global | ✅ M&A transactions | License | Not yet stubbed |

---

## REFACTORING STATUS

| Phase | Description | Status |
|-------|-------------|--------|
| R1 | Delete ~600 lines dead code | ✅ Done |
| R2 | Split orchestrator → `pipelines/` | ✅ Done |
| R3 | Centralise config → `goldroger/config.py` | ✅ Done |
| R4 | Split `models/__init__.py` → domain files | ✅ Done |
| R5 | Tests 20 → 55+ (agents, providers, scoring, json_parser) | ✅ Done |
| R6 | Scoring decoupled from sector data | ✅ Done |
| R7 | Valuation weights sector-aware + weight propagation | ✅ Done |
| R8 | SEC EDGAR `fetch_by_name()` + Companies House XBRL improvement | 🟨 In progress |

---

## COMPLETED PHASES

<details>
<summary>Click to expand</summary>

| Phase | Item | Status |
|-------|------|--------|
| 1 | yfinance fetcher, sector multiples, WACC CAPM, DCF, ValuationService | ✅ |
| 2 | Forward estimates, P/E path (banks), LBO engine, SOTP framework | ✅ |
| 3 | `run_ma_analysis()`, `run_pipeline()`, IC scoring 6-dim | ✅ |
| 4 | Cache TTL, structured logging | ✅ |
| 5 | DataRegistry + provider layer, Crunchbase, peer comps, scenarios, PPT, LBO/DCF fixes | ✅ |
| 6 | Auto output folder, `--quick` flag, pipeline retry, speed optimisations | ✅ |
| 7 | LLM-agnostic (Mistral/Anthropic/OpenAI), EU registries (FR/UK/DE), private triangulation | ✅ |
| 8 | No-placeholder policy, optional LLM deps, name resolver, revenue fallback chain | ✅ |
| 9 | KVK 🇳🇱, Registro Mercantil 🇪🇸, fuzzy matching, SOTP auto-detect, scenario narratives | ✅ |
| 10 | Target price fix, mega-cap tx exclusion, revenue lock, confidence tagging | ✅ |
| 11 | DCF NWC fix, LBO revenue fix, scenarios anchor, aggregator normalisation | ✅ |
| 12 | EU registry audit — dead APIs removed, auth gating corrected | ✅ |
| 13 | `--siren` CLI flag, SourcesLog, `sources.md` output | ✅ |
| 14 | Parallel agents (ThreadPoolExecutor), timing output | ✅ |
| 15 | Hallucination firewall (no-revenue path), peer ticker verification, JSON repair for Mistral | ✅ |
| 16 | Thread-safe rate limiter, Wikipedia revenue signal, `if market_data and revenue_ttm` bug fix | ✅ |
| 17 | HealthTech sector (EV/Rev 6–20x, WACC 11.5%, growth 22%), sector alias expansion | ✅ |
| 18 | Valuation weight reform — `compute_valuation_weights()`, private high-growth DCF 20/35/45 | ✅ |
| 19 | Sector-calibrated growth + EBITDA margin fallbacks (`get_sector_rev_growth` / `get_sector_ebitda_margin`) | ✅ |
| 20 | IC scoring decoupled — `ev_rev_sector_mid/high` from caller, `_financial_score` + `_lbo_score` helpers | ✅ |
| 21 | LBO growth-equity detection — EV/EBITDA > 25x threshold (config-driven) | ✅ |
| 22 | Private recommendation labels — ATTRACTIVE ENTRY / CONDITIONAL GO / SELECTIVE BUY / FULL PRICE | ✅ |
| 23 | Interactive data source selector — `--interactive`, country-filtered, credential-aware, manual revenue | ✅ |
| 24 | Scenario weights propagation — `run_scenarios(weights=result.weights_used)`, display weights corrected | ✅ |
| 25 | CLI source control (`--sources`, `--list-sources`) + auto-skip missing credentials + deterministic WACC/TG guardrail | ✅ |
| 26 | Deterministic private revenue merge + stricter transaction comp acceptance + tests | ✅ |
| 27 | Full per-field provenance — `sources.md` logs all financial inputs with source + confidence; `SourcesLog.add_once()` deduplication; WACC correctly tagged `verified` when CAPM used | ✅ |
| 28 | CLI footnote provenance — rendered `(S#)` markers for displayed values + source legend (source/confidence/url) | ✅ |
| 29 | Valuation credibility hardening — market-context quality penalties, verified forward growth precedence, zero-weight method hiding, mega-cap tech peer-range gate, standardized CLI formatting | ✅ |
| 30 | IC-grade credibility pass — skip tx comps for mega-caps, deterministic mega-cap peer fallback, EV→equity bridge provenance, mega-cap growth normalization, base football-field reconciliation | ✅ |
| 31 | Tier-1 credibility blockers — time-aware catalyst guardrail, 90-cap on estimated/proxy quality, peer premium/discount interpretation notes, recommendation conviction guardrails | ✅ |
| 32 | Real-comps enforcement — removed market-anchor ±25%, switched to peer P25/Median/P75 comps logic, tightened stale catalyst rewriting and low-conviction signaling | ✅ |
| 33 | Temporal + comps quality hardening — 6-month recency policy, historical labeling for stale events, mandatory mega-cap peer seeding, winsorized peer dispersion, fair-value range output, football-field low/mid/high cleanup | ✅ |
| 34 | Integrity guardrails — enforce mega-cap minimum peer policy (no sector-table fallback), scenario-based fair-value range containment, peer-level trace outputs, high-dispersion uncertainty flag | ✅ |
| 35 | JSON + valuation reliability patch — strict JSON failure logging, safe strict-retry for all agents, mega-cap comps fallback removal, minimum-3 peer low-confidence mode, DCF-only naming, public mega-cap quality cap when comps missing | ✅ |
| 36 | Peer-quality calibration pass — tiered Apple-class peer policy, similarity-weighted comps with low-similarity filtering, mega-cap terminal-growth guardrail (3.0–3.5%), weak-peer quality downgrades, and peer-policy traceability in sources | ✅ |
| 37 | Dynamic peer engine + graceful comps degradation — removed hardcoded peer fallback list, introduced staged dynamic peer search, economics-based similarity ranking/filtering, partial comps weighting (10/25/40%), DCF miscalibration sanity flag, and reduced-confidence banner when peers are expanded | ✅ |
| 38 | Reliability regression hotfix — fail-fast 60s agent timeouts, fatal JSON parsing for market analysis, explicit `INCONCLUSIVE` failed-valuation state (no peers + DCF sanity fail), non-contradictory comps messaging, and stronger quality penalties for weak/missing peers | ✅ |
| 39 | Temporal + fail-fast stabilization — stale-year query sanitization, 30s hard timeouts for market/peer agents, pipeline status reporting, `INCONCLUSIVE` target suppression, strict market-failure data consistency, and cleaner concise output formatting | ✅ |
| 40 | Quick-mode performance hardening — skip deep market analysis in `--quick`, cap web-search query budgets, reduce retries/tool rounds, add report-writer timeout fallback, and expose per-stage timing diagnostics in final output | ✅ |
| 41 | Quick-pipeline architecture hardening — deterministic quick peer screening (no LLM peer discovery), quick-mode JSON retry suppression, explicit stage-status semantics (`OK/SKIPPED_QUICK_MODE/TIMEOUT/FAILED/DEGRADED`), stricter INCONCLUSIVE rendering suppression, and `--debug` gated diagnostics | ✅ |
| 42 | Shared-core stabilization pass — fixed peer-scale filter path and thesis runtime bug, added financial-stage timeout + best-effort cancellation, ensured deterministic peers survive full-mode enrichment failures, expanded pipeline statuses (`core_valuation` / `research_enrichment` / `thesis`), and aligned quick-mode quality/tier semantics | ✅ |

</details>
