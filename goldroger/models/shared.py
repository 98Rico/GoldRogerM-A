from pydantic import BaseModel, ConfigDict, Field
from typing import Optional


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Risk(Model):
    level: str = Field(description="high / med / low")
    text: str


class Competitor(Model):
    name: str
    market_share: Optional[str] = None


class Projection(Model):
    year: str
    revenue: Optional[str] = None
    growth: Optional[str] = None
    ebitda_margin: Optional[str] = None


class KeyMetric(Model):
    name: str
    value: str
    delta: Optional[str] = None


class ValuationMethod(Model):
    name: str
    low: Optional[str] = None
    mid: Optional[str] = None
    high: Optional[str] = None
    current_pct: Optional[str] = None
    weight: Optional[int] = None


class DCFAssumptions(Model):
    wacc: Optional[str] = None
    terminal_growth: Optional[str] = None
    projection_years: Optional[str] = None


class ScenarioSummary(Model):
    """Football field row — one scenario (bear/base/bull)."""
    name: str
    dcf_ev: Optional[str] = None
    comps_ev: Optional[str] = None
    blended_ev: Optional[str] = None
    wacc: Optional[str] = None
    ebitda_margin: Optional[str] = None
    narrative: Optional[str] = None
