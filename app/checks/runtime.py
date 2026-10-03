"""Deterministic runtime checks for a built composition site (both formats).

- contract:      composition builds, duration matches the manifest, scenes cover the timeline,
                 cues are valid, no page errors, no blocked (external) requests
- determinism:   the same timestamp renders byte-identical PNGs regardless of seek order / page
- readability:   every [data-text-id] text is readable long enough (sampled from the real DOM)
- layout:        readable texts sit inside the stage / safe area in every format

The report is plain data so it can be shown in the UI and fed back to the model as fix input.
"""

from __future__ import annotations

import io
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops
from playwright.async_api import Browser, Page, async_playwright

from app.harness.build import Manifest
from app.music.synth import MusicPlan
from app.render.renderer import FORMATS, RenderError, VideoFormat, _open_page, _seek
from app.render.server import serve_directory

log = logging.getLogger(__name__)

Severity = Literal["error", "warning"]

SAMPLE_STEP = 0.1  # s
SAFE_MARGIN = 24  # px
MIN_FULL_HOLD = 0.8  # s every text must be completely visible at least this long
# Chromium may anti-alias text slightly differently depending on layer history; compare frames
# downscaled so only real differences (moved/changed content) count as nondeterminism.
DETERMINISM_REDUCE = 4
DETERMINISM_TOLERANCE = 24  # max channel difference after downscaling


def required_reading_time(words: int) -> float:
    """Seconds from first appearance until the text may leave (short display copy, ~240 wpm)."""
    return 0.5 + words / 4.0


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    format: str | None = None
    t: float | None = None
    text_id: str | None = None


@dataclass
class TextTiming:
    format: str
    text_id: str
    text: str
    words: int
    window: float  # first appearance -> last fully visible
    full_hold: float
    required: float
    mid_t: float | None


@dataclass
class CheckReport:
    issues: list[Issue] = field(default_factory=list)
    texts: list[TextTiming] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: Severity, code: str, message: str, **kw) -> None:
        self.issues.append(Issue(severity, code, message, **kw))

    def as_dict(self) -> dict:
        return {"ok": self.ok, "issues": [asdict(i) for i in self.issues], "texts": [asdict(t) for t in self.texts]}

    def feedback(self) -> str:
        """Compact, model-readable list of problems."""
        lines = []
        for i in self.issues:
            where = ", ".join(x for x in (i.format and f"format={i.format}", i.t is not None and f"t={i.t:.2f}s",
                                          i.text_id and f"text={i.text_id}") if x)
            lines.append(f"- [{i.severity}] {i.code}: {i.message}" + (f" ({where})" if where else ""))
        return "\n".join(lines) or "no issues"


async def _frame(page: Page, t: float) -> Image.Image:
    await _seek(page, t)
    return Image.open(io.BytesIO(await page.screenshot(type="png"))).convert("RGB").reduce(DETERMINISM_REDUCE)


def _same(a: Image.Image, b: Image.Image) -> bool:
    return max(hi for _, hi in ImageChops.difference(a, b).getextrema()) <= DETERMINISM_TOLERANCE


async def _check_determinism(browser: Browser, base: str, fmt: VideoFormat, page: Page, duration: float, report: CheckReport):
    times = [round(duration * f, 3) for f in (0.37, 0.61, 0.89)]
    forward = [await _frame(page, t) for t in times]
    await _seek(page, 0)
    backward = [await _frame(page, t) for t in reversed(times)][::-1]
    fresh_page, _ = await _open_page(browser, base, "index.html", fmt)
    try:
        fresh = [await _frame(fresh_page, t) for t in reversed(times)][::-1]
    finally:
        await fresh_page.context.close()
    for t, a, b, c in zip(times, forward, backward, fresh):
        if not (_same(a, b) and _same(a, c)):
            report.add("error", "nondeterministic",
                       "frame differs depending on seek order or page instance; drive everything from the paused "
                       "timeline (no CSS animations, timers, Date.now, requestAnimationFrame)", format=fmt.name, t=t)


