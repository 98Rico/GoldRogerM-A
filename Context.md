# Gold Roger — Context for New Chat Sessions

## What this project is

**Gold Roger** is an M&A analysis platform being built as a commercial product to license to PE funds, boutique banks, and M&A advisors. It takes a company name (public or private) and produces a full institutional investment memo: valuation, football field, Bear/Base/Bull scenarios, IC score, investment thesis, PPT slide deck, and Excel model.

It has two modes:
1. **Company analysis** — enter any company → full memo with valuation and outputs
2. **Deal sourcing** — enter an investment brief → shortlist of real acquisition targets with scores

The codebase lives at `/Users/federicocalderon/Documents/Repositories/finanalyst/`.
The main package is `goldroger/`. Run everything with `uv run python -m goldroger.cli`.

---

## The non-negotiable core rule

> **LLMs produce language, never numbers.**

Every financial figure — revenue, margin, EV, multiple, WACC — must come from a verified data source or a clearly-tagged deterministic estimate. The LLM writes the thesis, not the numbers. This rule is enforced in code (WACC/terminal growth are never sourced from LLM by default; a hallucination firewall blocks figure generation when revenue is missing).

---

## How the tool is structured

```
CLI / FastAPI
     │
     ▼
orchestrator.py  ←── single entry point
     │
     ├── DATA LAYER        yfinance, EU registries, SEC EDGAR, Crunchbase, triangulation
     ├── LLM LAYER         qualitative only — thesis, narratives, peer names
     ├── VALUATION ENGINE  pure Python, deterministic — DCF + comps + LBO + scenarios
     └── EXPORT LAYER      PPT (10 slides) + Excel + sources.md
```

Key files to know:
| File | Role |
|------|------|
| `goldroger/pipelines/equity.py` | Main equity analysis pipeline |
| `goldroger/finance/core/valuation_service.py` | DCF + comps + LBO + weight routing |
| `goldroger/data/fetcher.py` | yfinance market data fetch |
| `goldroger/utils/sources_log.py` | Provenance tracking → `sources.md` |
| `goldroger/agents/specialists.py` | LLM agents (thesis, peers, comps, etc.) |
| `goldroger/data/sector_multiples.py` | 25 sectors — EV/EBITDA, WACC, growth rates |
| `goldroger/exporters/pptx.py` | PPT 10-slide export |
| `goldroger/exporters/excel.py` | Excel DCF export |

---

## How to read README.md and NextSteps.md

**README.md** — the architecture and feature reference.
- Read it to understand how the system works, what each module does, what commands are available, and the full "Definition of Done" checklist at the bottom.
- The Definition of Done is the canonical list of what has been built and verified.

**NextSteps.md** — the living product roadmap.
- **"What Works Today"** — capabilities confirmed working in the current codebase.
- **"What Is Broken or Insufficient"** — confirmed bugs and gaps, ordered by severity. The 🔴 items are the most critical.
- **"Priority Roadmap"** — what to build next, in order, with root causes and fix specs already written.
- **"Completed Phases"** — full history of every phase, numbered 1–27.

When starting a new chat, read NextSteps.md first to know the current state and what to work on. README.md is the reference when you need to understand how something works.

---

## Current state (as of Phase 27)

### What was just fixed
- **Full per-field provenance tracking**: `sources.md` now logs every financial input used in a valuation — revenue, EBITDA margin, gross margin, beta, forward growth, market cap, net debt, WACC, terminal growth, peer EV/EBITDA — each tagged with source and confidence (`✅ verified` / `⚠️ estimated` / `🔵 inferred`). Previously only 3 fields were logged. WACC is now correctly tagged `✅ verified` when computed via CAPM with real beta, not `🔵 inferred`.
- Files changed: `goldroger/finance/core/valuation_service.py`, `goldroger/pipelines/equity.py`, `goldroger/utils/sources_log.py`

### Critical bug found (next to fix — Priority 0.0 in NextSteps.md)
- **EV volatility**: On NVIDIA, the Implied EV ranged from **$4.9T to $19.4T** across 4 runs with identical inputs. Root cause: the peer comps EV/EBITDA multiple comes from the LLM (varies each run). At 40% blend weight on NVIDIA's $133B EBITDA, a 10-point multiple swing adds ~$1.3T to the output. The fix is documented in NextSteps.md Priority 0.0: lock the comps mid to `market_data.ev_ebitda_market` (live yfinance) when available; LLM peers can only widen/narrow the range, never move the anchor.

### Known remaining issues (from NextSteps.md)
1. 🔴 EV volatility from LLM peer comps (fix spec in Priority 0.0)
2. Private revenue often missing (FR SAS confidentiality, DE/ES/NL registries)
3. PPT is text tables — no charts yet
4. Excel is DCF-only — not a 3-statement model
5. Deal sourcing uses LLM-hallucinated company names — not real targets

---

## The product roadmap in order

| Priority | What | Why |
|----------|------|-----|
| **0.0** | Fix EV volatility — lock comps anchor to live yfinance data | Numbers must be trustworthy before anything else |
| **0.1–0.3** | Data quality — private revenue, Companies House XBRL | Same reason |
| **1** | Transaction comps — real deal data, not LLM recall | Fund clients need defensible multiples |
| **2** | Output polish — PPT charts, 7-tab Excel, exec summary slide | Client-ready outputs for fund licensing |
| **3** | Web UI — Next.js + FastAPI, auth, download | Essential for commercial licensing |
| **4** | Deal sourcing — real company names from registries | The second product mode |
| **5** | Premium data connectors — Pappers, Crunchbase | Unlocks private company coverage at fund grade |

---

## LLM and keys

- **LLM**: Mistral (free, default). Switchable via `--llm claude` or `LLM_PROVIDER=anthropic` in `.env`.
- **Keys active**: Mistral API key, Companies House UK key.
- **Keys missing**: Pappers (FR private revenue), Crunchbase (global VC-backed), Bloomberg, Capital IQ.
- Running on free tier means private company revenue is often missing or triangulated.

---

## How to run

```bash
# Public company
uv run python -m goldroger.cli --company "NVIDIA" --excel --pptx

# Private company
uv run python -m goldroger.cli --company "Doctolib" --type private --excel --pptx

# List available data sources + credential status
uv run python -m goldroger.cli --list-sources

# Run tests
uv run python -m pytest tests/ -v
```

Outputs auto-save to `outputs/<Company>_<YYYYMMDD_HHMMSS>/`.
