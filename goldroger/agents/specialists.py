"""
Specialized agents for Gold Roger.
Each agent is responsible for one analytical brick.
"""
from datetime import datetime
from .base import BaseAgent

CURRENT_YEAR = datetime.now().year


class DataCollectorAgent(BaseAgent):
    model_tier = "large"
    use_tools = False

    def run(self, company: str, company_type: str, context: dict = None):

        prompt = f"""You are a financial data extraction system.

Return ONLY valid JSON.

Company: {company}
Type: {company_type}

Schema:
{{
  "company_name": "{company}",
  "description": "short description",
  "business_model": "how it makes money",
  "sector": "industry sector"
}}

Rules:
- ONLY JSON
- NO markdown
- NO text before or after
- if unknown, guess conservatively
- max 150 tokens"""

        try:
            response = self._llm.complete(
                messages=[{"role": "user", "content": prompt}],
                model=self._llm.resolve_model(self.model_tier),
                max_tokens=200,
            )
            content = response.content.strip() if response.content else "{}"
            return content or "{}"
        except Exception as e:
            print("[ERROR DataCollectorAgent]", e)
            return "{}"

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
    """Agent 3 — STRICT financial data extraction ONLY"""

    name = "FinancialModeler"
    max_tokens = 2000

    def _system_prompt(self) -> str:
        return (
            "You are a financial data extraction specialist inside a valuation engine. "
            "Search for real financial data. For public companies use verified filings. "
            "For private companies, use press reports, industry databases, and credible estimates — "
            "clearly tag estimated values. Return ONLY valid JSON. No markdown, no explanation."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        sector = context.get("sector", "")
        description = context.get("business_model", "") or context.get("description", "")
        is_private = company_type == "private"

        private_note = (
            "\nThis is a PRIVATE company. Public filings may not exist. "
            "Use press reports, industry research, Crunchbase, LinkedIn Revenue Estimates, "
            "or credible analyst estimates. Reasonable estimates are REQUIRED — do not return N/A "
            "if industry knowledge or press reports can provide an approximation."
        ) if is_private else ""

        return f"""Find financial data for "{company}" ({company_type}, sector: {sector or "unknown"}).
{private_note}
Description: {description or "N/A"}

Use web_search to retrieve the most recent available financials.

CRITICAL RULES:
- revenue_current MUST be a plain number (USD millions). NEVER null if any revenue figure was found.
  If you found "€350M revenue" → convert to USD → set revenue_current to "378" (350 × 1.08)
  If you found "revenue between €200M and €400M" → use midpoint → set to "324"
  If you found any mention of revenue in any source → YOU MUST populate revenue_current
- revenue_series: list oldest→newest, e.g. [280.0, 340.0, 378.0]
- All monetary values in USD millions (EUR×1.08, GBP×1.26, CHF×1.11)
- Margins as decimals: 18% → 0.18
- Only set null if absolutely no information exists anywhere

OUTPUT — return EXACTLY this JSON, no markdown, no extra keys:

{{
  "revenue_current": "<USD millions as plain number, e.g. 378 — NOT null if any revenue was found>",
  "revenue_series": [<oldest>, <middle>, <most recent>],
  "revenue_growth": "<decimal or null>",
  "ebitda_margin": "<decimal or null>",
  "net_margin": "<decimal or null>",
  "gross_margin": "<decimal or null>",
  "free_cash_flow": "<USD millions or null>",
  "debt_to_equity": "<decimal or null>",
  "sources": ["<source>"]
}}
"""

class ValuationEngineAgent(BaseAgent):
    """
    Agent 4 — STRUCTURING ONLY (NO VALUATION)
    """

    name = "ValuationEngine"
    model_tier = "large"
    max_tokens = 2000
    use_tools = False  # all data already in context, no web search needed

    def _system_prompt(self) -> str:
        return (
            "You are an investment banking associate. "
            "You do NOT compute valuation. "
            "You ONLY extract assumptions needed for valuation models. "
            "Your output feeds a deterministic Python valuation engine. "
            "Respond ONLY in JSON."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        sector = context.get("sector", "")
        revenue = context.get("revenue_current", "unknown")

        return f"""
Prepare valuation assumptions for "{company}".

Sector: {sector}
Revenue: {revenue}

TASK:
Use web_search to extract:

1. EV/EBITDA range for sector
2. EV/Revenue range
3. 5–8 comparable companies
4. 2–5 precedent transactions

IMPORTANT:
- DO NOT compute valuation
- DO NOT output prices
- ONLY return assumptions for Python engine

Return JSON:

{{
  "wacc": 0.10,
  "ev_ebitda_range": [10, 14],
  "ev_revenue_range": [2, 4],
  "tx_multiple": 12,
  "weights": {{
    "dcf": 0.5,
    "comps": 0.3,
    "transactions": 0.2
  }},
  "comparable_companies": ["A", "B", "C"],
  "sources": ["Title — URL"]
}}
"""
class ReportWriterAgent(BaseAgent):
    """Agent 5 — Investment thesis, scenarios, catalysts."""
    name = "ReportWriter"
    model_tier = "large"
    max_tokens = 2048
    use_tools = False  # synthesizes from prior agent outputs, no web search needed

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
        upside = context.get("upside_downside", context.get("upside", ""))
        verified_revenue = context.get("verified_revenue", "")
        rev_confidence = context.get("revenue_confidence", "estimated")
        rev_conf_label = "verified from filings" if rev_confidence == "verified" else "estimated — treat as approximate"
        revenue_lock = (
            f"\n⚠ VERIFIED FACTS — do NOT contradict these in your output:\n"
            f"  Revenue: ${verified_revenue}M (USD) [{rev_conf_label}]\n"
            f"  Implied EV: {context.get('valuation', 'N/A')}\n"
            f"  Recommendation: {rec}\n"
            "Use ONLY these numbers in your thesis. Never invent a different revenue figure.\n"
            f"  If revenue is marked 'estimated', you MAY note uncertainty but must use this figure."
        ) if verified_revenue and verified_revenue not in ("unknown", "0", "0.0", "null") else ""
        return f"""Write a complete investment thesis for "{company}".
Recommendation context: {rec} with {upside} upside/downside.{revenue_lock}

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
    model_tier = "small"
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
    model_tier = "large"
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
    model_tier = "large"
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
    model_tier = "small"
    max_tokens = 2000
    use_tools = False

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
    model_tier = "large"
    max_tokens = 2000
    use_tools = False

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
    model_tier = "small"
    max_tokens = 3000

    def _system_prompt(self) -> str:
        return (
            "You are a top-tier M&A analyst preparing an acquisition pipeline for an investment committee. "
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
        quick = context.get("quick", False)
        search_instruction = (
            "Use your training knowledge — do not perform web searches."
            if quick else
            "Triangulate estimates from credible secondary sources (press, trade publications, peer benchmarking)."
        )
        return f"""Scenario:
Buyer: {buyer}
Focus: {focus}

Task:
1) Identify a shortlist of exactly 3 companies that fit the focus.
2) For EACH target, provide an estimated valuation with triangulated revenue and EBITDA assumptions.
{search_instruction}

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


