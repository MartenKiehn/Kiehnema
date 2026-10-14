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
from app.pipeline.runtime import job_state, reopen_job, resume_job, start_job
from app.pipeline.llm import FakeLLM
from app.pipeline.schemas import CompanyProfile, Concept, QAReport
from app.pipeline.verify import QuoteIndex, lint_composition, validate_concept


async def test_render_progress_combines_formats_and_estimates_remaining_time(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.pipeline import graph

    monkeypatch.setattr(graph, '_dir', lambda state: tmp_path)
    monkeypatch.setattr(graph, 'build_site', lambda composition, site: site)
    clock = iter([0, 10, 20, 20, 35, 50])
    monkeypatch.setattr(graph, 'monotonic', lambda: next(clock))
    snapshots = []

    async def render(site, out_dir, formats, progress):
        snapshots.append(json.loads((tmp_path / 'status.json').read_text()))
        assert formats == ('feed', 'story')
        for fmt, count in [('feed', 0), ('feed', 50), ('feed', 100),
                           ('story', 0), ('story', 50), ('story', 100)]:
            progress(fmt, count, 100)
            snapshots.append(json.loads((tmp_path / 'status.json').read_text()))
        return SimpleNamespace(videos={fmt: out_dir / f'{fmt}.mp4' for fmt in formats})

    monkeypatch.setattr(graph, 'render_site', render)
    await graph.n_render({'job_id': 'example'}, {'configurable': {'llm': FakeLLM({})}})
    assert [s['render_progress']['percent'] for s in snapshots] == [0, 0, 25, 50, 50, 75, 99]
    assert [s['render_progress']['eta_seconds'] for s in snapshots] == [None, None, 30, 20, 20, 12, None]
    assert [s['render_progress'].get('format') for s in snapshots] == [None, 'feed', 'feed', 'feed', 'story', 'story', None]
    result = json.loads((tmp_path / 'status.json').read_text())
    assert result['status'] == 'done' and result['render_progress']['percent'] == 100
    assert all('frames' not in entry['message'] for entry in result['history'])


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
    monkeypatch.setattr("app.pipeline.runtime.workspace_dir", lambda: settings.data_dir)
    return tmp_path / "data"


async def test_full_pipeline_with_fake_model(site, data_dir, monkeypatch):
    llm = FakeLLM({
        "distill": [CompanyProfile.model_validate(_profile(site))],
        "concept": [Concept.model_validate(CONCEPT)],
        "compose": [compose_answer(JS_BAD)],
        "fix": [FIX_LINT, FIX_EDIT],
        "qa": [QAReport(approved=True, summary="Looks good.", issues=[])],
    })
    job_id, result = await start_job(site, 6, "Short ad for factories", llm=llm)
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

    result = await resume_job(job_id, {"action": "edit", "texts": {"proof-title": "Fixed prices"}}, llm=llm)
    assert "__interrupt__" in result and result["check"]["ok"]
    assert 'data-text-id="proof-title">Fixed prices' in (job / "composition" / "composition.html").read_text()
    concept = json.loads((job / "concept.json").read_text())
    assert concept["scenes"][1]["texts"][0]["text"] == "Fixed prices"

    result = await resume_job(job_id, {"action": "approve"}, llm=llm)
    assert result["status"] == "done"
    for name, size in (("feed", "1080x1350"), ("story", "1080x1920")):
        video = job / result["outputs"][name]
        info = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(video)], capture_output=True, text=True).stderr
        assert "Duration: 00:00:06.00" in info and size in info and "Audio: aac" in info
    status = json.loads((job / "status.json").read_text())
    assert status["status"] == "done" and status["cost_usd"] > 0
    assert any("characters so far" in h["message"] for h in status["history"])
    assert [c[0] for c in llm.calls] == ["distill", "concept", "compose", "fix", "qa", "fix"]

    exports = {name: (job / file).read_bytes() for name, file in result["outputs"].items()}
    monkeypatch.setattr("app.pipeline.runtime._bedrock", lambda *args: FakeLLM({}, usage=llm.usage))
    reopened = await reopen_job(job_id)
    assert "__interrupt__" in reopened
    saved = await job_state(job_id)
    assert saved["next"] == ["review"] and saved["interrupts"]
    assert saved["values"]["status"] == "review" and saved["values"]["usage"] == result["usage"]
    assert saved["values"]["keyframes"] == result["keyframes"]
    reopened_status = json.loads((job / "status.json").read_text())
    assert reopened_status["status"] == "review" and reopened_status["cost_usd"] == status["cost_usd"]
    assert all((job / result["outputs"][name]).read_bytes() == content for name, content in exports.items())
    with pytest.raises(ValueError, match="not a completed render"):
        await reopen_job(job_id)

    llm.script["fix"] = ['<edit file="composition.html"><search>data-text-id="proof-title">Fixed prices</search>'
                         '<replace>data-text-id="proof-title">Clear prices</replace></edit>']
    edited = await resume_job(job_id, {"action": "edit", "texts": {"proof-title": "Clear prices"}}, llm=llm)
    assert "__interrupt__" in edited and edited["check"]["ok"]
    assert 'data-text-id="proof-title">Clear prices' in (job / "composition/composition.html").read_text()
    rendered = await resume_job(job_id, {"action": "approve"}, llm=llm)
    assert rendered["status"] == "done" and not (await job_state(job_id))["next"]
    assert [c[0] for c in llm.calls] == ["distill", "concept", "compose", "fix", "qa", "fix", "fix"]


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


