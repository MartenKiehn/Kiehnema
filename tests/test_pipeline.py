"""Phase 3: the whole LangGraph pipeline with a scripted model (no Bedrock cost).

Covers: fact verification (an invented claim is dropped), storyboard validation, a first composition
that violates the sandbox lint and is repaired by the fix loop, QA, the review interrupt with a copy
edit, a second review with approval, and the final render of both formats.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from app.config import settings
from app.crawl.guard import UrlGuard
from app.pipeline.jobs import job_state, resume_job, start_job
from app.pipeline.llm import FakeLLM
from app.pipeline.schemas import CompanyProfile, Concept, QAReport
from app.pipeline.verify import QuoteIndex, lint_composition, validate_concept


def _profile(site: str) -> dict:
    return {
        "company_name": "Acme", "language": "en", "one_liner": "Acme builds robots.", "audience": "small factories",
        "tone": "friendly", "primary_cta": "Book a call",
        "facts": [
            {"id": "f1", "kind": "tagline", "text": "Robots that work.", "quote": "Robots that work.", "source_url": site},
            {"id": "f2", "kind": "service", "text": "Design, build and run robots", "quote": "We design, build and run robots for small factories.", "source_url": site},
            {"id": "f3", "kind": "differentiator", "text": "Fixed-price projects", "quote": "Fixed-price projects", "source_url": site + "about.html"},
            {"id": "f4", "kind": "differentiator", "text": "EU hosting", "quote": "EU hosting", "source_url": site + "about.html"},
            {"id": "f5", "kind": "proof", "text": "Founded 2019", "quote": "Founded in 2019 by three engineers.", "source_url": site + "about.html"},
            {"id": "f6", "kind": "service", "text": "Robot audits", "quote": "Two-week audit with a clear report.", "source_url": site + "services/"},
            {"id": "f9", "kind": "claim", "text": "World leader", "quote": "Acme is the undisputed world leader in robotics", "source_url": site},
        ],
    }


CONCEPT = {
    "title": "Acme – Robots that work", "language": "en", "rationale": "Short and direct.",
    "hook": "Robots that actually work.",
    "palette": {"dark": "#10243e", "light": "#ffffff", "primary": "#f2994a", "accent": "#0a7d5a"},
    "music": {"bpm": 120, "progression": ["C", "G", "Am", "F"], "outro_progression": ["F", "C"], "energy": "medium"},
    "scenes": [
        {"id": "s-hook", "start": 0, "duration": 2.1, "purpose": "hook", "visual": "big type", "background": "dark",
         "texts": [{"id": "hook-title", "role": "headline", "text": "Robots that work.", "fact_ids": ["f1"]}], "assets": ["logo.svg"],
         "cues": []},
        {"id": "s-proof", "start": 2.1, "duration": 1.9, "purpose": "proof", "visual": "chip", "background": "dark",
         "texts": [{"id": "proof-title", "role": "headline", "text": "Fixed-price projects", "fact_ids": ["f3"]}], "assets": [],
         "cues": [{"t": 2, "type": "drop"}, {"t": 2, "type": "impact"}]},
        {"id": "s-outro", "start": 4, "duration": 2, "purpose": "cta", "visual": "logo + cta", "background": "light",
         "texts": [{"id": "outro-cta", "role": "cta", "text": "Book a call"}, {"id": "outro-url", "role": "url", "text": "acme.test"}],
         "assets": ["logo.svg"], "cues": [{"t": 4, "type": "outro"}]},
    ],
}

HTML = """<section class="scene" id="s-hook"><h1 class="split" data-text-id="hook-title">Robots that work.</h1></section>
<section class="scene" id="s-proof"><h1 class="split" data-text-id="proof-title">Fixed-price projects</h1></section>
<section class="scene light" id="s-outro"><img class="logo" src="assets/brand/logo.svg" alt="">
<p data-text-id="outro-cta">Book a call</p><p data-text-id="outro-url">acme.test</p></section>"""
CSS = """#stage { background: var(--brand-dark); color: var(--brand-light); font-family: var(--font-body); }
.scene { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 30px; }
.light { background: var(--brand-light); color: var(--brand-dark); }
h1 { font-size: 96px; } p { font-size: 48px; } .logo { width: 360px; }"""
JS_GOOD = """MG.composition(({ manifest, scene, words, fadeUp, exitUp, EXIT }) => {
  const S = manifest.scenes;
  const a = scene('s-hook', ...S['s-hook']); words(`${a.sel} h1`, a.T + 0.1); exitUp(`${a.sel} h1`, a.end - EXIT);
  const b = scene('s-proof', ...S['s-proof']); words(`${b.sel} h1`, b.T + 0.05); exitUp(`${b.sel} h1`, b.end - EXIT);
  const c = scene('s-outro', ...S['s-outro']); fadeUp(`${c.sel} p, ${c.sel} img`, c.T + 0.1);
});"""
JS_BAD = JS_GOOD.replace("const S = manifest.scenes;", "const S = manifest.scenes; fetch('/track');")


def compose_answer(js: str) -> str:
    return (f'<file name="composition.html">\n{HTML}\n</file>\n<file name="composition.css">\n{CSS}\n</file>\n'
            f'<file name="composition.js">\n{js}\n</file>')


FIX_LINT = '<edit file="composition.js"><search>const S = manifest.scenes; fetch(\'/track\');</search><replace>const S = manifest.scenes;</replace></edit>'
FIX_EDIT = ('<edit file="composition.html"><search>data-text-id="proof-title">Fixed-price projects</search>'
            '<replace>data-text-id="proof-title">Fixed prices</replace></edit>')


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    return tmp_path / "data"


async def test_full_pipeline_with_fake_model(site, data_dir):
    llm = FakeLLM({
        "distill": [CompanyProfile.model_validate(_profile(site))],
        "concept": [Concept.model_validate(CONCEPT)],
        "compose": [compose_answer(JS_BAD)],
        "fix": [FIX_LINT, FIX_EDIT],
        "qa": [QAReport(approved=True, summary="Looks good.", issues=[])],
    })
    guard = UrlGuard(allow_hosts={"127.0.0.1"})

    job_id, result = await start_job(site, 6, "Short ad for factories", llm=llm, guard=guard)
    assert not result.get("error"), result.get("error")
    assert "__interrupt__" in result  # paused for review
    job = data_dir / "jobs" / job_id

    profile = json.loads((job / "profile.json").read_text())
    assert [f["id"] for f in profile["facts"]] == ["f1", "f2", "f3", "f4", "f5", "f6"]  # invented f9 dropped
    assert result["fix_round"] == 1 and result["check"]["ok"]  # lint violation fixed in one round
    assert "fetch(" not in (job / "composition" / "composition.js").read_text()
    assert result["qa"]["approved"]
    assert {(k["scene"], k["format"]) for k in result["keyframes"]} == {(s, f) for s in ("s-hook", "s-proof", "s-outro") for f in ("feed", "story")}
    assert (job / "brand" / "board.png").exists()

    # the fix prompt carried the lint problem and shared the cached system prompt with compose
    fix_call = next(m for step, m in llm.calls if step == "fix")
    compose_call = next(m for step, m in llm.calls if step == "compose")
    assert "network access (fetch)" in fix_call[1].content and fix_call[0].content == compose_call[0].content

    state = await job_state(job_id)
    assert state["next"] == ["review"] and state["interrupts"][0]["concept"]["title"] == CONCEPT["title"]

    result = await resume_job(job_id, {"action": "edit", "texts": {"proof-title": "Fixed prices"}}, llm=llm, guard=guard)
    assert "__interrupt__" in result and result["check"]["ok"]
    assert 'data-text-id="proof-title">Fixed prices' in (job / "composition" / "composition.html").read_text()
    concept = json.loads((job / "concept.json").read_text())
    assert concept["scenes"][1]["texts"][0]["text"] == "Fixed prices"

    result = await resume_job(job_id, {"action": "approve"}, llm=llm, guard=guard)
    assert result["status"] == "done"
    for name, size in (("feed", "1080x1350"), ("story", "1080x1920")):
        video = job / result["outputs"][name]
        info = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(video)], capture_output=True, text=True).stderr
        assert "Duration: 00:00:06.00" in info and size in info and "Audio: aac" in info
    status = json.loads((job / "status.json").read_text())
    assert status["status"] == "done" and status["cost_usd"] > 0
    assert any("characters so far" in h["message"] for h in status["history"])
    assert [c[0] for c in llm.calls] == ["distill", "concept", "compose", "fix", "qa", "fix"]


def test_concept_validation_catches_unbacked_claims_and_long_text():
    site = "https://example.com/"
    profile = CompanyProfile.model_validate(_profile(site))
    bad = json.loads(json.dumps(CONCEPT))
    bad["scenes"][0]["texts"][0]["fact_ids"] = []
    bad["scenes"][1]["texts"][0]["text"] = "This headline is much much too long for a scene that lasts less than two seconds"
    bad["scenes"][2]["assets"] = ["clients/client-00.png"]
    bad["scenes"][2]["cues"].append({"t": 4.5, "type": "drop"})
    _, errors = validate_concept(Concept.model_validate(bad), profile, 6, {"logo.svg", "clients/client-00.png"}, False)
    joined = "\n".join(errors)
    assert "needs fact_ids" in joined and "words need" in joined and "client logos are disabled" in joined
    assert "at most one 'drop'" in joined


def test_concept_timing_snaps_to_beats():
    site = "https://example.com/"
    profile = CompanyProfile.model_validate(_profile(site))
    concept, errors = validate_concept(Concept.model_validate(CONCEPT), profile, 6, {"logo.svg"}, False)
    assert not errors
    assert [(s.start, s.duration) for s in concept.scenes] == [(0, 2.0), (2.0, 2.0), (4.0, 2.0)]


def test_quote_index_tolerates_whitespace_and_typography():
    idx = QuoteIndex("Wir   entwickeln KI, die im\nTagesgeschäft wirkt – „klar“ und messbar.")
    assert idx.verified("Wir entwickeln KI, die im Tagesgeschäft wirkt - \"klar\" und messbar.")
    assert not idx.verified("Wir sind Weltmarktführer für KI im Tagesgeschäft")


def test_lint_flags_sandbox_violations():
    files = {"composition.html": '<p data-text-id="a">x</p><img src="https://cdn.example.com/x.png" onerror="x()">',
             "composition.css": "@import url(x.css); .a { animation: spin 1s; }",
             "composition.js": "MG.composition(() => { setTimeout(() => {}, 1); const t = Date.now(); eval('1'); });"}
    joined = "\n".join(lint_composition(files, {"a", "b"}))
    for needle in ("external URL", "inline event handlers", "@import", "timers", "wall-clock", "eval", "missing data-text-id"):
        assert needle in joined, needle


async def test_saved_review_combines_edits_and_feedback_and_deletes_checkpoint(data_dir, monkeypatch):
    """A saved review survives moving the workspace, without a browser or model call."""
    from langgraph.graph import END, START, StateGraph
    from app.pipeline import jobs
    from app.pipeline.graph import JobState, n_review

    def review_graph(saver):
        graph = StateGraph(JobState)
        graph.add_node("review", n_review)
        graph.add_edge(START, "review")
        graph.add_edge("review", END)
        return graph.compile(checkpointer=saver)

    monkeypatch.setattr(jobs, "build_graph", review_graph)
    job_id = jobs.create_job("https://example.com", 6)
    path = data_dir / "jobs" / job_id
    (path / "concept.json").write_text(json.dumps(CONCEPT))
    result = await jobs.run_job(job_id, llm=FakeLLM({}))
    assert "__interrupt__" in result
    state = await job_state(job_id)
    assert state["next"] == ["review"]
    moved = data_dir.with_name("Kiehnema-data")
    data_dir.rename(moved)
    monkeypatch.setattr(settings, "data_dir", moved)
    path = moved / "jobs" / job_id
    result = await resume_job(job_id, {"action": "edit", "texts": {"proof-title": "Fixed prices"},
                                       "text": "Make the opening bolder"}, llm=FakeLLM({}))
    assert "Fixed prices" in result["problems"] and "Make the opening bolder" in result["problems"]
    assert json.loads((path / "concept.json").read_text())["scenes"][1]["texts"][0]["text"] == "Fixed prices"
    with pytest.raises(ValueError, match="not waiting for review"):
        await resume_job(job_id, {"action": "approve"}, llm=FakeLLM({}))
    await jobs.delete_job(job_id)
    assert not path.exists()
    assert not (await job_state(job_id))["values"]
