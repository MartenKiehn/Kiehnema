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


@pytest.mark.parametrize('invalid', ['no_tool', 'validation'])
@pytest.mark.parametrize('name', ['bedrock', 'openai', 'anthropic'])
def test_structured_requests_schema_tool_and_corrects_invalid_output_once(monkeypatch, invalid, name):
    from langchain_core.messages import AIMessage
    from app.llm import client, provider

    config = provider.Provider(provider=name, model='test-model',
                               **({'auth': 'environment'} if name == 'bedrock' else {'api_key': 'synthetic-key'}))
    monkeypatch.setattr(client, 'require', lambda: config)
    secret = 'PRIVATE MODEL OUTPUT must not enter diagnostics or correction prompts'
    error = None
    if invalid == 'validation':
        try:
            Answer.model_validate({'city': [secret]})
        except ValueError as exc:
            error = exc
    raw = AIMessage(content=secret, usage_metadata={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12},
                    response_metadata={'stopReason': 'end_turn'})
    outputs = [dict(raw=raw, parsed=None, parsing_error=error), dict(raw=raw, parsed=Answer(city='Paris'))]
    seen = []

    class Model:
        def with_structured_output(self, schema, **kwargs):
            assert schema is Answer and kwargs == dict(method='function_calling', include_raw=True)
            return self
        def invoke(self, msgs, **kwargs):
            assert kwargs == ({'cache_control': {'type': 'ephemeral'}} if name == 'bedrock' else {})
            seen.append(msgs)
            return outputs.pop(0)

    monkeypatch.setattr(client, 'chat_model', lambda *args: Model())
    total = Usage()
    original = [HumanMessage('Capital of France?')]
    assert structured('concept', Answer, original, total).city == 'Paris'
    assert len(seen) == 2 and original == [HumanMessage('Capital of France?')]
    assert 'Answer' in seen[0][-1].content and 'tool' in seen[0][-1].content.lower()
    detail = 'no schema tool call' if invalid == 'no_tool' else 'string_type'
    assert detail in seen[1][-1].content
    assert secret not in str(seen)
    assert total.calls == 2 and total.by_step['concept'] == dict(input=20, output=4, cache_read=0, cache_write=0)


@pytest.mark.parametrize('name,field,stop,exception', [
    ('bedrock', 'stopReason', 'max_tokens', TruncatedOutput),
    ('bedrock', 'stopReason', 'refusal', Refused),
    ('anthropic', 'stop_reason', 'max_tokens', TruncatedOutput),
    ('anthropic', 'stop_reason', 'refusal', Refused),
    ('openai', 'finish_reason', 'length', TruncatedOutput),
    ('openai', 'finish_reason', 'content_filter', Refused),
])
def test_structured_never_retries_truncation_or_refusal(monkeypatch, name, field, stop, exception):
    from langchain_core.messages import AIMessage
    from app.llm import client, provider
    config = provider.Provider(provider=name, model='test-model',
                               **({'auth': 'environment'} if name == 'bedrock' else {'api_key': 'synthetic-key'}))
    monkeypatch.setattr(client, 'require', lambda: config)
    calls = []
    class Model:
        def with_structured_output(self, *args, **kwargs):
            return self
        def invoke(self, *args, **kwargs):
            calls.append(1)
            return dict(raw=AIMessage(content='', response_metadata={field: stop}), parsed=None)
    monkeypatch.setattr(client, 'chat_model', lambda *args: Model())
    total = Usage()
    with pytest.raises(exception):
        structured('concept', Answer, [], total)
    assert len(calls) == total.calls == 1


async def test_failed_structured_output_is_bounded_and_logs_usage_without_private_output(tmp_path, monkeypatch):
    import json
    from langchain_core.messages import AIMessage
    from app.llm import client, provider
    from app.pipeline.llm import ProviderLLM
    monkeypatch.setattr(client, 'require', lambda: provider.Provider(provider='bedrock', model='test', auth='environment'))
    calls = []
    class Model:
        def with_structured_output(self, *args, **kwargs):
            return self
        def invoke(self, *args, **kwargs):
            calls.append(1)
            return dict(raw=AIMessage(content='private response',
                        usage_metadata={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}),
                        parsed=None, parsing_error=ValueError('private response'))
    monkeypatch.setattr(client, 'chat_model', lambda *args: Model())
    log = tmp_path / 'llm.jsonl'
    llm = ProviderLLM(log_path=log)
    with pytest.raises(client.LLMError, match='no valid Answer') as error:
        await llm.structured('concept', Answer, [], effort='medium', max_tokens=32000)
    assert len(calls) == llm.usage.calls == 2
    assert 'private response' not in str(error.value) + log.read_text()
    row = json.loads(log.read_text())
    assert row['step'] == 'concept' and row['error'] == 'LLMError'
    assert row['usage']['input'] == 20 and row['usage']['output'] == 4
    assert 'schema' in row and row['seconds'] >= 0


def test_real_bedrock_adapter_recovers_from_plain_text_without_forcing_tool_choice(monkeypatch):
    from langchain_aws import ChatBedrockConverse
    from app.llm import client, provider
    calls = []
    class Bedrock:
        def converse(self, **request):
            calls.append(request)
            assert request['toolConfig'].get('toolChoice') in (None, {'auto': {}})
            assert 'Answer tool' in str(request['messages'])
            content = ([{'text': 'Paris'}] if len(calls) == 1 else
                       [{'toolUse': {'toolUseId': 'answer-1', 'name': 'Answer', 'input': {'city': 'Paris'}}}])
            return dict(output={'message': {'role': 'assistant', 'content': content}},
                        stopReason='end_turn' if len(calls) == 1 else 'tool_use',
                        usage={'inputTokens': 10, 'outputTokens': 2, 'totalTokens': 12}, metrics={'latencyMs': 1})
    config = provider.Provider(provider='bedrock', model='eu.anthropic.claude-opus-5-5', auth='environment')
    monkeypatch.setattr(client, 'require', lambda: config)
    model = ChatBedrockConverse(model=config.model, client=Bedrock(), region_name=config.region, reasoning_effort='medium')
    monkeypatch.setattr(client, 'chat_model', lambda *args: model)
    total = Usage()
    assert structured('concept', Answer, [HumanMessage('Capital of France?')], total).city == 'Paris'
    assert len(calls) == total.calls == 2
