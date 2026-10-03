"""Assemble a renderable site from a composition directory.

composition dir layout (written by the model in later phases, by hand for examples/):
    manifest.json        duration, language, music settings, scene plan, marks
    theme.json           brand colours + fonts (validated, turned into theme.css)
    composition.html     markup inserted into #stage
    composition.css
    composition.js       calls MG.composition(ctx => ...)
    assets/              logo, images, fonts (optional)

site dir layout (served by app.render.server, rendered by app.render.renderer):
    index.html  theme.css  harness/  composition/  assets/
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field, field_validator

HERE = Path(__file__).resolve().parent
GSAP = HERE.parent / "static" / "vendor" / "gsap.min.js"

_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?\s*(,\s*[\d.]+\s*)?\))$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
_FAMILY = re.compile(r"^[\w][\w \-]{0,60}$")


def _safe_relpath(value: str) -> str:
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or ":" in value or value.startswith("//"):
        raise ValueError(f"asset path must be relative inside the site: {value!r}")
    return str(p)


class FontSpec(BaseModel):
    family: str
    src: str
    role: Literal["display", "body", "other"] = "other"
    weight: int = 400
    style: Literal["normal", "italic"] = "normal"

    @field_validator("family")
    @classmethod
    def _family(cls, v: str) -> str:
        if not _FAMILY.match(v):
            raise ValueError(f"invalid font family {v!r}")
        return v

    @field_validator("src")
    @classmethod
    def _src(cls, v: str) -> str:
        return _safe_relpath(v)


class Theme(BaseModel):
    colors: dict[str, str]
    fonts: list[FontSpec] = Field(default_factory=list)

    @field_validator("colors")
    @classmethod
    def _colors(cls, v: dict[str, str]) -> dict[str, str]:
        for name, value in v.items():
            if not _NAME.match(name):
                raise ValueError(f"invalid colour name {name!r}")
            if not _COLOR.match(value.strip()):
                raise ValueError(f"invalid colour value {name}={value!r}")
        return v

    def css(self) -> str:
        fmt = {".ttf": "truetype", ".otf": "opentype", ".woff": "woff", ".woff2": "woff2"}
        lines = []
        for f in self.fonts:
            kind = fmt.get(PurePosixPath(f.src).suffix.lower(), "truetype")
            lines.append(
                f"@font-face {{ font-family: '{f.family}'; src: url({f.src}) format('{kind}'); "
                f"font-weight: {f.weight}; font-style: {f.style}; font-display: block; }}"
            )
        props = [f"  --brand-{k}: {v};" for k, v in self.colors.items()]
        for role in ("display", "body"):
            fam = next((f.family for f in self.fonts if f.role == role), None)
            if fam:
                props.append(f"  --font-{role}: '{fam}', sans-serif;")
        lines.append(":root {\n" + "\n".join(props) + "\n}")
        return "\n".join(lines) + "\n"


class MusicSpec(BaseModel):
    bpm: float = Field(120, ge=60, le=180)
    progression: list[str] = Field(default_factory=lambda: ["Am", "F", "C", "G"], min_length=1, max_length=8)
    outro_progression: list[str] = Field(default_factory=lambda: ["F", "C"], max_length=4)
    energy: Literal["calm", "medium", "high"] = "medium"


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = "Motion graphic"
    language: str = Field("en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    duration: float = Field(..., gt=2, le=120)
    music: MusicSpec = Field(default_factory=MusicSpec)
    scenes: dict[str, tuple[float, float]] = Field(default_factory=dict)
    marks: dict[str, float] = Field(default_factory=dict)


def _json_for_script(data: object) -> str:
    # Safe inside <script type="application/json">: never let the payload close the tag.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def load_composition(src: Path) -> tuple[Manifest, Theme]:
    manifest = Manifest.model_validate_json((src / "manifest.json").read_text())
    theme = Theme.model_validate_json((src / "theme.json").read_text())
    return manifest, theme


def build_site(src: Path, out: Path) -> Path:
    """Write a self-contained site for composition ``src`` into ``out`` (replaced)."""
    manifest, theme = load_composition(src)
    if out.exists():
        shutil.rmtree(out)
    (out / "harness").mkdir(parents=True)
    (out / "composition").mkdir()
    shutil.copy(HERE / "harness.js", out / "harness" / "harness.js")
    shutil.copy(HERE / "harness.css", out / "harness" / "harness.css")
    shutil.copy(GSAP, out / "harness" / "gsap.min.js")
    for name in ("composition.css", "composition.js"):
        shutil.copy(src / name, out / "composition" / name)
    (out / "composition" / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    if (src / "assets").is_dir():
        shutil.copytree(src / "assets", out / "assets")
    (out / "theme.css").write_text(theme.css())

    env = Environment(loader=FileSystemLoader(HERE), undefined=StrictUndefined, autoescape=False)
    html = env.get_template("shell.html.j2").render(
        language=manifest.language,
        title=manifest.title.replace("<", "&lt;"),
        manifest_json=_json_for_script(manifest.model_dump()),
        theme_json=_json_for_script(theme.model_dump()),
        composition_html=(src / "composition.html").read_text(),
    )
    (out / "index.html").write_text(html)
    return out
