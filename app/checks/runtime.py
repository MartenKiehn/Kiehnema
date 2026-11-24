"""Deterministic runtime checks for a built composition site (both formats).

- contract:      composition builds, duration matches the manifest, scenes cover the timeline,
                 cues are valid, no page errors, no blocked (external) requests
- determinism:   the same timestamp renders byte-identical PNGs regardless of seek order / page
- readability:   every [data-text-id] text is readable long enough (sampled from the real DOM)
- layout:        readable texts sit inside the stage / safe area in every format

The report is plain data so it can be shown in the UI and fed back to the model as fix input.
"""

from __future__ import annotations

from app.security.workspace import browser_environment

import io
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops
from playwright.async_api import Browser, Page, async_playwright

from app.harness.build import Manifest
from app.music.synth import MusicPlan
from app.render.job import music_plan
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


_COPY_PUNCTUATION = str.maketrans({
    "’": "'", "‘": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "‑": "-", "…": "...",
    "\u00ad": "", "\u200b": "",
})


def _copy_key(text: str, transform: str = "none") -> str:
    text = " ".join(unicodedata.normalize("NFC", text).translate(_COPY_PUNCTUATION).split())
    url = re.fullmatch(r"((?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?)([/?#]\S*)?", text, re.I)
    if url:
        return url[1].lower() + (url[2] or "")
    # A comma after a name is often replaced by a line break in an attribution.
    text = re.sub(r"(?<=[^\W\d_]),(?=\s)", "", text)
    # In a quantity such as 5 mW the case is part of the value, even in an all-caps layout.
    if len(text) > 1 and "/" not in text and not re.search(r"\d\s*[^\W\d_]{1,3}\b", text):
        if transform == "uppercase":
            return text.upper()
        if transform == "lowercase":
            return text.lower()
    return text


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
    keyframe_times: dict[str, dict[str, float]] = field(default_factory=dict)
    music: MusicPlan | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: Severity, code: str, message: str, **kw) -> None:
        self.issues.append(Issue(severity, code, message, **kw))

    def as_dict(self) -> dict:
        return {"ok": self.ok, "issues": [asdict(i) for i in self.issues], "texts": [asdict(t) for t in self.texts],
                "keyframe_times": self.keyframe_times}

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