@pytest.mark.parametrize("limits,expected_pages,expected_depth", [
    ({"max_pages": 7, "max_depth": 0}, 1, 0),
    ({"max_pages": 1, "max_depth": 2}, 1, 0),
    ({"max_pages": 2, "max_depth": 1}, 2, 1),
    ({}, 3, 1),
])
async def test_saved_scan_settings_reach_crawler(site, data_dir, monkeypatch, limits, expected_pages, expected_depth):
    from langgraph.graph import END, START, StateGraph
    from app.crawl.crawler import CrawlResult
    from app.pipeline import runtime as jobs
    from app.pipeline.graph import JobState, n_crawl

    def crawl_graph(saver):
        graph = StateGraph(JobState)
        graph.add_node("crawl", n_crawl)
        graph.add_edge(START, "crawl")
        graph.add_edge("crawl", END)
        return graph.compile(checkpointer=saver)

    monkeypatch.setattr(jobs, "build_graph", crawl_graph)
    job_id = jobs.create_job(site, 30, **limits)
    path = data_dir / "jobs" / job_id
    if not limits:
        request = json.loads((path / "request.json").read_text())
        del request["max_pages"], request["max_depth"]
        (path / "request.json").write_text(json.dumps(request))
    await jobs.run_job(job_id, llm=FakeLLM({}), guard=UrlGuard(allow_hosts={"127.0.0.1"}))
    result = CrawlResult.load(path / "crawl")
    assert len(result.pages) == expected_pages
    assert max(page.depth for page in result.pages) == expected_depth
    history = json.loads((path / "status.json").read_text())["history"]
    assert f"Reading page 1 (limit {limits.get('max_pages', 20)}): {site}" in [item["message"] for item in history]


async def test_saved_review_combines_edits_and_feedback_and_deletes_checkpoint(data_dir, monkeypatch):
    """A saved review survives moving the workspace, without a browser or model call."""
    from langgraph.graph import END, START, StateGraph
    from app.pipeline import runtime as jobs
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
                                       "text": "Make the opening bolder",
                                       "scenes": [{"id": s["id"], "duration": 5, "visual": s["visual"]} for s in CONCEPT["scenes"]],
                                       "comments": [{"time": 2.5, "scene_id": "s-proof", "format": "feed", "text": "Show our robot"}]}, llm=FakeLLM({}))
    assert "Fixed prices" in result["problems"] and "Make the opening bolder" in result["problems"]
    persisted = (await job_state(job_id))["values"]
    assert persisted["duration"] == 15 and persisted["review"]["comments"][0]["text"] == "Show our robot"
    assert "2.500s in original scene s-proof (feed format)" in persisted["problems"]
    assert json.loads((path / "concept.json").read_text())["scenes"][1]["texts"][0]["text"] == "Fixed prices"
    with pytest.raises(ValueError, match="not waiting for review"):
        await resume_job(job_id, {"action": "approve"}, llm=FakeLLM({}))
    await jobs.delete_job(job_id)
    assert not path.exists()
    assert not (await job_state(job_id))["values"]


