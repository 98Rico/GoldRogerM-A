# NEXT STEPS — GOLD ROGER

---

## PRODUCT VISION

**What this is**: An M&A analysis platform for funds and advisors.
Two modes:
1. **Company analysis** — enter any company (public or private), get a full memo: valuation, football field, scenarios, IC score, investment thesis, slide deck, Excel model.
2. **Deal sourcing** — enter a client investment brief, get a shortlist of real target companies with scoring, rationale, and preliminary valuation.

**Core rule (non-negotiable)**: LLMs produce language, never numbers. Every financial figure — revenue, margin, EV, multiple — must originate from a verified data source or a clearly-tagged deterministic estimate. N/A is not an acceptable output.

**LLM strategy**: Mistral by default (free). Switchable via `--llm` or `LLM_PROVIDER`. Architecture is already LLM-agnostic — no code change needed to switch providers.

**Data strategy**: Free tier must produce excellent analysis. Architecture is pluggable — Bloomberg, CapIQ, Refinitiv, Crunchbase Pro can be connected per client without touching core logic.

**UI**: CLI for now. Web interface (Next.js + FastAPI) once data layer is solid.

---

## WHAT WORKS TODAY

| Capability | Status | Notes |
|-----------|--------|-------|
| Public company valuation (DCF + Comps + LBO) | ✅ Solid | yfinance, CAPM WACC, sector multiples |
| M&A pipeline scoring (IC 6-dim) | ✅ Solid | Weighted scorecard |
| EU company registries (FR, UK, DE, ES, NL) | ✅ Wired | Revenue quality varies by country |
| Parallel agents (Market + Peers + Financials) | ✅ Fast | ThreadPoolExecutor, ~2–3 min/company |
| Bear/Base/Bull scenarios + football field | ✅ Solid | Anchored to actual revenue y0 |
| PPT 10 slides | ✅ Functional | Tables only, no charts yet |
| Excel DCF | ✅ Functional | Single model, not 3-statement |
| 55 unit tests | ✅ | Finance engines + agents + providers |

---

## WHAT IS BROKEN OR INSUFFICIENT

| Problem | Impact | Root cause |
|---------|--------|-----------|
| Private company revenue often missing | Blocks valuation entirely | Registries don't expose revenue (DE/ES/NL) |
| Peer comps: wrong sector or geography | Wrong multiples → wrong EV | PeerFinderAgent uses LLM names without validation |
| Transaction comps: sector average only | Overly broad multiples | No real deal database |
| LLM can inject financial figures via thesis | Silent hallucination risk | Thesis agent receives revenue lock but can still drift |
| PPT is text tables | Not presentable to fund clients | No charts in python-pptx |
| Excel is DCF only | Missing BS + CF | Not a real 3-statement model |
| N/A appears when data chain fails | Unacceptable in client output | No hard fallback policy enforced |

---

## PRIORITY ROADMAP

### 🔴 PRIORITY 0 — Data quality firewall (before anything else)

These are the trust foundation. Nothing else matters if numbers are wrong.

#### 0.1 — No-N/A policy (enforcement)

Every financial field must resolve to a value, never `None` displayed as `N/A`.

| Field | Fallback chain |
|-------|---------------|
| `revenue_current` | Registry → Crunchbase → Private triangulation → LLM-estimated (tagged) |
| `ebitda_margin` | Sector average from `sector_multiples.py` (tagged `[sector avg]`) |
| `revenue_growth` | LLM-estimated from business description (tagged `[estimated]`) |
| `ev_ebitda`, `ev_revenue` | Sector median (always available) |
| `peer_comps` | Validated yfinance peers (see 0.2) |

**Implementation**: add a `fill_gaps()` post-processing step after all agents complete — walks every required field, applies fallback chain, ensures no `None` reaches the exporter.

#### 0.2 — Peer comps validation

**Problem**: `PeerFinderAgent` returns company names from LLM. These may not exist, be in the wrong sector, or have no yfinance data.

**Fix**:
1. PeerFinderAgent outputs only ticker symbols (not names)
2. After agent call: fetch each ticker from yfinance — drop any that fail or have `sector != target sector`
3. Compute medians from validated set only
4. Minimum 3 peers required — if fewer pass validation, expand search to sector ETF constituents

#### 0.3 — LLM hallucination firewall

**Problem**: thesis agent and other text agents sometimes echo financial figures that differ from the data layer.

**Fix**:
- Expand the `verified_revenue` lock pattern (already exists for thesis agent) to ALL text-producing agents
- Add a post-processing assertion: `assert result.financials.revenue_current == pipeline_revenue` — fail loudly, not silently
- Tag all agent-originated text: strip any dollar figures from agent outputs if they contradict data layer (regex replace with `[see valuation]`)

#### 0.4 — Crunchbase (enterprise, if available)

**Status**: implemented and tested — set `CRUNCHBASE_API_KEY` in `.env` to activate.

