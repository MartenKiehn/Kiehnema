"""Provider adapters with shared streaming, structured output and usage accounting."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence, TypeVar

import boto3
from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk, BaseMessage
from pydantic import BaseModel

from app.config import settings
from app.llm.provider import require

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
    cost_known: bool = True
    by_step: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, step: str, meta: dict | None, *, cost_known: bool = True) -> None:
        self.cost_known &= cost_known
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


def chat_model(effort: Effort = "medium", max_tokens: int | None = None):
    config = require()
    limit = min(max_tokens or settings.max_tokens_default, config.max_output_tokens)
    if config.provider == 'openai':
        return ChatOpenAI(model=config.model, api_key=config.api_key, base_url='https://api.openai.com/v1',
                          max_completion_tokens=limit, timeout=settings.read_timeout_s, stream_usage=True)
    if config.provider == 'anthropic':
        return ChatAnthropic(model=config.model, api_key=config.api_key, base_url='https://api.anthropic.com',
                             max_tokens=limit, timeout=settings.read_timeout_s)
    credentials = ({'aws_access_key_id': config.access_key.get_secret_value(),
                    'aws_secret_access_key': config.secret_key.get_secret_value(),
                    'aws_session_token': config.session_token.get_secret_value() or None}
                   if config.auth == 'keys' else {'profile_name': settings.aws_profile})
    session = boto3.Session(region_name=config.region, **credentials)
    client = session.client(
        "bedrock-runtime",
        config=Config(read_timeout=settings.read_timeout_s, connect_timeout=30, retries={"max_attempts": 4, "mode": "adaptive"}),
    )
    return ChatBedrockConverse(
        model=config.model,
        client=client,
        max_tokens=limit,
        **({'reasoning_effort': effort} if 'anthropic.' in config.model else {}),
    )


def _check_stop(step: str, stop: str | None) -> None:
    if stop in {"max_tokens", "length"}:
        raise TruncatedOutput(f"{step}: output hit max_tokens")
    if stop in {"refusal", "content_filter"}:
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
    try:
        model = chat_model(effort, max_tokens)
        kwargs = {"cache_control": {"type": "ephemeral"}} if cache and require().provider == 'bedrock' else {}
        final: AIMessageChunk | None = None
        reported = 0
        for chunk in model.stream(list(messages), **kwargs):
            final = chunk if final is None else final + chunk
            if on_progress and len(final.text) - reported >= 2000:
                reported = len(final.text)
                on_progress(reported)
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f'AI request failed ({type(exc).__name__}). Check provider, model, credentials, permissions and quota.') from None
    if final is None:
        raise LLMError(f"{step}: empty stream")
    config = require()
    usage.add(step, final.usage_metadata, cost_known=config.provider == 'bedrock' and config.model == 'eu.anthropic.claude-opus-5-5')
    _check_stop(step, final.response_metadata.get('stopReason') or final.response_metadata.get('stop_reason')
                or final.response_metadata.get('finish_reason'))
    if final.additional_kwargs.get('refusal'):
        raise Refused(f'{step}: model declined the request')
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
    try:
        model = chat_model(effort, max_tokens).with_structured_output(schema, method="function_calling", include_raw=True)
        kwargs = {"cache_control": {"type": "ephemeral"}} if cache and require().provider == 'bedrock' else {}
        result = model.invoke(list(messages), **kwargs)
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f'AI request failed ({type(exc).__name__}). Check provider, model, credentials, permissions and quota.') from None
    raw = result["raw"]
    config = require()
    usage.add(step, raw.usage_metadata, cost_known=config.provider == 'bedrock' and config.model == 'eu.anthropic.claude-opus-5-5')
    _check_stop(step, raw.response_metadata.get('stopReason') or raw.response_metadata.get('stop_reason')
                or raw.response_metadata.get('finish_reason'))
    if raw.additional_kwargs.get('refusal'):
        raise Refused(f'{step}: model declined the request')
    if result.get("parsing_error") or result.get("parsed") is None:
        raise LLMError(f"{step}: no valid {schema.__name__}")
    return result["parsed"]
