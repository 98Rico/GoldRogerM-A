"""
Base agent — provider-agnostic LLM wrapper with web_search tool + retry logic.
All specialized agents inherit from this.

Switch providers via LLM_PROVIDER env var or --llm CLI flag:
    LLM_PROVIDER=mistral    (default, free)
    LLM_PROVIDER=anthropic  (Claude — better thesis/DD quality)
    LLM_PROVIDER=openai     (GPT-4o)
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date

import httpx
from dotenv import load_dotenv

from goldroger.config import DEFAULT_CONFIG as _cfg
from .llm_client import LLMProvider, build_llm_provider

load_dotenv()

# Global rate limiter: enforce minimum gap between LLM API calls (thread-safe)
_last_api_call: float = 0.0
_MIN_CALL_GAP: float = _cfg.agent.min_call_gap_s
_rate_lock = threading.Lock()


def _rate_limit_wait() -> None:
    global _last_api_call
    with _rate_lock:
        elapsed = time.monotonic() - _last_api_call
        if elapsed < _MIN_CALL_GAP:
            time.sleep(_MIN_CALL_GAP - elapsed)
        _last_api_call = time.monotonic()

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for real-time information about companies, markets, "
            "financial data, stock prices, earnings, and industry analysis. "
            "Use specific queries like '<company> annual revenue 2024' or "
            "'<sector> market size CAGR'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                }
            },
            "required": ["query"],
        },
    },
}

_HTTP = httpx.Client(
    timeout=15,
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    },
    follow_redirects=True,
)


def _yahoo_finance_data(query: str) -> str:
    """Fetch financial data from Yahoo Finance search + quoteSummary."""
    try:
        search = _HTTP.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 3, "newsCount": 3},
        )
        data = search.json()
        quotes = data.get("quotes", [])
        news = data.get("news", [])

        parts: list[str] = []

        headlines = [f"• {n['title']}" for n in news[:3] if n.get("title")]
        if headlines:
            parts.append("Recent news:\n" + "\n".join(headlines))

        if not quotes:
            return "\n".join(parts)

        ticker = quotes[0].get("symbol", "")
        short_name = quotes[0].get("shortname", "")
        if ticker:
            parts.append(f"Ticker: {ticker}  ({short_name})")
            parts.append(f"Source: https://finance.yahoo.com/quote/{ticker}")

        if not ticker:
            return "\n".join(parts)

        summary = _HTTP.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
            params={
                "modules": (
                    "summaryDetail,financialData,"
                    "defaultKeyStatistics,incomeStatementHistory"
                )
            },
        )
        result = (
            (summary.json().get("quoteSummary") or {})
            .get("result") or [{}]
        )[0]

        fd = result.get("financialData", {})
        if fd:
            parts.append(
                "Financials:\n"
                + "\n".join(
                    f"  {k}: {v.get('fmt', 'N/A')}"
                    for k, v in {
                        "Current Price": fd.get("currentPrice", {}),
                        "Revenue (TTM)": fd.get("totalRevenue", {}),
                        "Revenue Growth": fd.get("revenueGrowth", {}),
                        "Gross Margin": fd.get("grossMargins", {}),
                        "EBITDA Margin": fd.get("ebitdaMargins", {}),
                        "Net Margin": fd.get("profitMargins", {}),
                        "Free Cash Flow": fd.get("freeCashflow", {}),
                        "EBITDA": fd.get("ebitda", {}),
                        "Debt/Equity": fd.get("debtToEquity", {}),
                        "Target Price": fd.get("targetMeanPrice", {}),
                        "Analyst Rec.": {"fmt": fd.get("recommendationKey", "N/A")},
                    }.items()
                    if v
                )
            )

        ks = result.get("defaultKeyStatistics", {})
        if ks:
            parts.append(
                "Key Statistics:\n"
                + "\n".join(
                    f"  {k}: {v.get('fmt', 'N/A')}"
                    for k, v in {
                        "Market Cap": ks.get("marketCap", {}),
                        "Enterprise Value": ks.get("enterpriseValue", {}),
                        "EV/EBITDA": ks.get("enterpriseToEbitda", {}),
                        "EV/Revenue": ks.get("enterpriseToRevenue", {}),
                        "Forward P/E": ks.get("forwardPE", {}),
                        "Beta": ks.get("beta", {}),
                    }.items()
                    if v
                )
            )

        return "\n\n".join(parts)
    except Exception as exc:
        return f"(Yahoo Finance error: {exc})"


def _duckduckgo_search(query: str, max_results: int = 5) -> str:
    """POST to DuckDuckGo HTML endpoint and extract result snippets."""
    try:
        resp = _HTTP.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"Accept": "text/html"},
        )
        text = resp.text
        results: list[str] = []

        # Each result block ends at </div></div></div>
        blocks = re.findall(
            r'class="result__body">(.*?)</div>\s*</div>',
            text,
            re.DOTALL,
        )
        for i, block in enumerate(blocks[:max_results]):
            title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
            href_m = re.search(r'class="result__a"[^>]*href="([^"]+)"', block, re.DOTALL)
            snip_m = re.search(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span|div)',
                block,
                re.DOTALL,
            )
            if title_m and snip_m:
                title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                snippet = re.sub(r"<[^>]+>", "", snip_m.group(1)).strip()
                href = (href_m.group(1) if href_m else "").strip()
                # DuckDuckGo HTML often uses redirect links; keep as-is (still usable as a citation).
                line_url = f"URL: {href}" if href else "URL: N/A"
                results.append(f"[{i+1}] {title}\n   {line_url}\n   Snippet: {snippet}")

        return "\n\n".join(results) if results else "No web results found."
    except Exception as exc:
        return f"(DuckDuckGo error: {exc})"


def _execute_web_search(query: str, max_results: int = 5) -> str:
    """Combine Yahoo Finance and DuckDuckGo results."""
    parts: list[str] = []
    yf = _yahoo_finance_data(query)
    if yf.strip() and not yf.startswith("(Yahoo"):
        parts.append(f"=== Yahoo Finance ===\n{yf}")
    ddg = _duckduckgo_search(query, max_results=max_results)
    if ddg.strip() and not ddg.startswith("(Duck"):
        parts.append(f"=== Web Search ===\n{ddg}")
    return "\n\n".join(parts) if parts else f"No data found for: {query}"


def _sanitize_search_query(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return q
    # Avoid stale-year anchoring unless explicitly historical.
    if re.search(r"\b(historical|history|since|from 20\d{2})\b", q, flags=re.IGNORECASE):
        return q
    current_year = str(date.today().year)
    q = re.sub(r"\b2023\b", "latest", q)
    q = re.sub(r"\b2024\b", "latest", q)
    q = re.sub(r"\b2025\b", "latest", q) if current_year not in {"2025"} else q
    return q


class BaseAgent:
    name: str = "BaseAgent"
    model_tier: str = "small"   # "small" or "large" — resolved per provider
    max_tokens: int = 2048
    max_retries: int = 2
    max_tool_rounds: int = _cfg.agent.max_tool_rounds
    use_tools: bool = True      # False for synthesis-only agents

    def __init__(self, client: LLMProvider | None = None):
        if client is not None:
            self._llm = client
        else:
            self._llm = build_llm_provider()

    def _system_prompt(self) -> str:
        raise NotImplementedError

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        raise NotImplementedError

    def run(
        self,
        company: str,
        company_type: str = "public",
        context: dict | None = None,
        _strict_json: bool = False,
    ) -> str:
        """Call the API, handle web_search tool calls, return final text."""
        if context is None:
            context = {}
        if _strict_json:
            # Injected when previous attempt returned invalid JSON
            context = {**context, "__strict_json_hint": True}

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._user_prompt(company, company_type, context)},
        ]

        tools = [WEB_SEARCH_TOOL] if self.use_tools else None
        model = self._llm.resolve_model(self.model_tier)
        quick_mode = bool(context.get("quick_mode", False))
        effective_retries = 1 if quick_mode else self.max_retries
        effective_tool_rounds = 1 if quick_mode else self.max_tool_rounds
        max_queries = int(context.get("max_queries", 5 if quick_mode else 8))
        max_results = int(context.get("max_results", 3 if quick_mode else 5))
        seen_queries: set[str] = set()
        queries_used = 0

        for attempt in range(effective_retries + 1):
            try:
                _rate_limit_wait()
                response = self._llm.complete(
                    messages=messages,
                    model=model,
                    max_tokens=self.max_tokens,
                    tools=tools,
                )

                tool_rounds = 0
                while response.wants_tool and tool_rounds < effective_tool_rounds:
                    tool_rounds += 1
                    messages.append(self._llm.format_assistant_with_tools(response))

                    for tc in response.tool_calls:
                        query = _sanitize_search_query(tc.arguments.get("query", ""))
                        if not query or query in seen_queries:
                            result = f"Skipped duplicate/empty query: {query}"
                        elif queries_used >= max_queries:
                            result = f"Query budget reached ({max_queries})."
                        else:
                            seen_queries.add(query)
                            queries_used += 1
                            # Override ddg result depth via lightweight query suffix convention.
                            result = _execute_web_search(query, max_results=max_results)
                        messages.append(self._llm.format_tool_result(tc.id, result))

                    _rate_limit_wait()
                    response = self._llm.complete(
                        messages=messages,
                        model=model,
                        max_tokens=self.max_tokens,
                        tools=tools,
                    )

                return response.content

            except Exception as exc:
                exc_str = str(exc).lower()
                is_rate_limit = "429" in str(exc) or "rate_limit" in exc_str or "rate limit" in exc_str
                if attempt < effective_retries:
                    wait = 60 if is_rate_limit else 2 ** attempt
                    print(f"[{self.name}] Attempt {attempt + 1} failed: {exc} — retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        return ""