async def test_review_retimes_scenes_and_preserves_comment_targets(data_dir, monkeypatch):
    from app.pipeline import runtime as jobs
    from app.pipeline.graph import n_review

    job_id = jobs.create_job("https://example.com", 6)
    path = data_dir / "jobs" / job_id
    concept = json.loads(json.dumps(CONCEPT))
    concept['scenes'][1]['cues'][0]['t'] = 3.05
    (path / 'concept.json').write_text(json.dumps(concept))
    changes = [{'id': 's-proof', 'duration': 6, 'visual': 'Product photo'},
               {'id': 's-hook', 'duration': 5, 'visual': 'New opening'},
               {'id': 's-outro', 'duration': 4, 'visual': 'Logo'}]
    decision = {'action': 'feedback', 'text': '', 'scenes': changes,
                'comments': [{'time': 3.1, 'scene_id': 's-proof', 'format': 'story', 'text': 'Show the product close up'}]}
    monkeypatch.setattr('app.pipeline.graph.interrupt', lambda value: decision)
    result = await n_review({'job_id': job_id, 'duration': 6}, {'configurable': {'llm': FakeLLM({})}})
    updated = json.loads((path / 'concept.json').read_text())
    assert [(s['id'], s['start'], s['duration']) for s in updated['scenes']] == [('s-proof', 0, 6), ('s-hook', 6, 5), ('s-outro', 11, 4)]
    assert [c['t'] for c in updated['scenes'][0]['cues']] == [3, 0]
    assert updated['scenes'][2]['cues'][0]['t'] == 11
    assert result['duration'] == json.loads((path / 'request.json').read_text())['duration'] == 15
    assert '3.100s in original scene s-proof (story format): Show the product close up' in result['problems']
    assert 'new order, timing and visual directions' in result['fix_instruction']
    decision['scenes'] = changes[:2]
    with pytest.raises(ValueError, match='every scene exactly once'):
        await n_review({'job_id': job_id}, {'configurable': {'llm': FakeLLM({})}})
    assert json.loads((path / 'concept.json').read_text()) == updated


@pytest.mark.parametrize("allow_clients", [False, True])
def test_composition_includes_all_offered_replacement_images(data_dir, allow_clients):
    from app.harness.build import build_site
    from PIL import Image
    from app.pipeline import runtime as jobs
    from app.pipeline.graph import _save_files

    job_id = jobs.create_job("https://example.com", 6, allow_client_logos=allow_clients)
    job = data_dir / 'jobs' / job_id
    (job / 'concept.json').write_text(json.dumps(CONCEPT))
    brand = job / 'brand'
    brand.mkdir()
    images = []
    for file, kind, colour in [('img/old.png', 'photo', 'red'), ('img/replacement.png', 'photo', 'blue'),
                               ('clients/client.png', 'client-logo', 'green')]:
        target = brand / file
        target.parent.mkdir(exist_ok=True)
        Image.new('RGB', (20, 20), colour).save(target)
        images.append({'file': file, 'kind': kind, 'alt': file, 'section': '', 'width': 20, 'height': 20})
    (brand / 'brand.json').write_text(json.dumps({'theme': {'colors': CONCEPT['palette'], 'fonts': []}, 'images': images}))
    files = {'composition.html': HTML.replace('assets/brand/logo.svg', 'assets/brand/img/replacement.png'),
             'composition.css': CSS, 'composition.js': JS_GOOD}
    uploads = job / 'review-assets'
    uploads.mkdir()
    Image.new('RGB', (20, 20), 'purple').save(uploads / ('a' * 32 + '.png'))
    state = {'job_id': job_id, 'duration': 6, 'allow_client_logos': allow_clients}
    for _ in range(2):  # Rebuilding during a later repair must also keep replacement images.
        assert not _save_files(state, files)
        site = build_site(job / 'composition', job / 'site')
        assert (site / 'assets/brand/img/replacement.png').read_bytes() == (brand / 'img/replacement.png').read_bytes()
        assert (site / 'assets/brand/clients/client.png').exists() == allow_clients
        assert (site / 'assets/user' / ('a' * 32 + '.png')).read_bytes() == (uploads / ('a' * 32 + '.png')).read_bytes()


