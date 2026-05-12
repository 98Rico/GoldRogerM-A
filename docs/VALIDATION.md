# Validation Benchmarks

This document defines regression expectations for public-company reliability checks.

## How To Run

```bash
uv run python -m goldroger.cli --company "AAPL" --type public
uv run python -m goldroger.cli --company "BATS.L" --type public
uv run python -m goldroger.cli --company "NHY.OL" --type public
```

Quick mode checks:

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --quick
uv run python -m goldroger.cli --company "BATS.L" --type public --quick
uv run python -m goldroger.cli --company "NHY.OL" --type public --quick
```

## AAPL

Expected:
- ticker resolves to `AAPL`
- quote/market-cap/financial currency: `USD`
- normalization status: `OK`
- no foreign-share-basis warning
- peer purity remains low/adjacent (Apple has structural pure-peer limits)
- recommendation remains guarded (`HOLD / LOW CONVICTION`) unless safety gates force `INCONCLUSIVE`
- filing sources block appears

Invariant examples:
- `Quote/market cap currency: USD/USD`
- no non-USD currency leakage in valuation bridge

## BATS.L

Expected:
- ticker resolves to `BATS.L` for local-primary listing path
- valuation/reporting currency: `GBP`
- normalization status: `OK`
- no `$` symbols for valuation values
- pence/GBP quote-unit normalization applies before per-share upside/downside checks
- peer set excludes same-issuer alternate listing (`BTI`)

Invariant examples:
- `Quote/market cap currency: GBP/GBP`
- valuation bridge/target/range values rendered in `GBP`
- no peer row with `BTI`

## NHY.OL

Expected:
- ticker resolves to `NHY.OL` for local-primary listing path
- valuation/reporting currency: `NOK`
- normalization status: `OK`
- no `$` symbols for valuation values
- peer set excludes same-issuer alternates (`NHYDY`, `NHYKF`)
- cyclical/commodity caution is surfaced in low-confidence cases

Invariant examples:
- `Quote/market cap currency: NOK/NOK`
- valuation bridge/target/range values rendered in `NOK`
- no peer rows with `NHYDY` or `NHYKF`

## Suppression Behavior (All Tickers)

If critical normalization or sanity checks fail:
- recommendation must be `INCONCLUSIVE`
- target/upside/downside must be `N/A`
- valuation sections should be suppressed or clearly marked diagnostic
- output must not look investable

## Test Suite

Run all tests:

```bash
uv run pytest -q
```

Key test modules for these guarantees:
- `tests/test_normalization.py`
- `tests/test_comparables.py`
- `tests/test_cli_rendering.py`
- `tests/test_pipeline_suppression.py`
- `tests/test_filings_market_context.py`
- `tests/test_thesis_guardrails.py`

