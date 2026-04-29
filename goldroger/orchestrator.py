"""
Orchestrator — thin re-export for backward compatibility.

All pipeline logic lives in goldroger/pipelines/:
  pipelines/equity.py    — run_analysis()
  pipelines/ma.py        — run_ma_analysis()
  pipelines/pipeline.py  — run_pipeline()
  pipelines/_shared.py   — shared utilities (ValuationAssumptions, helpers)
"""
from goldroger.pipelines.equity import run_analysis
from goldroger.pipelines.ma import run_ma_analysis
from goldroger.pipelines.pipeline import run_pipeline

__all__ = ["run_analysis", "run_ma_analysis", "run_pipeline"]