**Note**: Crunchbase removed their free tier in 2024. The API is now enterprise-only (contact sales).
If a client already has a Crunchbase subscription, activating it immediately covers most VC-backed private companies globally.

#### 0.5 — SEC EDGAR `fetch_by_name()`

**Status**: `fetch(ticker)` works. Missing: name → CIK lookup for private US filers.

**Implementation**:
```python
# data/providers/sec_edgar.py
def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
    # POST to efts.sec.gov/LATEST/search-index?q={name}&dateRange=custom&...
    # Resolve name → CIK → call existing XBRL revenue logic
```
Covers: US private companies that file 10-K with SEC (uncommon but high-value).

---

### 🟠 PRIORITY 1 — Transaction comps (real deal data)

**Current**: EV/EBITDA multiples come from `sector_multiples.py` — static sector averages.

**Problem for M&A funds**: a fund building a deal thesis needs multiples from *comparable recent transactions*, not generic sector averages. "SaaS" could mean 8x or 25x depending on growth profile.

**Solution**: `TransactionCompsAgent` + local deal cache

```
data/
  transaction_comps.json    ← local cache, updated per run
  providers/
    mergermarket.py         ← stub (requires subscription)
    press_releases.py       ← free: scrape PR Newswire + BusinessWire M&A announcements
```

**Agent behavior**:
1. web_search: `"{sector}" acquisition deal "{year}" EV EBITDA multiple`
2. Extract: acquirer, target, sector, deal EV, revenue/EBITDA at deal, implied multiple, date
3. Cache to `transaction_comps.json` (append, dedup by target)
4. ValuationService uses transaction comps as 3rd method (alongside DCF + peer comps)

**Output impact**: `Valuation.methods` gains a real `Transactions` row with actual deal references.

---

### 🟠 PRIORITY 2 — Output polish (client-ready)

M&A fund clients expect outputs that look like they came from a bank. Current output would not pass that bar.

#### 2.1 — PPT: real charts

| Slide | Current | Target |
|-------|---------|--------|
| Football field | Text table | Horizontal bar chart (bear/base/bull ranges per method) |
| DCF | Text | Waterfall: FCF bars + terminal value |
| Peer comps | Text table | Scatter plot: EV/EBITDA vs EBITDA margin |
| IC Score | Text table | Radar/spider chart (6 dimensions) |

All implementable with `python-pptx` chart API (`prs.slides[x].shapes.add_chart()`).

#### 2.2 — Excel: 3-statement model

Current: DCF tab only.
Target: 7-tab model used by analysts:

| Tab | Content |
|-----|---------|
| `Assumptions` | Revenue CAGR, margins, WACC, exit multiple — all editable |
| `P&L` | 5-year projected Income Statement |
| `Balance Sheet` | Simplified (assets, debt, equity) |
| `Cash Flow` | Operating + investing + financing |
| `DCF` | FCF waterfall → EV → equity value |
| `LBO` | Entry/exit, debt schedule, IRR/MOIC |
| `Scenarios` | Bear/Base/Bull sensitivity matrix |

#### 2.3 — Executive summary slide

First slide only: company name, recommendation (BUY/HOLD/SELL), EV range, 3 key bullets, IC score badge. One page — the slide a partner reads before the full deck.

---

### 🟡 PRIORITY 3 — Deal sourcing (investment brief → target shortlist)

This is the second product mode. A client says "we want to acquire a €50–200M EBITDA SaaS company in Southern Europe." The tool returns 5–10 screened targets with scoring.

**Current state**: `run_pipeline()` exists but uses LLM-hallucinated company names.

**Target state**:
1. **Real company sourcing**: use Crunchbase + EU registries + web search to find actual companies matching brief criteria (sector, geography, revenue range, stage)
2. **Scoring against brief**: score each target on: sector fit, size fit, geography, growth profile, deal complexity, estimated valuation
3. **Output**: shortlist PPT (one slide per target: overview, financials, why attractive, estimated EV, IC score, next step)
4. **Validation**: every target must be a real company verifiable via at least one data source

**New command**: `goldroger source --brief "SaaS, €50–200M EBITDA, Southern Europe" --n 10`

---

### 🟢 PRIORITY 4 — Pluggable data connectors (client tier)

Architecture: already has `DataRegistry` + `DataProvider` ABC. Adding a new source = one new file in `data/providers/`.

**What to document and formalize**:

```python
# To add Bloomberg:
class BloombergProvider(DataProvider):
    name = "bloomberg"
    requires_credentials = True

    def is_available(self) -> bool:
        return bool(os.getenv("BLOOMBERG_API_KEY"))

    def fetch(self, ticker: str) -> Optional[MarketData]:
        # Bloomberg Data License API call
        ...

    def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
        # BDP() lookup by name
        ...
```

**Priority connectors** (most requested by M&A funds):

