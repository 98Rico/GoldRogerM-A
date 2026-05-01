"""
Central configuration for Gold Roger.

All hardcoded thresholds and constants live here.
Override any value by subclassing or passing a custom config instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WACCConfig:
    risk_free_rate: float = 0.045        # 10-yr US Treasury proxy, April 2025
    equity_risk_premium: float = 0.055   # Damodaran long-run ERP


@dataclass
class LBOConfig:
    min_irr: float = 0.15            # minimum acceptable IRR for LBO feasibility
    max_leverage: float = 6.5        # maximum entry leverage (Debt/EBITDA)
    fcf_sweep_rate: float = 0.75     # fraction of FCF used for debt paydown
    mega_cap_skip_usd_bn: float = 500.0  # skip LBO for companies above this MCap ($B)


@dataclass
class ICScoreConfig:
    strong_buy_threshold: int = 75   # >= 75 → STRONG BUY
    buy_threshold: int = 60          # >= 60 → BUY
    watch_threshold: int = 45        # >= 45 → WATCH, else NO GO
    min_lbo_score: float = 2.0       # below this → hard NO GO regardless of total
    growth_equity_ev_rev: float = 12.0    # EV/Revenue above this → LBO structurally N/A
    growth_equity_ev_ebitda: float = 25.0 # EV/EBITDA above this → LBO structurally N/A


@dataclass
class AgentConfig:
    min_call_gap_s: float = 3.0      # minimum seconds between LLM calls (Mistral free tier)
    max_tool_rounds: int = 3         # max web_search iterations per agent call
    parallel_workers: int = 2        # ThreadPoolExecutor workers (keep low for free-tier APIs)


@dataclass
class GoldRogerConfig:
    wacc: WACCConfig = field(default_factory=WACCConfig)
    lbo: LBOConfig = field(default_factory=LBOConfig)
    ic_score: ICScoreConfig = field(default_factory=ICScoreConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)


DEFAULT_CONFIG = GoldRogerConfig()
