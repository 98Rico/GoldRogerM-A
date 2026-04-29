# REFACTORING STEPS — GOLD ROGER

> **How to use this file**: each phase is a self-contained git worktree branch.
> Work on independent phases in parallel using worktrees (see "Worktree method" below).
> Never merge a phase until its tests pass.

---

## Worktree Method (parallel development)

```bash
# Create isolated worktrees for independent phases
git worktree add ../goldroger-cleanup  refactor/cleanup
git worktree add ../goldroger-split    refactor/split-orchestrator
git worktree add ../goldroger-config   refactor/centralize-config

# Work in each independently, then merge in order
# Phase 1 → Phase 2 → Phase 3 (sequential dependency)
# Phase 1 and Phase 4 are independent — can run in parallel
```

---

## PHASE 1 — Dead code removal (1–2h, no risk, immediate)

**Goal**: delete ~600 lines of dead/duplicate code that pollute the codebase.
**Risk**: zero — none of these files are imported anywhere.

### 1.1 Delete dead files

| File | Lines | Why dead |
|------|-------|----------|
| `goldroger/agents/_backup_specialists.py` | 512 | Duplicate of `specialists.py`; never imported |
| `goldroger/finance/valuation/engine.py` | 64 | Old orchestration code; `valuation_service.py` is used instead |
| `goldroger/llm/__init__.py` | 0 | Empty; planned architecture never built |
| `goldroger/llm/thesis.py` | 0 | Empty stub |
| `goldroger/llm/narrative.py` | 0 | Empty stub |
| `goldroger/llm/assumptions.py` | 0 | Empty stub |
| `goldroger/finance/models/assumptions.py` | 0 | Empty stub |
| `goldroger/finance/models/cashflows.py` | 0 | Empty stub |
| `goldroger/finance/models/output.py` | 0 | Empty stub |
| `goldroger/finance/valuation/scenario.py` | 0 | Empty; scenarios live in `finance/core/scenarios.py` |
| `goldroger/finance/core/cashflow.py` | 0 | Empty stub |
| `goldroger/finance/core/discounting.py` | 0 | Empty stub |
| `goldroger/orchestration/__init__.py` | 0 | Empty; planned module never built |
| `goldroger/finance/core/errors.py` | 1 | Single-line empty exception; unused |
| `goldroger/finance/core/contracts/financials.py` | 8 | 8-line stub; not imported |

```bash
# Worktree: refactor/cleanup
git rm goldroger/agents/_backup_specialists.py
git rm goldroger/finance/valuation/engine.py
git rm goldroger/llm/__init__.py goldroger/llm/thesis.py goldroger/llm/narrative.py goldroger/llm/assumptions.py
git rm goldroger/finance/models/assumptions.py goldroger/finance/models/cashflows.py goldroger/finance/models/output.py
git rm goldroger/finance/valuation/scenario.py
git rm goldroger/finance/core/cashflow.py goldroger/finance/core/discounting.py
git rm goldroger/orchestration/__init__.py
git rm goldroger/finance/core/errors.py
git rm goldroger/finance/core/contracts/financials.py
```

### 1.2 Rename confusingly-named stubs

These files exist and are registered in `DataRegistry` but always return `None`:

| File | Status | Action |
|------|--------|--------|
| `data/providers/sec_edgar.py` | Returns None; no revenue parsing | Add `# NOT IMPLEMENTED` header + `raises NotImplementedError` in `fetch()` |
| `data/providers/crunchbase.py` | Returns None; no API call | Same |
| `data/providers/bloomberg.py` | Returns None; credential stub | OK as-is — clearly documented |
| `data/providers/capitaliq.py` | Returns None; credential stub | OK as-is |

The SEC EDGAR and Crunchbase stubs are dangerous because they're registered in the provider chain and silently return None, wasting registry lookup time. Either implement them or mark them clearly.

### 1.3 Remove empty package directories

```bash
rmdir goldroger/llm goldroger/finance/models goldroger/orchestration 2>/dev/null
# goldroger/finance/core/contracts/ — remove if only financials.py was there
```