async def _check_texts(page: Page, fmt: VideoFormat, duration: float, report: CheckReport):
    samples: list[tuple[float, list[dict]]] = []
    steps = int(duration / SAMPLE_STEP)
    for k in range(steps + 1):
        t = round(k * SAMPLE_STEP, 3)
        await _seek(page, t)
        samples.append((t, await page.evaluate("() => window.__mgProbe()")))
    if not samples[0][1]:
        report.add("warning", "no-text-ids", "no [data-text-id] elements found; readability cannot be checked", format=fmt.name)
        return

    ids = [p["id"] for p in samples[0][1]]
    if len(set(ids)) != len(ids):
        report.add("error", "duplicate-text-id", f"duplicate data-text-id values: {sorted({i for i in ids if ids.count(i) > 1})}", format=fmt.name)

    def inside(rect) -> bool:
        return rect["w"] > 0 and rect["h"] > 0 and rect["x"] < fmt.width and rect["y"] < fmt.height \
            and rect["x"] + rect["w"] > 0 and rect["y"] + rect["h"] > 0

    for idx, text_id in enumerate(ids):
        states = []
        for t, probes in samples:
            p = probes[idx]
            partial = not p["hidden"] and p["opacity"] > 0.3 and p["wordsShown"] > 0 and inside(p["rect"])
            full = partial and p["opacity"] >= 0.95 and not p["blurred"] and p["wordsShown"] >= 0.999
            states.append((t, partial, full, p))
        info = samples[0][1][idx]
        words = max(1, info["words"])
        required = required_reading_time(words)

        best: tuple[float, float, float | None] = (0.0, 0.0, None)  # window, full_hold, mid_t
        run: list[tuple[float, bool, bool, dict]] = []
        for st in states + [(None, False, False, None)]:
            if st[1]:
                run.append(st)
                continue
            if run:
                full_ts = [s[0] for s in run if s[2]]
                if full_ts:
                    window = full_ts[-1] - run[0][0] + SAMPLE_STEP
                    hold = len(full_ts) * SAMPLE_STEP
                    if window > best[0]:
                        best = (window, hold, full_ts[len(full_ts) // 2])
                run = []
        window, hold, mid_t = best
        report.texts.append(TextTiming(fmt.name, text_id, info["text"][:80], words, round(window, 2), round(hold, 2), round(required, 2), mid_t))

        if mid_t is None:
            report.add("error", "text-never-visible", f"“{info['text'][:60]}” is never fully visible", format=fmt.name, text_id=text_id)
            continue
        if window < required * 0.75 or hold < MIN_FULL_HOLD:
            report.add("error", "text-too-short",
                       f"“{info['text'][:60]}” readable for {window:.1f}s (fully visible {hold:.1f}s), "
                       f"needs ≥{required:.1f}s for {words} words", format=fmt.name, text_id=text_id)
        elif window < required:
            report.add("warning", "text-short",
                       f"“{info['text'][:60]}” readable for {window:.1f}s, recommended {required:.1f}s",
                       format=fmt.name, text_id=text_id)

        rect = next(s[3]["rect"] for s in states if s[0] == mid_t)
        r, b = rect["x"] + rect["w"], rect["y"] + rect["h"]
        if rect["x"] < 0 or rect["y"] < 0 or r > fmt.width or b > fmt.height:
            report.add("error", "text-outside-stage", f"“{info['text'][:60]}” is cut off by the frame edge",
                       format=fmt.name, t=mid_t, text_id=text_id)
        elif rect["x"] < SAFE_MARGIN or rect["y"] < SAFE_MARGIN or r > fmt.width - SAFE_MARGIN or b > fmt.height - SAFE_MARGIN:
            report.add("warning", "text-near-edge", f"“{info['text'][:60]}” is within {SAFE_MARGIN}px of the frame edge",
                       format=fmt.name, t=mid_t, text_id=text_id)


def _check_contract(registry: dict, manifest: Manifest, duration: float, fmt: VideoFormat, report: CheckReport):
    if abs(duration - manifest.duration) > 1 / 30:
        report.add("error", "duration-mismatch", f"__duration {duration}s != manifest {manifest.duration}s", format=fmt.name)
    scenes = sorted(registry.get("scenes", []), key=lambda s: s["start"])
    if not scenes:
        report.add("error", "no-scenes", "no scenes registered via scene()", format=fmt.name)
        return
    if scenes[0]["start"] > 0.05:
        report.add("error", "scene-gap", f"timeline starts empty until {scenes[0]['start']}s", format=fmt.name)
    covered = scenes[0]["start"]
    for s in scenes:
        if s["start"] - covered > 0.3:
            report.add("error", "scene-gap", f"no scene between {covered:.2f}s and {s['start']:.2f}s", format=fmt.name)
        covered = max(covered, s["start"] + s["duration"])
    if covered < manifest.duration - 0.3:
        report.add("error", "scene-gap", f"no scene after {covered:.2f}s (duration {manifest.duration}s)", format=fmt.name)
    plan = MusicPlan.from_registry(manifest.duration, manifest.music, scenes, registry.get("cues", []))
    for w in plan.warnings:
        report.add("warning", "cue", w, format=fmt.name)


async def check_site(site: Path, formats: tuple[str, ...] = ("feed", "story"), determinism: bool = True) -> CheckReport:
    manifest = Manifest.model_validate_json((site / "composition" / "manifest.json").read_text())
    report = CheckReport()
    registries: dict[str, dict] = {}
    with serve_directory(site) as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for name in formats:
                    fmt = FORMATS[name]
                    try:
                        page, diag = await _open_page(browser, base, "index.html", fmt)
                    except RenderError as exc:
                        report.add("error", "build-failed", str(exc)[:1500], format=name)
                        continue
                    try:
                        duration = float(await page.evaluate("() => window.__duration"))
                        registry = await page.evaluate("() => JSON.parse(JSON.stringify(window.__mg))")
                        registries[name] = registry
                        _check_contract(registry, manifest, duration, fmt, report)
                        if determinism:
                            await _check_determinism(browser, base, fmt, page, duration, report)
                        await _check_texts(page, fmt, duration, report)
                        for err in dict.fromkeys(diag.errors):
                            if "Content Security Policy" in err:
                                report.add("error", "external-request", f"blocked by sandbox: {err[:300]}", format=name)
                            else:
                                report.add("error", "page-error", err[:500], format=name)
                        for url in dict.fromkeys(diag.blocked_requests):
                            report.add("error", "external-request", f"blocked request to {url}", format=name)
                    finally:
                        await page.context.close()
            finally:
                await browser.close()
    if len(registries) == 2:
        a, b = registries["feed"], registries["story"]
        if [s["start"] for s in a["scenes"]] != [s["start"] for s in b["scenes"]] or len(a["cues"]) != len(b["cues"]):
            report.add("error", "format-divergence", "scenes or cues differ between feed and story; timing must be format-independent")
    return report


if __name__ == "__main__":
    import asyncio
    import sys

    rep = asyncio.run(check_site(Path(sys.argv[1])))
    for t in rep.texts:
        flag = "OK " if t.window >= t.required and t.full_hold >= MIN_FULL_HOLD else "LOW"
        print(f"{flag} {t.format:5s} {t.text_id:22s} window {t.window:4.1f}s hold {t.full_hold:4.1f}s need {t.required:4.1f}s  {t.text[:45]}")
    print(rep.feedback())
    print("OK" if rep.ok else f"{len(rep.errors)} errors")
