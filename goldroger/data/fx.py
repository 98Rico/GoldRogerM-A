"""FX rate sourcing with live->cache->static fallback hierarchy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from goldroger.data.sourcing import SourceResult, make_source_result
from goldroger.utils.cache import fx_rate_cache

_HTTP = httpx.Client(
    timeout=5.0,
    headers={"User-Agent": "GoldRoger FX/1.0"},
    follow_redirects=True,
)

# Deterministic fallback: USD value per one unit of local currency.
_STATIC_USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.26,
    "CHF": 1.11,
    "CAD": 0.74,
    "AUD": 0.66,
    "JPY": 0.0067,
    "NOK": 0.095,
    "SEK": 0.093,
    "DKK": 0.145,
}


@dataclass
class FXRateResult:
    base_currency: str
    quote_currency: str
    rate: Optional[float]
    source: SourceResult

    @property
    def ok(self) -> bool:
        return self.rate is not None and self.rate > 0


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cache_key(base: str, quote: str) -> str:
    return f"fx:{base}->{quote}"


def _from_static(base: str, quote: str) -> FXRateResult:
    b = _STATIC_USD_PER_UNIT.get(base)
    q = _STATIC_USD_PER_UNIT.get(quote)
    if b is None or q is None or q == 0:
        src = make_source_result(
            None,
            source_name="static_fx_table",
            source_confidence="low",
            currency=quote,
            unit="rate",
            as_of_date="static_table",
            is_fallback=True,
            warning_flags=["static_fx_unavailable"],
        )
        return FXRateResult(base, quote, None, src)
    rate = float(b / q)
    src = make_source_result(
        rate,
        source_name="static_fx_table",
        source_confidence="low",
        currency=quote,
        unit=f"{quote} per {base}",
        as_of_date="static_table",
        is_fallback=True,
        normalization_notes="static fallback table",
        warning_flags=["fx_static_fallback"],
    )
    return FXRateResult(base, quote, rate, src)


def _from_cache(base: str, quote: str) -> FXRateResult | None:
    raw = fx_rate_cache.get(_cache_key(base, quote))
    if not isinstance(raw, dict):
        return None
    rate = raw.get("rate")
    try:
        rate_f = float(rate)
    except Exception:
        return None
    if rate_f <= 0:
        return None
    src = make_source_result(
        rate_f,
        source_name=str(raw.get("source_name") or "cached_fx"),
        source_confidence="medium",
        currency=quote,
        unit=f"{quote} per {base}",
        as_of_date=str(raw.get("as_of_date") or _utc_iso()),
        source_url=str(raw.get("source_url") or ""),
        cached=True,
        normalization_notes="cached from prior live FX lookup",
        warning_flags=["fx_cached"],
    )
    return FXRateResult(base, quote, rate_f, src)


def _save_cache(base: str, quote: str, rate: float, source_name: str, source_url: str, as_of_date: str) -> None:
    fx_rate_cache.set(
        _cache_key(base, quote),
        {
            "rate": float(rate),
            "source_name": source_name,
            "source_url": source_url,
            "as_of_date": as_of_date,
        },
    )


def _from_frankfurter(base: str, quote: str) -> FXRateResult | None:
    """
    Try Frankfurter v2 first, then v1-compatible endpoint.
    Returns None on any fetch/parse failure.
    """
    urls = [
        ("https://api.frankfurter.dev/v2/rates", {"base": base, "quotes": quote}),
        ("https://api.frankfurter.dev/v1/latest", {"base": base, "symbols": quote}),
    ]
    for url, params in urls:
        try:
            resp = _HTTP.get(url, params=params)
            if resp.status_code != 200:
                continue
            payload = resp.json()
            rates = payload.get("rates") or {}
            if not isinstance(rates, dict):
                continue
            rate = rates.get(quote)
            if rate is None:
                continue
            rate_f = float(rate)
            if rate_f <= 0:
                continue
            as_of = str(payload.get("date") or _utc_iso())
            src = make_source_result(
                rate_f,
                source_name="frankfurter",
                source_confidence="high",
                currency=quote,
                unit=f"{quote} per {base}",
                as_of_date=as_of,
                source_url=resp.url.__str__(),
                normalization_notes="live free FX source",
            )
            _save_cache(base, quote, rate_f, "frankfurter", resp.url.__str__(), as_of)
            return FXRateResult(base, quote, rate_f, src)
        except Exception:
            continue
    return None


def get_fx_rate(base_currency: str, quote_currency: str) -> FXRateResult:
    """
    Hierarchy:
      1) live free FX (Frankfurter)
      2) cached prior live FX
      3) static deterministic fallback
    """
    base = str(base_currency or "").upper().strip()
    quote = str(quote_currency or "").upper().strip()

    if not base or not quote:
        src = make_source_result(
            None,
            source_name="fx_resolver",
            source_confidence="low",
            currency=quote or "unknown",
            unit="rate",
            warning_flags=["missing_currency_code"],
        )
        return FXRateResult(base or "unknown", quote or "unknown", None, src)

    if base == quote:
        src = make_source_result(
            1.0,
            source_name="fx_identity",
            source_confidence="verified",
            currency=quote,
            unit=f"{quote} per {base}",
            as_of_date=_utc_iso(),
        )
        return FXRateResult(base, quote, 1.0, src)

    live = _from_frankfurter(base, quote)
    if live and live.ok:
        return live

    cached = _from_cache(base, quote)
    if cached and cached.ok:
        return cached

    return _from_static(base, quote)

