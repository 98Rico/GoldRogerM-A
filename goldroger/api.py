from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .data.registry import DEFAULT_REGISTRY
from .exporters import generate_excel, generate_pptx
from .orchestrator import run_analysis, run_ma_analysis, run_pipeline

app = FastAPI(title="Gold Roger API", version="0.1.0")


class AnalyzeRequest(BaseModel):
    company: str = Field(min_length=1, description="Company name or ticker")
    company_type: Literal["public", "private"] = "public"
    mode: Literal["equity", "ma", "pipeline"] = "equity"
    acquirer: str | None = None
    objective: str | None = None
    buyer: str | None = None
    focus: str | None = None
    export_excel: bool = False
    export_pptx: bool = False
    output_dir: str = "outputs"
    confirmed_company: bool = False
    none_of_the_suggested_companies: bool = False
    selected_symbol: str | None = None
    selected_company_name: str | None = None


class AnalyzeResponse(BaseModel):
    result: dict
    excel_path: str | None = None
    pptx_path: str | None = None


class CredentialUpdateRequest(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    persist_to_env_file: bool = True


class CredentialUpdateResponse(BaseModel):
    saved: list[str]
    skipped: list[str]
    persisted_to: str | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/resolve-company")
def resolve_company(query: str, company_type: Literal["public", "private"] = "public") -> dict:
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query is required")

    suggestions: list[dict] = []
    try:
        with httpx.Client(timeout=12, follow_redirects=True) as client:
            resp = client.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "quotesCount": 7, "newsCount": 0},
            )
            quotes = resp.json().get("quotes", [])
            for item in quotes:
                symbol = item.get("symbol")
                name = item.get("longname") or item.get("shortname") or item.get("name") or q
                qtype = item.get("quoteType") or ""
                exch = item.get("exchDisp") or item.get("exchange") or ""
                region = item.get("region") or ""
                if not symbol and not name:
                    continue
                if company_type == "public" and qtype not in ("EQUITY", "ETF"):
                    continue
                suggestions.append(
                    {
                        "display_name": name,
                        "symbol": symbol or "",
                        "quote_type": qtype,
                        "exchange": exch,
                        "region": region,
                    }
                )
    except Exception:
        suggestions = []

    # Ensure we always return at least one suggestion row for explicit confirmation.
    if not suggestions:
        suggestions = [
            {
                "display_name": q,
                "symbol": "",
                "quote_type": "UNKNOWN",
                "exchange": "",
                "region": "",
            }
        ]

    return {"query": q, "company_type": company_type, "suggestions": suggestions[:7]}


@app.get("/data-sources")
def data_sources() -> dict:
    providers = [c.__dict__ for c in DEFAULT_REGISTRY.list_providers()]
    return {"providers": providers}


def _persist_env_values(values: dict[str, str]) -> str:
    env_path = Path(".env")
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    for key, value in values.items():
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        line = f"{key}={value}"
        if pattern.search(content):
            content = pattern.sub(line, content)
        else:
            if content and not content.endswith("\n"):
                content += "\n"
            content += line + "\n"
    env_path.write_text(content, encoding="utf-8")
    return str(env_path)


@app.post("/settings/credentials", response_model=CredentialUpdateResponse)
def settings_credentials(req: CredentialUpdateRequest) -> CredentialUpdateResponse:
    allowed_env_vars = {
        c.key_env_var
        for c in DEFAULT_REGISTRY.list_providers()
        if c.requires_key and c.key_env_var
    }
    saved: list[str] = []
    skipped: list[str] = []
    clean_values: dict[str, str] = {}
    for key, value in req.values.items():
        k = (key or "").strip()
        v = (value or "").strip()
        if not k or not v:
            skipped.append(k or "<empty>")
            continue
        if k not in allowed_env_vars:
            skipped.append(k)
            continue
        os.environ[k] = v
        clean_values[k] = v
        saved.append(k)

    persisted_to = None
    if req.persist_to_env_file and clean_values:
        persisted_to = _persist_env_values(clean_values)

    return CredentialUpdateResponse(saved=saved, skipped=skipped, persisted_to=persisted_to)


