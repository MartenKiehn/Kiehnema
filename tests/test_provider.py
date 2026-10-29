"""Instance credentials, adapter routing and admin authorization without paid API calls."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from app.config import settings
from app.llm import client, provider
from app.pipeline.llm import estimate_cost
from app.web.main import app
from test_security import people, headers, ORIGIN


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr(settings, 'provider_setup_required', True)
    token = provider.current.set(None)
    yield tmp_path
    provider.current.reset(token)


def configuration(name='openai', **extra):
    return provider.Provider(provider=name, model='test-model', api_key='test-private-api-key', **extra)


def test_encrypted_credentials_roundtrip_and_fail_closed(isolated):
    assert provider.load() is None
    with pytest.raises(ValueError, match='administrator'):
        provider.require()
    value = configuration()
    provider.save(value)
    assert provider.load().private() == value.private()
    assert 'test-private-api-key' not in str(value.public()) + repr(value)
    for name in ('provider.enc', 'provider.key'):
        path = isolated / 'secure' / name
        assert path.stat().st_mode & 0o777 == 0o600
        assert b'test-private-api-key' not in path.read_bytes()
    (isolated / 'secure/provider.key').unlink()
    with pytest.raises(ValueError, match='unlocked'):
        provider.load()
    with pytest.raises(ValueError, match='missing'):
        provider.save(value)


def test_legacy_configuration_and_worker_snapshot(isolated, monkeypatch):
    monkeypatch.setattr(settings, 'provider_setup_required', False)
    assert provider.load().provider == 'bedrock'
    assert provider.load().auth == 'environment'
    token = provider.current.set(configuration())
    try:
        provider.save(configuration('anthropic'))
        assert provider.require().provider == 'openai'
        assert estimate_cost(client.Usage(input_tokens=1000)) is None
    finally:
        provider.current.reset(token)


@pytest.mark.parametrize('extra', [dict(api_key=''), dict(model='bad\nmodel'), dict(max_output_tokens=0),
                                  dict(api_key='bad\ncredential')])
def test_invalid_credentials_and_models_are_rejected(extra):
    with pytest.raises(ValueError):
        provider.Provider.model_validate(dict(provider='openai', model='test', api_key='test') | extra)


def test_provider_page_requires_admin_and_csrf_and_never_echoes_keys(people, isolated):
    admin, user, *_ = people
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.get('/admin/provider', follow_redirects=False).status_code == 303
        user_headers, admin_headers = headers(user), headers(admin)
        values = dict(provider='openai', model='test-model', max_output_tokens='8192', api_key='unique-private-key')
        assert web.get('/admin/provider', headers=user_headers).status_code == 403
        assert web.post('/admin/provider', headers=user_headers, data=values).status_code == 403
        assert web.post('/admin/provider', headers={'Cookie': admin_headers['Cookie'], 'Origin': ORIGIN}, data=values).status_code == 403
        assert web.post('/admin/provider', headers=admin_headers | {'Origin': 'https://evil.example'}, data=values).status_code == 403
        assert 'AI provider' in web.get('/', headers=admin_headers).text
        assert web.post('/jobs', headers=user_headers, data={'url': 'https://example.com'}).status_code == 422
        response = web.post('/admin/provider', headers=admin_headers, data=values, follow_redirects=False)
        assert response.status_code == 303
        page = web.get('/admin/provider', headers=admin_headers)
        assert page.status_code == 200 and 'unique-private-key' not in page.text
        assert page.headers['cache-control'] == 'no-store'
        # Empty credentials keep the same provider's key; switching providers requires a new key.
        values['api_key'] = ''
        assert web.post('/admin/provider', headers=admin_headers, data=values, follow_redirects=False).status_code == 303
        assert provider.load().api_key.get_secret_value() == 'unique-private-key'
        values['provider'] = 'anthropic'
        assert web.post('/admin/provider', headers=admin_headers, data=values).status_code == 422
        values.update(api_key='rotated-private-key', max_output_tokens='invalid')
        response = web.post('/admin/provider', headers=admin_headers, data=values)
        assert response.status_code == 422 and 'rotated-private-key' not in response.text
        values['max_output_tokens'] = '8192'
        assert web.post('/admin/provider', headers=admin_headers, data=values, follow_redirects=False).status_code == 303
        assert provider.load().provider == 'anthropic'
        assert provider.load().api_key.get_secret_value() == 'rotated-private-key'


@pytest.mark.parametrize('role', ['admin', 'manager', 'user'])
@pytest.mark.parametrize('configured', [False, True])
def test_provider_access_and_navigation_are_admin_only(people, isolated, role, configured):
    from app.security import accounts as a
    admin, member, *_ = people
    if role == 'manager':
        a.set_role(admin, member.id, 'manager')
    if configured:
        provider.save(configuration())
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin if role == 'admin' else member)
        for path in ('/', '/videos', '/account'):
            page = web.get(path, headers=credentials)
            assert page.status_code == 200
            navigation = page.text.split('<nav aria-label="Main navigation">', 1)[1].split('</nav>', 1)[0]
            assert ('href="/admin/provider"' in navigation) == (role == 'admin')
        page = web.get('/admin/provider', headers=credentials)
        assert page.status_code == (200 if role == 'admin' else 403)
        if role != 'admin':
            assert web.post('/admin/provider', headers=credentials, data={
                'provider':'anthropic', 'model':'forbidden', 'api_key':'forbidden-key',
                'max_output_tokens':'8192'}).status_code == 403
            saved = provider.load()
            assert (saved is not None) == configured
            if saved:
                assert saved.private() == configuration().private()


def test_aws_key_rotation_requires_complete_set_and_clears_session_token(people, isolated):
    admin_headers = headers(people[0])
    values = dict(provider='bedrock', model='test-model', region='eu-central-1', auth='keys',
                  max_output_tokens='8192', access_key='access', secret_key='secret', session_token='session')
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.post('/admin/provider', headers=admin_headers, data=values, follow_redirects=False).status_code == 303
        values.update(access_key='new-access', secret_key='', session_token='')
        assert web.post('/admin/provider', headers=admin_headers, data=values).status_code == 422
        assert provider.load().access_key.get_secret_value() == 'access'
        values['secret_key'] = 'new-secret'
        assert web.post('/admin/provider', headers=admin_headers, data=values, follow_redirects=False).status_code == 303
        assert not provider.load().session_token.get_secret_value()
        values['auth'] = 'environment'
        assert web.post('/admin/provider', headers=admin_headers, data=values, follow_redirects=False).status_code == 303
        assert not provider.load().public()['credentials_saved']


@pytest.mark.parametrize(('name', 'adapter', 'url', 'limit'), [
    ('openai', 'ChatOpenAI', 'https://api.openai.com/v1', 'max_completion_tokens'),
    ('anthropic', 'ChatAnthropic', 'https://api.anthropic.com', 'max_tokens'),
])
def test_api_adapters_use_explicit_key_official_endpoint_and_token_cap(isolated, monkeypatch, name, adapter, url, limit):
    provider.current.set(configuration(name, max_output_tokens=8192))
    monkeypatch.setattr(client, adapter, lambda **kwargs: kwargs)
    args = client.chat_model(max_tokens=32000)
    assert args['base_url'] == url and args[limit] == 8192
    assert args['api_key'].get_secret_value() == 'test-private-api-key'
    assert 'reasoning_effort' not in args and 'cache_control' not in args


@pytest.mark.parametrize('auth', ['keys', 'environment'])
def test_bedrock_explicit_keys_or_server_auth(isolated, monkeypatch, auth):
    provider.current.set(provider.Provider(provider='bedrock', model='eu.anthropic.claude-opus-5-5',
        auth=auth, access_key='access', secret_key='secret', session_token='session'))
    sessions = []
    def session(**kwargs):
        sessions.append(kwargs)
        return SimpleNamespace(client=lambda *args, **kw: 'runtime-client')
    monkeypatch.setattr(client.boto3, 'Session', session)
    monkeypatch.setattr(client, 'ChatBedrockConverse', lambda **kwargs: kwargs)
    assert client.chat_model()['client'] == 'runtime-client'
    if auth == 'keys':
        assert sessions[0]['aws_access_key_id'] == 'access'
        assert sessions[0]['aws_session_token'] == 'session'
    else:
        assert 'aws_access_key_id' not in sessions[0]


@pytest.mark.parametrize('name', ['openai', 'anthropic'])
def test_stream_and_structured_output_normalize_usage_without_bedrock_options(isolated, monkeypatch, name):
    from test_llm_client import Answer
    provider.current.set(configuration(name))
    usage = dict(input_tokens=10, output_tokens=2, total_tokens=12)
    class Model:
        def stream(self, messages, **kwargs):
            assert not kwargs
            yield AIMessageChunk(content='Paris', usage_metadata=usage, response_metadata={'finish_reason': 'stop'})
        def with_structured_output(self, schema, **kwargs):
            assert schema is Answer and kwargs['include_raw']
            return self
        def invoke(self, messages, **kwargs):
            assert not kwargs
            return dict(raw=AIMessage(content='', usage_metadata=usage), parsed=Answer(city='Paris'))
    monkeypatch.setattr(client, 'chat_model', lambda *args: Model())
    total = client.Usage()
    assert client.stream_text('test', [], total) == 'Paris'
    assert client.structured('test', Answer, [], total).city == 'Paris'
    assert total.calls == 2 and total.output_tokens == 4
    # Restoring cumulative usage after switching back must not price earlier API calls as Bedrock.
    from dataclasses import asdict
    provider.current.set(provider.Provider(provider='bedrock', model='eu.anthropic.claude-opus-5-5', auth='environment'))
    restored = client.Usage(**asdict(total))
    restored.add('next', usage)
    assert estimate_cost(restored) is None


def test_provider_errors_do_not_disclose_credentials(isolated, monkeypatch):
    provider.current.set(configuration())
    def broken(*args):
        raise RuntimeError('Request header Authorization: test-private-api-key')
    monkeypatch.setattr(client, 'chat_model', broken)
    with pytest.raises(client.LLMError) as exc:
        client.stream_text('test', [], client.Usage())
    assert 'test-private-api-key' not in str(exc.value)
    assert exc.value.__suppress_context__


@pytest.mark.parametrize('role', ['admin', 'manager', 'user'])
def test_missing_provider_locks_entire_brief_and_only_admin_gets_setup_link(people, isolated, monkeypatch, role):
    from app.security import accounts as a
    admin, member, *_ = people
    if role == 'manager':
        a.set_role(admin, member.id, 'manager')
    person = admin if role == 'admin' else member
    def no_crawl(*args):
        raise AssertionError('No crawl or job may start before AI setup.')
    monkeypatch.setattr('app.web.main.UrlGuard.check', no_crawl)
    monkeypatch.setattr('app.web.main.enqueue', no_crawl)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(person)
        page = web.get('/', headers=credentials)
        assert page.status_code == 200
        assert 'class="provider-lock"' in page.text and 'AI provider required' in page.text
        assert 'Only a Regisseur can configure the AI provider.' in page.text
        assert 'disabled aria-describedby="provider-lock-message"' in page.text
        assert ('href="/admin/provider"' in page.text) == (role == 'admin')
        # Direct POSTs remain blocked and their error page must keep the full form locked.
        page = web.post('/jobs', headers=credentials, data={'url':'https://example.com'})
        assert page.status_code == 422
        assert 'class="provider-lock"' in page.text and 'disabled aria-describedby="provider-lock-message"' in page.text
        provider.save(configuration())
        page = web.get('/', headers=credentials)
        assert 'class="provider-lock"' not in page.text
        assert 'disabled aria-describedby="provider-lock-message"' not in page.text


def test_unreadable_provider_configuration_keeps_form_locked(people, isolated, monkeypatch):
    monkeypatch.setattr(provider, 'load', lambda: (_ for _ in ()).throw(ValueError('Cannot decrypt configuration.')))
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(people[0])
        for page in (web.get('/', headers=credentials),
                     web.post('/jobs', headers=credentials, data={'url':'https://example.com'})):
            assert 'class="provider-lock"' in page.text
            assert 'disabled aria-describedby="provider-lock-message"' in page.text
