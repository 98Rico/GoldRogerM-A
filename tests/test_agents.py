"""Tests for agent layer — mock LLMProvider to return fixture JSON."""
from unittest.mock import MagicMock
from goldroger.agents.llm_client import LLMResponse


def _mock_llm(content: str) -> MagicMock:
    """Return a mock LLMProvider that always returns the given text content."""
    resp = LLMResponse(content=content, tool_calls=[])
    mock = MagicMock()
    mock.complete.return_value = resp
    mock.resolve_model.return_value = "mock-model"
    mock.format_assistant_with_tools.return_value = {}
    mock.format_tool_result.return_value = {}
    return mock


_FUNDAMENTALS_JSON = """{
  "company_name": "Acme Corp",
  "description": "A test company.",
  "business_model": "SaaS",
  "competitive_advantages": ["brand", "network effects"],
  "key_risks": [],
  "sources": []
}"""

_MARKET_JSON = """{
  "market_size": "$50B",
  "market_growth": "8% CAGR",
  "key_trends": ["AI adoption", "cloud shift"],
  "main_competitors": [],
  "sources": []
}"""

_FINANCIALS_JSON = """{
  "revenue_current": "500",
  "revenue_growth": "0.15",
  "ebitda_margin": "0.22",
  "projections": [],
  "key_metrics": [],
  "income_statement": [],
  "sources": []
}"""

_THESIS_JSON = """{
  "thesis": "Acme has a defensible market position.",
  "catalysts": ["product expansion"],
  "key_questions": ["sustainable moat?"],
  "sources": []
}"""


def test_data_collector_agent_returns_json():
    from goldroger.agents.specialists import DataCollectorAgent
    from goldroger.models import Fundamentals
    from goldroger.utils.json_parser import parse_model

    agent = DataCollectorAgent(client=_mock_llm(_FUNDAMENTALS_JSON))
    raw = agent.run("Acme Corp", "public")
    result = parse_model(raw, Fundamentals, Fundamentals(
        company_name="fallback", description="", business_model=""
    ))
    assert result.company_name == "Acme Corp"
    assert result.business_model == "SaaS"


def test_sector_analyst_agent_returns_market():
    from goldroger.agents.specialists import SectorAnalystAgent
    from goldroger.models import MarketAnalysis
    from goldroger.utils.json_parser import parse_model

    agent = SectorAnalystAgent(client=_mock_llm(_MARKET_JSON))
    raw = agent.run("Acme Corp", "public")
    result = parse_model(raw, MarketAnalysis, MarketAnalysis())
    assert result.market_size == "$50B"


def test_financial_modeler_agent_returns_financials():
    from goldroger.agents.specialists import FinancialModelerAgent
    from goldroger.models import Financials
    from goldroger.utils.json_parser import parse_model

    agent = FinancialModelerAgent(client=_mock_llm(_FINANCIALS_JSON))
    raw = agent.run("Acme Corp", "public")
    result = parse_model(raw, Financials, Financials())
    assert result.revenue_current == "500"


def test_report_writer_agent_returns_thesis():
    from goldroger.agents.specialists import ReportWriterAgent
    from goldroger.models import InvestmentThesis
    from goldroger.utils.json_parser import parse_model

    agent = ReportWriterAgent(client=_mock_llm(_THESIS_JSON))
    raw = agent.run("Acme Corp", "public")
    result = parse_model(raw, InvestmentThesis, InvestmentThesis(thesis="fallback"))
    assert "defensible" in result.thesis


def test_agent_retries_on_transient_error():
    """BaseAgent.run() should retry on transient exceptions."""
    from goldroger.agents.specialists import FinancialModelerAgent

    call_count = 0

    class _FailThenSucceed:
        def resolve_model(self, tier):
            return "mock"
        def format_assistant_with_tools(self, r):
            return {}
        def format_tool_result(self, id, result):
            return {}
        def complete(self, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient error")
            return LLMResponse(content=_FINANCIALS_JSON, tool_calls=[])

    agent = FinancialModelerAgent(client=_FailThenSucceed())
    agent.max_retries = 2
    raw = agent.run("Acme Corp", "public")
    assert call_count == 2
    assert "revenue" in raw


def test_agent_raises_after_max_retries():
    """BaseAgent.run() should re-raise after exhausting retries."""
    import pytest
    from goldroger.agents.specialists import FinancialModelerAgent

    class _AlwaysFails:
        def resolve_model(self, tier): return "mock"
        def format_assistant_with_tools(self, r): return {}
        def format_tool_result(self, id, result): return {}
        def complete(self, **kwargs):
            raise RuntimeError("persistent error")

    agent = FinancialModelerAgent(client=_AlwaysFails())
    agent.max_retries = 1
    with pytest.raises(RuntimeError, match="persistent error"):
        agent.run("Acme Corp", "public")


def test_agent_cli_mode_raises_capacity_immediately(monkeypatch):
    """In CLI mode, capacity/rate-limit errors should not trigger long backoff retries."""
    import pytest
    import goldroger.agents.base as base_mod
    from goldroger.agents.errors import APICapacityError
    from goldroger.agents.specialists import FinancialModelerAgent

    class _AlwaysRateLimited:
        def resolve_model(self, tier): return "mock"
        def format_assistant_with_tools(self, r): return {}
        def format_tool_result(self, id, result): return {}
        def complete(self, **kwargs):
            raise RuntimeError('API error occurred: Status 429. code="3505" service_tier_capacity_exceeded')

    sleep_calls = {"n": 0}

    def _sleep_stub(_s):
        sleep_calls["n"] += 1

    monkeypatch.setattr(base_mod, "_MIN_CALL_GAP", 0.0, raising=False)
    monkeypatch.setattr(base_mod.time, "sleep", _sleep_stub, raising=True)

    agent = FinancialModelerAgent(client=_AlwaysRateLimited())
    agent.max_retries = 2
    with pytest.raises(APICapacityError):
        agent.run("Acme Corp", "public", context={"cli_mode": True})
    # No retry backoff sleeps should happen in CLI mode.
    assert sleep_calls["n"] == 0


def test_agent_non_cli_still_retries_on_capacity(monkeypatch):
    """Outside CLI mode, transient capacity errors keep retry behavior."""
    import goldroger.agents.base as base_mod
    from goldroger.agents.specialists import FinancialModelerAgent

    call_count = 0
    sleep_calls = {"n": 0}

    class _FailOnceCapacityThenSucceed:
        def resolve_model(self, tier): return "mock"
        def format_assistant_with_tools(self, r): return {}
        def format_tool_result(self, id, result): return {}
        def complete(self, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("429 rate_limited")
            return LLMResponse(content=_FINANCIALS_JSON, tool_calls=[])

    def _sleep_stub(_s):
        sleep_calls["n"] += 1

    monkeypatch.setattr(base_mod, "_MIN_CALL_GAP", 0.0, raising=False)
    monkeypatch.setattr(base_mod.time, "sleep", _sleep_stub, raising=True)

    agent = FinancialModelerAgent(client=_FailOnceCapacityThenSucceed())
    agent.max_retries = 2
    raw = agent.run("Acme Corp", "public", context={"cli_mode": False})
    assert "revenue" in raw
    assert call_count == 2
    # One backoff sleep for the retry path.
    assert sleep_calls["n"] == 1
