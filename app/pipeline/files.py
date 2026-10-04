"""Model output formats (tagged files / search-replace edits) and composition directory assembly."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from app.harness.build import FontSpec, Manifest, MusicSpec, Theme
from app.pipeline.schemas import Concept

COMPOSITION_FILES = ("composition.html", "composition.css", "composition.js")
_FILE = re.compile(r'<file name="(composition\.(?:html|css|js))">\n?(.*?)\n?</file>', re.S)
_EDIT = re.compile(r'<edit file="(composition\.(?:html|css|js))">\s*<search>\n?(.*?)\n?</search>\s*<replace>\n?(.*?)\n?</replace>\s*</edit>', re.S)


def _strip_fence(body: str) -> str:
    m = re.fullmatch(r"\s*```[a-z]*\n(.*?)\n```\s*", body, re.S)
    return m.group(1) if m else body


def parse_files(text: str) -> dict[str, str]:
    return {name: _strip_fence(body) for name, body in _FILE.findall(text)}


def apply_edits(files: dict[str, str], text: str) -> tuple[dict[str, str], list[str]]:
    """Apply <edit> blocks and whole-file <file> replacements; returns (files, problems)."""
    out = dict(files)
    problems: list[str] = []
    out.update(parse_files(text))
    edits = _EDIT.findall(text)
    for name, search, replace in edits:
        body = out.get(name, "")
        count = body.count(search)
        if count == 0:
            problems.append(f"edit for {name}: search text not found: {search[:120]!r}")
            continue
        if count > 1:
            problems.append(f"edit for {name}: search text is ambiguous ({count} matches): {search[:120]!r}")
            continue
        out[name] = body.replace(search, replace, 1)
    if not edits and not parse_files(text):
        problems.append("no <edit> or <file> blocks found in the answer")
    return out, problems


def theme_for(concept: Concept, brand_theme: Theme) -> Theme:
    colors = dict(concept.palette) or dict(brand_theme.colors)
    fonts = [FontSpec(family=f.family, src=f"assets/brand/{f.src}", role=f.role, weight=f.weight) for f in brand_theme.fonts]
    return Theme(colors=colors, fonts=fonts)


def manifest_for(concept: Concept, duration: float) -> Manifest:
    return Manifest(
        title=concept.title,
        language=concept.language if re.fullmatch(r"[a-z]{2}", concept.language or "") else "en",
        duration=duration,
        music=MusicSpec(**concept.music.model_dump()),
        scenes={s.id: (s.start, s.duration) for s in concept.scenes},
        marks={},
    )


def write_composition(dest: Path, files: dict[str, str], concept: Concept, duration: float,
                      brand_dir: Path, brand_theme: Theme, used_assets: set[str]) -> Path:
    """Create a complete composition dir: model files + manifest + theme + referenced brand assets."""
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "assets" / "brand").mkdir(parents=True)
    for name in COMPOSITION_FILES:
        (dest / name).write_text(files.get(name, ""))
    (dest / "manifest.json").write_text(manifest_for(concept, duration).model_dump_json(indent=2))
    (dest / "theme.json").write_text(theme_for(concept, brand_theme).model_dump_json(indent=2))
    wanted = set(used_assets) | {f.src for f in brand_theme.fonts}
    for rel in sorted(wanted):
        src = (brand_dir / rel).resolve()
        if brand_dir.resolve() not in src.parents or not src.is_file():
            continue
        target = dest / "assets" / "brand" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, target)
    (dest / "concept.json").write_text(json.dumps(concept.model_dump(), ensure_ascii=False, indent=1))
    return dest
