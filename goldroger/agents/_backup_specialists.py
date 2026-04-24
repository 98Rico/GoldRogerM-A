"""
Specialized agents for Gold Roger.
Each agent is responsible for one analytical brick.
"""
from datetime import datetime
from .base import BaseAgent

CURRENT_YEAR = datetime.now().year


class DataCollectorAgent(BaseAgent):
    """Agent 1 — Collects fundamentals, business model, competitive position."""
    name = "DataCollector"

    def _system_prompt(self) -> str:
        return (
            "You are a senior equity research analyst at a top investment bank. "
            "You MUST use web_search to find real, current information about the company. "
            "Search for the company's official website, Wikipedia, press releases, "
            "annual reports, and recent news articles. "
            "Respond ONLY with a valid JSON object — no markdown fences, no preamble, no explanation. "
            "Never wrap the JSON in ``` and do not include comments."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        data_hint = (
            "Find their latest annual report, investor relations page, and SEC/AMF filings."
            if company_type == "public"
            else "Find their latest funding rounds, Crunchbase profile, LinkedIn, and any press coverage."
        )
        return f"""Search for "{company}" ({company_type} company). {data_hint}

Return ONLY this JSON object with real data found:
{{
  "company_name": "full official name",
  "ticker": "stock ticker or null",
  "sector": "specific industry sector",
  "founded": "founding year",
  "headquarters": "city, country",
  "employees": "approximate headcount",
  "description": "2-3 sentence factual description",
  "business_model": "detailed paragraph on revenue model and how they make money",
  "competitive_advantages": ["specific advantage 1", "advantage 2", "advantage 3"],
  "key_risks": [
    {{"level": "high", "text": "specific risk description"}},
    {{"level": "med", "text": "specific risk description"}},
    {{"level": "low", "text": "specific risk description"}}
  ],
  "market_position": "brief statement on market leadership or position"
  ,"sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class SectorAnalystAgent(BaseAgent):
    """Agent 2 — Market sizing, competitive landscape, trends."""
    name = "SectorAnalyst"

    def _system_prompt(self) -> str:
        return (
            "You are a market research expert and strategy consultant. "
            "Use web_search to find real market data, industry reports, and competitive intelligence. "
            "Respond ONLY with a valid JSON object — no markdown fences, no preamble. "
            "Never wrap the JSON in ```."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        sector = context.get("sector", "")
        business = context.get("business_model", "") or context.get("description", "")
        return f"""Research the market for "{company}"{f" in the {sector} sector" if sector else ""}.

CRITICAL:
- Do NOT use an overly broad market (e.g. "global luxury market") if the company operates in a narrower segment.
- Pick the most relevant sub-segment (e.g. "personal luxury goods", "luxury leather goods", "premium handbags") and size THAT market.
- Report your scope explicitly in market_segment.

Context about the company (may be incomplete): {business}

Search for market size reports, industry analysis, and competitor profiles. Use queries like:
- "{company} segment handbags personal luxury goods"
- "personal luxury goods market size 2024"
- "luxury leather goods market size 2024 CAGR"
- "{company} competitors handbags"

Return ONLY this JSON:
{{
  "market_size": "TAM in dollars (e.g. '$12B')",
  "market_growth": "CAGR percentage (e.g. '18%')",
  "market_segment": "specific segment company operates in",
  "key_trends": ["specific trend 1", "trend 2", "trend 3", "trend 4"],
  "main_competitors": [
    {{"name": "competitor name", "market_share": "estimated share"}},
    {{"name": "competitor name", "market_share": "estimated share"}},
    {{"name": "competitor name", "market_share": "estimated share"}}
  ],
  "company_market_share": "estimated share of {company}",
  "competitive_position": "paragraph on competitive dynamics and {company}'s positioning",
  "sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class FinancialModelerAgent(BaseAgent):
    """Agent 3 — P&L extraction, margin analysis, 3-year projections."""
    name = "FinancialModeler"
    max_tokens = 2500

    def _system_prompt(self) -> str:
        return (
            "You are a financial analyst specializing in financial modeling. "
            "Use web_search to find real financial data: annual reports, earnings releases, "
            "investor presentations, news about revenue figures. "
            "For private companies, use credible secondary sources and triangulate estimates. "
            "Respond ONLY with a valid JSON object — no markdown fences, no preamble. "
            "If exact numbers are unavailable, provide clearly marked estimates (e.g. '$1.2B (est.)')."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        sector = context.get("sector", "")
        business = context.get("business_model", "") or context.get("description", "")
        if company_type == "public":
            data_sources = (
                "Search for their latest annual report, earnings releases, and financial statements. "
                "Prefer primary filings and investor relations."
            )
        else:
            data_sources = (
                "Search for disclosed revenue/sales figures from credible secondary sources: "
                "press interviews, reputable trade publications, industry reports, and well-known sector sites. "
                "If EBITDA is not disclosed, infer an EBITDA margin range from close peers and clearly mark it as (est.). "
                "Prefer EUR for European private brands when appropriate."
            )
        y0, y1, y2, y3 = CURRENT_YEAR - 1, CURRENT_YEAR, CURRENT_YEAR + 1, CURRENT_YEAR + 2
        return f"""Find financial data for "{company}" ({company_type}). {data_sources}

Context (may be incomplete):
- Sector: {sector or "N/A"}
- Company description/business model: {business or "N/A"}

IMPORTANT (private companies):
- It is better to return a reasonable estimate range than "N/A" for revenue/EBITDA margin if credible sources exist.
- Label estimates explicitly: "€1.5B–€2.0B (est.)" or "15–20% (est.)".
- Use web_search with queries in local language too (e.g. French): "{company} chiffre d'affaires", "{company} ventes", "{company} EBITDA marge".

Return ONLY this JSON (use "N/A" if data unavailable, never null for string fields):
{{
  "revenue_current": "latest annual revenue in $ (e.g. '$4.2B')",
  "revenue_growth": "YoY revenue growth (e.g. '+22%')",
  "ebitda_margin": "EBITDA margin (e.g. '18%')",
  "net_margin": "net profit margin (e.g. '12%')",
  "gross_margin": "gross margin (e.g. '65%')",
  "debt_to_equity": "D/E ratio or 'N/A'",
  "free_cash_flow": "latest FCF (e.g. '$800M')",
  "projections": [
    {{"year": "{y1}", "revenue": "projected $", "growth": "%", "ebitda_margin": "%"}},
    {{"year": "{y2}", "revenue": "projected $", "growth": "%", "ebitda_margin": "%"}},
    {{"year": "{y3}", "revenue": "projected $", "growth": "%", "ebitda_margin": "%"}}
  ],
  "key_metrics": [
    {{"name": "metric relevant to this business", "value": "value", "delta": "YoY change"}}
  ],
  "income_statement": [
    {{"line": "Revenue", "values": ["FY{y0-1}", "FY{y0}", "FY{y1}E", "FY{y2}E"]}},
    {{"line": "Gross Profit", "values": ["...", "...", "...", "..."]}},
    {{"line": "EBITDA", "values": ["...", "...", "...", "..."]}},
    {{"line": "Net Income", "values": ["...", "...", "...", "..."]}}
  ],
  "sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class ValuationEngineAgent(BaseAgent):
    """Agent 4 — DCF, trading comps, transaction comps, football field."""
    name = "ValuationEngine"
    model = "mistral-large-latest"
    max_tokens = 2048

    def _system_prompt(self) -> str:
        return (
            "You are a valuation expert at a top M&A advisory firm. "
            "Use web_search to find: current stock price, EV/EBITDA multiples of comparable public companies, "
            "recent M&A transaction multiples in the sector, analyst price targets. "
            "Respond ONLY with a valid JSON object — no markdown fences, no preamble. "
            "If the company is private and market prices are unavailable, use a last-round valuation "
            "or infer a value range from comparable multiples and clearly mark it as an estimate."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        revenue = context.get("revenue_current", "unknown")
        sector = context.get("sector", "")
        return f"""Build a valuation for "{company}" (sector: {sector}, latest revenue: {revenue}).
Search for: comparable public company multiples, recent sector M&A transactions, analyst targets.

Return ONLY this JSON:
{{
  "current_price": "current stock price or latest valuation/last round $",
  "currency": "USD or EUR",
  "methods": [
    {{"name": "DCF (5Y)", "low": "$X", "mid": "$X", "high": "$X", "current_pct": "% vs current", "weight": 40}},
    {{"name": "EV/EBITDA Comps", "low": "$X", "mid": "$X", "high": "$X", "current_pct": "%", "weight": 30}},
    {{"name": "EV/Revenue Comps", "low": "$X", "mid": "$X", "high": "$X", "current_pct": "%", "weight": 20}},
    {{"name": "Precedent Transactions", "low": "$X", "mid": "$X", "high": "$X", "current_pct": "%", "weight": 10}}
  ],
  "implied_value": "weighted average target $",
  "upside_downside": "+X% or -X%",
  "recommendation": "BUY or HOLD or SELL",
  "dcf_assumptions": {{
    "wacc": "X%",
    "terminal_growth": "X%",
    "projection_years": "5"
  }},
  "comparable_multiples": {{
    "ev_ebitda": "Xx",
    "ev_revenue": "Xx",
    "pe": "Xx"
  }},
  "sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class ReportWriterAgent(BaseAgent):
    """Agent 5 — Investment thesis, scenarios, catalysts."""
    name = "ReportWriter"
    model = "mistral-large-latest"
    max_tokens = 2048

    def _system_prompt(self) -> str:
        return (
            "You are a senior M&A banker and equity research director. "
            "Write with the precision and style of a Goldman Sachs or Morgan Stanley research note. "
            "Use web_search if needed for recent news, catalysts, or analyst views. "
            "Respond ONLY with a valid JSON object — no markdown fences, no preamble. "
            "Never wrap the JSON in ```; return raw JSON only. "
            "All string values must be single-line (no literal newlines)."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        rec = context.get("recommendation", "HOLD")
        upside = context.get("upside_downside", "")
        return f"""Write a complete investment thesis for "{company}". 
Recommendation context: {rec} with {upside} upside/downside.

Return ONLY this JSON:
{{
  "thesis": "3-4 paragraph investment thesis with specific evidence, numbers, and rationale. Reference competitive moat, financial trajectory, and valuation support.",
  "bull_case": "Specific bull scenario with 2-3 concrete drivers and outcome",
  "base_case": "Base scenario — most likely outcome with key assumptions",
  "bear_case": "Bear scenario with specific downside risks and triggers",
  "catalysts": [
    "Specific near-term catalyst 1 (e.g. product launch, regulation, earnings)",
    "Catalyst 2",
    "Catalyst 3"
  ],
  "key_questions": [
    "Critical strategic question for due diligence 1",
    "Question 2",
    "Question 3"
  ],
  "sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


# ── M&A workflow agents (optional) ──────────────────────────────────────────


class DealSourcingAgent(BaseAgent):
    """M&A — Identify targets, partnerships, divestitures, expansion plays."""

    name = "DealSourcing"
    model = "mistral-small-latest"
    max_tokens = 2200

    def _system_prompt(self) -> str:
        return (
            "You are an expert M&A analyst focused on deal sourcing and pipeline building. "
            "Use web_search to identify real companies and opportunities. "
            "Respond ONLY with a valid JSON object — no markdown, no code fences."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        acquirer = context.get("acquirer", "")
        objective = context.get("objective", "")
        sector = context.get("sector", "")
        return f"""We are advising {acquirer or "an acquirer"} on M&A opportunities involving "{company}".
- Target sector: {sector or "N/A"}
- Objective: {objective or "expand capabilities / grow share / enter new geographies"}
- Company type: {company_type}

Tasks:
1) Identify acquisition targets / merger opportunities / strategic partnerships / divestiture angles.
2) Provide a short screening criteria list we can use to build a pipeline.

Return ONLY this JSON:
{{
  "acquirer_objective": "1 sentence",
  "screening_criteria": ["criterion 1", "criterion 2", "criterion 3", "criterion 4"],
  "opportunities": [
    {{"name": "company/opportunity name", "geography": "country/region", "est_size": "revenue/EV if known or 'N/A'", "rationale": "2-3 sentences", "notes": "any constraints / ownership / rumored interest"}},
    {{"name": "company/opportunity name", "geography": "country/region", "est_size": "revenue/EV", "rationale": "2-3 sentences", "notes": ""}}
  ],
  "sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class StrategicFitAgent(BaseAgent):
    """M&A — Strategic fit, synergies, integration risk, deal structure."""

    name = "StrategicFit"
    model = "mistral-large-latest"
    max_tokens = 2200

    def _system_prompt(self) -> str:
        return (
            "You are an expert M&A strategist. Evaluate strategic fit and synergies like a bulge-bracket banker. "
            "Use web_search as needed for strategy context, overlaps, geographies, product lines. "
            "Respond ONLY with JSON, no markdown/code fences."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        acquirer = context.get("acquirer", "")
        objective = context.get("objective", "")
        sector = context.get("sector", "")
        return f"""Assess strategic fit for a potential transaction involving:
- Target: {company} ({company_type})
- Acquirer: {acquirer or "N/A"}
- Objective: {objective or "N/A"}
- Sector: {sector or "N/A"}

Return ONLY this JSON:
{{
  "fit_score": "High/Medium/Low",
  "key_synergies": [
    {{"type": "revenue", "description": "specific synergy", "est_impact": "$X (est.) or 'N/A'", "timing": "0-12m / 12-24m / 24m+"}},
    {{"type": "cost", "description": "specific synergy", "est_impact": "$X (est.) or 'N/A'", "timing": "0-12m"}}
  ],
  "integration_complexity": "Low/Medium/High with 1-sentence justification",
  "integration_risks": ["risk 1", "risk 2", "risk 3"],
  "recommended_structure": "full buyout / majority / minority / JV + 1 sentence why"
  ,"sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class DueDiligenceAgent(BaseAgent):
    """M&A — Diligence support: requests, red flags, value drivers."""

    name = "DueDiligence"
    model = "mistral-large-latest"
    max_tokens = 2200

    def _system_prompt(self) -> str:
        return (
            "You are an M&A diligence lead. Create a diligence plan and identify red flags. "
            "Use web_search to find any known controversies, legal issues, regulatory constraints, "
            "ownership structure, and customer concentration hints. "
            "Respond ONLY with JSON, no markdown/code fences."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        sector = context.get("sector", "")
        return f"""Build a due diligence plan for acquiring/partnering with "{company}" ({company_type}) in sector {sector or "N/A"}.

Return ONLY this JSON:
{{
  "key_requests": [
    "Last 3 years audited financials (or management accounts)",
    "Customer cohort & concentration",
    "Pipeline/backlog and churn",
    "Key contracts + change of control clauses",
    "Tax structure and intercompany agreements",
    "Litigation / regulatory matters"
  ],
  "value_drivers": ["driver 1", "driver 2", "driver 3"],
  "red_flags": [
    {{"area": "legal", "severity": "high", "finding": "specific risk", "mitigation": "mitigation idea"}},
    {{"area": "commercial", "severity": "med", "finding": "specific risk", "mitigation": "mitigation idea"}}
  ],
  "sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class DealExecutionAgent(BaseAgent):
    """M&A — Execution support: memo, negotiation, approvals, process."""

    name = "DealExecution"
    model = "mistral-small-latest"
    max_tokens = 2000

    def _system_prompt(self) -> str:
        return (
            "You are an M&A execution analyst. Provide a practical workplan and materials list. "
            "Respond ONLY with JSON, no markdown/code fences."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        acquirer = context.get("acquirer", "")
        return f"""Provide a deal execution checklist for a transaction involving:
- Target: {company} ({company_type})
- Acquirer: {acquirer or "N/A"}

Return ONLY this JSON:
{{
  "workplan": [
    "Define deal thesis and valuation range",
    "NDA + teaser/CIM review (if sell-side)",
    "Management meeting + Q&A log",
    "Data room diligence workstreams",
    "Synergy model + integration plan",
    "SPA negotiation + financing docs",
    "Board memo and approvals"
  ],
  "key_materials": ["Investment memo", "Valuation model", "Synergy case", "Integration plan", "Risk register"],
  "negotiation_points": ["price mechanism", "reps & warranties", "escrow/holdback", "earn-out", "MAC clause"],
  "approvals": ["board approval", "regulatory filings", "antitrust (if applicable)", "works council (if applicable)"]
}}"""


class LBOAgent(BaseAgent):
    """M&A — High-level LBO feasibility & IRR ranges (when relevant)."""

    name = "LBO"
    model = "mistral-large-latest"
    max_tokens = 2000

    def _system_prompt(self) -> str:
        return (
            "You are a private equity / LBO specialist. Provide a high-level LBO view. "
            "Use web_search for any leverage norms and comps. "
            "Respond ONLY with JSON, no markdown/code fences."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        sector = context.get("sector", "")
        return f"""Assess LBO feasibility for "{company}" ({company_type}) in sector {sector or "N/A"}.

Return ONLY this JSON:
{{
  "feasible": true,
  "entry_multiple": "Xx EV/EBITDA (or 'N/A')",
  "leverage": "X.0x net debt/EBITDA (or 'N/A')",
  "exit_multiple": "Xx EV/EBITDA (or 'N/A')",
  "irr_range": "e.g. 15–25% (est.)",
  "key_sensitivities": ["revenue growth", "margin expansion", "entry multiple", "exit multiple", "leverage"],
  "sources": ["Title — https://example.com", "Title — https://example.com"]
}}"""


class PipelineBuilderAgent(BaseAgent):
    """M&A — Build an acquisition pipeline (targets + private valuation estimates)."""

    name = "PipelineBuilder"
    model = "mistral-large-latest"
    max_tokens = 3000

    def _system_prompt(self) -> str:
        return (
            "You are a top-tier M&A analyst preparing an acquisition pipeline for an investment committee. "
            "You MUST use web_search and triangulate private-company estimates from credible secondary sources. "
            "You must generate targets yourself (do not ask the user for company names). "
            "Respond ONLY with valid JSON (no markdown/code fences). "
            "CRITICAL: revenue_range, revenue_working, ebitda_margin, implied_ev must NEVER be 'N/A'. "
            "If uncertain, provide a reasonable estimated range and label it '(est.)'. "
            "Every numeric claim should be supported by at least one item in `sources` (title + URL)."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        buyer = context.get("buyer", "a global consumer goods group")
        focus = context.get(
            "focus",
            "premium beauty and wellness; high-growth founder-led private companies in Europe; skincare, wellness, premium personal care; younger consumers; DTC",
        )
        return f"""Scenario:
Buyer: {buyer}
Focus: {focus}

Task:
1) Identify a shortlist of 8–12 PRIVATE, founder-led companies in Europe that fit the focus.
2) For EACH target, provide an estimated valuation with triangulated revenue and EBITDA assumptions.

Rules:
- Prefer premium positioning, strong brand/loyalty, DTC/digital strength, international expansion potential.
- Provide synergies and key risks tailored to a strategic buyer.
- Use credible secondary sources (press interviews, reputable trade publications, market intelligence, job postings, store footprint, employee count, channel mix, peer benchmarking).
- If exact numbers are not available, produce a working estimate and a range, explicitly marked as '(est.)'.
- Do not output placeholders like 'N/A' for revenue_range, revenue_working, ebitda_margin, implied_ev.

Return ONLY this JSON:
{{
  "buyer": "{buyer}",
  "thesis": "2-4 sentences investment thesis",
  "focus": "{focus}",
  "screening_criteria": ["criterion 1", "criterion 2", "criterion 3", "criterion 4", "criterion 5"],
  "targets": [
    {{
      "name": "Target name",
      "headquarters": "City, Country",
      "geography": "Europe region focus",
      "segment": "Skincare / Wellness / Personal care",
      "positioning": "Premium / Luxury / Masstige",
      "channels": ["DTC", "Retail", "Wholesale"],
      "founder_led": true,
      "why_attractive": ["bullet 1", "bullet 2", "bullet 3"],
      "strategic_value": ["value 1", "value 2"],
      "synergies": ["synergy 1", "synergy 2"],
      "key_risks": ["risk 1", "risk 2"],
      "revenue_range": "€Xm–€Ym (est.)",
      "revenue_working": "€Zm (est.)",
      "ebitda_margin": "AA–BB% (est.)",
      "implied_ev": "€X–€Y (est.)",
      "valuation_rationale": ["peer multiples used", "growth/margin logic", "any transaction comps"],
      "sources": ["Title — https://example.com", "Title — https://example.com"]
    }}
  ],
  "next_steps": ["next step 1", "next step 2", "next step 3"]
}}"""
