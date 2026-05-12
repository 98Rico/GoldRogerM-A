# Engineering Notes

This file stores implementation-history style notes that were previously in `README.md`.

## Why This Exists

`README.md` is now intentionally concise and user-focused.
Historical milestone logs and long completion checklists live here to avoid overstating maturity.

## Recent Reliability Cleanup Highlights

- Added deterministic money/currency formatting helpers (`goldroger/utils/money.py`).
- Added quote-unit normalization support (including `GBp`/`GBX` -> `GBP` pence-to-pound handling).
- Hardened normalization audit metadata and gating.
- Improved valuation source rendering to avoid hardcoded `$` for non-USD runs.
- Added per-share vs market-cap upside reconciliation diagnostics.
- Excluded same-issuer alternate listings from peer sets.
- Added filing URL classification (`SEC_10Q`, `CONSENSUS_PAGE`, etc.).
- Added thesis/scenario guardrails against interpolation artifacts and unsupported specificity.
- Added report modes (`quick` / `standard` / `full-report`) and mode-aware narrative behavior.

## Validation Focus Set

Current regression focus for public reliability:
- `AAPL`
- `BATS.L`
- `NHY.OL`

See `docs/VALIDATION.md` for expected invariants and run commands.

