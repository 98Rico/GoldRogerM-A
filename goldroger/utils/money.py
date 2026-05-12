"""Deterministic money/currency formatting and quote-unit normalization helpers."""
from __future__ import annotations

from typing import Tuple


def normalize_currency_code(raw: str) -> tuple[str, str, float]:
    """
    Normalize feed currency codes to report currency codes.

    Returns:
      (normalized_currency_code, normalization_note, quote_to_major_factor)

    Notes:
    - Yahoo sometimes reports London quote currency as GBp/GBX (pence units).
      In that case we normalize currency to GBP and provide a 0.01 price factor.
    """
    raw_s = str(raw or "").strip()
    up = raw_s.upper()
    if raw_s in {"GBp", "GBX"} or up == "GBX":
        return "GBP", f"{raw_s or 'GBX'} quote currency normalized to GBP; quote price unit converted from pence to pounds", 0.01
    if up == "GBP":
        return "GBP", "", 1.0
    if up:
        return up, "", 1.0
    return "", "", 1.0


def currency_prefix(currency: str) -> str:
    code = str(currency or "USD").upper() or "USD"
    return "$" if code == "USD" else f"{code} "


def format_money_millions(value_m: float | None, currency: str = "USD") -> str:
    """Format million-based monetary values (e.g. 282934 -> USD 282.9B)."""
    if value_m is None:
        return "N/A"
    try:
        v = float(value_m)
    except Exception:
        return str(value_m)
    sign = "-" if v < 0 else ""
    abs_v = abs(v)
    if abs_v >= 1_000_000:
        body = f"{abs_v / 1_000_000:.2f}T"
    elif abs_v >= 1_000:
        body = f"{abs_v / 1_000:.1f}B"
    else:
        body = f"{abs_v:,.0f}M"
    return f"{sign}{currency_prefix(currency)}{body}"


def format_price(value: float | None, currency: str = "USD", decimals: int = 2, per_share: bool = False) -> str:
    """Format per-share value in report currency."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except Exception:
        return str(value)
    fmt = f"{{:,.{max(0, int(decimals))}f}}"
    out = f"{currency_prefix(currency)}{fmt.format(v)}"
    if per_share:
        out += "/share"
    return out


def parse_monetary_to_millions(text: str) -> float | None:
    """Parse formatted money text into million units when possible."""
    s = str(text or "").strip().replace(",", "")
    if not s:
        return None
    # Strip currency prefix and per-share suffix.
    if s.startswith("$"):
        s = s[1:].strip()
    elif len(s) >= 4 and s[:3].isalpha() and s[3] == " ":
        s = s[4:].strip()
    s = s.replace("/share", "")
    mult = 1.0
    if s.endswith("T"):
        mult = 1_000_000.0
        s = s[:-1]
    elif s.endswith("B"):
        mult = 1_000.0
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1.0
        s = s[:-1]
    elif s.endswith("K"):
        mult = 0.001
        s = s[:-1]
    try:
        return float(s) * mult
    except Exception:
        return None


def convert_quote_price_to_major_unit(price: float | None, raw_quote_currency: str) -> tuple[float | None, bool, float]:
    """Convert quote price from minor to major currency units when needed."""
    _, _, factor = normalize_currency_code(raw_quote_currency)
    if price is None:
        return None, False, factor
    try:
        p = float(price)
    except Exception:
        return price, False, factor
    if factor != 1.0:
        return p * factor, True, factor
    return p, False, factor