**Verification**: `uv run pytest tests/ -q` must still pass 20/20.

---

## PHASE 2 — Split orchestrator.py (4–6h, high value, some risk)

**Goal**: `orchestrator.py` is 976 lines with `run_analysis()` at 575 lines. Split into focused modules.
**Risk**: medium — requires careful import reorganization, but logic is just moved, not changed.
**Worktree**: `refactor/split-orchestrator`

### Current structure (bad)

```
orchestrator.py (976 lines)
  run_analysis()           ← 575 lines — god function
  run_ma_analysis()        ← 167 lines
  run_pipeline()           ← 47 lines
  + 8 tiny helpers
```

### Target structure

```
orchestrator.py            ← 150 lines (routes to sub-pipelines only)
pipelines/
  equity.py                ← run_analysis() split into 4 stages
  ma.py                    ← run_ma_analysis() moved here
  pipeline.py              ← run_pipeline() moved here
  _data.py                 ← data fetching stage (private module)
  _agents.py               ← agent orchestration stage (private module)
  _valuation.py            ← valuation + scenarios stage (private module)
```

### Split plan for run_analysis()

**Stage 1 — DataStage** (lines 175–231, ~57 lines):
```python
# pipelines/_data.py
def fetch_company_data(company, company_type, siren=None, llm=None) -> DataBundle:
    """Resolves ticker, fetches yfinance (public) or registry (private)."""
    ...
```

**Stage 2 — AgentStage** (lines 232–429, ~198 lines):
```python
# pipelines/_agents.py
def run_agent_pipeline(company, company_type, data_bundle, client) -> AgentOutputs:
    """Fundamentals (sequential) → Market + Peers + Financials (parallel)."""
    ...
```

**Stage 3 — ValuationStage** (lines 430–703, ~273 lines):
```python
# pipelines/_valuation.py
def run_valuation_pipeline(company, company_type, agent_outputs, data_bundle) -> ValuationOutputs:
    """ValuationService + Scenarios + IC scoring + Thesis."""
    ...
```

**Stage 4 — Assembly** (lines 704–750, ~46 lines):
```python
# pipelines/equity.py
def run_analysis(company, company_type, llm=None, siren=None) -> AnalysisResult:
    data = fetch_company_data(company, company_type, siren, llm)
    agents = run_agent_pipeline(company, company_type, data, ...)
    val = run_valuation_pipeline(company, company_type, agents, data)
    return _assemble_result(company, company_type, data, agents, val)
```

### Backward compatibility
`orchestrator.py` keeps `run_analysis`, `run_ma_analysis`, `run_pipeline` as thin re-exports:
```python
from goldroger.pipelines.equity import run_analysis
from goldroger.pipelines.ma import run_ma_analysis
from goldroger.pipelines.pipeline import run_pipeline
```
→ CLI and API require zero changes.

### New `_parse_with_retry` decorator
Replace the inline retry wrapper (called 8+ times) with a single decorator:
```python
# utils/agent_runner.py
def run_agent(agent, company, company_type, context, model_class, fallback):
    """Unified agent executor: runs agent → parses JSON → retries once on failure."""
```

---

## PHASE 3 — Centralize configuration (2–3h, medium value, low risk)

**Goal**: eliminate hardcoded constants scattered across 6 files.
**Risk**: low — values unchanged, just moved to one place.
**Worktree**: `refactor/centralize-config`

### Files with hardcoded config today

| File | Hardcoded constants |
|------|-------------------|
| `finance/core/wacc.py` | `RISK_FREE_RATE = 0.025`, `EQUITY_RISK_PREMIUM = 0.07` |
| `finance/valuation/lbo.py` | `min_irr = 0.15`, `max_leverage = 6.5`, `fcf_sweep = 0.75` |
| `finance/core/scenarios.py` | Bear/base/bull delta percentages |
| `ma/scoring.py` | IC score thresholds (75→STRONG BUY, 60→BUY, 45→WATCH) |
| `agents/base.py` | `_MIN_CALL_GAP = 1.0` (rate limit gap), max tool rounds |
| `orchestrator.py` | Parallel worker count = 3, conglomerate keywords list |

