"""
Structured run logger for Gold Roger pipelines.

Each run gets a unique run_id and logs:
- step timings
- data confidence per field
- WACC derivation method
- valuation notes (full audit trail)
- errors and fallbacks

Writes JSON-lines to goldroger_runs.log alongside the process working dir.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


_LOG_FILE = os.path.join(os.getcwd(), "goldroger_runs.log")
_file_handler = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)

_logger = logging.getLogger("goldroger")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    _logger.addHandler(_file_handler)


@dataclass
class RunLog:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    company: str = ""
    company_type: str = "public"
    ticker: str = ""
    started_at: float = field(default_factory=time.time)

    # Step timings (seconds)
    step_times: dict[str, float] = field(default_factory=dict)

    # Data quality
    data_confidence: str = "unknown"
    wacc_method: str = "unknown"
    valuation_notes: list[str] = field(default_factory=list)

    # Final outputs
    recommendation: str = ""
    upside_pct: float | None = None
    blended_ev: float | None = None

    # Errors / fallbacks
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def start_step(self, name: str) -> float:
        ts = time.time()
        self.step_times[f"{name}_start"] = ts
        return ts

    def end_step(self, name: str, start: float) -> float:
        elapsed = round(time.time() - start, 2)
        self.step_times[name] = elapsed
        return elapsed

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def flush(self) -> None:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "company": self.company,
            "company_type": self.company_type,
            "ticker": self.ticker,
            "duration_s": round(time.time() - self.started_at, 2),
            "step_times": {k: v for k, v in self.step_times.items() if not k.endswith("_start")},
            "data_confidence": self.data_confidence,
            "wacc_method": self.wacc_method,
            "recommendation": self.recommendation,
            "upside_pct": self.upside_pct,
            "blended_ev_M": self.blended_ev,
            "valuation_notes": self.valuation_notes,
            "warnings": self.warnings,
            "errors": self.errors,
        }
        _logger.info(json.dumps(payload))


def new_run(company: str, company_type: str = "public") -> RunLog:
    return RunLog(company=company, company_type=company_type)
