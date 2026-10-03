"""LLM adapter: usage accounting, stop-reason handling, optional live Bedrock smoke test."""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.llm.client import Refused, TruncatedOutput, Usage, _check_stop, stream_text, structured


def test_usage_accumulates_per_step():
    usage = Usage()
    meta = {"input_tokens": 100, "output_tokens": 20, "input_token_details": {"cache_read": 80, "ephemeral_5m_input_tokens": 5}}
    usage.add("distill", meta)
    usage.add("distill", meta)
    usage.add("compose", {"input_tokens": 1, "output_tokens": 2})
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read, usage.cache_write, usage.calls) == (201, 42, 160, 10, 3)
    assert usage.by_step["distill"]["output"] == 40


@pytest.mark.parametrize(("stop", "exc"), [("max_tokens", TruncatedOutput), ("refusal", Refused)])
def test_bad_stop_reasons_raise(stop, exc):
    with pytest.raises(exc):
        _check_stop("compose", stop)


def test_normal_stop_passes():
    _check_stop("compose", "end_turn")


live = pytest.mark.skipif(os.environ.get("MG_LIVE_BEDROCK") != "1", reason="set MG_LIVE_BEDROCK=1 to call Bedrock")


class Answer(BaseModel):
    city: str


@live
def test_live_structured_and_stream():
    usage = Usage()
    out = structured("smoke", Answer, [HumanMessage("Capital of France? Fill the schema.")], usage, effort="low", cache=False)
    assert out.city.lower() == "paris"
    text = stream_text("smoke", [HumanMessage("Reply with exactly: OK")], usage, effort="low", cache=False)
    assert "OK" in text
    assert usage.calls == 2 and usage.output_tokens > 0
