"""
Base agent — wraps Mistral AI API with web_search tool + retry logic.
All specialized agents inherit from this.
"""
import json
import os
import re
import time

import httpx
from dotenv import load_dotenv
from mistralai.client import Mistral

load_dotenv()

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


def _execute_web_search(query: str) -> str:
    """Combine Yahoo Finance and DuckDuckGo results."""
    parts: list[str] = []
    yf = _yahoo_finance_data(query)
    if yf.strip() and not yf.startswith("(Yahoo"):
        parts.append(f"=== Yahoo Finance ===\n{yf}")
    ddg = _duckduckgo_search(query)
    if ddg.strip() and not ddg.startswith("(Duck"):
        parts.append(f"=== Web Search ===\n{ddg}")
    return "\n\n".join(parts) if parts else f"No data found for: {query}"


class BaseAgent:
    name: str = "BaseAgent"
    model: str = "mistral-small-latest"
    max_tokens: int = 2048
    max_retries: int = 2
    max_tool_rounds: int = 6

    def __init__(self, client: Mistral | None = None):
        if client is not None:
            self.client = client
            return

        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Missing `MISTRAL_API_KEY`. Set it in your environment or in a local `.env` file."
            )
        self.client = Mistral(api_key=api_key)

    def _system_prompt(self) -> str:
        raise NotImplementedError

    def _user_prompt(self, company: str, company_type: str, context: dict) -> str:
        raise NotImplementedError

    def run(
        self,
        company: str,
        company_type: str = "public",
        context: dict | None = None,
    ) -> str:
        """Call the API, handle web_search tool calls, return final text."""
        if context is None:
            context = {}

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._user_prompt(company, company_type, context)},
        ]

        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat.complete(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    tools=[WEB_SEARCH_TOOL],
                    tool_choice="auto",
                )

                tool_rounds = 0
                while (
                    response.choices[0].finish_reason == "tool_calls"
                    and tool_rounds < self.max_tool_rounds
                ):
                    tool_rounds += 1
                    msg = response.choices[0].message

                    # Append assistant turn with its tool call requests
                    messages.append(
                        {
                            "role": "assistant",
                            "content": msg.content or "",
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in (msg.tool_calls or [])
                            ],
                        }
                    )

                    # Execute each tool call and append results
                    for tc in msg.tool_calls or []:
                        args = json.loads(tc.function.arguments)
                        result = _execute_web_search(args.get("query", ""))
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            }
                        )

                    response = self.client.chat.complete(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        messages=messages,
                        tools=[WEB_SEARCH_TOOL],
                        tool_choice="auto",
                    )

                return response.choices[0].message.content or ""

            except Exception as exc:
                if attempt < self.max_retries:
                    print(f"[{self.name}] Attempt {attempt + 1} failed: {exc} — retrying...")
                    time.sleep(2**attempt)
                else:
                    raise
        return ""
