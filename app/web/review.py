"""Validated review drafts tied to one saved preview."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False, extra="forbid")
    revision: str
    action: Literal["save", "delete"] = "save"
    id: str = Field(default="", max_length=40, pattern=r"^[a-f0-9]*$")
    time: float = Field(default=0, ge=0, le=60)
    text: str = Field(default="", max_length=2000)
    format: Literal["both", "feed", "story"] = "both"


def saved_comments(path: Path, revision: str) -> list[dict]:
    file = path / "review-comments.json"
    saved = json.loads(file.read_text()) if file.exists() else {}
    return saved.get("comments", []) if saved.get("revision") == revision else []


def save_comments(path: Path, revision: str, comments: list[dict]) -> None:
    temporary = path / "review-comments.tmp"
    temporary.write_text(json.dumps({"revision": revision, "comments": comments}, ensure_ascii=False))
    temporary.replace(path / "review-comments.json")
