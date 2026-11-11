"""External administrative changes record metadata, never credentials or raw errors."""

import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import settings
from app.llm import provider
from app.security import accounts as a, audit
from app.web.main import app
from tests.test_security import people, headers, ORIGIN


def events(category):
    with a.database() as con:
        return [dict(row) for row in con.execute(
            'SELECT * FROM audit_events WHERE action LIKE ? ORDER BY id', (category + '.%',))]


def test_provider_only_records_validated_save_without_settings_or_secrets(people, monkeypatch):
    monkeypatch.setattr(settings, 'provider_setup_required', True)
    monkeypatch.setattr('app.web.provider.test_connection', lambda config: None)
    values = {'provider': 'openai', 'model': 'private-model-name', 'api_key': 'PRIVATE-API-KEY',
              'max_output_tokens': '8192'}
    original_save = provider.save

    def save(config):
        assert events('provider')[-1]['result'] == 'requested'
        original_save(config)

    monkeypatch.setattr(provider, 'save', save)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(people[0])
        assert web.post('/admin/provider', headers=credentials, data=values).status_code == 409
        assert web.post('/admin/provider/test', headers=credentials,
                        data=values | {'model': 'invalid\nmodel'}).status_code == 422
        assert not events('provider')
        assert web.post('/admin/provider/test', headers=credentials, data=values).status_code == 200
        assert not events('provider')
        assert web.post('/admin/provider', headers=credentials, data=values,
                        follow_redirects=False).status_code == 303
        rows = events('provider')
        assert len(rows) == 1
        assert rows[0]['actor_name'] == people[0].username
        assert rows[0]['actor_id'] == people[0].id
        assert rows[0]['action'] == 'provider.configuration_saved'
        assert rows[0]['result'] == 'saved'
        assert rows[0]['target'] == rows[0]['detail'] == ''
        assert 'PRIVATE-API-KEY' not in str(rows) and 'private-model-name' not in str(rows)


def test_provider_failed_save_records_failure_without_raw_error(people, monkeypatch):
    monkeypatch.setattr(settings, 'provider_setup_required', True)
    monkeypatch.setattr('app.web.provider.test_connection', lambda config: None)

    def fail(config):
        raise OSError('PRIVATE-API-KEY cannot be saved')

    monkeypatch.setattr(provider, 'save', fail)
    values = {'provider': 'openai', 'model': 'test-model', 'api_key': 'PRIVATE-API-KEY',
              'max_output_tokens': '8192'}
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(people[0])
        assert web.post('/admin/provider/test', headers=credentials, data=values).status_code == 200
        assert web.post('/admin/provider', headers=credentials, data=values).status_code == 409
        rows = events('provider')
        assert len(rows) == 1 and rows[0]['result'] == 'failed'
        assert 'PRIVATE-API-KEY' not in str(rows)
        assert provider.load() is None


@pytest.mark.parametrize('path', ['provider', 'updates/install'])
def test_audit_insert_failure_prevents_external_changes(people, monkeypatch, path):
    monkeypatch.setattr(settings, 'provider_setup_required', True)
    monkeypatch.setattr('app.web.provider.test_connection', lambda config: None)
    called = []

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('PRIVATE-STORAGE-ERROR')

    async def request(*args):
        called.append(args)
        return {}

    monkeypatch.setattr(provider, 'save', lambda config: called.append(config))
    monkeypatch.setattr('app.updates.client.request', request)
    values = {'provider': 'openai', 'model': 'test-model', 'api_key': 'PRIVATE-API-KEY',
              'max_output_tokens': '8192', 'version': '0.3.0'}
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as web:
        credentials = headers(people[0])
        if path == 'provider':
            assert web.post('/admin/provider/test', headers=credentials, data=values).status_code == 200
        monkeypatch.setattr(audit, 'record', unavailable)
        response = web.post('/admin/' + path, headers=credentials, data=values, follow_redirects=False)
        assert response.status_code == 500
        assert not called
        assert not events('provider') and not events('updates')
        assert 'PRIVATE-' not in response.text


def test_provider_audit_finish_failure_keeps_requested_after_successful_save(people, monkeypatch):
    monkeypatch.setattr(settings, 'provider_setup_required', True)
    monkeypatch.setattr('app.web.provider.test_connection', lambda config: None)
    attempted_results = []

    def unavailable(event_id, result):
        attempted_results.append(result)
        raise OSError('PRIVATE-STORAGE-ERROR')

    monkeypatch.setattr(audit, 'finish', unavailable)
    values = {'provider': 'openai', 'model': 'test-model', 'api_key': 'PRIVATE-API-KEY',
              'max_output_tokens': '8192'}
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as web:
        credentials = headers(people[0])
        assert web.post('/admin/provider/test', headers=credentials, data=values).status_code == 200
        response = web.post('/admin/provider', headers=credentials, data=values, follow_redirects=False)
        assert response.status_code == 500
        assert provider.load().api_key.get_secret_value() == 'PRIVATE-API-KEY'
        assert attempted_results == ['saved']
        rows = events('provider')
        assert len(rows) == 1 and rows[0]['result'] == 'requested'
        assert 'PRIVATE-' not in str(rows) + response.text