### Target: `goldroger/config.py`

```python
# goldroger/config.py
from dataclasses import dataclass, field

@dataclass
class WACCConfig:
    risk_free_rate: float = 0.025
    equity_risk_premium: float = 0.07

@dataclass
class LBOConfig:
    min_irr: float = 0.15
    max_leverage: float = 6.5
    fcf_sweep_rate: float = 0.75
    mega_cap_skip_usd_bn: float = 500.0

@dataclass
class ICScoreConfig:
    strong_buy_threshold: int = 75
    buy_threshold: int = 60
    watch_threshold: int = 45

@dataclass
class AgentConfig:
    min_call_gap_s: float = 1.0
    max_tool_rounds: int = 3
    parallel_workers: int = 3

@dataclass
class GoldRogerConfig:
    wacc: WACCConfig = field(default_factory=WACCConfig)
    lbo: LBOConfig = field(default_factory=LBOConfig)
    ic_score: ICScoreConfig = field(default_factory=ICScoreConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

DEFAULT_CONFIG = GoldRogerConfig()
```

Each module imports from `config.py`:
```python
from goldroger.config import DEFAULT_CONFIG
wacc = DEFAULT_CONFIG.wacc.risk_free_rate
```

---

## PHASE 4 — Implement real SEC EDGAR + Crunchbase providers (3–4h, high value)

**Goal**: SEC EDGAR is free and covers 10-K revenues for all US public companies. Crunchbase
has a free tier. Both stubs exist — implement them.
**Worktree**: `refactor/real-providers` (independent from Phase 2/3)

### 4.1 SEC EDGAR — add `fetch_by_name()` (`data/providers/sec_edgar.py`)

**Status**: already implemented for `fetch(ticker)` — XBRL revenue extraction works.
**Gap**: no `fetch_by_name()` so private/unlisted company lookups never reach it.

```python
def fetch_by_name(self, company_name: str) -> Optional[MarketData]:
    # Use EDGAR full-text search: efts.sec.gov/LATEST/search-index?q={name}&...
    # Resolve company name → CIK → then call existing fetch(ticker) logic
```
Covers: US private companies that file with SEC (less common but worth having).

### 4.2 Crunchbase — activate and verify (`data/providers/crunchbase.py`)

**Status**: already implemented — `fetch_by_name()` + revenue range parsing exist.
**Gap**: requires `CRUNCHBASE_API_KEY` (free tier, 200 req/day at data.crunchbase.com).

Action: add `CRUNCHBASE_API_KEY` to `.env` and test with a known startup (e.g., Notion, Figma).
The `_parse_revenue_range()` function handles "$10M to $50M" → midpoint in USD millions.

---

## PHASE 5 — Expand test coverage (3–4h, high value for long-term)

**Goal**: go from 20 unit tests (finance engines only) to 40+ tests covering all layers.
**Worktree**: `refactor/tests`

### Tests to add

**Agents (mock LLM)**:
```python
# tests/test_agents.py
# Mock LLMProvider.complete() to return fixture JSON
# Test: DataCollectorAgent returns valid Fundamentals
# Test: FinancialModelerAgent returns valid Financials
# Test: PeerFinderAgent returns peer list in expected format
```

**JSON parsing**:
```python
# tests/test_json_parser.py
# Test: parse_model() with valid JSON
# Test: parse_model() with markdown-fenced JSON (```json...```)
# Test: parse_model() with malformed JSON → returns fallback
# Test: parse_model() with extra keys → strips gracefully
```

**Data providers**:
```python
# tests/test_providers.py
# Test: InfogreffeProvider.fetch_by_name() with mock httpx
# Test: InfogreffeProvider.fetch_by_siren() with mock httpx
# Test: PappersProvider.fetch_by_siren() with mock httpx
# Test: is_available() returns False when key missing
```