def _review_time(scores: list[tuple[float, int]]) -> float | None:
    """Centre of the longest hold showing the most fully revealed text elements."""
    best = (0, 0)
    chosen = None
    for count, group in groupby(scores, key=lambda sample: sample[1]):
        times = [t for t, _ in group]
        if count and (count, len(times)) > best:
            best = (count, len(times))
            chosen = times[len(times) // 2]
    return chosen


async def _check_texts(page: Page, fmt: VideoFormat, duration: float, scenes: list[dict], report: CheckReport,
                       expected_texts: dict[str, str]):
    samples: list[tuple[float, dict[str, dict]]] = []
    first: dict[str, dict] = {}
    duplicate_ids = set()
    steps = int(duration / SAMPLE_STEP)
    for k in range(steps + 1):
        t = round(k * SAMPLE_STEP, 3)
        await _seek(page, t)
        frame = {}
        for probe in await page.evaluate("() => window.__mgProbe()"):
            key = probe["id"]
            if key in frame:
                duplicate_ids.add(key)
            frame[key] = probe
            first.setdefault(key, probe)
        samples.append((t, frame))
    for missing in sorted(expected_texts.keys() - first.keys()):
        report.add("error", "text-missing", f"Storyboard text {expected_texts[missing]!r} is missing",
                   format=fmt.name, text_id=missing)
    if not first:
        report.add("warning", "no-text-ids", "no [data-text-id] elements found; readability cannot be checked", format=fmt.name)
        return

    if duplicate_ids:
        report.add("error", "duplicate-text-id", f"duplicate data-text-id values: {sorted(duplicate_ids)}", format=fmt.name)

    def inside(rect) -> bool:
        return rect["w"] > 0 and rect["h"] > 0 and rect["x"] < fmt.width and rect["y"] < fmt.height \
            and rect["x"] + rect["w"] > 0 and rect["y"] + rect["h"] > 0

    readable: dict[tuple[str, float], int] = {}
    for text_id, first_probe in first.items():
        states = []
        for t, probes in samples:
            p = probes.get(text_id)
            if p is None:
                states.append((t, False, False, None))
                continue
            partial = not p["hidden"] and p["opacity"] > 0.3 and p["wordsShown"] > 0 and inside(p["rect"])
            full = partial and p["opacity"] >= 0.95 and not p["blurred"] and p["wordsShown"] >= 0.999
            if full and p.get("scene") and p["words"]:
                key = (p["scene"], t)
                readable[key] = readable.get(key, 0) + 1
            states.append((t, partial, full, p))
        # A counter may start at zero. Compare the copy viewers can actually read after its reveal.
        visible_copy: dict[tuple[str, str], int] = {}
        variants = ((p["text"], p.get("textTransform", "none")) if full else None for _, _, full, p in states)
        for variant, group in groupby(variants):
            if variant is not None:
                visible_copy[variant] = max(visible_copy.get(variant, 0), sum(1 for _ in group))
        shown = max(visible_copy, key=visible_copy.get)[0] if visible_copy else first_probe["text"]
        expected = expected_texts.get(text_id)
        if expected is not None and visible_copy:
            matched = max((n for (copy, transform), n in visible_copy.items()
                           if _copy_key(copy) == _copy_key(expected, transform)), default=0)
            if not matched:
                report.add("error", "text-mismatch", f"Storyboard expects {expected!r}, video shows {shown!r}",
                           format=fmt.name, text_id=text_id)
            elif matched * SAMPLE_STEP < MIN_FULL_HOLD and len(visible_copy) > 1:
                report.add("error", "text-too-short", f"Storyboard text {expected!r} settles for only "
                           f"{matched * SAMPLE_STEP:.1f}s; hold it for {MIN_FULL_HOLD:.1f}s",
                           format=fmt.name, text_id=text_id)
        words = max(1, len(shown.split()))
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
                    holds = [list(group) for full, group in groupby(run, key=lambda st: st[2]) if full]
                    longest = max(holds, key=len)
                    hold = len(longest) * SAMPLE_STEP
                    candidate = (window, hold, longest[len(longest) // 2][0])
                    # A later flicker must not displace an earlier, uninterrupted reading opportunity.
                    if (hold >= MIN_FULL_HOLD, window, hold) > (best[1] >= MIN_FULL_HOLD, best[0], best[1]):
                        best = candidate
                run = []
        window, hold, mid_t = best
        report.texts.append(TextTiming(fmt.name, text_id, shown[:80], words, round(window, 2), round(hold, 2), round(required, 2), mid_t))

        if mid_t is None:
            report.add("error", "text-never-visible", f"“{shown[:60]}” is never fully visible", format=fmt.name, text_id=text_id)
            continue
        if window < required * 0.75 or hold < MIN_FULL_HOLD:
            report.add("error", "text-too-short",
                       f"“{shown[:60]}” readable for {window:.1f}s (fully visible {hold:.1f}s), "
                       f"needs ≥{required:.1f}s for {words} words", format=fmt.name, text_id=text_id)
        elif window < required:
            report.add("warning", "text-short",
                       f"“{shown[:60]}” readable for {window:.1f}s, recommended {required:.1f}s",
                       format=fmt.name, text_id=text_id)

        rect = next(s[3]["rect"] for s in states if s[0] == mid_t)
        r, b = rect["x"] + rect["w"], rect["y"] + rect["h"]
        if rect["x"] < 0 or rect["y"] < 0 or r > fmt.width or b > fmt.height:
            report.add("error", "text-outside-stage", f"“{shown[:60]}” is cut off by the frame edge",
                       format=fmt.name, t=mid_t, text_id=text_id)
        elif rect["x"] < SAFE_MARGIN or rect["y"] < SAFE_MARGIN or r > fmt.width - SAFE_MARGIN or b > fmt.height - SAFE_MARGIN:
            report.add("warning", "text-near-edge", f"“{shown[:60]}” is within {SAFE_MARGIN}px of the frame edge",
                       format=fmt.name, t=mid_t, text_id=text_id)

    measured = {}
    for scene in scenes:
        sid = scene["id"]
        scores = [(t, readable.get((sid, t), 0)) for t, _ in samples
                  if scene["start"] <= t < scene["start"] + scene["duration"]]
        chosen = _review_time(scores)
        if chosen is not None:
            measured[sid] = chosen
    report.keyframe_times[fmt.name] = measured


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
    try:
        plan = music_plan(manifest, registry)
    except (TypeError, ValueError, OverflowError) as exc:
        report.add("error", "soundtrack", f"Invalid scene or cue time: {exc}", format=fmt.name)
        return
    if fmt.name == "feed":
        report.music = plan
    for w in plan.warnings:
        report.add("warning", "cue", w, format=fmt.name)


async def check_site(site: Path, formats: tuple[str, ...] = ("feed", "story"), determinism: bool = True,
                     *, expected_texts: dict[str, str] | None = None) -> CheckReport:
    manifest = Manifest.model_validate_json((site / "composition" / "manifest.json").read_text())
    report = CheckReport()
    registries: dict[str, dict] = {}
    with serve_directory(site) as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(env=browser_environment(), chromium_sandbox=True)
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
                        await _check_texts(page, fmt, duration, registry["scenes"], report, expected_texts or {})
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
        # Registration order can vary with layout. Both exports must fit the same soundtrack.
        def events(registry, field):
            return sorted(json.dumps(event, sort_keys=True) for event in registry[field])

        for field in ("scenes", "cues"):
            if events(a, field) != events(b, field):
                report.add("error", "format-divergence", f"{field} differ between feed and story. "
                           "Use the same scene timings, cut sounds and cue options in both formats.")
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