| Source | Covers | Notes |
|--------|--------|-------|
| **Crunchbase Pro** | Global private, VC-backed | Free tier already active (P0.4) |
| **Companies House** | 🇬🇧 UK private revenue | Free API key, XBRL revenue parsing improvement needed |
| **Bloomberg** | Global, all assets | Stub exists, needs BDP API implementation |
| **Capital IQ** | Global private + transactions | Stub exists |
| **Pappers** | 🇫🇷 French private (best) | ~€30/mo, RNCS verified revenue |
| **Refinitiv / LSEG** | Global M&A transactions | Best source for real deal comps |

**Connector SDK**: write `data/providers/TEMPLATE.py` + `docs/adding_a_provider.md` so a client's tech team can plug in their data source in < 2h.

---

### 🔵 PRIORITY 5 — Web interface

**When**: after Priority 0 + 1 are done (data is trusted).
**Stack**: Next.js frontend + existing FastAPI backend (`api.py`).

**MVP screens**:
1. Search bar → analysis in progress → memo page with PPT/Excel download
2. Deal sourcing form → target shortlist with scores
3. Data source configuration page (connect Pappers, Bloomberg, etc.)

**Not before**: a web interface built on bad data just makes wrong numbers more visible.

---

## DATA PROVIDER STATE

| Source | Country | Revenue | Auth | Priority |
|--------|---------|---------|------|----------|
| **yfinance** | Global | ✅ Verified (public) | None | Active |
| **SEC EDGAR** | 🇺🇸 | ✅ 10-K XBRL (ticker) | None | Add `fetch_by_name` (P0.5) |
| **Crunchbase** | Global | ⚠️ Range estimate | Enterprise only (no free tier) | Active if key present |
| **recherche-entreprises** | 🇫🇷 | ❌ Sector only | None | Active (no revenue) |
| **Pappers** | 🇫🇷 | ✅ RNCS verified | ~€30/mo | Active if key present |
| **Companies House** | 🇬🇧 | ⚠️ Best-effort XBRL | Free key | Improve parsing |
| **Bundesanzeiger** | 🇩🇪 | ⚠️ Best-effort HTML | None | Active |
| **BORME** | 🇪🇸 | ❌ Existence only | None | Active (no revenue) |
| **KVK** | 🇳🇱 | ❌ Sector only | Free key | Active if key present |
| **Bloomberg** | Global | ✅ Everything | License | Stub ready |
| **Capital IQ** | Global | ✅ Everything + deals | License | Stub ready |
| **Refinitiv** | Global | ✅ M&A transactions | License | Not yet stubbed |

---

## REFACTORING STATUS

| Phase | Description | Status |
|-------|-------------|--------|
| R1 | Delete ~600 lines dead code | ✅ Done |
| R2 | Split orchestrator → `pipelines/` | ✅ Done |
| R3 | Centralize config → `goldroger/config.py` | ✅ Done |
| R4 | Split `models/__init__.py` → domain files; delete dead code | ✅ Done |
| R5 | Tests 20 → 55 (agents, providers, scoring, json_parser, config) | ✅ Done |
| R6 | SEC EDGAR `fetch_by_name()` + Crunchbase activation | ⬜ P0 |

---

## COMPLETED PHASES (1–15)

<details>
<summary>Click to expand</summary>

| Phase | Item | Status |
|-------|------|--------|
| 1 | yfinance fetcher, sector multiples, WACC CAPM, DCF, ValuationService | ✅ |
| 2 | Forward estimates, P/E path (banks), LBO engine, SOTP framework | ✅ |
| 3 | `run_ma_analysis()`, `run_pipeline()`, IC scoring 6-dim | ✅ |
| 4 | Cache TTL, structured logging | ✅ |
| 5 | DataRegistry + provider layer, Crunchbase, peer comps, scenarios, PPT, LBO/DCF fixes | ✅ |
| 6 | Auto output folder, `--quick` flag, pipeline retry, speed optimizations | ✅ |
| 7 | LLM-agnostic (Mistral/Anthropic/OpenAI), EU registries (FR/UK/DE), private triangulation | ✅ |
| 8 | DataCollectorAgent fix, no-placeholder policy, optional LLM deps, name resolver, revenue fallback | ✅ |
| 9 | KVK 🇳🇱, Registro Mercantil 🇪🇸, fuzzy matching, SOTP auto-detect, scenario narratives | ✅ |
| 10 | Target price fix, mega-cap tx exclusion, private recommendation, revenue lock | ✅ |
| 11 | DCF NWC fix, LBO revenue fix, scenarios anchor, aggregator normalization | ✅ |
| 12 | EU registry audit — dead APIs removed, auth gating corrected | ✅ |
| 13 | (reserved) | — |
| 14 | `--siren` CLI flag, SourcesLog, `sources.md` output | ✅ |
| 15 | Parallel agents (ThreadPoolExecutor), revenue fallback in correct scope, timing output | ✅ |

</details>
