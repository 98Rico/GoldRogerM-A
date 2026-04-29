from typing import Optional

from .shared import Model
from .equity import ICScoreSummary, FootballField, PeerCompsTable


class Opportunity(Model):
    name: str
    rationale: str
    geography: Optional[str] = None
    est_size: Optional[str] = None
    notes: Optional[str] = None


class DealSourcing(Model):
    acquirer_objective: Optional[str] = None
    opportunities: list[Opportunity] = []
    screening_criteria: list[str] = []
    sources: list[str] = []


class Synergy(Model):
    type: str  # revenue / cost / capital / tax
    description: str
    est_impact: Optional[str] = None
    timing: Optional[str] = None


class StrategicFit(Model):
    fit_score: Optional[str] = None
    key_synergies: list[Synergy] = []
    integration_complexity: Optional[str] = None
    integration_risks: list[str] = []
    recommended_structure: Optional[str] = None
    sources: list[str] = []


class DiligenceFinding(Model):
    area: str
    severity: str  # high / med / low
    finding: str
    mitigation: Optional[str] = None


class DueDiligence(Model):
    key_requests: list[str] = []
    red_flags: list[DiligenceFinding] = []
    value_drivers: list[str] = []
    sources: list[str] = []


class DealExecution(Model):
    workplan: list[str] = []
    key_materials: list[str] = []
    negotiation_points: list[str] = []
    approvals: list[str] = []


class LBOModel(Model):
    feasible: Optional[bool] = None
    entry_multiple: Optional[str] = None
    leverage: Optional[str] = None
    exit_multiple: Optional[str] = None
    irr_range: Optional[str] = None
    key_sensitivities: list[str] = []
    sources: list[str] = []


class MAResult(Model):
    company: str
    company_type: str
    acquirer: Optional[str] = None
    deal_sourcing: DealSourcing = DealSourcing()
    strategic_fit: StrategicFit = StrategicFit()
    due_diligence: DueDiligence = DueDiligence()
    deal_execution: DealExecution = DealExecution()
    lbo: LBOModel = LBOModel()
    ic_score: Optional[ICScoreSummary] = None
    football_field: Optional[FootballField] = None
    peer_comps: Optional[PeerCompsTable] = None
