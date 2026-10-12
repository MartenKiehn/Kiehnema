"""Structured outputs of the model steps (validated with Pydantic, requested via function calling)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FactKind = Literal[
    "claim", "tagline", "service", "process-step", "differentiator", "proof", "client", "quote",
    "certification", "number", "cta", "team", "other",
]


class Fact(BaseModel):
    id: str = Field(description="f1, f2, ... unique")
    kind: FactKind
    text: str = Field(description="concise statement in the website's language")
    quote: str = Field(description="VERBATIM excerpt from the website text that supports the fact (max ~300 chars)")
    source_url: str = Field(description="URL of the page the quote is from (one of the PAGE urls)")
    attribution: str | None = Field(None, description="for kind=quote: person and role, exactly as on the site")


class CompanyProfile(BaseModel):
    """What the website says about the company – only facts that are literally on the site."""

    company_name: str
    language: str = Field(description="ISO 639-1 code of the website language, e.g. de")
    one_liner: str = Field(description="what the company does, one sentence")
    audience: str = Field(description="who the website addresses")
    tone: str = Field(description="tone of voice, e.g. 'confident, pragmatic, no buzzwords'")
    primary_cta: str | None = Field(None, description="main call to action text as on the site")
    facts: list[Fact] = Field(description="10-40 facts covering claims, services, process, proof, quotes, CTA")


TextRole = Literal["headline", "subline", "chip", "label", "quote", "attribution", "body", "cta", "url"]


class ConceptText(BaseModel):
    id: str = Field(description="kebab-case, unique across the video, e.g. hook-question")
    role: TextRole
    text: str = Field(description="final on-screen copy in the video language; short")
    fact_ids: list[str] = Field(default_factory=list, description="facts that support this text (required for headline/subline/quote/body)")


class SceneChange(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False, extra="forbid")
    id: str
    duration: float = Field(ge=0.5, le=60)
    visual: str = Field(min_length=1, max_length=4000)


class ConceptCue(BaseModel):
    t: float = Field(description="absolute time in seconds")
    type: str = Field(description="one of the harness cue types")
    note: str | None = Field(None, description="why (for the composer)")


class ConceptScene(BaseModel):
    id: str = Field(description="element id, e.g. s-hook")
    start: float
    duration: float
    purpose: str = Field(description="what this scene must achieve for the viewer")
    visual: str = Field(description="concrete visual idea / motion (metaphor, objects, layout, transitions)")
    background: Literal["dark", "light", "photo", "brand-color"] = "dark"
    texts: list[ConceptText] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list, description="brand asset files used, e.g. logo.png, img/photo-07.jpg")
    cues: list[ConceptCue] = Field(default_factory=list)


class ConceptMusic(BaseModel):
    bpm: float = Field(120, ge=80, le=150)
    progression: list[str] = Field(default_factory=lambda: ["Am", "F", "C", "G"])
    outro_progression: list[str] = Field(default_factory=lambda: ["F", "C"])
    energy: Literal["calm", "medium", "high"] = "medium"


class Concept(BaseModel):
    """Storyboard for the motion graphic."""

    title: str
    language: str
    rationale: str = Field(description="why this story works for the audience and the brief (2-4 sentences)")
    hook: str = Field(description="the opening idea that makes viewers stop scrolling, and its payoff")
    palette: dict[str, str] = Field(description="colour roles used in the video -> hex, chosen from the brand colours, e.g. dark, light, primary, accent")
    music: ConceptMusic = Field(default_factory=ConceptMusic)
    scenes: list[ConceptScene]


class QAIssue(BaseModel):
    severity: Literal["major", "minor"]
    scene_id: str
    format: Literal["feed", "story", "both"]
    problem: str
    fix: str = Field(description="concrete instruction for the composer")


class QAReport(BaseModel):
    approved: bool = Field(description="true if there is no major issue")
    summary: str
    issues: list[QAIssue] = Field(default_factory=list)
