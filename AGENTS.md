# AGENTS.md

Repository-level guide for contributors and coding agents.

## Setup

```bash
uv sync
cp .env.example .env  # if available
```

## Core Commands

Run CLI:

```bash
uv run python -m goldroger.cli --company "AAPL" --type public
```

Run tests:

```bash
uv run pytest -q
```

## Conventions

- Keep valuation math deterministic (no LLM-generated valuation numbers).
- Prefer small, targeted patches over broad rewrites.
- Preserve public-company safety gates (normalization audit, sanity breaker, suppression behavior).
- Use shared formatting/utilities (`goldroger/utils/money.py`) for currency/unit rendering.
- Avoid hardcoded `$` for non-USD runs.
- Keep research-status semantics explicit (qualitative context vs quantitative valuation inputs).

## Reliability Guardrails

- If normalization is `FAILED`, recommendation must be `INCONCLUSIVE` and target/upside `N/A`.
- Do not surface investable-looking targets when safety breakers trigger.
- Exclude same-issuer alternate listings from peers.
- Keep scenario ordering valid (`low <= base <= high`), else suppress diagnostic output.
- Any new reliability behavior must have regression coverage.

## Tests To Touch When Relevant

- `tests/test_normalization.py`
- `tests/test_comparables.py`
- `tests/test_cli_rendering.py`
- `tests/test_pipeline_suppression.py`
- `tests/test_filings_market_context.py`
- `tests/test_thesis_guardrails.py`

## Documentation Rules

When behavior changes:
- update `README.md` (user-facing truth),
- update `docs/VALIDATION.md` (expected invariants),
- add engineering details to `docs/engineering_notes.md`.