@pytest.mark.parametrize('role', ['admin', 'manager'])
def test_update_operations_record_actor_before_request_and_accepted_result(people, monkeypatch, role):
    admin, member, *_ = people
    if role == 'manager':
        a.set_role(admin, member.id, role)
    person = admin if role == 'admin' else member
    calls = []

    async def request(method, path, body=None):
        calls.append((method, path, body))
        assert events('updates')[-1]['result'] == 'requested'
        return {}

    monkeypatch.setattr('app.updates.client.request', request)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(person)
        assert web.get('/admin/updates', headers=credentials).status_code == 200
        assert web.get('/admin/updates/status', headers=credentials).status_code == 200
        assert not events('updates') and not calls
        operations = [('check', {}), ('install', {'version': '0.3.0'}), ('cancel', {})]
        if role == 'admin':
            operations.append(('credentials', {'username': 'PRIVATE-REGISTRY-USER', 'token': 'PRIVATE-TOKEN'}))
        for path, body in operations:
            response = web.post('/admin/updates/' + path, headers=credentials, data=body, follow_redirects=False)
            assert response.status_code == 303
        rows = events('updates')
        assert [row['action'] for row in rows] == [
            'updates.check_requested', 'updates.install_requested', 'updates.cancel_requested',
            *(['updates.credentials_changed'] if role == 'admin' else [])]
        assert all(row['actor_id'] == person.id and row['actor_name'] == person.username for row in rows)
        assert all(row['result'] == 'accepted' for row in rows)
        assert rows[1]['detail'] == '0.3.0'
        assert 'PRIVATE-REGISTRY-USER' not in str(rows) and 'PRIVATE-TOKEN' not in str(rows)


@pytest.mark.parametrize('failure', ['rejected', 'unavailable', 'server_error'])
def test_update_failure_does_not_store_untrusted_version_or_service_error(people, monkeypatch, failure):
    async def request(method, path, body=None):
        assert events('updates')[-1]['result'] == 'requested'
        req = httpx.Request(method, 'http://updater' + path)
        if failure == 'unavailable':
            raise httpx.ConnectError('PRIVATE-SERVICE-ERROR', request=req)
        response = httpx.Response(500 if failure == 'server_error' else 409,
                                  json={'error': 'PRIVATE-SERVICE-ERROR'}, request=req)
        raise httpx.HTTPStatusError('PRIVATE-SERVICE-ERROR', request=req, response=response)

    monkeypatch.setattr('app.updates.client.request', request)
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/admin/updates/install', headers=headers(people[0]),
                            data={'version': 'PRIVATE-UNTRUSTED-VERSION'}, follow_redirects=False)
        assert response.status_code == (409 if failure == 'rejected' else 503)
        rows = events('updates')
        assert len(rows) == 1 and rows[0]['result'] == ('failed' if failure == 'rejected' else 'requested')
        assert rows[0]['detail'] == ''
        assert 'PRIVATE-' not in str(rows)
        if failure != 'rejected':
            assert 'PRIVATE-' not in response.text


def test_unauthorized_external_mutations_do_not_call_services_or_record_changes(people, monkeypatch):
    called = []

    async def request(*args):
        called.append(args)
        return {}

    monkeypatch.setattr('app.updates.client.request', request)
    with TestClient(app, base_url=ORIGIN) as web:
        member_credentials = headers(people[1])
        admin_credentials = headers(people[0])
        paths = ['provider', 'updates/check', 'updates/install', 'updates/cancel', 'updates/credentials']
        for path in paths:
            assert web.post('/admin/' + path, headers=member_credentials).status_code == 403
            for invalid in (admin_credentials | {'X-CSRF-Token': ''},
                            admin_credentials | {'Origin': 'https://other.example'}):
                assert web.post('/admin/' + path, headers=invalid).status_code == 403
        assert not events('provider') and not events('updates') and not called


@pytest.mark.parametrize('path', ['provider', 'updates/install', 'updates/credentials'])
def test_permissions_are_rechecked_after_form_read(people, monkeypatch, path):
    admin = people[0]
    original = Request.form
    called = []

    async def form(request, *args, **kwargs):
        result = await original(request, *args, **kwargs)
        with a.database() as con:
            con.execute('UPDATE users SET admin=0 WHERE id=?', (admin.id,))
        return result

    async def request(*args):
        called.append(args)
        return {}

    monkeypatch.setattr(Request, 'form', form)
    monkeypatch.setattr('app.updates.client.request', request)
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/admin/' + path, headers=headers(admin),
                            data={'version': '0.3.0', 'username': 'test', 'token': 'private'})
        assert response.status_code == 403
        assert not events('provider') and not events('updates') and not called
