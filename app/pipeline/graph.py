"""LangGraph pipeline: website -> brand -> facts -> storyboard -> code -> checks/QA -> review -> render.

    crawl → brand → distill → concept → compose → check ─┬─> fix → check (≤ 3 automatic rounds)
                                                          └─> keyframes → qa ─┬─> fix (major issues, once)
                                                                               └─> review ⏸ ─┬─> fix (edits/feedback)
                                                                                              └─> render → END
Large artefacts live in the job directory; the graph state only holds small JSON-able values.
The model client is injected via ``config["configurable"]["llm"]`` (BedrockLLM or FakeLLM).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from playwright.async_api import async_playwright

from app.brand.board import render_board
from app.brand.extract import extract_brand
from app.checks.runtime import check_site
from app.config import settings
from app.crawl.crawler import CrawlResult, crawl, site_markdown
from app.crawl.guard import UrlGuard
from app.harness.build import Theme, build_site
from app.pipeline import prompts
from app.pipeline.files import COMPOSITION_FILES, apply_edits, parse_files, theme_for, write_composition
from app.pipeline.llm import LLM, estimate_cost, image_block, messages
from app.pipeline.schemas import CompanyProfile, Concept, QAReport
from app.pipeline.status import write_status
from app.pipeline.verify import lint_composition, validate_concept, verify_profile
from app.render.job import render_site
from app.render.renderer import FORMATS, render_stills
from app.render.server import serve_directory

log = logging.getLogger(__name__)

MAX_FIX_ROUNDS = 3
MAX_CONCEPT_ATTEMPTS = 3


class JobState(TypedDict, total=False):
    job_id: str
    job_dir: str
    url: str
    duration: float
    description: str
    allow_client_logos: bool
    max_pages: int
    max_depth: int
    status: str
    error: str | None
    profile_notes: list[str]
    concept_errors: list[str]
    lint: list[str]
    check: dict
    problems: str  # input for the next fix round
    fix_instruction: str | None
    fix_round: int
    qa_done: bool
    qa: dict
    keyframes: list[dict]
    review: dict
    outputs: dict
    usage: dict
    cost_usd: float


# ---------------------------------------------------------------- helpers

def _llm(config: RunnableConfig) -> LLM:
    return config["configurable"]["llm"]


def _guard(config: RunnableConfig) -> UrlGuard:
    return config["configurable"].get("guard") or UrlGuard()


def _dir(state: JobState) -> Path:
    # Resolve against the current workspace, including after a project-folder rename.
    return settings.data_dir / "jobs" / state["job_id"]


def _status(state: JobState, status: str, message: str, config: RunnableConfig | None = None, **extra: Any) -> dict:
    if config is not None:
        llm = _llm(config)
        extra.update(usage=asdict(llm.usage), cost_usd=estimate_cost(llm.usage))
    write_status(_dir(state), status, message, **extra)
    return {"status": status}


def _usage(config: RunnableConfig) -> dict:
    llm = _llm(config)
    return {"usage": asdict(llm.usage), "cost_usd": estimate_cost(llm.usage)}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _brand(state: JobState) -> dict:
    return _read_json(_dir(state) / "brand" / "brand.json")


def _concept(state: JobState) -> Concept:
    return Concept.model_validate_json((_dir(state) / "concept.json").read_text())


def _files(state: JobState) -> dict[str, str]:
    comp = _dir(state) / "composition"
    return {n: (comp / n).read_text() if (comp / n).exists() else "" for n in COMPOSITION_FILES}


def _asset_list(brand: dict, allow_clients: bool, prefix: str = "") -> list[dict]:
    assets: list[dict] = []
    if brand.get("logo"):
        lg = brand["logo"]
        assets.append({"file": prefix + lg["file"], "kind": "logo", "format": lg["kind"], "width": lg["width"],
                       "height": lg["height"], "colors": lg["colors"], "works_on_dark": lg["on_dark_ok"],
                       "works_on_light": lg["on_light_ok"], "svg_shapes": lg["shapes"]})
    for img in brand["images"]:
        if img["kind"] == "client-logo" and not allow_clients:
            continue
        assets.append({"file": prefix + img["file"], "kind": img["kind"], "alt": img["alt"], "section": img["section"],
                       "width": img["width"], "height": img["height"]})
    return assets


def _brand_summary(brand: dict) -> dict:
    return {
        "name": brand["site_name"], "language": brand["language"],
        "default_theme": brand["theme"]["colors"],
        "palette_ranked": [{"hex": c["hex"], "sources": c["sources"][:4]} for c in brand["colors"][:10]],
        "fonts": [{"role": f["role"], "family": f["family"]} for f in brand["fonts"]],
        "logo_image": bool(brand.get("logo")),
        "wordmark_text": brand.get("wordmark"),
        "notes": brand["notes"],
    }


def _compose_context(state: JobState) -> dict:
    brand = _brand(state)
    concept = _concept(state)
    theme = theme_for(concept, Theme.model_validate(brand["theme"]))
    theme_vars = {f"--brand-{k}": v for k, v in theme.colors.items()}
    for f in theme.fonts:
        theme_vars[f"--font-{f.role}"] = f.family
    assets = _asset_list(brand, state.get("allow_client_logos", False), prefix="assets/brand/")
    if not brand.get("logo") and brand.get("wordmark"):
        assets.append({"kind": "wordmark-text", "note": "no logo image – recreate the brand name typographically", **brand["wordmark"]})
    return {"concept": concept.model_dump(), "theme": theme_vars, "assets": assets}


def _save_files(state: JobState, files: dict[str, str]) -> list[str]:
    """Write the composition dir for these files and return lint errors."""
    brand = _brand(state)
    concept = _concept(state)
    used = {a for s in concept.scenes for a in s.assets} | ({brand["logo"]["file"]} if brand.get("logo") else set())
    write_composition(_dir(state) / "composition", files, concept, state["duration"], _dir(state) / "brand",
                      Theme.model_validate(brand["theme"]), used)
    text_ids = {t.id for s in concept.scenes for t in s.texts}
    return lint_composition(files, text_ids)


# ---------------------------------------------------------------- nodes

async def n_crawl(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "crawl", f"Crawling {state['url']}")
    out = _dir(state) / "crawl"
    result = await crawl(state["url"], out, _guard(config),
                         max_pages=state.get("max_pages", 20), max_depth=state.get("max_depth", 2),
                         progress=lambda message: _status(state, "crawl", message))
    if not result.pages:
        return {"error": f"could not load the website: {(result.errors + result.blocked)[:3]}", "status": "failed"}
    (out / "site.md").write_text(site_markdown(result))
    return _status(state, "crawl", f"Crawled {len(result.pages)} pages")


async def n_brand(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "brand", "Extracting brand colours, fonts, logo and images")
    out = _dir(state) / "brand"
    await extract_brand(CrawlResult.load(_dir(state) / "crawl"), out, _guard(config))
    await render_board(out)
    return _status(state, "brand", "Brand kit ready")


async def n_distill(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "distill", "Reading the website (facts with verbatim sources)", config)
    site_md = (_dir(state) / "crawl" / "site.md").read_text()
    profile = await _llm(config).structured(
        "distill", CompanyProfile, messages(prompts.DISTILL_SYSTEM, prompts.distill_user(site_md)),
        effort="low", max_tokens=32_000)
    pages = {p.url for p in CrawlResult.load(_dir(state) / "crawl").pages}
    profile, notes = verify_profile(profile, site_md, pages)
    (_dir(state) / "profile.json").write_text(profile.model_dump_json(indent=1))
    if len(profile.facts) < 5:
        return {"error": f"only {len(profile.facts)} verifiable facts found on the website", "status": "failed",
                "profile_notes": notes, **_usage(config)}
    _status(state, "distill", f"{len(profile.facts)} verified facts ({len(notes)} dropped)", config)
    return {"profile_notes": notes, **_usage(config)}


async def n_concept(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "concept", "Writing the storyboard", config)
    brand = _brand(state)
    profile = CompanyProfile.model_validate_json((_dir(state) / "profile.json").read_text())
    allow = state.get("allow_client_logos", False)
    assets = _asset_list(brand, allow)
    user = prompts.concept_user(url=state["url"], duration=state["duration"], description=state.get("description", ""),
                                profile=profile.model_dump(), brand=_brand_summary(brand), assets=assets,
                                allow_client_logos=allow)
    errors: list[str] = []
    concept: Concept | None = None
    for attempt in range(MAX_CONCEPT_ATTEMPTS):
        content = user if not errors else (
            f"{user}\n\nYour previous storyboard:\n{concept.model_dump_json()}\n\n"
            "It violates these constraints – return a corrected storyboard:\n- " + "\n- ".join(errors))
        concept = await _llm(config).structured("concept", Concept, messages(prompts.CONCEPT_SYSTEM, content),
                                                effort="medium", max_tokens=32_000)
        concept, errors = validate_concept(concept, profile, state["duration"], {a["file"] for a in assets}, allow)
        if not errors:
            break
        _status(state, "concept", f"Storyboard attempt {attempt + 1}: {len(errors)} problems, revising", config)
    (_dir(state) / "concept.json").write_text(concept.model_dump_json(indent=1))
    if errors:
        return {"error": "storyboard still invalid: " + "; ".join(errors[:5]), "status": "failed",
                "concept_errors": errors, **_usage(config)}
    _status(state, "concept", f"Storyboard: {len(concept.scenes)} scenes – {concept.hook[:120]}", config)
    return {"concept_errors": [], **_usage(config)}


async def n_compose(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "compose", "Writing the composition code (this takes several minutes)", config)
    ctx = _compose_context(state)
    text = await _llm(config).text(
        "compose", messages(prompts.compose_system(), prompts.compose_user(**ctx)), effort="medium", max_tokens=100_000,
        on_progress=lambda n: _status(state, "compose", f"Writing the composition code – {n:,} characters so far"))
    files = parse_files(text)
    (_dir(state) / "logs").mkdir(exist_ok=True)
    (_dir(state) / "logs" / "compose.txt").write_text(text)
    lint = _save_files(state, files)
    _status(state, "compose", f"Composition written ({sum(len(v) for v in files.values())} chars)", config)
    return {"lint": lint, "fix_round": 0, "qa_done": False, "problems": "", **_usage(config)}


async def n_check(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "check", "Checking contract, determinism, readability and layout")
    lint = state.get("lint", [])
    if lint:
        report = {"ok": False, "issues": [{"severity": "error", "code": "lint", "message": m} for m in lint], "texts": []}
    else:
        site = build_site(_dir(state) / "composition", _dir(state) / "site")
        rep = await check_site(site)
        report = rep.as_dict()
    (_dir(state) / "check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    problems = "\n".join(f"- [{i['severity']}] {i['code']}: {i['message']}"
                         + (f" (format={i.get('format')}, text={i.get('text_id')})" if i.get("format") or i.get("text_id") else "")
                         for i in report["issues"])
    _status(state, "check", "All checks passed" if not errors else f"{len(errors)} problems found")
    return {"check": {"ok": not errors, "errors": len(errors), "warnings": len(report["issues"]) - len(errors)},
            "problems": problems}


def after_check(state: JobState) -> str:
    if state["check"]["ok"]:
        return "keyframes"
    if state.get("fix_round", 0) < MAX_FIX_ROUNDS:
        return "fix"
    return "keyframes" if (_dir(state) / "site" / "index.html").exists() and not state.get("lint") else "review"


async def n_fix(state: JobState, config: RunnableConfig) -> dict:
    rnd = state.get("fix_round", 0) + 1
    _status(state, "fix", f"Revising the composition (round {rnd})", config)
    ctx = _compose_context(state)
    files = _files(state)
    instruction = state.get("fix_instruction") or "The automated checks found these problems. Fix all errors; fix warnings where easy."
    user = prompts.compose_user(**ctx).rsplit("\n\n", 1)[0] + "\n\n" + prompts.fix_user(
        files=files, problems=state.get("problems", ""), instruction=instruction)
    text = await _llm(config).text(
        "fix", messages(prompts.compose_system(), user), effort="low", max_tokens=48_000,
        on_progress=lambda n: _status(state, "fix", f"Revising the composition (round {rnd}) – {n:,} characters so far"))
    (_dir(state) / "logs").mkdir(exist_ok=True)
    (_dir(state) / "logs" / f"fix-{rnd}.txt").write_text(text)
    files, edit_problems = apply_edits(files, text)
    lint = _save_files(state, files) + edit_problems
    return {"lint": lint, "fix_round": rnd, "fix_instruction": None, **_usage(config)}


def _keyframe_times(concept: Concept) -> list[tuple[str, float]]:
    return [(s.id, round(s.start + min(s.duration - 0.5, max(1.3, 0.62 * s.duration)), 2)) for s in concept.scenes]


async def n_keyframes(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "keyframes", "Rendering keyframes")
    concept = _concept(state)
    site = _dir(state) / "site"
    rnd = state.get("fix_round", 0)
    out = _dir(state) / "keyframes" / f"r{rnd}"
    frames: list[dict] = []
    times = _keyframe_times(concept)
    with serve_directory(site) as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for fmt in ("feed", "story"):
                    paths, _ = await render_stills(browser, base, "index.html", FORMATS[fmt], [t for _, t in times], out)
                    frames += [{"scene": sid, "format": fmt, "t": t, "file": str(p.relative_to(_dir(state)))}
                               for (sid, t), p in zip(times, paths)]
            finally:
                await browser.close()
    return {"keyframes": frames}


async def n_qa(state: JobState, config: RunnableConfig) -> dict:
    if state.get("qa_done"):
        return {}
    _status(state, "qa", "Art-director review of the keyframes", config)
    concept = _concept(state)
    frames = [(f"scene {f['scene']} · {f['format']} · t={f['t']}s", image_block(_dir(state) / f["file"]))
              for f in state["keyframes"]]
    report = await _llm(config).structured("qa", QAReport, messages(prompts.QA_SYSTEM, prompts.qa_user(frames, concept.model_dump())),
                                           effort="medium", max_tokens=16_000)
    (_dir(state) / "qa.json").write_text(report.model_dump_json(indent=1))
    major = [i for i in report.issues if i.severity == "major"]
    _status(state, "qa", f"QA: {report.summary[:160]}", config)
    update: dict = {"qa_done": True, "qa": {"approved": report.approved and not major, "major": len(major),
                                            "minor": len(report.issues) - len(major)}, **_usage(config)}
    if major:
        update["problems"] = "\n".join(f"- [{i.severity}] scene {i.scene_id} ({i.format}): {i.problem} → {i.fix}" for i in report.issues)
        update["fix_instruction"] = "The art director reviewed rendered keyframes and found these problems. Fix them."
        update["fix_round"] = 0
    return update


def after_qa(state: JobState) -> str:
    return "fix" if state.get("fix_instruction") else "review"


async def n_review(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "review", "Waiting for your review", config)
    decision = interrupt({
        "keyframes": state.get("keyframes", []),
        "check": state.get("check"),
        "qa": state.get("qa"),
        "concept": _concept(state).model_dump(),
    })
    action = (decision or {}).get("action", "approve")
    if action == "approve":
        return {"review": {"action": "approve"}}
    if action == "edit":
        edits = decision.get("texts", {})
        problems = "\n".join(f'- text data-text-id="{k}": change the copy to “{v}” (keep its styling)' for k, v in edits.items())
        concept = _concept(state)
        for scene in concept.scenes:  # keep the storyboard in sync with the edited copy
            for t in scene.texts:
                if t.id in edits:
                    t.text = edits[t.id]
        (_dir(state) / "concept.json").write_text(concept.model_dump_json(indent=1))
        instruction = "The reviewer changed these texts. Apply exactly these copy changes and adjust layout if needed."
        if decision.get("text"):
            problems += f"\n- Additional reviewer feedback: {decision['text']}"
    else:
        problems = f"- {decision.get('text', '')}"
        instruction = "The reviewer gave this feedback on the video. Apply it while keeping everything that works."
    return {"review": {"action": action}, "problems": problems, "fix_instruction": instruction, "fix_round": 0, "qa_done": True}


def after_review(state: JobState) -> str:
    return "render" if state["review"]["action"] == "approve" else "fix"


async def n_render(state: JobState, config: RunnableConfig) -> dict:
    _status(state, "render", "Rendering 4:5 and 9:16 videos with music", config)
    site = build_site(_dir(state) / "composition", _dir(state) / "site")

    def progress(fmt: str, i: int, total: int) -> None:
        _status(state, "render", f"Rendering {fmt}: {i}/{total} frames")

    result = await render_site(site, _dir(state) / "renders", progress=progress)
    outputs = {k: str(v.relative_to(_dir(state))) for k, v in result.videos.items()}
    _status(state, "done", "Videos ready", config, outputs=outputs)
    return {"outputs": outputs, "status": "done", **_usage(config)}


def _stop_on_error(next_node: str):
    def route(state: JobState) -> str:
        return END if state.get("error") else next_node
    return route


def _track(name: str, fn):
    """Record the current step in the graph state (status.json is written by the nodes themselves)."""
    async def node(state: JobState, config: RunnableConfig) -> dict:
        update = await fn(state, config) or {}
        if update.get("error"):
            _status(state, "failed", update["error"], config)
        if "status" not in update or update["status"] not in ("failed", "done"):
            update["status"] = name
        return update
    node.__name__ = fn.__name__
    return node


def build_graph(checkpointer=None):
    g = StateGraph(JobState)
    for name, fn in (("crawl", n_crawl), ("brand", n_brand), ("distill", n_distill), ("concept", n_concept),
                     ("compose", n_compose), ("check", n_check), ("fix", n_fix), ("keyframes", n_keyframes),
                     ("qa", n_qa), ("review", n_review), ("render", n_render)):
        g.add_node(name, _track(name, fn))
    g.add_edge(START, "crawl")
    g.add_conditional_edges("crawl", _stop_on_error("brand"), ["brand", END])
    g.add_edge("brand", "distill")
    g.add_conditional_edges("distill", _stop_on_error("concept"), ["concept", END])
    g.add_conditional_edges("concept", _stop_on_error("compose"), ["compose", END])
    g.add_edge("compose", "check")
    g.add_conditional_edges("check", after_check, ["fix", "keyframes", "review"])
    g.add_edge("fix", "check")
    g.add_edge("keyframes", "qa")
    g.add_conditional_edges("qa", after_qa, ["fix", "review"])
    g.add_conditional_edges("review", after_review, ["fix", "render"])
    g.add_edge("render", END)
    return g.compile(checkpointer=checkpointer)
