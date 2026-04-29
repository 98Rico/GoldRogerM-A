"""Acquisition pipeline builder — run_pipeline()."""
from __future__ import annotations

from dotenv import load_dotenv

from goldroger.agents.specialists import PipelineBuilderAgent
from goldroger.ma.scoring import score_from_analysis
from goldroger.models import AcquisitionPipeline
from goldroger.utils.json_parser import parse_model, did_fallback
from goldroger.utils.logger import new_run

from ._shared import _client, _done, _step, console

load_dotenv()


def run_pipeline(
    buyer: str,
    focus: str = "",
    company_type: str = "private",
    quick: bool = False,
    llm: str | None = None,
) -> AcquisitionPipeline:
    log = new_run(buyer, "pipeline")
    client = _client(llm)
    pipeline_agent = PipelineBuilderAgent(client)

    console.rule(f"[PIPELINE] {buyer}")

    t0 = _step("Pipeline Generation")
    ctx = {"buyer": buyer, "focus": focus, "quick": quick}
    fallback = AcquisitionPipeline(buyer=buyer, thesis="N/A", focus=focus)
    raw = pipeline_agent.run(buyer, company_type, ctx)
    pipeline = parse_model(raw, AcquisitionPipeline, fallback)
    if did_fallback(pipeline) or not pipeline.targets:
        console.print("  [yellow]Retrying pipeline with strict JSON prompt…[/]")
        raw2 = pipeline_agent.run(buyer, company_type, {**ctx, "__strict_json_hint": True})
        pipeline2 = parse_model(raw2, AcquisitionPipeline, fallback, _retry=True)
        if pipeline2.targets:
            pipeline = pipeline2
    _done("Pipeline Generation", t0)

    t0 = _step("IC Scoring — Pipeline Targets")
    for i, tgt in enumerate(pipeline.targets):
        ic = score_from_analysis(
            strategy=6.0, synergies=6.0, financial=5.0,
            lbo=5.0, integration=6.0, risk=5.0, company=tgt.name,
        )
        console.print(f"  [{i+1}] {tgt.name}: IC {ic.ic_score:.0f}/100 → {ic.recommendation}")
    _done("IC Scoring", t0)

    console.rule("[DONE PIPELINE]")
    log.flush()

    return pipeline
