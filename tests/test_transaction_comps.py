"""Tests for transaction comps cache, validation, and aggregation."""
import json
import os
import tempfile
import pytest

from goldroger.data import transaction_comps as tc
from goldroger.data.transaction_comps import (
    TransactionComp,
    _validate,
    add_comps,
    load_cache,
    parse_agent_output,
    save_cache,
    sector_medians,
)


def _comp(target="Acme", acquirer="BigCo", sector="software", year=2023,
          ev_m=200.0, ev_ebitda=18.0, ev_revenue=4.0) -> TransactionComp:
    return TransactionComp(
        target=target, acquirer=acquirer, sector=sector, year=year,
        ev_m=ev_m, revenue_m=ev_m / ev_revenue if ev_revenue else None,
        ebitda_m=ev_m / ev_ebitda if ev_ebitda else None,
        ev_ebitda=ev_ebitda, ev_revenue=ev_revenue, source="test",
    )


# ── Validation ────────────────────────────────────────────────────────────────

def test_valid_comp_passes():
    assert _validate(_comp()) is True


def test_tiny_ev_rejected():
    assert _validate(_comp(ev_m=2.0)) is False


def test_extreme_ev_ebitda_rejected():
    assert _validate(_comp(ev_ebitda=999.0)) is False


def test_extreme_ev_revenue_rejected():
    assert _validate(_comp(ev_revenue=99.0)) is False


def test_borderline_ev_ebitda_passes():
    assert _validate(_comp(ev_ebitda=55.0)) is True
    assert _validate(_comp(ev_ebitda=61.0)) is False


# ── Cache persistence ─────────────────────────────────────────────────────────

def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    cache = tmp_path / "comps.json"
    monkeypatch.setattr(tc, "_CACHE_PATH", str(cache))
    comps = [_comp("Alpha"), _comp("Beta", year=2022)]
    save_cache(comps)
    loaded = load_cache()
    assert len(loaded) == 2
    assert loaded[0].target == "Alpha"


def test_load_missing_cache_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "_CACHE_PATH", str(tmp_path / "nonexistent.json"))
    assert load_cache() == []


def test_add_comps_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "_CACHE_PATH", str(tmp_path / "cache.json"))
    c1 = _comp("Alpha", year=2023)
    add_comps([c1])
    add_comps([c1])  # duplicate
    add_comps([_comp("Beta", year=2023)])
    result = load_cache()
    targets = [r.target for r in result]
    assert targets.count("Alpha") == 1
    assert "Beta" in targets


# ── Sector medians ────────────────────────────────────────────────────────────

def test_sector_medians_correct():
    comps = [
        _comp(ev_ebitda=10.0, ev_revenue=2.0, year=2022),
        _comp(ev_ebitda=20.0, ev_revenue=4.0, year=2023),
        _comp(ev_ebitda=30.0, ev_revenue=6.0, year=2023),
    ]
    result = sector_medians(comps, "software", min_year=2020)
    assert result["n_deals"] == 3
    assert result["ev_ebitda_median"] == 20.0
    assert result["ev_revenue_median"] == 4.0


def test_sector_medians_filters_old_deals():
    comps = [_comp(year=2019), _comp(year=2023)]
    result = sector_medians(comps, "software", min_year=2020)
    assert result["n_deals"] == 1


def test_sector_medians_broad_match():
    comps = [_comp(sector="enterprise software"), _comp(sector="SaaS")]
    result = sector_medians(comps, "software")
    assert result["n_deals"] == 2


def test_sector_medians_no_matches():
    comps = [_comp(sector="biotech")]
    result = sector_medians(comps, "fintech")
    assert result["n_deals"] == 0
    assert result["ev_ebitda_median"] is None


# ── parse_agent_output ────────────────────────────────────────────────────────

def test_parse_valid_deal_list():
    raw = json.dumps([{
        "target": "TargetCo", "acquirer": "AcquirerCo",
        "sector": "software", "year": 2023,
        "ev_m": 300.0, "revenue_m": 60.0, "ebitda_m": 20.0,
        "ev_ebitda": 15.0, "ev_revenue": 5.0, "source": "WSJ",
    }])
    result = parse_agent_output(raw, "software")
    assert len(result) == 1
    assert result[0].target == "TargetCo"
    assert result[0].ev_ebitda == 15.0


def test_parse_computes_multiples_when_missing():
    raw = json.dumps([{
        "target": "T", "acquirer": "A", "sector": "tech", "year": 2023,
        "ev_m": 200.0, "revenue_m": 40.0, "ebitda_m": 10.0,
        "ev_ebitda": None, "ev_revenue": None, "source": "",
    }])
    result = parse_agent_output(raw, "tech")
    assert len(result) == 1
    assert result[0].ev_ebitda == 20.0
    assert result[0].ev_revenue == 5.0


def test_parse_rejects_tiny_ev():
    raw = json.dumps([{
        "target": "Tiny", "acquirer": "Big", "sector": "tech", "year": 2023,
        "ev_m": 1.0, "revenue_m": None, "ebitda_m": None,
        "ev_ebitda": None, "ev_revenue": None, "source": "",
    }])
    result = parse_agent_output(raw, "tech")
    assert result == []


def test_parse_rejects_extreme_multiple():
    raw = json.dumps([{
        "target": "Crazy", "acquirer": "Big", "sector": "tech", "year": 2023,
        "ev_m": 500.0, "revenue_m": 10.0, "ebitda_m": 1.0,
        "ev_ebitda": 500.0, "ev_revenue": 50.0, "source": "",
    }])
    result = parse_agent_output(raw, "tech")
    assert result == []


def test_parse_dict_with_deals_key():
    raw = json.dumps({"deals": [
        {"target": "X", "acquirer": "Y", "sector": "tech", "year": 2022,
         "ev_m": 100.0, "revenue_m": 20.0, "ebitda_m": 8.0,
         "ev_ebitda": 12.5, "ev_revenue": 5.0, "source": "FT"}
    ]})
    result = parse_agent_output(raw, "tech")
    assert len(result) == 1


def test_parse_invalid_json_returns_empty():
    assert parse_agent_output("not json", "tech") == []


def test_parse_skips_pre_2010_deals():
    raw = json.dumps([{
        "target": "Old", "acquirer": "Big", "sector": "tech", "year": 2005,
        "ev_m": 200.0, "ev_ebitda": 10.0, "source": "",
    }])
    assert parse_agent_output(raw, "tech") == []
