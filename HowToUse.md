# How To Use Gold Roger

This guide is a practical walkthrough for running Gold Roger from the CLI.

## 1) Setup

```bash
uv sync
```

Optional (if you use provider keys):

```bash
cp .env.example .env
# then edit .env with your keys
```

## 2) Core Public-Company Commands

### Standard run (default)

```bash
uv run python -m goldroger.cli --company "AAPL" --type public
```

### Quick screen (fast, deterministic)

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --quick
```

### Full report (longer narrative/scenarios)

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --full-report
```

Notes:
- `--quick` and `--full-report` are mutually exclusive.
- Default mode is `standard` (concise report).

## 3) Public Validation Trio (recommended)

Run these together to check reliability across US + UK + Nordic patterns:

```bash
uv run python -m goldroger.cli --company "AAPL" --type public && \
uv run python -m goldroger.cli --company "BATS.L" --type public && \
uv run python -m goldroger.cli --company "NHY.OL" --type public
```

## 4) Private Company Commands

### Standard private run

```bash
uv run python -m goldroger.cli --company "Doctolib" --type private
```

### Interactive source selection

```bash
uv run python -m goldroger.cli --company "Doctolib" --type private --interactive
```

### Explicit source selection

```bash
uv run python -m goldroger.cli \
  --company "Doctolib" \
  --type private \
  --sources "infogreffe,pappers,sec_edgar"
```

### Manual revenue override (prototype)

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

If legal identity is unresolved and you still want a prototype valuation path:

```bash
uv run python -m goldroger.cli \
  --company "Personio" \
  --type private \
  --country-hint DE \
  --manual-revenue 300 \
  --manual-revenue-currency EUR \
  --manual-identity-confirmed
```

## 5) Export Commands

### Excel + PowerPoint output

```bash
uv run python -m goldroger.cli --company "AAPL" --type public --excel --pptx
```

Files are saved under timestamped folders in `outputs/`.

## 6) Mode Semantics

- `--quick`
  - deterministic fast screen
  - skips deep research
  - outputs are indicative

- `standard` (default)
  - full valuation pipeline
  - concise thesis output

- `--full-report`
  - full narrative + scenarios + catalysts
  - slower than standard

## 7) How To Read Output Safely

Always check these sections first:

1. `Data normalization`
- `OK`: consistent basis
- `OK_FX_NORMALIZED`: converted basis (check FX confidence)
- `FAILED`: recommendation should be `INCONCLUSIVE`

2. `Pipeline status`
- `Research` state
- `Peers` state
- `Valuation` state
- `Recommendation`

3. `Confidence` and `Method dispersion`
- high dispersion + low confidence means use range, not point estimate

## 8) Regression + Tests

Run full tests:

```bash
uv run pytest -q
```

Validation benchmark expectations are documented in:
- `docs/VALIDATION.md`

## 9) Troubleshooting

If a run is suppressed (`INCONCLUSIVE`):
- verify ticker/listing selection
- check normalization block reason
- prefer local-primary listing for foreign issuers
- re-run with `--full-report` only when you need extended narrative

If data sources are limited:

```bash
uv run python -m goldroger.cli --list-sources
uv run python -m goldroger.cli --list-sources --type private --country-hint FR
uv run python -m goldroger.cli --list-sources --type private --country-hint GB
```
