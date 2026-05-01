"""
Fast + robust JSON parser for LLM outputs (production-grade)

Handles common failure modes from weaker models (Mistral free tier, etc.):
  - Trailing commas in objects/arrays
  - Markdown fences with extra prose before/after
  - Truncated output (unclosed braces at token limit)
  - Python-style None/True/False literals
  - Single-line // comments
"""

import json
import re
from typing import Optional, Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _repair_json(raw: str) -> str:
    """Best-effort repairs for common LLM JSON generation bugs."""
    # Trailing commas before } or ]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    # Python/JS-style None/True/False → JSON
    raw = re.sub(r'\bNone\b', 'null', raw)
    raw = re.sub(r'\bTrue\b', 'true', raw)
    raw = re.sub(r'\bFalse\b', 'false', raw)
    # Single-line // comments (outside strings — best-effort)
    raw = re.sub(r'//[^\n"]*\n', '\n', raw)
    return raw


def _try_recover_truncated(raw: str):
    """Close unclosed braces/brackets caused by token-limit truncation."""
    opens = raw.count("{") - raw.count("}")
    closes = raw.count("[") - raw.count("]")
    if opens <= 0 and closes <= 0:
        return None
    candidate = raw.rstrip().rstrip(",") + "}" * opens + "]" * closes
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_json(raw: str):  # noqa: ANN201 — returns Any (parsed JSON)
    if not raw:
        return None

    raw = raw.strip()
    if raw in ("", "{}", "null", "None"):
        return None

    # Strip markdown fences
    raw = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = raw.find(start_char)
        if start == -1:
            continue
        end = raw.rfind(end_char)

        if end != -1:
            candidate = raw[start:end + 1]

            # Fast path — valid as-is
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                pass

            # Repair pass (trailing commas, None/True/False)
            repaired = _repair_json(candidate)
            try:
                return json.loads(repaired)
            except (json.JSONDecodeError, ValueError):
                pass

            # Truncation recovery on repaired slice
            recovered = _try_recover_truncated(repaired)
            if recovered is not None:
                return recovered

        # No closing bracket found — token-limit truncation: try to close from start
        truncated = _repair_json(raw[start:])
        recovered = _try_recover_truncated(truncated)
        if recovered is not None:
            return recovered

    return None


# ── Revenue string normalisation (used by Financials-specific parsing) ────────

_REVENUE_PATTERNS = [
    # "$700M", "€700M", "~$700M", "approximately €700 million"
    re.compile(
        r'^[~≈]?\s*(?:approximately|approx\.?|circa)?\s*[$€£]?\s*'
        r'([\d,]+(?:\.\d+)?)\s*[Bb](?:illion)?$', re.I
    ),
    re.compile(
        r'^[~≈]?\s*(?:approximately|approx\.?|circa)?\s*[$€£]?\s*'
        r'([\d,]+(?:\.\d+)?)\s*[Mm](?:illion)?$', re.I
    ),
    re.compile(r'^[~≈]?\s*[$€£]?\s*([\d,]+(?:\.\d+)?)$'),
]


def normalise_revenue_string(v: Optional[str]) -> Optional[str]:
    """
    Convert "~$700M", "€700 million", "1.2B", "700" → plain USD-millions string.
    Returns the original string unchanged if it doesn't match any pattern.
    """
    if v is None:
        return None
    s = str(v).strip()
    for pat in _REVENUE_PATTERNS:
        m = pat.match(s)
        if m:
            num = float(m.group(1).replace(",", ""))
            # Billion → millions conversion
            if re.search(r'[Bb](?:illion)?', s):
                return str(round(num * 1000, 1))
            return str(round(num, 1))
    return v


def parse_model(raw: str, model_class: Type[T], fallback: T, *, _retry: bool = False) -> T:
    """Parse LLM JSON output into a Pydantic model, returning fallback on failure."""
    data = extract_json(raw)

    if data is None:
        if not _retry:
            fallback.__dict__.setdefault("_json_parse_failed", True)
        return fallback

    try:
        return model_class.model_validate(data)
    except (ValueError, TypeError, KeyError):
        try:
            allowed = set(model_class.model_fields.keys())
            filtered = {k: v for k, v in data.items() if k in allowed}
            return model_class.model_validate(filtered)
        except (ValueError, TypeError, KeyError):
            return fallback


def did_fallback(obj) -> bool:
    """Returns True if parse_model fell back to the default (JSON was invalid)."""
    return bool(getattr(obj, "_json_parse_failed", False))
