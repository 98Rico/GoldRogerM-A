from pydantic import Field
from typing import Optional

from .shared import Model, Risk, Competitor, Projection, KeyMetric, ValuationMethod, DCFAssumptions, ScenarioSummary


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


class MarketAnalysis(Model):
    market_size: Optional[str] = None
    market_growth: Optional[str] = None
    market_segment: Optional[str] = None
    key_trends: list[str] = []
    main_competitors: list[Competitor] = []
    company_market_share: Optional[str] = None
    competitive_position: Optional[str] = None
    sources: list[str] = []


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


class Valuation(Model):
    current_price: Optional[str] = None
    currency: Optional[str] = "USD"
    methods: list[ValuationMethod] = []
    implied_value: Optional[str] = None
    target_price: Optional[str] = None
    upside_downside: Optional[str] = None
    recommendation: Optional[str] = "HOLD"
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


class FootballField(Model):
    """Bear/Base/Bull scenario table + ranges per valuation method."""
    bear: Optional[ScenarioSummary] = None
    base: Optional[ScenarioSummary] = None
    bull: Optional[ScenarioSummary] = None
    dcf_range: Optional[str] = None
    comps_range: Optional[str] = None
    blended_range: Optional[str] = None


class PeerComp(Model):
    name: str
    ticker: str
    bucket: Optional[str] = None
    market_cap: Optional[str] = None
    ev_ebitda: Optional[str] = None
    ev_revenue: Optional[str] = None
    ebitda_margin: Optional[str] = None
    revenue_growth: Optional[str] = None
    similarity: Optional[str] = None
    weight: Optional[str] = None
    include_reason: Optional[str] = None


class PeerCompsTable(Model):
    peers: list[PeerComp] = []
    median_ev_ebitda: Optional[str] = None
    median_ev_revenue: Optional[str] = None
    median_ebitda_margin: Optional[str] = None
    n_peers: int = 0


class ICScoreSummary(Model):
    ic_score: Optional[str] = None
    recommendation: Optional[str] = None
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
    company_type: str
    fundamentals: Fundamentals
    market: MarketAnalysis
    financials: Financials
    valuation: Valuation
    thesis: InvestmentThesis
    football_field: Optional[FootballField] = None
    peer_comps: Optional[PeerCompsTable] = None
    ic_score: Optional[ICScoreSummary] = None
    data_quality: Optional[dict] = None
    sources_md: Optional[str] = None