async def test_reviewer_image_request_survives_automatic_repair(data_dir, monkeypatch):
    from app.pipeline import runtime as jobs
    from app.pipeline.graph import n_review, n_fix

    job_id = jobs.create_job('https://example.com', 6)
    job = data_dir / 'jobs' / job_id
    (job / 'concept.json').write_text(json.dumps(CONCEPT))
    request = 'Replace the background photo with a different image'
    decision = {'action': 'feedback', 'text': '', 'comments': [
        {'time': 3, 'scene_id': 's-proof', 'format': 'both', 'text': request}]}
    monkeypatch.setattr('app.pipeline.graph.interrupt', lambda value: decision)
    llm = FakeLLM({'fix': [FIX_LINT]})
    config = {'configurable': {'llm': llm}}
    state = {'job_id': job_id, 'duration': 6}
    state.update(await n_review(state, config))
    monkeypatch.setattr('app.pipeline.graph._compose_context', lambda state: {'concept': CONCEPT, 'theme': {}, 'assets': []})
    monkeypatch.setattr('app.pipeline.graph._files', lambda state: {'composition.js': JS_BAD})
    monkeypatch.setattr('app.pipeline.graph._save_files', lambda state, files: [])
    state.update(await n_fix(state, config))
    state['problems'] = '- [error] page-error: image returned 404'
    await n_fix(state, config)
    prompt = llm.calls[-1][1][1].content
    assert 'image returned 404' in prompt and request in prompt and 'original scene s-proof' in prompt
    assert 'without undoing these requests' in prompt
    decision['comments'][0]['text'] = 'Now change only the heading'
    state.update(await n_review(state, config))
    assert request not in state['review_requirements']
    assert 'Now change only the heading' in state['review_requirements']


async def test_advanced_ideas_reach_model_and_survive_repairs(data_dir, monkeypatch):
    from PIL import Image
    from app.pipeline import runtime as jobs
    from app.pipeline.graph import n_review, n_fix

    job_id = jobs.create_job('https://example.com', 6)
    path = data_dir / 'jobs' / job_id
    (path / 'concept.json').write_text(json.dumps(CONCEPT))
    (path / 'review-assets').mkdir()
    asset = 'a' * 32 + '.png'
    Image.new('RGB', (30, 20), 'orange').save(path / 'review-assets' / asset)
    base = {'x': .25, 'y': .3, 'width': .4, 'height': .2, 'layer': 2}
    decision = {'action': 'feedback', 'text': '', 'design': {'frames': [
        {'scene_id': 's-proof', 'format': 'story', 'time': 3, 'elements': [
            {**base, 'id': 'new-caption', 'kind': 'text', 'text': 'Made for your team'},
            {**base, 'id': 'photo', 'kind': 'image', 'asset': asset, 'comment': 'Give this image a softer entrance.'},
            {**base, 'id': 'existing-0', 'kind': 'existing', 'selector': '#s-proof > :nth-child(1)',
             'removed': True, 'text_ids': ['proof-title']}]}]}}
    monkeypatch.setattr('app.pipeline.graph.interrupt', lambda value: decision)
    state = {'job_id': job_id, 'duration': 6}
    llm = FakeLLM({'fix': [FIX_LINT]})
    config = {'configurable': {'llm': llm}}
    state.update(await n_review(state, config))
    concept = json.loads((path / 'concept.json').read_text())
    assert concept['scenes'][1]['texts'][-1]['id'] == 'design-new-caption'
    assert concept['scenes'][1]['texts'][-1]['text'] == 'Made for your team'
    assert not any(t['id'] == 'proof-title' for t in concept['scenes'][1]['texts'])
    assert concept['scenes'][0]['texts'] == CONCEPT['scenes'][0]['texts']
    monkeypatch.setattr('app.pipeline.graph._compose_context', lambda state: {'concept': concept, 'theme': {}, 'assets': []})
    monkeypatch.setattr('app.pipeline.graph._files', lambda state: {'composition.js': JS_BAD})
    monkeypatch.setattr('app.pipeline.graph._save_files', lambda state, files: [])
    for _ in range(2):
        state.update(await n_fix(state, config))
        state['problems'] = 'Repair animation timing'
        prompt = llm.calls[-1][1][1].content
        assert prompt[2]['type'] == 'image'
        assert 'assets/user/' + asset in prompt[1]['text']
        assert 'NOT strict coordinates' in prompt[0]['text']
        assert 'entrance/exit animation' in prompt[0]['text']
        assert 'Made for your team' in prompt[0]['text']
        assert 'BOTH formats' in prompt[0]['text']
        assert 'DELETE that entire group' in prompt[0]['text']
        assert 'Do not recreate removed content elsewhere' in prompt[0]['text']
        assert '"removed":true' in prompt[0]['text']
        assert 'Give this image a softer entrance.' in prompt[0]['text']
        assert 'reviewer feedback specifically for that element' in prompt[0]['text']
        assert 'do not turn the comment into on-screen copy' in prompt[0]['text']
