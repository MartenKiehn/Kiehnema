import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.security import accounts as a
from app.web.main import app
from tests.test_security import people, headers, ORIGIN


def test_updates_require_administrator_origin_and_csrf(people, monkeypatch, tmp_path):
    calls = []
    async def request(method, path, body=None):
        calls.append((method, path, body))
        return {'version': '0.1.0', 'available': [{'version': '0.2.0', 'notes': '<script>bad()</script>'}],
                'busy': False, 'operation': {}, 'error': None}
    monkeypatch.setattr('app.updates.client.request', request)
    monkeypatch.setattr('app.web.main.writes_allowed', lambda: True)
    with TestClient(app, base_url=ORIGIN) as client:
        monkeypatch.setattr(settings, 'update_control_dir', tmp_path)
        admin, user = headers(people[0]), headers(people[1])
        for path in ('', '/status'):
            assert client.get('/admin/updates' + path, headers=user).status_code == 403
        for path in ('check', 'install', 'cancel', 'credentials'):
            assert client.post('/admin/updates/' + path, headers=user).status_code == 403
        assert not calls
        html = client.get('/admin/updates', headers=admin).text
        assert '&lt;script&gt;bad()&lt;/script&gt;' in html
        assert '<script>bad()' not in html
        assert client.post('/admin/updates/install', headers={**admin, 'X-CSRF-Token': ''}, data={'version': '0.2.0'}).status_code == 403
        assert client.post('/admin/updates/install', headers={**admin, 'Origin': 'https://other.example'}, data={'version': '0.2.0'}).status_code == 403
        assert client.post('/admin/updates/install', headers=admin, data={'version': '0.2.0'}, follow_redirects=False).status_code == 303
        assert calls[-1] == ('POST', '/install', {'version': '0.2.0'})


@pytest.mark.parametrize('busy', [False, True])
def test_assistant_can_manage_updates_but_not_update_credentials(people, monkeypatch, tmp_path, busy):
    admin, assistant, *_ = people
    a.set_role(admin, assistant.id, 'manager')
    calls = []
    async def request(method, path, body=None):
        calls.append((method, path, body))
        return {'version': '0.1.0', 'available': [{'version': '0.2.0', 'notes': 'Release notes.'}],
                'busy': busy, 'operation': {'phase': 'queued'}, 'progress': {'message': 'Waiting.'}, 'error': None}
    monkeypatch.setattr('app.updates.client.request', request)
    monkeypatch.setattr('app.web.main.writes_allowed', lambda: True)
    with TestClient(app, base_url=ORIGIN) as client:
        monkeypatch.setattr(settings, 'update_control_dir', tmp_path)
        credentials = headers(assistant)
        page = client.get('/admin/updates', headers=credentials)
        assert page.status_code == 200
        assert 'update-notice.js' in page.text and 'href="/admin/provider"' not in page.text
        assert 'action="/admin/updates/credentials"' not in page.text
        assert 'Update access</summary>' not in page.text
        assert ('action="/admin/updates/cancel"' in page.text) == busy
        assert ('action="/admin/updates/install"' in page.text) == (not busy)
        assert ('action="/admin/updates/check"' in page.text) == (not busy)
        assert client.get('/admin/updates/status', headers=credentials).status_code == 200
        assert all(method == 'GET' for method, *_ in calls)
        for path, body in [('check', {}), ('install', {'version': '0.2.0'}), ('cancel', {})]:
            assert client.post('/admin/updates/' + path, headers=credentials,
                               data=body, follow_redirects=False).status_code == 303
        assert calls[-3:] == [('POST', '/check', None), ('POST', '/install', {'version': '0.2.0'}), ('POST', '/cancel', None)]
        previous = list(calls)
        assert client.post('/admin/updates/credentials', headers=credentials,
                           data={'username': 'test', 'token': 'synthetic-test-token'}).status_code == 403
        for invalid in (credentials | {'X-CSRF-Token': ''}, credentials | {'Origin': 'https://other.example'}):
            assert client.post('/admin/updates/install', headers=invalid, data={'version': '0.2.0'}).status_code == 403
        assert calls == previous


@pytest.mark.browser
async def test_independent_progress_reconnects_and_waits_for_release(tmp_path):
    from playwright.async_api import async_playwright, expect
    state = {'phase': 'migrating', 'message': 'Updating the data format.', 'updated_at': time.time(),
             'maintenance': True, 'needs_repair': False, 'received': None, 'total': None}
    disconnected = False
    async def handle(route):
        path = route.request.url.split('example.test')[-1]
        if path == '/_update/status':
            if disconnected:
                await route.abort()
            else:
                await route.fulfill(content_type='application/json', body=json.dumps(state))
        elif path.endswith('update-progress.js'):
            await route.fulfill(content_type='text/javascript', body=Path('app/static/update-progress.js').read_text())
        elif path.endswith('maintenance.css'):
            await route.fulfill(content_type='text/css', body=Path('deploy/maintenance.css').read_text())
        else:
            await route.fulfill(status=503, content_type='text/html', body=Path('deploy/maintenance.html').read_text())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 390, 'height': 844})
            await page.route('**/*', handle)
            await page.goto('http://example.test/')
            await expect(page.get_by_role('status')).to_have_text('Updating the data format.')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.screenshot(path=str(tmp_path / 'update-mobile.png'))
            disconnected = True
            await expect(page.get_by_role('status')).to_contain_text('Reconnecting', timeout=5000)
            disconnected = False
            state.update(phase='succeeded', message='Update complete. Sign in again.')
            await expect(page.get_by_role('status')).to_contain_text('Update complete', timeout=5000)
            await expect(page.get_by_role('link', name='Sign in again')).to_be_hidden()
            state['maintenance'] = False
            await expect(page.get_by_role('link', name='Sign in again')).to_be_visible(timeout=5000)
            await page.reload()
            await expect(page.get_by_role('link', name='Sign in again')).to_be_visible()
            state.update(phase='failed', maintenance=True, needs_repair=True)
            await page.reload()
            await expect(page.get_by_role('status')).to_contain_text('server operator')
        finally:
            await browser.close()
