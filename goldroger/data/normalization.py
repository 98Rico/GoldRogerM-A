"""Currency/listing normalization and safety audit for public-company valuation."""
from __future__ import annotations

from typing import Any

from goldroger.data.fetcher import MarketData
from goldroger.data.fx import get_fx_rate
from goldroger.utils.money import convert_quote_price_to_major_unit, normalize_currency_code


def _to_float_or_none(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def build_data_normalization_audit(market_data: MarketData | None) -> dict[str, Any]:
    """Build currency/share-basis normalization audit for valuation safety."""
    base: dict[str, Any] = {
        "status": "UNKNOWN",
        "reason": "normalization metadata unavailable",
        "quote_currency": "unknown",
        "financial_statement_currency": "unknown",
        "market_cap_currency": "unknown",
        "valuation_currency": "USD",
        "adr_detected": False,
        "depository_receipt_detected": False,
        "listing_type": "unknown",
        "adr_ratio": None,
        "share_count_basis": "unknown",
        "selected_listing": str((market_data.ticker if market_data else "") or "").upper() or "unknown",
        "primary_listing": "unknown",
        "exchange": "unknown",
        "country": "unknown",
    }
    if not market_data:
        base["status"] = "FAILED"
        base["reason"] = "market data unavailable"
        return base

    meta = market_data.additional_metadata if isinstance(market_data.additional_metadata, dict) else {}

    q_ccy_raw = str(meta.get("quote_currency") or "")
    f_ccy_raw = str(meta.get("financial_currency") or "")
    mcap_ccy_raw = str(meta.get("market_cap_currency") or q_ccy_raw)
    quote_ccy, q_note, q_price_factor = normalize_currency_code(q_ccy_raw)
    fin_ccy, f_note, _ = normalize_currency_code(f_ccy_raw)
    mcap_ccy, m_note, _ = normalize_currency_code(mcap_ccy_raw)

    country = str(meta.get("country") or "").strip().lower()
    ticker = str(market_data.ticker or "").strip().upper()
    quote_type = str(meta.get("quote_type") or "").strip().lower()
    exchange = str(meta.get("exchange") or "").strip()
    underlying_symbol = str(meta.get("underlying_symbol") or "").strip()
    adr_ratio = _to_float_or_none(meta.get("adr_ratio"))
    hinted_adr = bool(meta.get("is_adr_hint"))
    primary_listing = str(meta.get("primary_listing_symbol") or underlying_symbol or ticker or "").strip().upper() or "unknown"

    confirmed_depository = any(tok in quote_type for tok in ("adr", "gdr", "depository", "depositary receipt"))
    likely_depository = bool(
        ticker.endswith("Y")
        and quote_ccy == "USD"
        and fin_ccy
        and fin_ccy != "USD"
    ) or bool(hinted_adr and ticker.endswith("Y") and underlying_symbol)
    likely_foreign_ordinary_otc = (
        ticker.endswith("F")
        and quote_ccy == "USD"
        and fin_ccy
        and fin_ccy != "USD"
        and country not in {"united states", "usa", "us"}
    )
    likely_foreign_us_quote = (
        quote_ccy == "USD"
        and fin_ccy
        and fin_ccy != "USD"
        and country not in {"united states", "usa", "us"}
        and not likely_foreign_ordinary_otc
    )
    is_depository = bool(confirmed_depository or likely_depository)

    reasons: list[str] = []
    status = "OK"
    if quote_ccy and fin_ccy and quote_ccy != fin_ccy:
        status = "FAILED"
        reasons.append(
            f"currency mismatch ({fin_ccy} financials vs {quote_ccy} quote) without FX normalization"
        )
    elif not quote_ccy or not fin_ccy:
        status = "UNKNOWN"
        reasons.append("missing quote/financial statement currency metadata")

    if q_note:
        reasons.append(q_note)
    if f_note:
        reasons.append(f_note)
    if m_note:
        reasons.append(m_note)

    share_consistency_ok = None
    share_consistency_delta = None
    try:
        if (
            market_data.current_price is not None
            and market_data.shares_outstanding is not None
            and market_data.market_cap is not None
            and market_data.market_cap > 0
        ):
            _px_for_consistency = float(market_data.current_price) * float(q_price_factor or 1.0)
            implied_mcap = _px_for_consistency * float(market_data.shares_outstanding)
            share_consistency_delta = abs(implied_mcap - float(market_data.market_cap)) / float(market_data.market_cap)
            share_consistency_ok = bool(share_consistency_delta <= 0.25)
    except Exception:
        share_consistency_ok = None

    if is_depository and (adr_ratio is None or adr_ratio <= 0):
        if share_consistency_ok is True:
            if status != "FAILED":
                status = "OK_HEURISTIC"
            reasons.append(
                "depositary ratio unavailable; using quote-listing share basis heuristic "
                f"(market-cap/share-price consistency delta={share_consistency_delta:.1%})"
            )
        else:
            status = "FAILED"
            reasons.append("foreign listing/share-basis normalization unresolved; ADR/depositary ratio unavailable if applicable")

    if confirmed_depository:
        listing_type = "depositary_receipt_confirmed"
    elif likely_depository:
        listing_type = "depositary_receipt_likely_unconfirmed"
    elif likely_foreign_us_quote:
        listing_type = "foreign_issuer_us_listing_unresolved"
    elif likely_foreign_ordinary_otc:
        listing_type = "foreign_ordinary_otc_likely"
    elif quote_ccy and fin_ccy and quote_ccy == fin_ccy:
        listing_type = "ordinary_listing"
    else:
        listing_type = "foreign_listing_unknown_basis"

    if is_depository and adr_ratio and adr_ratio > 0:
        share_basis = "adr_equivalent"
    elif is_depository:
        share_basis = "unknown_depositary_ratio"
    elif likely_foreign_us_quote:
        share_basis = "foreign_us_listing_unverified_share_basis"
    elif likely_foreign_ordinary_otc:
        share_basis = "foreign_ordinary_unresolved"
    else:
        share_basis = "ordinary"

    if status == "OK" and not reasons:
        reasons.append("currency/share basis appears internally consistent")

    return {
        "status": status,
        "reason": "; ".join(reasons),
        "quote_currency": quote_ccy or "unknown",
        "quote_currency_raw": q_ccy_raw or "unknown",
        "financial_statement_currency": fin_ccy or "unknown",
        "market_cap_currency": mcap_ccy or "unknown",
        "valuation_currency": quote_ccy or "USD",
        "adr_detected": bool(confirmed_depository),
        "depository_receipt_detected": is_depository,
        "listing_type": listing_type,
        "adr_ratio": adr_ratio,
        "share_count_basis": share_basis,
        "selected_listing": ticker or "unknown",
        "primary_listing": primary_listing,
        "exchange": exchange or "unknown",
        "country": (country.title() if country else "unknown"),
        "quote_price_unit_factor": float(q_price_factor or 1.0),
        "quote_price_unit_note": q_note or "",
        "quote_price_normalized": bool(meta.get("quote_price_normalized_to_major")),
    }


def apply_currency_normalization(
    market_data: MarketData | None,
    audit: dict[str, Any],
) -> tuple[MarketData | None, dict[str, Any], bool]:
    """Convert statement-currency fields to quote currency when FX is available."""
    if not market_data:
        return market_data, audit, False

    # Quote-unit normalization (for example GBp/GBX price to GBP price) should
    # always run before any valuation/sanity checks that compare per-share values.
    _quote_unit_factor = float(audit.get("quote_price_unit_factor") or 1.0)
    _quote_unit_normalized = bool(audit.get("quote_price_normalized"))
    if _quote_unit_factor != 1.0 and not _quote_unit_normalized:
        _raw_quote_ccy = str(audit.get("quote_currency_raw") or "")
        _new_px, _did_convert, _factor = convert_quote_price_to_major_unit(
            market_data.current_price,
            _raw_quote_ccy,
        )
        if _did_convert:
            market_data.current_price = _new_px
            if isinstance(market_data.additional_metadata, dict):
                market_data.additional_metadata["quote_price_raw_unit_factor"] = float(_factor)
                market_data.additional_metadata["quote_price_normalized_to_major"] = True
            audit = dict(audit)
            audit["quote_price_normalized"] = True
            audit["reason"] = (
                str(audit.get("reason") or "").rstrip("; ")
                + "; quote price normalized from minor unit to major currency"
            ).strip("; ")

    status = str(audit.get("status") or "").upper()
    reason = str(audit.get("reason") or "")
    if "share-basis normalization unresolved" in reason.lower():
        return market_data, audit, False
    if "currency mismatch" not in reason.lower():
        return market_data, audit, False

    from_ccy = str(audit.get("financial_statement_currency") or "")
    to_ccy = str(audit.get("quote_currency") or "")
    fx_result = get_fx_rate(from_ccy, to_ccy)
    fx_rate = fx_result.rate if fx_result.ok else None
    fx_source = str(fx_result.source.source_name or "unknown")
    fx_ts = str(fx_result.source.as_of_date or "unknown")
    fx_conf = str(fx_result.source.source_confidence or "low")
    fx_note = str(fx_result.source.normalization_notes or "")

    if fx_rate is None or fx_rate <= 0:
        out = dict(audit)
        out["status"] = "FAILED"
        out["reason"] = (reason + "; FX normalization unavailable for financial->quote currency conversion").strip("; ")
        out["fx_source"] = fx_source
        out["fx_timestamp"] = fx_ts
        out["fx_confidence"] = fx_conf
        return market_data, out, False

    # Convert statement-currency monetary fields (all in millions already).
    fields = (
        "revenue_ttm",
        "ebitda_ttm",
        "ebit_ttm",
        "net_income_ttm",
        "fcf_ttm",
        "capex_ttm",
        "da_ttm",
        "total_debt",
        "cash_and_equivalents",
        "net_debt",
        "enterprise_value",
        "total_equity",
        "forward_revenue_1y",
        "interest_expense",
    )
    for field_name in fields:
        try:
            value = getattr(market_data, field_name, None)
            if value is not None:
                setattr(market_data, field_name, float(value) * float(fx_rate))
        except Exception:
            continue

    try:
        if market_data.revenue_history:
            market_data.revenue_history = [float(x) * float(fx_rate) for x in market_data.revenue_history]
    except Exception:
        pass

    if isinstance(market_data.additional_metadata, dict):
        market_data.additional_metadata["financial_currency_normalized_from"] = from_ccy or "unknown"
        market_data.additional_metadata["financial_currency_normalized_to"] = to_ccy or "unknown"
        market_data.additional_metadata["fx_rate_used_fin_to_quote"] = fx_rate
        market_data.additional_metadata["fx_source"] = fx_source
        market_data.additional_metadata["fx_timestamp"] = fx_ts
        market_data.additional_metadata["fx_confidence"] = fx_conf
        market_data.additional_metadata["fx_fallback"] = bool(fx_result.source.is_fallback)

    out = dict(audit)
    out["status"] = "OK_FX_NORMALIZED" if status in {"FAILED", "UNKNOWN"} else status
    out["valuation_currency"] = to_ccy or out.get("valuation_currency") or "USD"
    out["fx_source"] = fx_source
    out["fx_timestamp"] = fx_ts
    out["fx_confidence"] = fx_conf

    share_basis = str(out.get("share_count_basis") or "")
    share_basis_note = ""
    if share_basis == "foreign_us_listing_unverified_share_basis":
        share_basis_note = "; share basis unverified for foreign issuer USD listing"

    fx_conf_note = ""
    if fx_conf.lower() in {"low", "medium"}:
        fx_conf_note = f"; FX confidence {fx_conf}"

    note_frag = f"; {fx_note}" if fx_note else ""
    out["reason"] = (
        "currency/share basis normalized via FX conversion; "
        f"{from_ccy}->{to_ccy} rate={fx_rate:.6f} ({fx_source})"
        f"{fx_conf_note}{share_basis_note}{note_frag}"
    )
    return market_data, out, True
