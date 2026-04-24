"""
Robust JSON extraction from LLM outputs.
Handles markdown fences, partial JSON, nested structures, etc.
"""
import json
import re
from typing import Any, Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def extract_json(raw: str) -> dict | list | None:
    """
    Try multiple strategies to extract valid JSON from a raw LLM response.
    Returns the parsed object or None if all strategies fail.
    """
    if not raw or not raw.strip():
        return None

    # Strategy 1: direct parse
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown fences ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = cleaned.replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 3: find the first { ... } block (greedy, handles trailing text)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Strategy 4: find the LARGEST { ... } block by trying from each {
    start_positions = [m.start() for m in re.finditer(r"\{", cleaned)]
    for start in start_positions:
        # try to find matching closing brace
        depth = 0
        for i, ch in enumerate(cleaned[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break

    # Strategy 5: lenient — fix common issues (trailing commas, single quotes)
    try:
        fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)  # trailing commas
        fixed = re.sub(r"'([^']*)'", r'"\1"', fixed)    # single → double quotes
        match = re.search(r"\{[\s\S]*\}", fixed)
        if match:
            return json.loads(match.group())
    except Exception:
        pass

    # Strategy 6: sanitize common LLM JSON issues (unescaped newlines in strings)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        candidate = match.group()

        def _sanitize(s: str) -> str:
            out: list[str] = []
            in_string = False
            escaped = False
            for ch in s:
                if escaped:
                    out.append(ch)
                    escaped = False
                    continue

                if ch == "\\":
                    out.append(ch)
                    escaped = True
                    continue

                if ch == '"':
                    out.append(ch)
                    in_string = not in_string
                    continue

                if in_string and ch in ("\n", "\r", "\t"):
                    out.append({"\\n": "\\n", "\\r": "\\r", "\\t": "\\t"}[repr(ch)[1:-1]])
                    continue

                # Replace non-breaking space which sometimes breaks parsing
                if ch == "\u00a0":
                    out.append(" ")
                    continue

                out.append(ch)
            return "".join(out)

        try:
            fixed = _sanitize(candidate)
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            return json.loads(fixed)
        except Exception:
            pass

    return None


def parse_model(raw: str, model_class: Type[T], fallback: T) -> T:
    """
    Extract JSON from raw LLM output and validate against a Pydantic model.
    Falls back to a safe default on any failure, with partial field recovery.
    """
    data = extract_json(raw)

    if data is None:
        print(f"[warn] Could not extract JSON for {model_class.__name__}, using fallback")
        print(f"[debug] Raw response (first 300 chars): {raw[:300]!r}")
        return fallback

    try:
        return model_class.model_validate(data)
    except Exception as e:
        print(f"[warn] Pydantic validation failed for {model_class.__name__}: {e}")
        # Attempt partial construction — fill what we can
        try:
            # Remove unknown fields gracefully
            known_fields = set(model_class.model_fields.keys())
            filtered = {k: v for k, v in data.items() if k in known_fields}
            return model_class.model_validate(filtered)
        except Exception:
            print(f"[warn] Partial construction also failed, using fallback")
            return fallback
