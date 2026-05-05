from fastapi.testclient import TestClient

from goldroger.api import app


def test_analyze_requires_confirmation_for_equity():
    client = TestClient(app)
    resp = client.post(
        "/analyze",
        json={
            "company": "NVIDIA",
            "company_type": "public",
            "mode": "equity",
            "confirmed_company": False,
        },
    )
    assert resp.status_code == 400
    assert "confirmation is required" in resp.json()["detail"].lower()


def test_analyze_rejects_none_of_suggested_companies():
    client = TestClient(app)
    resp = client.post(
        "/analyze",
        json={
            "company": "NVIDIA",
            "company_type": "public",
            "mode": "equity",
            "confirmed_company": True,
            "none_of_the_suggested_companies": True,
        },
    )
    assert resp.status_code == 400
    assert "refine" in resp.json()["detail"].lower()


def test_analyze_accepts_confirmed_company(monkeypatch):
    class _DummyResult:
        def model_dump(self):
            return {"ok": True}

    def _fake_run_analysis(company, company_type):
        assert company == "NVDA"
        assert company_type == "public"
        return _DummyResult()

    import goldroger.api as api_mod

    monkeypatch.setattr(api_mod, "run_analysis", _fake_run_analysis)
    client = TestClient(app)
    resp = client.post(
        "/analyze",
        json={
            "company": "NVIDIA",
            "company_type": "public",
            "mode": "equity",
            "confirmed_company": True,
            "selected_symbol": "NVDA",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["ok"] is True
