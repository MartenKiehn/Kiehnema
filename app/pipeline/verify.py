"""Deterministic validation between the model steps."""

from __future__ import annotations

import re
import unicodedata

from app.checks.runtime import required_reading_time
from app.music.synth import CUE_TYPES, parse_chord
from app.pipeline.schemas import CompanyProfile, Concept

# ---------------------------------------------------------------- quotes

_TRANS = str.maketrans({"“": '"', "”": '"', "„": '"', "‚": "'", "‘": "'", "’": "'", "–": "-", "—": "-", "­": "", " ": " "})


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANS).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%&.,!?'\"-]", " ", text)).strip()


def _shingles(words: list[str], n: int = 4) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


class QuoteIndex:
    """Checks that quotes are (almost) verbatim parts of the crawled site text."""

    def __init__(self, site_text: str):
        self.text = _norm(site_text)
        self.words = re.findall(r"\w+", self.text)
        self.shingles = _shingles(self.words)

    def verified(self, quote: str) -> bool:
        q = _norm(quote)
        if len(q) < 3:
            return False
        if q in self.text:
            return True
        words = re.findall(r"\w+", q)
        if len(words) < 4:
            return False
        sh = _shingles(words)
        return len(sh & self.shingles) / len(sh) >= 0.85


def verify_profile(profile: CompanyProfile, site_text: str, page_urls: set[str]) -> tuple[CompanyProfile, list[str]]:
    """Drop facts whose quote is not on the site; returns the cleaned profile and notes."""
    index = QuoteIndex(site_text)
    kept, notes = [], []
    seen_ids: set[str] = set()
    for fact in profile.facts:
        if fact.id in seen_ids:
            notes.append(f"{fact.id}: duplicate id dropped")
            continue
        if not index.verified(fact.quote):
            notes.append(f"{fact.id}: quote not found on the site – dropped ({fact.quote[:60]!r})")
            continue
        if fact.source_url.rstrip("/") not in {u.rstrip("/") for u in page_urls}:
            fact.source_url = next(iter(page_urls)) if len(page_urls) == 1 else fact.source_url
        seen_ids.add(fact.id)
        kept.append(fact)
    return profile.model_copy(update={"facts": kept}), notes


# ---------------------------------------------------------------- concept

NEEDS_FACT = {"headline", "subline", "quote", "body"}


def validate_concept(concept: Concept, profile: CompanyProfile, duration: float, assets: set[str],
                     allow_client_logos: bool) -> tuple[Concept, list[str]]:
    """Normalise timing to the beat grid; return (concept, errors). Errors are fed back to the model."""
    errors: list[str] = []
    beat = 60.0 / concept.music.bpm
    scenes = sorted(concept.scenes, key=lambda s: s.start)
    if not 3 <= len(scenes) <= 12:
        errors.append(f"use 3-12 scenes (got {len(scenes)})")
    # snap cuts to the beat grid and make scenes contiguous, ending exactly at `duration`
    t = 0.0
    for i, s in enumerate(scenes):
        s.start = round(t, 3)
        end = duration if i == len(scenes) - 1 else round(round((s.start + s.duration) / beat) * beat, 3)
        s.duration = round(end - s.start, 3)
        if s.duration < 1.5:
            errors.append(f"scene {s.id}: too short ({s.duration:.2f}s, minimum 1.5s)")
        t = end
    ids = [s.id for s in scenes]
    if len(set(ids)) != len(ids) or not all(re.fullmatch(r"s-[a-z0-9-]+", i) for i in ids):
        errors.append("scene ids must be unique and look like s-hook, s-proof, ...")

    facts = {f.id for f in profile.facts}
    text_ids: set[str] = set()
    for s in scenes:
        for txt in s.texts:
            if txt.id in text_ids or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", txt.id):
                errors.append(f"text id {txt.id!r} must be unique kebab-case")
            text_ids.add(txt.id)
            unknown = [f for f in txt.fact_ids if f not in facts]
            if unknown:
                errors.append(f"text {txt.id}: unknown fact ids {unknown} (only verified facts may be used)")
            if txt.role in NEEDS_FACT and not txt.fact_ids:
                errors.append(f"text {txt.id} ({txt.role}): needs fact_ids – every claim must be backed by the website")
            need = required_reading_time(len(txt.text.split()))
            if need > s.duration - 0.7:
                errors.append(f"text {txt.id}: {len(txt.text.split())} words need {need:.1f}s but scene {s.id} lasts "
                              f"{s.duration:.1f}s – shorten the text or the scene count")
        words = sum(len(x.text.split()) for x in s.texts)
        if words > 4 * s.duration:
            errors.append(f"scene {s.id}: {words} words in {s.duration:.1f}s is too much to read")
        for a in s.assets:
            if a not in assets:
                errors.append(f"scene {s.id}: unknown asset {a!r}")
            elif a.startswith("clients/") and not allow_client_logos:
                errors.append(f"scene {s.id}: client logos are disabled for this video – remove {a!r}")
        for c in s.cues:
            if c.type not in CUE_TYPES:
                errors.append(f"scene {s.id}: unknown cue type {c.type!r}")
    all_cues = [c for s in scenes for c in s.cues]
    for kind in ("drop", "outro"):
        if len([c for c in all_cues if c.type == kind]) > 1:
            errors.append(f"use at most one {kind!r} cue (the music has a single {kind})")
    outro = next((c for c in all_cues if c.type == "outro"), None)
    if outro and scenes and outro.t < scenes[-1].start - 0.5:
        errors.append("the 'outro' cue belongs to the final scene")
    for chord in concept.music.progression + concept.music.outro_progression:
        try:
            parse_chord(chord)
        except ValueError:
            errors.append(f"music: unsupported chord {chord!r}")
    for name, value in concept.palette.items():
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value) or not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
            errors.append(f"palette entry {name}={value} must be lowercase-name -> #rrggbb")
    return concept.model_copy(update={"scenes": scenes}), errors


