"""Tests for goldroger.utils.json_parser — parse_model() edge cases."""
from pydantic import BaseModel
from goldroger.utils.json_parser import parse_model


class _Simple(BaseModel):
    name: str
    value: int = 0


_fallback = _Simple(name="fallback")


def test_parse_valid_json():
    result = parse_model('{"name": "Alice", "value": 42}', _Simple, _fallback)
    assert result.name == "Alice"
    assert result.value == 42


def test_parse_markdown_fenced_json():
    text = '```json\n{"name": "Bob", "value": 7}\n```'
    result = parse_model(text, _Simple, _fallback)
    assert result.name == "Bob"
    assert result.value == 7


def test_parse_fenced_no_language_tag():
    text = '```\n{"name": "Carol", "value": 3}\n```'
    result = parse_model(text, _Simple, _fallback)
    assert result.name == "Carol"


def test_parse_extra_keys_stripped():
    # extra="ignore" on Model means extra fields are silently dropped
    result = parse_model('{"name": "Dave", "value": 1, "extra": "ignored"}', _Simple, _fallback)
    assert result.name == "Dave"
    assert result.value == 1


def test_parse_malformed_returns_fallback():
    result = parse_model("this is not json at all !!!", _Simple, _fallback)
    assert result.name == "fallback"


def test_parse_missing_required_field_returns_fallback():
    result = parse_model('{"value": 99}', _Simple, _fallback)
    assert result.name == "fallback"


def test_parse_json_embedded_in_prose():
    text = 'Here is my analysis:\n{"name": "Eve", "value": 5}\nEnd of analysis.'
    result = parse_model(text, _Simple, _fallback)
    assert result.name == "Eve"