@app.get("/ui", response_class=HTMLResponse)
def ui() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Gold Roger — UI</title>
  <style>
    body { font-family: -apple-system, system-ui, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; color: #111827; background:#f7f7fb; }
    h1 { margin: 0 0 12px 0; }
    .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; max-width: 980px; background: #fff; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; }
    label { font-size: 12px; color: #374151; display: block; margin-bottom: 6px; }
    input, select, textarea { width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 10px; font-size: 14px; }
    textarea { min-height: 92px; }
    .col { flex: 1 1 220px; }
    button { background: #1b2a4a; color: white; border: 0; padding: 10px 14px; border-radius: 10px; cursor: pointer; font-weight: 600; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .muted { color: #6b7280; font-size: 13px; }
    pre { background: #0b1020; color: #e5e7eb; padding: 14px; border-radius: 12px; overflow: auto; }
    .links a { display: inline-block; margin-right: 12px; }
    .pill { display:inline-block; padding: 4px 10px; border-radius:999px; background:#eef2ff; color:#1e3a8a; font-size:12px; margin-right:8px; }
  </style>
</head>
<body>
  <h1>Gold Roger</h1>
  <p class="muted">Quick test UI — choose the workflow, fill only the relevant fields, click <b>Run</b>.</p>

  <div class="card">
    <div class="row">
      <div class="col">
        <label>Workflow</label>
        <select id="mode">
          <option value="equity">Equity analysis (5 agents) — company deep-dive</option>
          <option value="ma">M&A analysis — fit / diligence / execution</option>
          <option value="pipeline">Opportunity sourcing — generate target shortlist + EV</option>
        </select>
      </div>
      <div class="col">
        <label>Company (target)</label>
        <input id="company" placeholder="LVMH / Longchamp / ..." value="LVMH" />
      </div>
      <div class="col">
        <label>Company type</label>
        <select id="company_type">
          <option value="public">public</option>
          <option value="private">private</option>
        </select>
      </div>
    </div>

    <div class="row" style="margin-top: 12px;">
      <div class="col" style="flex: 1 1 100%;">
        <div class="muted" id="mode_help" style="padding:10px 12px; border:1px dashed #d1d5db; border-radius:12px; background:#fafafa;">
          <b>Equity analysis:</b> Enter a company name/ticker → optional exports (Excel/PPTX) → you get a structured JSON + deck.
        </div>
      </div>
    </div>

    <div class="row" id="ma_fields" style="margin-top: 12px;">
      <div class="col">
        <label>Acquirer (M&A)</label>
        <input id="acquirer" placeholder="e.g. LVMH" />
      </div>
      <div class="col">
        <label>Objective (M&A)</label>
        <input id="objective" placeholder="e.g. expand in premium leather goods" />
      </div>
    </div>

    <div class="row" id="pipeline_fields" style="margin-top: 12px;">
      <div class="col">
        <label>Buyer (pipeline)</label>
        <input id="buyer" placeholder="e.g. Global consumer goods group" />
      </div>
      <div class="col" style="flex: 2 1 440px;">
        <label>Focus (pipeline) — what to source</label>
        <textarea id="focus" placeholder="Paste your sourcing brief (sector, geo, size, channel mix, etc.)"></textarea>
      </div>
    </div>

    <div class="row" style="margin-top: 12px;">
      <div class="col">
        <label>Exports</label>
        <div class="muted">
          <label><input type="checkbox" id="export_excel" /> Excel (equity only)</label><br/>
          <label><input type="checkbox" id="export_pptx" checked /> PPTX (equity / M&A / pipeline)</label>
        </div>
      </div>
      <div class="col">
        <label>Output dir</label>
        <input id="output_dir" value="outputs" />
      </div>
      <div class="col" style="display:flex; align-items:flex-end;">
        <button id="runBtn">Run</button>
      </div>
    </div>
  </div>

  <div style="margin-top: 16px;" class="card">
    <h3 style="margin-top:0;">Data Source Credentials</h3>
    <p class="muted">Set missing provider keys here. Values are applied immediately; optionally persisted to <code>.env</code>.</p>
    <div id="cred_list" class="row"></div>
    <div class="row" style="margin-top: 12px;">
      <div class="col">
        <label><input type="checkbox" id="persist_env" checked /> Persist to .env</label>
      </div>
      <div class="col" style="display:flex; align-items:flex-end;">
        <button id="saveCredsBtn">Save Credentials</button>
      </div>
    </div>
    <div class="muted" id="cred_status"></div>
  </div>

  <div style="margin-top: 16px;" class="card">
    <div id="company_confirm" style="display:none; margin-bottom: 12px; padding:12px; border:1px solid #d1d5db; border-radius:12px; background:#fafafa;">
      <div style="font-weight:600; margin-bottom:8px;">Confirm Company</div>
      <div class="muted" style="margin-bottom:8px;">Please confirm the company before we run analysis.</div>
      <div id="company_options"></div>
      <div class="muted" id="company_confirm_status" style="margin-top:8px;"></div>
    </div>

    <div style="margin-bottom: 8px;">
      <span class="pill" id="status">idle</span>
      <span class="muted" id="hint"></span>
    </div>
    <div class="links" id="links"></div>
    <pre id="out">{}</pre>
  </div>

  <script>
    const el = (id) => document.getElementById(id);
    const status = (t) => { el("status").textContent = t; };
    const hint = (t) => { el("hint").textContent = t || ""; };
    let confirmedSelection = null;
    let confirmationQuery = "";

    const resetConfirmation = () => {
      confirmedSelection = null;
      confirmationQuery = "";
      el("company_confirm").style.display = "none";
      el("company_options").innerHTML = "";
      el("company_confirm_status").textContent = "";
    };

    const setModeUI = () => {
      const m = el("mode").value;
      const isEquity = m === "equity";
      const isMA = m === "ma";
      const isPipeline = m === "pipeline";

      el("ma_fields").style.display = isMA ? "flex" : "none";
      el("pipeline_fields").style.display = isPipeline ? "flex" : "none";

      // For pipeline, company fields are ignored; keep them but make it obvious.
      el("company").disabled = isPipeline;
      el("company_type").disabled = isPipeline;
      if (isPipeline) {
        el("company").value = "pipeline";
        el("company_type").value = "private";
        resetConfirmation();
      } else {
        el("company").disabled = false;
        el("company_type").disabled = false;
      }

      // Excel only makes sense in equity mode.
      el("export_excel").disabled = !isEquity;
      if (!isEquity) el("export_excel").checked = false;

      if (isEquity) {
        el("mode_help").innerHTML = "<b>Equity analysis:</b> Enter a company name/ticker → optional exports (Excel/PPTX) → structured JSON + deck.";
      } else if (isMA) {
        el("mode_help").innerHTML = "<b>M&A analysis:</b> Enter a target company + <i>Acquirer</i> + <i>Objective</i> → returns opportunities, fit/synergies, diligence red flags, execution plan, and an LBO snapshot.";
      } else {
        el("mode_help").innerHTML = "<b>Opportunity sourcing (pipeline):</b> Fill <i>Buyer</i> + <i>Focus</i> → Gold Roger generates a shortlist of private targets with estimated Revenue / EBITDA % / EV + a PPTX pipeline deck.";
        if (!el('focus').value.trim()) {
          el("focus").value = "Premium beauty & wellness; founder-led private companies in Europe; skincare, wellness, premium personal care; high-growth; strong brand + loyalty; DTC/digital-first; younger consumers; international expansion potential; profitability potential; prefer €20m–€300m revenue range; avoid pharma/medtech.";
        }
      }
    };

    el("mode").addEventListener("change", setModeUI);
    el("company").addEventListener("input", resetConfirmation);
    el("company_type").addEventListener("change", resetConfirmation);
    setModeUI();

    const fetchCompanySuggestions = async () => {
      const query = el("company").value.trim();
      const companyType = el("company_type").value;
      const resp = await fetch(`/resolve-company?query=${encodeURIComponent(query)}&company_type=${encodeURIComponent(companyType)}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Company resolution failed");
      confirmationQuery = query;
      const rows = data.suggestions || [];
      const html = rows.map((s, idx) => `
        <label style="display:block; border:1px solid #e5e7eb; border-radius:10px; padding:8px; margin-bottom:8px;">
          <input type="radio" name="company_candidate" value="${idx}" />
          <b>${s.display_name || query}</b>
          <span class="muted"> ${s.symbol ? `(${s.symbol})` : ""} ${s.exchange || ""} ${s.region || ""}</span>
        </label>
      `).join("");
      el("company_options").innerHTML = html + `
        <label style="display:block; border:1px solid #f1c1c1; border-radius:10px; padding:8px; margin-bottom:8px;">
          <input type="radio" name="company_candidate" value="none" />
          <b>None of these companies</b>
        </label>
      `;
      el("company_confirm").style.display = "block";
      el("company_confirm_status").textContent = "Select one option, then click Run again.";
      confirmedSelection = { suggestions: rows, selected: null, none: false };
      document.querySelectorAll('input[name=\"company_candidate\"]').forEach(n => {
        n.addEventListener('change', () => {
          const v = n.value;
          if (v === "none") {
            confirmedSelection.selected = null;
            confirmedSelection.none = true;
            el("company_confirm_status").textContent = "You selected 'None of these companies'. Refine the name and run again.";
          } else if (n.checked) {
            const i = Number(v);
            confirmedSelection.selected = confirmedSelection.suggestions[i];
            confirmedSelection.none = false;
            el("company_confirm_status").textContent = `Confirmed: ${confirmedSelection.selected.display_name}${confirmedSelection.selected.symbol ? ` (${confirmedSelection.selected.symbol})` : ""}`;
          }
        });
      });
    };

    const loadProviders = async () => {
      try {
        const resp = await fetch("/data-sources");
        const data = await resp.json();
        const providers = data.providers || [];
        const keyed = providers.filter(p => p.requires_key && p.key_env_var);
        if (!keyed.length) {
          el("cred_list").innerHTML = '<div class="col"><span class="muted">No keyed providers configured.</span></div>';
          return;
        }
        el("cred_list").innerHTML = keyed.map((p, idx) => `
          <div class="col">
            <label>${p.display_name} (${p.key_env_var})</label>
            <input type="password" id="cred_${idx}" data-env="${p.key_env_var}" placeholder="${p.status === 'active' ? 'Already configured (enter to replace)' : 'Enter API key'}" />
            <div class="muted">${p.description}</div>
          </div>
        `).join("");
      } catch (e) {
        el("cred_status").textContent = "Failed to load providers: " + String(e);
      }
    };

    el("saveCredsBtn").addEventListener("click", async () => {
      const inputs = Array.from(document.querySelectorAll('#cred_list input[data-env]'));
      const values = {};
      for (const i of inputs) {
        if (i.value && i.value.trim()) values[i.dataset.env] = i.value.trim();
      }
      if (!Object.keys(values).length) {
        el("cred_status").textContent = "No credential values entered.";
        return;
      }
      try {
        const resp = await fetch("/settings/credentials", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            values,
            persist_to_env_file: el("persist_env").checked
          }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Failed to save credentials");
        el("cred_status").textContent = `Saved: ${data.saved.join(", ")}${data.skipped.length ? ` | Skipped: ${data.skipped.join(", ")}` : ""}${data.persisted_to ? ` | Persisted: ${data.persisted_to}` : ""}`;
        inputs.forEach(i => i.value = "");
        loadProviders();
      } catch (e) {
        el("cred_status").textContent = "Credential save failed: " + String(e);
      }
    });

    loadProviders();

    el("runBtn").addEventListener("click", async () => {
      el("runBtn").disabled = true;
      status("running");
      hint("This can take a few minutes depending on web_search + model.");
      el("links").innerHTML = "";

      const payload = {
        company: el("company").value.trim() || "LVMH",
        company_type: el("company_type").value,
        mode: el("mode").value,
        acquirer: el("acquirer").value.trim() || null,
        objective: el("objective").value.trim() || null,
        buyer: el("buyer").value.trim() || null,
        focus: el("focus").value.trim() || null,
        export_excel: el("export_excel").checked,
        export_pptx: el("export_pptx").checked,
        output_dir: el("output_dir").value.trim() || "outputs",
        confirmed_company: false,
        none_of_the_suggested_companies: false,
        selected_symbol: null,
        selected_company_name: null,
      };

      // Mandatory confirmation for non-pipeline workflows.
      if (payload.mode !== "pipeline") {
        const companyNow = el("company").value.trim() || "";
        if (!confirmedSelection || confirmationQuery !== companyNow) {
          try {
            await fetchCompanySuggestions();
            status("awaiting_confirmation");
            hint("Please confirm the company selection.");
          } catch (e) {
            status("error");
            hint(String(e));
          } finally {
            el("runBtn").disabled = false;
          }
          return;
        }
        if (confirmedSelection.none) {
          status("awaiting_confirmation");
          hint("Please refine company name; current selection is 'None of these companies'.");
          el("runBtn").disabled = false;
          return;
        }
        if (!confirmedSelection.selected) {
          status("awaiting_confirmation");
          hint("Please select one suggested company or choose 'None of these companies'.");
          el("runBtn").disabled = false;
          return;
        }
        payload.confirmed_company = true;
        payload.selected_symbol = confirmedSelection.selected.symbol || null;
        payload.selected_company_name = confirmedSelection.selected.display_name || payload.company;
      }

      try {
        const resp = await fetch("/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Request failed");
        el("out").textContent = JSON.stringify(data.result, null, 2);

        const links = [];
        if (data.excel_path) links.push({label: "Download Excel", path: data.excel_path});
        if (data.pptx_path) links.push({label: "Download PPTX", path: data.pptx_path});

        if (links.length) {
          el("links").innerHTML = links.map(l => `<a href="/files?path=${encodeURIComponent(l.path)}" target="_blank">${l.label}</a>`).join("");
        }
        status("done");
        hint("");
      } catch (e) {
        status("error");
        hint(String(e));
      } finally {
        el("runBtn").disabled = false;
      }
    });
  </script>
</body>
</html>
""".strip()


@app.get("/files")
def files(path: str) -> FileResponse:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    out_dir = Path("outputs").resolve()
    try:
        rp = p.resolve()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if out_dir not in rp.parents:
        raise HTTPException(status_code=403, detail="Only files under ./outputs can be downloaded")

    return FileResponse(rp, filename=rp.name)


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    try:
        if req.mode != "pipeline":
            if req.none_of_the_suggested_companies:
                raise HTTPException(
                    status_code=400,
                    detail="No company selected. Please refine the name and confirm the correct company.",
                )
            if not req.confirmed_company:
                raise HTTPException(
                    status_code=400,
                    detail="Company confirmation is required before analysis.",
                )

        if req.mode == "pipeline":
            buyer = req.buyer or "Global consumer goods group"
            focus = req.focus or (
                "Premium beauty and wellness; high-growth founder-led private companies in Europe; "
                "skincare, wellness, premium personal care; younger consumers; DTC"
            )
            result = run_pipeline(buyer=buyer, focus=focus)
        elif req.mode == "ma":
            company_input = req.selected_symbol or req.selected_company_name or req.company
            result = run_ma_analysis(
                company_input,
                req.company_type,
                acquirer=req.acquirer,
                objective=req.objective,
            )
        else:
            company_input = req.selected_symbol or req.selected_company_name or req.company
            result = run_analysis(company_input, req.company_type)
    except KeyError as exc:
        # typically missing env var like MISTRAL_API_KEY
        raise HTTPException(status_code=500, detail=f"Missing configuration: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    excel_path = None
    pptx_path = None

    out_dir = Path(req.output_dir)
    if req.export_excel or req.export_pptx:
        out_dir.mkdir(parents=True, exist_ok=True)

    if req.export_excel and req.mode == "equity":
        excel_path = str(out_dir / f"{req.company}_analysis.xlsx".replace(" ", "_"))
        generate_excel(result, excel_path)

    if req.export_pptx:
        fname = f"{req.company}_analysis.pptx" if req.mode != "pipeline" else "pipeline_deck.pptx"
        pptx_path = str(out_dir / fname.replace(" ", "_"))
        generate_pptx(result, pptx_path)

    return AnalyzeResponse(
        result=result.model_dump(),
        excel_path=excel_path,
        pptx_path=pptx_path,
    )
