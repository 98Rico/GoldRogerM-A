from typing import Optional

from .shared import Model


class PipelineTarget(Model):
    name: str
    headquarters: Optional[str] = None
    geography: Optional[str] = None
    segment: Optional[str] = None
    positioning: Optional[str] = None
    channels: list[str] = []
    founder_led: Optional[bool] = None
    why_attractive: list[str] = []
    strategic_value: list[str] = []
    synergies: list[str] = []
    key_risks: list[str] = []
    revenue_range: Optional[str] = "(est. unavailable)"
    revenue_working: Optional[str] = "(est. unavailable)"
    ebitda_margin: Optional[str] = "(est. unavailable)"
    implied_ev: Optional[str] = "(est. unavailable)"
    valuation_rationale: list[str] = []
    sources: list[str] = []


class AcquisitionPipeline(Model):
    buyer: str
    thesis: str
    focus: str
    targets: list[PipelineTarget] = []
    screening_criteria: list[str] = []
    next_steps: list[str] = []
