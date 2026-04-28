from pydantic import BaseModel, Field
from pydantic import ConfigDict
from typing import Optional


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Risk(Model):
    level: str = Field(description="high / med / low")
    text: str


class Fundamentals(Model):
    company_name: str
    ticker: Optional[str] = None
    sector: Optional[str] = None
    founded: Optional[str] = None
    headquarters: Optional[str] = None
    employees: Optional[str] = None
    description: str
    business_model: str
    competitive_advantages: list[str] = []
    key_risks: list[Risk] = []
    market_position: Optional[str] = None
    sources: list[str] = []


class Competitor(Model):
    name: str
    market_share: Optional[str] = None


class MarketAnalysis(Model):
    market_size: Optional[str] = None
    market_growth: Optional[str] = None
    market_segment: Optional[str] = None
    key_trends: list[str] = []
    main_competitors: list[Competitor] = []
    company_market_share: Optional[str] = None
    competitive_position: Optional[str] = None
    sources: list[str] = []


class Projection(Model):
    year: str
    revenue: Optional[str] = None
    growth: Optional[str] = None
    ebitda_margin: Optional[str] = None


class KeyMetric(Model):
    name: str
    value: str
    delta: Optional[str] = None


class IncomeStatementRow(Model):
    line: str
    values: list[str] = []


class Financials(Model):
    revenue_series: list[float] = Field(default_factory=list)

    revenue_current: Optional[str] = None
    revenue_growth: Optional[str] = None

    ebitda_margin: Optional[str] = None
    net_margin: Optional[str] = None
    gross_margin: Optional[str] = None

    debt_to_equity: Optional[str] = None
    free_cash_flow: Optional[str] = None

    projections: list[Projection] = Field(default_factory=list)
    key_metrics: list[KeyMetric] = Field(default_factory=list)
    income_statement: list[IncomeStatementRow] = Field(default_factory=list)

    sources: list[str] = Field(default_factory=list)


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


class Valuation(Model):
    current_price: Optional[str] = None
    currency: Optional[str] = "USD"
    methods: list[ValuationMethod] = []
    implied_value: Optional[str] = None       # human-readable EV, e.g. "$4.97T" or "$1.2B"
    target_price: Optional[str] = None        # per-share intrinsic price (public only)
    upside_downside: Optional[str] = None
    recommendation: Optional[str] = "HOLD"   # BUY/HOLD/SELL (public) or ATTRACTIVE/NEUTRAL/EXPENSIVE (private)
    dcf_assumptions: Optional[DCFAssumptions] = None
    comparable_multiples: Optional[dict] = None
    sources: list[str] = []


class InvestmentThesis(Model):
    thesis: str
    bull_case: Optional[str] = None
    base_case: Optional[str] = None
    bear_case: Optional[str] = None
    catalysts: list[str] = []
    key_questions: list[str] = []
    sources: list[str] = []


class ScenarioSummary(Model):
    """Football field row — one scenario (bear/base/bull)."""
    name: str                          # Bear / Base / Bull
    dcf_ev: Optional[str] = None       # USD millions
    comps_ev: Optional[str] = None
    blended_ev: Optional[str] = None
    wacc: Optional[str] = None
    ebitda_margin: Optional[str] = None
    narrative: Optional[str] = None    # 1-2 sentence description from thesis agent


class FootballField(Model):
    """Bear/Base/Bull scenario table + ranges per valuation method."""
    bear: Optional[ScenarioSummary] = None
    base: Optional[ScenarioSummary] = None
    bull: Optional[ScenarioSummary] = None
    dcf_range: Optional[str] = None       # "Bear $Xm — Bull $Ym"
    comps_range: Optional[str] = None
    blended_range: Optional[str] = None


class PeerComp(Model):
    """Single peer company multiple."""
    name: str
    ticker: str
    ev_ebitda: Optional[str] = None
    ev_revenue: Optional[str] = None
    ebitda_margin: Optional[str] = None
    revenue_growth: Optional[str] = None


class PeerCompsTable(Model):
    """Peer comparables table with medians."""
    peers: list[PeerComp] = []
    median_ev_ebitda: Optional[str] = None
    median_ev_revenue: Optional[str] = None
    median_ebitda_margin: Optional[str] = None
    n_peers: int = 0


class ICScoreSummary(Model):
    """IC scoring breakdown for PPT."""
    ic_score: Optional[str] = None        # "72/100"
    recommendation: Optional[str] = None  # STRONG BUY / BUY / WATCH / NO GO
    strategy: Optional[str] = None
    synergies: Optional[str] = None
    financial: Optional[str] = None
    lbo: Optional[str] = None
    integration: Optional[str] = None
    risk: Optional[str] = None
    rationale: Optional[str] = None
    next_steps: list[str] = []


class AnalysisResult(Model):
    company: str
    company_type: str  # "public" or "private"
    fundamentals: Fundamentals
    market: MarketAnalysis
    financials: Financials
    valuation: Valuation
    thesis: InvestmentThesis
    football_field: Optional[FootballField] = None
    peer_comps: Optional[PeerCompsTable] = None
    ic_score: Optional[ICScoreSummary] = None


# ── M&A Extensions ──────────────────────────────────────────────────────────


class Opportunity(Model):
    name: str
    rationale: str
    geography: Optional[str] = None
    est_size: Optional[str] = None  # revenue/EV/etc.
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
    fit_score: Optional[str] = None  # e.g. "High / Medium / Low"
    key_synergies: list[Synergy] = []
    integration_complexity: Optional[str] = None
    integration_risks: list[str] = []
    recommended_structure: Optional[str] = None  # minority, majority, full buyout, JV
    sources: list[str] = []


class DiligenceFinding(Model):
    area: str  # finance, legal, tax, ops, commercial, tech, HR
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


class PipelineTarget(Model):
    name: str
    headquarters: Optional[str] = None
    geography: Optional[str] = None
    segment: Optional[str] = None  # skincare / wellness / personal care etc.
    positioning: Optional[str] = None  # premium, masstige, luxury
    channels: list[str] = []  # DTC, retail, wholesale, marketplaces
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
    sources: list[str] = []  # short source hints (no URLs required)


class AcquisitionPipeline(Model):
    buyer: str
    thesis: str
    focus: str
    targets: list[PipelineTarget] = []
    screening_criteria: list[str] = []
    next_steps: list[str] = []
