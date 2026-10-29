"""Configured model access for the pipeline, with a scripted fake for offline tests."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from PIL import Image
from pydantic import BaseModel

from app.llm import client
from app.llm.client import Effort, Usage

T = TypeVar("T", bound=BaseModel)

# USD per million tokens (Anthropic list price for Claude Opus 5.5; Bedrock is billed separately
# by AWS and may differ – shown as an estimate only).
PRICE = {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write": 5.0}


def estimate_cost(usage: Usage) -> float | None:
    from app.llm.provider import current
    configured = current.get()
    if not usage.cost_known or (configured and (configured.provider != 'bedrock' or configured.model != 'eu.anthropic.claude-opus-5-5')):
        return None
    uncached = max(0, usage.input_tokens - usage.cache_read - usage.cache_write)
    return round((uncached * PRICE["input"] + usage.output_tokens * PRICE["output"]
                  + usage.cache_read * PRICE["cache_read"] + usage.cache_write * PRICE["cache_write"]) / 1e6, 3)


def image_block(path: Path, max_side: int = 540) -> dict:
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side * 2))
    import io

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return {"type": "image", "source_type": "base64", "mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}


def messages(system: str, user: str | list[dict]) -> list[BaseMessage]:
    return [SystemMessage(system), HumanMessage(content=user)]


class LLM(Protocol):
    usage: Usage

    async def structured(self, step: str, schema: type[T], msgs: list[BaseMessage], *, effort: Effort, max_tokens: int) -> T: ...

    async def text(self, step: str, msgs: list[BaseMessage], *, effort: Effort, max_tokens: int,
                   on_progress: Callable[[int], None] | None = None) -> str: ...


@dataclass
class ProviderLLM:
    """Configured LangChain provider; only usage/timing metadata is logged to ``log_path``."""

    log_path: Path | None = None
    usage: Usage = field(default_factory=Usage)

    def _log(self, step: str, seconds: float, extra: dict) -> None:
        if self.log_path:
            with self.log_path.open("a") as fh:
                fh.write(json.dumps({"step": step, "seconds": round(seconds, 1), "usage": self.usage.by_step.get(step), **extra}) + "\n")

    async def structured(self, step, schema, msgs, *, effort, max_tokens):
        t0 = time.monotonic()
        out = await asyncio.to_thread(client.structured, step, schema, msgs, self.usage, effort=effort, max_tokens=max_tokens)
        self._log(step, time.monotonic() - t0, {"schema": schema.__name__})
        return out

    async def text(self, step, msgs, *, effort, max_tokens, on_progress=None):
        t0 = time.monotonic()
        out = await asyncio.to_thread(client.stream_text, step, msgs, self.usage, effort=effort, max_tokens=max_tokens,
                                      on_progress=on_progress)
        self._log(step, time.monotonic() - t0, {"chars": len(out)})
        return out


@dataclass
class FakeLLM:
    """Scripted responses per step: a value, or a callable(msgs) -> value. Records every call."""

    script: dict[str, list[Any]]
    usage: Usage = field(default_factory=Usage)
    calls: list[tuple[str, list[BaseMessage]]] = field(default_factory=list)

    def _next(self, step: str, msgs: list[BaseMessage]) -> Any:
        self.calls.append((step, msgs))
        queue = self.script.get(step)
        if not queue:
            raise AssertionError(f"FakeLLM: no scripted response left for step {step!r}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        self.usage.add(step, {"input_tokens": 100, "output_tokens": 50})
        return item(msgs) if isinstance(item, Callable) else item

    async def structured(self, step, schema, msgs, *, effort, max_tokens):
        value = self._next(step, msgs)
        return value if isinstance(value, schema) else schema.model_validate(value)

    async def text(self, step, msgs, *, effort, max_tokens, on_progress=None):
        out = self._next(step, msgs)
        if on_progress:
            on_progress(len(out))
        return out
