from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

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


class AnalyzeResponse(BaseModel):
    result: dict
    excel_path: str | None = None
    pptx_path: str | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": True}


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
    setModeUI();

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
      };

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
        if req.mode == "pipeline":
            buyer = req.buyer or "Global consumer goods group"
            focus = req.focus or (
                "Premium beauty and wellness; high-growth founder-led private companies in Europe; "
                "skincare, wellness, premium personal care; younger consumers; DTC"
            )
            result = run_pipeline(buyer=buyer, focus=focus)
        elif req.mode == "ma":
            result = run_ma_analysis(
                req.company,
                req.company_type,
                acquirer=req.acquirer,
                objective=req.objective,
            )
        else:
            result = run_analysis(req.company, req.company_type)
    except KeyError as exc:
        # typically missing env var like MISTRAL_API_KEY
        raise HTTPException(status_code=500, detail=f"Missing configuration: {exc}") from exc
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