class PeerFinderAgent(BaseAgent):
    """
    Identifies 4–6 publicly listed comparable companies for a given target.
    Used to build real-market-data peer multiples instead of sector table averages.
    Critical for private company valuation accuracy.
    """
    name = "PeerFinder"
    max_tokens = 800

    def _system_prompt(self) -> str:
        return (
            "You are a sell-side equity research analyst. "
            "Your task is to identify the most relevant publicly listed comparable companies "
            "for a given target. Focus on business model similarity, sector, size, and geography. "
            "Return ONLY valid JSON. No markdown, no explanation."
        )

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        sector = context.get("sector", "unknown")
        description = context.get("description", "")
        revenue_m = context.get("revenue_usd_m")

        # Build a revenue scale bracket to constrain peer selection
        if revenue_m and revenue_m > 0:
            low = revenue_m * 0.25
            high = revenue_m * 4.0
            if high < 100:
                scale_note = f"Target revenue ~${revenue_m:.0f}M. Prefer peers with revenue ${ low:.0f}M–${high:.0f}M. DO NOT select mega-caps."
            elif high < 2000:
                scale_note = f"Target revenue ~${revenue_m:.0f}M. Prefer peers with revenue ${low:.0f}M–${high:.0f}M (same order of magnitude). Avoid companies >10× larger."
            else:
                scale_note = f"Target revenue ~${revenue_m:.0f}M. Match peers by revenue scale, avoid micro-caps."
        else:
            scale_note = "Revenue unknown — match peers on business model and sector."

        return f"""Find 4–6 publicly listed companies comparable to "{company}".

Context:
- Sector: {sector}
- Description: {description or "N/A"}
- Company type: {company_type}
- Scale: {scale_note}

Instructions:
- MOST IMPORTANT: match business model similarity first, then revenue scale
- {scale_note}
- Avoid selecting industry giants as peers for small/mid-size targets — it inflates multiples
- Prefer listed peers with public financials (avoid OTC/micro-caps with illiquid data)
- Use web_search if needed to verify current listings

Return ONLY this JSON:
{{
  "peers": [
    {{"name": "Company Name", "ticker": "TICK", "exchange": "NYSE/NASDAQ/LSE/etc", "rationale": "why comparable"}},
    {{"name": "Company Name", "ticker": "TICK", "exchange": "NYSE/NASDAQ/LSE/etc", "rationale": "why comparable"}}
  ]
}}"""
