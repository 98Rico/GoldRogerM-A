"""
Real financial data fetcher using yfinance.

For public companies: pulls verified, structured data directly from Yahoo Finance.
For private companies: returns None — caller falls back to LLM estimation.

All monetary values are stored in source-feed currency millions unless
explicitly normalized downstream.
Confidence levels: "verified" (yfinance), "estimated" (LLM), "inferred" (defaults).

Results are cached in-process for 1 hour to avoid redundant network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx
import yfinance as yf

from goldroger.data.sourcing import make_source_result
from goldroger.utils.money import normalize_currency_code
from goldroger.utils.cache import company_meta_cache, market_data_cache, ticker_cache

_HTTP = httpx.Client(
    timeout=15,
    headers={"User-Agent": "Mozilla/5.0"},
    follow_redirects=True,
)


@dataclass
class MarketData:
    ticker: str
    company_name: str
    sector: str

    # Price & market structure
    current_price: Optional[float] = None
    market_cap: Optional[float] = None          # source-feed currency millions
    shares_outstanding: Optional[float] = None  # millions

    # Capital structure (source-feed currency millions)
    total_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    net_debt: Optional[float] = None
    enterprise_value: Optional[float] = None    # market-implied EV

    # Income statement TTM (source-feed currency millions)
    revenue_ttm: Optional[float] = None
    ebitda_ttm: Optional[float] = None
    ebit_ttm: Optional[float] = None
    net_income_ttm: Optional[float] = None

    # Margins (0–1 decimal)
    gross_margin: Optional[float] = None
    ebitda_margin: Optional[float] = None
    net_margin: Optional[float] = None

    # Cash flow TTM (source-feed currency millions)
    fcf_ttm: Optional[float] = None
    capex_ttm: Optional[float] = None
    da_ttm: Optional[float] = None

    # Historical annual revenue oldest-first (source-feed currency millions, up to 5 years)
    revenue_history: list[float] = field(default_factory=list)
    revenue_growth_yoy: Optional[float] = None  # most recent TTM YoY (decimal)

    # Forward / consensus estimates
    forward_revenue_growth: Optional[float] = None  # analyst forward 1Y growth
    forward_revenue_1y: Optional[float] = None      # source-feed currency millions
    earnings_growth: Optional[float] = None          # fwd EPS growth (decimal)
    forward_eps: Optional[float] = None

    # Balance sheet (for P/B valuation)
    book_value_per_share: Optional[float] = None    # total equity / shares
    total_equity: Optional[float] = None            # source-feed currency millions

    # Tax
    effective_tax_rate: Optional[float] = None

    # CAPM inputs
    beta: Optional[float] = None

    # Market-implied multiples
    ev_ebitda_market: Optional[float] = None
    ev_revenue_market: Optional[float] = None
    pe_ratio: Optional[float] = None               # trailing P/E
    forward_pe: Optional[float] = None             # forward P/E

    # Analyst consensus
    analyst_target_price: Optional[float] = None
    analyst_recommendation: Optional[str] = None

    # Cost of debt proxy
    interest_expense: Optional[float] = None        # source-feed currency millions TTM

    confidence: str = "verified"
    data_source: str = "yfinance"
    additional_metadata: dict = field(default_factory=dict)


def fetch_market_data(ticker: str) -> Optional[MarketData]:
    """
    Pull real structured financial data for a public company via yfinance.
    Returns None if the ticker is invalid or critical data is missing.
    Results cached for 1 hour.
    """
    key = f"md:{ticker.upper()}"
    cached = market_data_cache.get(key)
    if cached is not None:
        return cached

    result = _fetch_raw(ticker)
    if result is not None:
        market_data_cache.set(key, result)
    return result


def resolve_ticker(company_name: str) -> Optional[str]:
    """
    Resolve a company name to its primary exchange ticker via Yahoo Finance search.
    Returns the best EQUITY match or None. Cached for 24 hours.
    """
    ctx = resolve_ticker_with_context(company_name)
    if not ctx:
        return None
    symbol = str(ctx.get("selected_symbol") or "").strip().upper()
    return symbol or None


def resolve_ticker_with_context(company_name: str) -> Optional[dict]:
    """
    Resolve a company name to a ticker with listing context.

    Returns:
      {
        selected_symbol,
        primary_listing_symbol,
        selected_exchange,
        selected_quote_type,
        selected_region,
        reason,
      }
    """
    key = f"ticker_ctx:{company_name.lower()}"
    cached = ticker_cache.get(key)
    if isinstance(cached, dict):
        return cached

    result = _resolve_raw_with_context(company_name)
    if result:
        ticker_cache.set(key, result)
    return result


# ── Private implementation ────────────────────────────────────────────────────

def _fetch_raw(ticker: str) -> Optional[MarketData]:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        _meta_key = f"company_meta:{ticker.upper()}"
        cached_meta = company_meta_cache.get(_meta_key)
        if not isinstance(cached_meta, dict):
            cached_meta = {}
        if cached_meta:
            for _k in (
                "longBusinessSummary",
                "longName",
                "shortName",
                "country",
                "industry",
                "sector",
                "exchange",
                "fullExchangeName",
                "currency",
                "financialCurrency",
                "quoteType",
                "underlyingSymbol",
                "website",
                "sharesPerUnderlying",
                "conversionRatio",
            ):
                if info.get(_k) in (None, "", []):
                    info[_k] = cached_meta.get(_k)

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None
        _quote_ccy_raw = str(info.get("currency") or "")
        _quote_ccy_norm, _quote_norm_note, _quote_unit_factor = normalize_currency_code(_quote_ccy_raw)
        _price_normalized = False
        try:
            price = float(price) * float(_quote_unit_factor or 1.0)
            _price_normalized = bool(float(_quote_unit_factor or 1.0) != 1.0)
        except Exception:
            pass

        # ── Annual income statement ───────────────────────────────────────
        revenue_history: list[float] = []
        da_ttm: Optional[float] = None
        capex_ttm: Optional[float] = None
        interest_expense: Optional[float] = None
        effective_tax_rate: Optional[float] = None

        try:
            fin = stock.financials
            if fin is not None and not fin.empty:
                if "Total Revenue" in fin.index:
                    vals = fin.loc["Total Revenue"].dropna().tolist()
                    revenue_history = [v / 1e6 for v in reversed(vals[:5])]
                if "Tax Provision" in fin.index and "Pretax Income" in fin.index:
                    tax = fin.loc["Tax Provision"].iloc[0]
                    pretax = fin.loc["Pretax Income"].iloc[0]
                    if pretax and pretax > 0:
                        effective_tax_rate = max(0.0, min(tax / pretax, 0.50))
                if "Interest Expense" in fin.index:
                    ie = fin.loc["Interest Expense"].iloc[0]
                    if ie:
                        interest_expense = abs(ie) / 1e6
        except Exception:
            pass

        # ── Cash flow statement ───────────────────────────────────────────
        try:
            cf = stock.cashflow
            if cf is not None and not cf.empty:
                for da_key in ("Depreciation And Amortization", "Depreciation"):
                    if da_key in cf.index:
                        da_ttm = abs(cf.loc[da_key].iloc[0]) / 1e6
                        break
                if "Capital Expenditure" in cf.index:
                    capex_ttm = abs(cf.loc["Capital Expenditure"].iloc[0]) / 1e6
        except Exception:
            pass

        # ── Balance sheet ─────────────────────────────────────────────────
        total_equity: Optional[float] = None
        book_value_per_share: Optional[float] = None
        try:
            bs = stock.balance_sheet
            if bs is not None and not bs.empty:
                for eq_key in ("Stockholders Equity", "Total Equity Gross Minority Interest", "Common Stock Equity"):
                    if eq_key in bs.index:
                        total_equity = abs(bs.loc[eq_key].iloc[0]) / 1e6
                        break
        except Exception:
            pass

        bv = info.get("bookValue")
        if bv:
            book_value_per_share = float(bv)

        # ── Forward / consensus estimates ─────────────────────────────────
        forward_revenue_growth: Optional[float] = None
        forward_revenue_1y: Optional[float] = None
        try:
            rev_est = stock.revenue_estimate
            if rev_est is not None and not rev_est.empty:
                cols = rev_est.columns.tolist()
                fwd_col = None
                for candidate in ("+1y", "0y"):
                    if candidate in cols:
                        fwd_col = candidate
                        break
                if fwd_col:
                    avg = rev_est.loc["avg", fwd_col] if "avg" in rev_est.index else None
                    if avg and not _is_nan(avg):
                        forward_revenue_1y = float(avg) / 1e6
                        revenue_ttm_raw = info.get("totalRevenue")
                        if revenue_ttm_raw and revenue_ttm_raw > 0:
                            forward_revenue_growth = (forward_revenue_1y / (revenue_ttm_raw / 1e6)) - 1
        except Exception:
            pass

        # Fallback: use earningsGrowth as a proxy
        if forward_revenue_growth is None:
            eg = info.get("earningsGrowth") or info.get("revenueGrowth")
            if eg:
                forward_revenue_growth = float(eg)

        # ── Core info fields ──────────────────────────────────────────────
        revenue_ttm = _millions(info.get("totalRevenue"))
        ebitda_ttm = _millions(info.get("ebitda"))
        market_cap = _millions(info.get("marketCap"))
        enterprise_value = _millions(info.get("enterpriseValue"))
        total_debt = _millions(info.get("totalDebt"))
        cash = _millions(info.get("totalCash"))
        fcf_ttm = _millions(info.get("freeCashflow"))
        shares = _millions(info.get("sharesOutstanding"))

        net_debt: Optional[float] = None
        if total_debt is not None and cash is not None:
            net_debt = total_debt - cash

        ebitda_margin = _safe_pct(info.get("ebitdaMargins"))
        gross_margin = _safe_pct(info.get("grossMargins"))
        net_margin = _safe_pct(info.get("profitMargins"))

        if ebitda_margin is None and ebitda_ttm and revenue_ttm and revenue_ttm > 0:
            ebitda_margin = ebitda_ttm / revenue_ttm

        revenue_growth = info.get("revenueGrowth")
        beta = info.get("beta")
        ev_ebitda = info.get("enterpriseToEbitda")
        ev_revenue = info.get("enterpriseToRevenue")
        trailing_pe = info.get("trailingPE")
        fwd_pe = info.get("forwardPE")
        fwd_eps = info.get("forwardEps")
        target = info.get("targetMeanPrice")
        rec = info.get("recommendationKey")
        earnings_growth = info.get("earningsGrowth")

        if not revenue_ttm and not revenue_history:
            return None

        _fin_ccy_raw = str(info.get("financialCurrency") or info.get("currency") or "unknown")
        _fin_ccy, _fin_ccy_note, _ = normalize_currency_code(_fin_ccy_raw)
        _quote_ccy = _quote_ccy_norm or "unknown"
        source_results = {
            "revenue_ttm": make_source_result(
                revenue_ttm,
                source_name="yfinance",
                source_confidence="verified" if revenue_ttm is not None else "unavailable",
                currency=_fin_ccy,
                unit="millions",
            ).to_dict(),
            "ebitda_ttm": make_source_result(
                ebitda_ttm,
                source_name="yfinance",
                source_confidence="verified" if ebitda_ttm is not None else "unavailable",
                currency=_fin_ccy,
                unit="millions",
            ).to_dict(),
            "free_cash_flow": make_source_result(
                fcf_ttm,
                source_name="yfinance",
                source_confidence="verified" if fcf_ttm is not None else "unavailable",
                currency=_fin_ccy,
                unit="millions",
            ).to_dict(),
            "market_cap": make_source_result(
                market_cap,
                source_name="yfinance",
                source_confidence="verified" if market_cap is not None else "unavailable",
                currency=_quote_ccy,
                unit="millions",
            ).to_dict(),
            "enterprise_value": make_source_result(
                enterprise_value,
                source_name="yfinance",
                source_confidence="verified" if enterprise_value is not None else "unavailable",
                currency=_quote_ccy,
                unit="millions",
            ).to_dict(),
            "shares_outstanding": make_source_result(
                shares,
                source_name="yfinance",
                source_confidence="verified" if shares is not None else "unavailable",
                currency="shares",
                unit="millions",
            ).to_dict(),
        }

        md = MarketData(
            ticker=ticker.upper(),
            company_name=info.get("longName") or info.get("shortName") or ticker,
            sector=info.get("sector") or info.get("industry") or "Unknown",
            current_price=float(price),
            market_cap=market_cap,
            shares_outstanding=shares,
            total_debt=total_debt,
            cash_and_equivalents=cash,
            net_debt=net_debt,
            enterprise_value=enterprise_value,
            revenue_ttm=revenue_ttm,
            ebitda_ttm=ebitda_ttm,
            gross_margin=gross_margin,
            ebitda_margin=ebitda_margin,
            net_margin=net_margin,
            fcf_ttm=fcf_ttm,
            capex_ttm=capex_ttm,
            da_ttm=da_ttm,
            revenue_history=revenue_history,
            revenue_growth_yoy=float(revenue_growth) if revenue_growth else None,
            forward_revenue_growth=forward_revenue_growth,
            forward_revenue_1y=forward_revenue_1y,
            earnings_growth=float(earnings_growth) if earnings_growth else None,
            forward_eps=float(fwd_eps) if fwd_eps else None,
            book_value_per_share=book_value_per_share,
            total_equity=total_equity,
            effective_tax_rate=effective_tax_rate,
            beta=float(beta) if beta else None,
            ev_ebitda_market=float(ev_ebitda) if ev_ebitda and ev_ebitda > 0 else None,
            ev_revenue_market=float(ev_revenue) if ev_revenue and ev_revenue > 0 else None,
            pe_ratio=float(trailing_pe) if trailing_pe and trailing_pe > 0 else None,
            forward_pe=float(fwd_pe) if fwd_pe and fwd_pe > 0 else None,
            analyst_target_price=(float(target) * float(_quote_unit_factor or 1.0) if target else None),
            analyst_recommendation=rec,
            interest_expense=interest_expense,
            additional_metadata={
                "sector_key": info.get("sectorKey"),
                "industry_key": info.get("industryKey"),
                "industry": info.get("industry"),
                "country": info.get("country"),
                "exchange": info.get("exchange") or info.get("fullExchangeName"),
                "business_summary": info.get("longBusinessSummary"),
                "quote_currency": info.get("currency"),
                "quote_currency_raw": _quote_ccy_raw or "unknown",
                "quote_currency_normalized": _quote_ccy or "unknown",
                "financial_currency": _fin_ccy_raw,
                "financial_currency_normalized": _fin_ccy or "unknown",
                "market_cap_currency": info.get("currency"),
                "quote_type": info.get("quoteType"),
                "quote_source_name": info.get("quoteSourceName"),
                "exchange_timezone": info.get("exchangeTimezoneName"),
                "underlying_symbol": info.get("underlyingSymbol"),
                "website": info.get("website"),
                "company_website": info.get("website"),
                "selected_listing_symbol": ticker.upper(),
                "primary_listing_symbol": str(info.get("underlyingSymbol") or ticker).upper(),
                "adr_ratio": (
                    info.get("sharesPerUnderlying")
                    if info.get("sharesPerUnderlying") is not None
                    else info.get("conversionRatio")
                ),
                "quote_price_normalized_to_major": _price_normalized,
                "quote_price_normalization_factor": float(_quote_unit_factor or 1.0),
                "quote_price_normalization_note": _quote_norm_note or "",
                "currency_normalization_note": "; ".join(
                    [x for x in (_quote_norm_note, _fin_ccy_note) if x]
                ),
                "is_adr_hint": bool(
                    (info.get("currency") == "USD")
                    and info.get("financialCurrency")
                    and info.get("financialCurrency") != "USD"
                    and str(info.get("country") or "").strip().lower() not in {"united states", "usa", "us"}
                ),
                "dividend_yield": (
                    float(info.get("dividendYield"))
                    if info.get("dividendYield") is not None
                    else (
                        float(info.get("trailingAnnualDividendYield"))
                        if info.get("trailingAnnualDividendYield") is not None
                        else None
                    )
                ),
                "dividend_rate": (
                    float(info.get("dividendRate"))
                    if info.get("dividendRate") is not None
                    else (
                        float(info.get("trailingAnnualDividendRate"))
                        if info.get("trailingAnnualDividendRate") is not None
                        else None
                    )
                ),
                "payout_ratio": (
                    float(info.get("payoutRatio"))
                    if info.get("payoutRatio") is not None
                    else None
                ),
                "source_results": source_results,
            },
        )
        company_meta_cache.set(
            _meta_key,
            {
                "longBusinessSummary": info.get("longBusinessSummary"),
                "longName": info.get("longName"),
                "shortName": info.get("shortName"),
                "country": info.get("country"),
                "industry": info.get("industry"),
                "sector": info.get("sector"),
                "exchange": info.get("exchange"),
                "fullExchangeName": info.get("fullExchangeName"),
                "currency": info.get("currency"),
                "financialCurrency": info.get("financialCurrency"),
                "quoteType": info.get("quoteType"),
                "underlyingSymbol": info.get("underlyingSymbol"),
                "website": info.get("website"),
                "sharesPerUnderlying": info.get("sharesPerUnderlying"),
                "conversionRatio": info.get("conversionRatio"),
            },
        )
        return md
    except Exception as exc:
        print(f"[fetcher] Error fetching {ticker}: {exc}")
        return None


def _resolve_raw(company_name: str) -> Optional[str]:
    ctx = _resolve_raw_with_context(company_name)
    if not ctx:
        return None
    return str(ctx.get("selected_symbol") or "").strip().upper() or None


def _candidate_score(q: dict, query: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    sym = str(q.get("symbol") or "").strip().upper()
    qtype = str(q.get("quoteType") or "").strip().upper()
    exchange = str(q.get("exchange") or q.get("exchDisp") or "").strip().upper()
    region = str(q.get("region") or "").strip().upper()
    short_name = str(q.get("shortname") or q.get("longname") or "").lower()
    query_l = str(query or "").strip().lower()
    explicit_ticker = bool(query and query.replace(".", "").replace("-", "").isalnum() and len(query) <= 10)

    if qtype == "EQUITY":
        score += 30
        reasons.append("equity")
    elif qtype:
        score += 10

    if sym and query and sym == query.strip().upper():
        score += 80
        reasons.append("exact_symbol_match")

    # Prefer primary/local listings for foreign issuers when name query is used.
    if "." in sym:
        score += 18
        reasons.append("dot_suffix_local_listing_hint")
    if sym.endswith(("Y", "F")):
        score -= 12
        reasons.append("otc_or_depositary_suffix_penalty")
    if "OTC" in exchange:
        score -= 15
        reasons.append("otc_exchange_penalty")

    # Keep US primary listings favored for explicit ticker-like inputs (e.g., AAPL).
    if explicit_ticker and "." not in query:
        if "." not in sym:
            score += 15
            reasons.append("explicit_ticker_prefers_primary")
        else:
            score -= 8

    # If this is a company-name query, prioritize semantic name match.
    if query_l and short_name:
        if query_l in short_name:
            score += 20
            reasons.append("name_contains_query")
        else:
            q_tokens = {t for t in query_l.split() if t}
            n_tokens = {t for t in short_name.split() if t}
            overlap = len(q_tokens.intersection(n_tokens))
            if overlap > 0:
                score += min(15, overlap * 4)
                reasons.append("name_token_overlap")

    # Modest bump to known developed markets (local primaries often here).
    if region in {"US", "GB", "NO", "DE", "FR", "NL", "SE", "DK", "JP", "CH"}:
        score += 3

    return score, reasons


def _resolve_raw_with_context(company_name: str) -> Optional[dict]:
    try:
        resp = _HTTP.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": company_name, "quotesCount": 12, "newsCount": 0},
        )
        payload = resp.json()
        quotes = payload.get("quotes", [])
        if not isinstance(quotes, list) or not quotes:
            return None

        eligible: list[dict] = []
        for q in quotes:
            sym = str(q.get("symbol") or "").strip()
            if not sym:
                continue
            qtype = str(q.get("quoteType") or "").strip().upper()
            if qtype not in {"EQUITY", "ETF"}:
                continue
            score, reasons = _candidate_score(q, company_name)
            eligible.append(
                {
                    "symbol": sym.upper(),
                    "quote_type": qtype,
                    "exchange": str(q.get("exchange") or q.get("exchDisp") or "").strip(),
                    "region": str(q.get("region") or "").strip(),
                    "longname": str(q.get("longname") or q.get("shortname") or "").strip(),
                    "score": score,
                    "reasons": reasons,
                }
            )

        if not eligible:
            return None

        eligible.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        selected = eligible[0]
        selected_sym = str(selected.get("symbol") or "").upper()

        primary = selected_sym
        # If we selected likely US depositary/OTC symbol, try matching same-name local listing with dot suffix.
        if selected_sym.endswith(("Y", "F")):
            for cand in eligible[1:]:
                sym = str(cand.get("symbol") or "").upper()
                if "." in sym and not sym.endswith(("Y", "F")):
                    primary = sym
                    break

        return {
            "selected_symbol": selected_sym,
            "primary_listing_symbol": primary,
            "selected_exchange": str(selected.get("exchange") or ""),
            "selected_quote_type": str(selected.get("quote_type") or ""),
            "selected_region": str(selected.get("region") or ""),
            "reason": ",".join(selected.get("reasons") or []),
            "candidates": eligible[:6],
        }
    except Exception:
        return None


def _millions(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return f / 1e6 if abs(f) > 1000 else f
    except Exception:
        return None


def _safe_pct(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        if f > 1.0:
            f /= 100.0
        return max(-1.0, min(f, 1.0))
    except Exception:
        return None


def _is_nan(value) -> bool:
    try:
        import math
        return math.isnan(float(value))
    except Exception:
        return True
