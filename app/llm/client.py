"""Thin adapter around LangChain's ChatBedrockConverse for Claude Opus 5.5.

Encodes what the Phase-0 spike verified (see docs/phase0-spike.md):
- structured output works via ``method="function_calling"`` (langchain-aws uses tool_choice "auto"
  for Opus 5.5, which rejects forced tool use); ``method="json_schema"`` is rejected by Bedrock.
- ``reasoning_effort`` maps to adaptive thinking + ``output_config.effort``.
- prompt caching works with ``cache_control={"type": "ephemeral"}`` on invoke/stream.
- long outputs must stream; a ``max_tokens`` stop is surfaced as an error instead of
  silently returning truncated code.
The graph only talks to this module, so a call can be moved to the Anthropic SDK if needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence, TypeVar

import boto3
from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessageChunk, BaseMessage
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The model call finished without a usable result."""


class TruncatedOutput(LLMError):
    """Hit max_tokens: output is incomplete."""


class Refused(LLMError):
    """The model declined the request (stop reason ``refusal``)."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    calls: int = 0
    by_step: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, step: str, meta: dict | None) -> None:
        meta = meta or {}
        details = meta.get("input_token_details") or {}
        row = {
            "input": int(meta.get("input_tokens", 0)),
            "output": int(meta.get("output_tokens", 0)),
            "cache_read": int(details.get("cache_read", 0)),
            "cache_write": int(details.get("ephemeral_5m_input_tokens", 0) or details.get("cache_creation", 0)),
        }
        self.input_tokens += row["input"]
        self.output_tokens += row["output"]
        self.cache_read += row["cache_read"]
        self.cache_write += row["cache_write"]
        self.calls += 1
        acc = self.by_step.setdefault(step, {k: 0 for k in row})
        for k, v in row.items():
            acc[k] += v


def chat_model(effort: Effort = "medium", max_tokens: int | None = None) -> ChatBedrockConverse:
    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    client = session.client(
        "bedrock-runtime",
        config=Config(read_timeout=settings.read_timeout_s, connect_timeout=30, retries={"max_attempts": 4, "mode": "adaptive"}),
    )
    return ChatBedrockConverse(
        model=settings.bedrock_model_id,
        client=client,
        max_tokens=max_tokens or settings.max_tokens_default,
        reasoning_effort=effort,
    )


def _check_stop(step: str, stop: str | None) -> None:
    if stop == "max_tokens":
        raise TruncatedOutput(f"{step}: output hit max_tokens")
    if stop == "refusal":
        raise Refused(f"{step}: model declined the request")


def stream_text(
    step: str,
    messages: Sequence[BaseMessage],
    usage: Usage,
    *,
    effort: Effort = "medium",
    max_tokens: int | None = None,
    cache: bool = True,
    on_progress: Callable[[int], None] | None = None,
) -> str:
    """Stream a free-form completion (used for code generation) and return the full text.

    ``on_progress(chars)`` is called roughly every 2,000 streamed characters."""
    model = chat_model(effort, max_tokens)
    kwargs = {"cache_control": {"type": "ephemeral"}} if cache else {}
    final: AIMessageChunk | None = None
    reported = 0
    for chunk in model.stream(list(messages), **kwargs):
        final = chunk if final is None else final + chunk
        if on_progress and len(final.text) - reported >= 2000:
            reported = len(final.text)
            on_progress(reported)
    if final is None:
        raise LLMError(f"{step}: empty stream")
    usage.add(step, final.usage_metadata)
    _check_stop(step, final.response_metadata.get("stopReason"))
    return final.text


def structured(
    step: str,
    schema: type[T],
    messages: Sequence[BaseMessage],
    usage: Usage,
    *,
    effort: Effort = "medium",
    max_tokens: int | None = None,
    cache: bool = True,
) -> T:
    """Return a validated Pydantic object (function calling with tool_choice=auto)."""
    model = chat_model(effort, max_tokens).with_structured_output(schema, method="function_calling", include_raw=True)
    kwargs = {"cache_control": {"type": "ephemeral"}} if cache else {}
    result = model.invoke(list(messages), **kwargs)
    raw = result["raw"]
    usage.add(step, raw.usage_metadata)
    _check_stop(step, raw.response_metadata.get("stopReason"))
    if result.get("parsing_error") or result.get("parsed") is None:
        raise LLMError(f"{step}: no valid {schema.__name__} ({result.get('parsing_error')})")
    return result["parsed"]