# ---------------------------------------------------------------- generated code lint

_ALLOWED_URLS = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink", "http://www.w3.org/1999/xhtml")
_JS_FORBIDDEN = {
    r"\bfetch\s*\(": "network access (fetch)",
    r"XMLHttpRequest|WebSocket|EventSource|sendBeacon": "network access",
    r"\bimport\s*\(|\bimportScripts\b": "dynamic import",
    r"\beval\s*\(|new\s+Function\s*\(": "eval / new Function",
    r"\bset(Timeout|Interval)\s*\(|requestAnimationFrame": "timers (must be timeline-driven)",
    r"Date\.now|new\s+Date\s*\(|performance\.now": "wall-clock time (nondeterministic)",
    r"window\.__seek\s*=|window\.__ready\s*=|window\.__duration\s*=": "overrides the harness contract",
    r"localStorage|sessionStorage|indexedDB|document\.cookie": "storage access",
}


def lint_composition(files: dict[str, str], text_ids: set[str]) -> list[str]:
    errors: list[str] = []
    for name in ("composition.html", "composition.css", "composition.js"):
        if not files.get(name, "").strip():
            errors.append(f"{name} is missing or empty")
    html, css, js = files.get("composition.html", ""), files.get("composition.css", ""), files.get("composition.js", "")
    for name, body in files.items():
        for url in re.findall(r"(?:https?:)?//[\w.-]+\.[a-z]{2,}[^\s\"')]*", body):
            if not url.startswith(_ALLOWED_URLS):
                errors.append(f"{name}: external URL {url[:80]!r} – only local assets are allowed")
    if re.search(r"<script\b|<iframe\b|<object\b|<embed\b|\son\w+\s*=", html, re.I):
        errors.append("composition.html: no <script>/<iframe>/<object>/inline event handlers allowed")
    if re.search(r"@import|@keyframes|\banimation\s*:|\btransition\s*:", css):
        errors.append("composition.css: no @import, @keyframes, animation or transition – animate with the GSAP timeline")
    for pattern, why in _JS_FORBIDDEN.items():
        if re.search(pattern, js):
            errors.append(f"composition.js: {why} is not allowed ({pattern})")
    if "MG.composition(" not in js:
        errors.append("composition.js must call MG.composition(ctx => { ... })")
    found = set(re.findall(r'data-text-id="([^"]+)"', html))
    missing = sorted(text_ids - found)
    if missing:
        errors.append(f"composition.html: missing data-text-id for concept texts {missing}")
    return errors
