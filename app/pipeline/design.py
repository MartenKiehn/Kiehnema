"""Visual review ideas: proportions guide the composer, not final pixel coordinates."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DesignElement(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False, extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    kind: Literal["existing", "text", "image"]
    selector: str = Field(default="", max_length=300)
    label: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=500)
    comment: str = Field(default="", max_length=2000)
    asset: str = Field(default="", max_length=36)
    x: float = Field(ge=-1, le=1)
    y: float = Field(ge=-1, le=1)
    width: float = Field(gt=0, le=3)
    height: float = Field(gt=0, le=3)
    layer: int = Field(ge=0, le=200)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    size: int = Field(default=64, ge=24, le=180)
    removed: bool = False
    text_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def content(self):
        import re
        if self.kind == "existing" and not self.selector:
            raise ValueError("Existing artwork needs a source selector")
        if self.removed and self.kind != "existing":
            raise ValueError("Only existing artwork needs a removal instruction")
        if self.kind == "text" and not self.text:
            raise ValueError("Text boxes must contain text")
        if self.kind == "image" and not re.fullmatch(r"[a-f0-9]{32}\.png", self.asset):
            raise ValueError("Choose an uploaded image")
        return self


class FrameDesign(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    scene_id: str = Field(min_length=1, max_length=80)
    format: Literal["feed", "story"]
    time: float = Field(ge=0, le=60)
    elements: list[DesignElement] = Field(min_length=1, max_length=40)


class DesignDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frames: list[FrameDesign] = Field(default_factory=list, max_length=20)

    def validate_context(self, scenes: list[dict], directory: Path):
        seen = set()
        for frame in self.frames:
            key = (frame.scene_id, frame.format)
            scene = next((s for s in scenes if s["id"] == frame.scene_id), None)
            if key in seen or not scene or not scene["start"] <= frame.time <= scene["start"] + scene["duration"]:
                raise ValueError("Each frame idea must refer to a scene in this preview")
            seen.add(key)
            ids = [e.id for e in frame.elements]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate element IDs")
            for element in frame.elements:
                if element.removed and not set(element.text_ids) <= {t['id'] for t in scene.get('texts', [])}:
                    raise ValueError("Removed text must belong to this scene")
                if element.kind == "image":
                    file = directory / "review-assets" / element.asset
                    if not file.is_file() or file.is_symlink():
                        raise ValueError("An uploaded image is missing")
        return self
