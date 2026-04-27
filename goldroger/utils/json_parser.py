"""
Fast + robust JSON parser for LLM outputs (production-grade)
"""

import json
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def extract_json(raw: str):
    if not raw:
        return None

    raw = raw.strip()

    if raw in ("", "{}", "null", "None"):
        return None

    # fast path
    if raw.startswith("{") or raw.startswith("["):
        try:
            return json.loads(raw)
        except:
            pass

    # markdown cleanup
    raw = raw.replace("```json", "").replace("```", "").strip()

    # object
    start = raw.find("{")
    end = raw.rfind("}")

    if start != -1 and end != -1:
        try:
            return json.loads(raw[start:end + 1])
        except:
            pass

    # array
    start = raw.find("[")
    end = raw.rfind("]")

    if start != -1 and end != -1:
        try:
            return json.loads(raw[start:end + 1])
        except:
            pass

    return None


def parse_model(raw: str, model_class: Type[T], fallback: T, *, _retry: bool = False) -> T:
    data = extract_json(raw)

    if data is None:
        if not _retry:
            # Signal to caller that JSON was unparseable so it can retry with stricter prompt
            fallback.__dict__.setdefault("_json_parse_failed", True)
        return fallback

    try:
        return model_class.model_validate(data)
    except Exception:
        try:
            allowed = set(model_class.model_fields.keys())
            filtered = {k: v for k, v in data.items() if k in allowed}
            return model_class.model_validate(filtered)
        except Exception:
            return fallback


def did_fallback(obj) -> bool:
    """Returns True if parse_model fell back to the default (JSON was invalid)."""
    return bool(getattr(obj, "_json_parse_failed", False))