**IC scoring**:
```python
# tests/test_scoring.py
# Test: auto_score_from_valuation() → STRONG BUY for IRR 30%
# Test: auto_score_from_valuation() → WATCH for IRR 12%
# Test: score_from_ma_agents() dimension weightings
```

**Integration (smoke test with fixture)**:
```python
# tests/test_integration.py
# Mock all LLM calls + yfinance calls
# Test: run_analysis("Apple", "public") → AnalysisResult with no None critical fields
# Test: DCF + comps + blended EV all populated
# Test: generate_excel() produces a valid .xlsx file
# Test: generate_pptx() produces a valid .pptx file
```

---

## PHASE 6 — Clean up `models/__init__.py` (1–2h, low risk)

**Goal**: 296-line mega-file with 50+ Pydantic models in one file. Split by domain.
**Worktree**: can be part of Phase 2 cleanup.

### Target structure

```
models/
  __init__.py        ← re-exports everything (backward compat)
  equity.py          ← AnalysisResult, Fundamentals, MarketAnalysis, Financials, Valuation, InvestmentThesis, FootballField, PeerComp, ICScore
  ma.py              ← MAResult, DealSourcing, StrategicFit, DueDiligence, DealExecution, LBOModel
  pipeline.py        ← AcquisitionPipeline, PipelineTarget
  shared.py          ← Risk, ValuationMethod, DCFAssumptions, Projection, KeyMetric, Competitor, ScenarioSummary
```

`models/__init__.py` becomes:
```python
from .equity import *
from .ma import *
from .pipeline import *
from .shared import *
```
→ all existing imports continue to work.

---

## PHASE 7 — API + FastAPI cleanup (1h, low risk)

**Goal**: `api.py` has 190 lines of inline HTML. Extract and validate properly.
- Move the HTML form to `goldroger/static/index.html` (or drop it — CLI is primary interface)
- Add Pydantic request/response models for `/analyze` endpoint
- Add input validation (company name max length, allowed modes, etc.)

---

## Priority Order & Dependencies

```
Phase 1 (cleanup)           ─── no deps, do first ────────────────────────┐
Phase 4 (providers)         ─── no deps, parallel with Phase 1 ───────────┤
Phase 5 (tests)             ─── parallel with Phase 1 ────────────────────┤
                                                                            │ merge all
Phase 2 (split orchestrator)─── after Phase 1 ───────────────────────────→┤
Phase 3 (config)            ─── after Phase 1 ───────────────────────────→┤
Phase 6 (models split)      ─── after Phase 2 ───────────────────────────→┤
Phase 7 (API cleanup)       ─── after Phase 2 ───────────────────────────→┘
```

**Estimated total**: 15–22h of work, fully parallelizable across Phases 1/4/5 in worktrees.

---

## What NOT to refactor

- `finance/valuation/dcf.py` — well-tested, correct, ~100 lines. Leave it.
- `finance/valuation/lbo.py` — same. Fixed formula, tested. Leave it.
- `finance/core/scenarios.py` — clean 232-line file. Leave it.
- `data/sector_multiples.py` — 320-line lookup table, no logic. Leave it (move to config in Phase 3 if needed).
- Agent prompts in `specialists.py` — don't over-engineer; they work.
- Export logic in `excel.py` / `pptx.py` — messy but functional; not on critical path.

---

## Definition of Done

- [ ] Phase 1: `uv run pytest tests/ -q` passes 20/20; `_backup_specialists.py` deleted; all 0-line stubs deleted
- [ ] Phase 2: `orchestrator.py` < 200 lines; `run_analysis()` < 50 lines; same 20 tests pass
- [ ] Phase 3: `config.py` exists; no hardcoded thresholds in `wacc.py`, `lbo.py`, `scoring.py`
- [ ] Phase 4: SEC EDGAR returns real revenue for Apple/Tesla; Crunchbase returns org data for Notion
- [ ] Phase 5: 40+ tests pass including agent mock tests and provider tests
- [ ] Phase 6: `models/__init__.py` < 30 lines (re-exports only)
