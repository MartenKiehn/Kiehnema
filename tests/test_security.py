"""Security boundaries with real cryptography and synthetic, non-billable jobs."""

import asyncio
import base64
import io
import json
import os
import re
import runpy
import secrets
import sqlite3
from pathlib import Path

import httpx
import pyotp
import pytest
import tink
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from langgraph.graph import StateGraph, START, END

from app.config import AdminSetupSettings, settings
from app.pipeline import jobs
from app.security import accounts as a, vault
from app.security.checkpoints import checkpoint
from app.web.main import app

PASSWORD = 'A strong and unique test password!'
ORIGIN = 'http://127.0.0.1'
SECRET = 'CONFIDENTIAL campaign that must never be on disk'


@pytest.fixture
def people(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr('app.web.main.memory_policy', lambda: None)
    a.sessions.clear()
    draft = a.enrollment('admin', PASSWORD, two_factor=True)
    admin = a.finish_enrollment(draft, pyotp.TOTP(draft['totp']).now(), setup=True)
    invitation = a.invite_user(admin, 'alice')
    other_draft = a.enrollment('alice', PASSWORD, two_factor=True)
    alice = a.finish_enrollment(other_draft, pyotp.TOTP(other_draft['totp']).now(), invite=invitation)
    token = a.current_identity.set(alice)
    yield admin, alice, draft, other_draft
    a.current_identity.reset(token)
    a.sessions.clear()


def headers(person):
    token = a.new_session(person)
    session = a.get_session(token)
    return {'Origin': ORIGIN, 'X-CSRF-Token': session.csrf, 'Cookie': 'kiehnema-local=' + token}


def csrf(response):
    return re.search(r'name="csrf-token" content="([^"]+)"', response.text)[1]


@pytest.fixture
def admin_setup(tmp_path, monkeypatch):
    from app.security import __main__ as cli

    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    monkeypatch.setattr('app.security.workspace.memory_policy', lambda: None)
    monkeypatch.setattr(cli.sys, 'stdin', io.StringIO())
    monkeypatch.setattr(cli, 'AdminSetupSettings', lambda: AdminSetupSettings(_env_file=None))
    for key in list(os.environ):
        if key.upper().startswith('MG_ADMIN_'):
            monkeypatch.delenv(key)
    return cli


@pytest.fixture
def admin_env(admin_setup, monkeypatch):
    values = {'MG_ADMIN_USERNAME': 'marten', 'MG_ADMIN_PASSWORD': PASSWORD,
              'MG_ADMIN_TOTP_SECRET': pyotp.random_base32(), 'MG_ADMIN_RECOVERY_KEY': secrets.token_urlsafe(32)}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


@pytest.mark.parametrize(('attempts', 'message'), [
    (['x' * 14], 'Use a password of 15–256 characters.'),
    (['x' * 257], 'Use a password of 15–256 characters.'),
    ([PASSWORD, 'different password'], 'Passwords do not match.'),
])
def test_admin_setup_retries_invalid_input(admin_setup, monkeypatch, capsys, attempts, message):
    cli = admin_setup
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    passwords = iter([*attempts, PASSWORD, PASSWORD])
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: next(passwords))
    names = iter(['invalid name', 'marten'])
    def answer(prompt):
        assert prompt.startswith('Administrator username')
        return next(names)

    monkeypatch.setattr('builtins.input', answer)
    cli.setup_admin()
    output = capsys.readouterr().out
    assert message in output
    assert 'Use 3–64 letters' in output
    assert PASSWORD not in output
    assert 'time-based setup key' not in output
    with a.database() as con:
        users = con.execute('SELECT * FROM users').fetchall()
    assert len(users) == 1 and users[0]['username'] == 'marten' and users[0]['admin']
    a.Argon2id.verify_phc_encoded(PASSWORD.encode(), users[0]['password_hash'])
    assert not users[0]['totp']
    assert a.authenticate('marten', PASSWORD).admin


@pytest.mark.parametrize('source', ['environment', 'dotenv'])
def test_unattended_admin_startup_and_restart(admin_setup, admin_env, tmp_path, monkeypatch, capsys, source):
    if source == 'dotenv':
        dotenv = tmp_path / '.env'
        dotenv.write_text('\n'.join(f'{key}={value}' for key, value in admin_env.items()))
        for key in admin_env:
            monkeypatch.delenv(key)
        monkeypatch.setattr(admin_setup, 'AdminSetupSettings', lambda: AdminSetupSettings(_env_file=dotenv))
    calls = []
    monkeypatch.setattr('uvicorn.run', lambda *args, **kwargs: calls.append(a.initialized()))
    runpy.run_module('app', run_name='__main__')
    person = a.authenticate('marten', PASSWORD, pyotp.TOTP(admin_env['MG_ADMIN_TOTP_SECRET']).now())
    assert person.admin
    recovered = a.recover('marten', admin_env['MG_ADMIN_RECOVERY_KEY'], PASSWORD + ' recovered')
    assert recovered['key'] == person.key
    with a.database() as con:
        before = dict(con.execute('SELECT * FROM users').fetchone())
    monkeypatch.setenv('MG_ADMIN_PASSWORD', 'invalid on purpose')
    runpy.run_module('app', run_name='__main__')
    with a.database() as con:
        assert [dict(row) for row in con.execute('SELECT * FROM users')] == [before]
    assert calls == [True, True]
    with pytest.raises(ValueError, match='already complete'):
        admin_setup.setup_admin()
    output = capsys.readouterr()
    for secret in (PASSWORD, admin_env['MG_ADMIN_TOTP_SECRET'], admin_env['MG_ADMIN_RECOVERY_KEY']):
        assert secret not in output.out + output.err


@pytest.mark.parametrize(('key', 'value', 'message'), [
    ('MG_ADMIN_USERNAME', None, 'together'),
    ('MG_ADMIN_PASSWORD', None, 'together'),
    ('MG_ADMIN_RECOVERY_KEY', None, 'together'),
    ('MG_ADMIN_PASSWORD', '', 'together'),
    ('MG_ADMIN_TOTP_SECRET', '', 'MG_ADMIN_TOTP_SECRET'),
    ('MG_ADMIN_USERNAME', 'invalid user', 'username'),
    ('MG_ADMIN_PASSWORD', 'short', '15–256'),
    ('MG_ADMIN_PASSWORD', 'x' * 257, '15–256'),
    ('MG_ADMIN_TOTP_SECRET', 'INVALID!SECRET', 'MG_ADMIN_TOTP_SECRET'),
    ('MG_ADMIN_TOTP_SECRET', 'é' * 32, 'MG_ADMIN_TOTP_SECRET'),
    ('MG_ADMIN_TOTP_SECRET', 'A' * 16, 'MG_ADMIN_TOTP_SECRET'),
    ('MG_ADMIN_RECOVERY_KEY', '!' * 43, 'MG_ADMIN_RECOVERY_KEY'),
    ('MG_ADMIN_RECOVERY_KEY', 'é' * 43, 'MG_ADMIN_RECOVERY_KEY'),
    ('MG_ADMIN_RECOVERY_KEY', base64.urlsafe_b64encode(b'x' * 31).decode(), 'MG_ADMIN_RECOVERY_KEY'),
])
def test_unattended_admin_rejects_invalid_configuration(admin_setup, admin_env, monkeypatch, capsys, key, value, message):
    if value is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message) as error:
        admin_setup.setup_admin()
    assert not a.initialized()
    output = capsys.readouterr()
    for secret in (PASSWORD, admin_env['MG_ADMIN_TOTP_SECRET'], admin_env['MG_ADMIN_RECOVERY_KEY']):
        assert secret not in output.out + output.err + str(error.value)


def test_headless_admin_setup_requires_configuration(admin_setup):
    with pytest.raises(ValueError, match='MG_ADMIN_USERNAME'):
        admin_setup.setup_admin()
    assert not a.initialized()


def test_setup_and_invitation_cannot_be_reused(people):
    admin, alice, draft, other = people
    with pytest.raises(ValueError, match='already complete'):
        a.finish_enrollment(draft, pyotp.TOTP(draft['totp']).now(), setup=True)
    invitation = a.invite_user(admin, 'bob')
    new = a.enrollment('bob', PASSWORD, two_factor=True)
    a.finish_enrollment(new, pyotp.TOTP(new['totp']).now(), invite=invitation)
    with pytest.raises(ValueError, match='invitation'):
        a.finish_enrollment(new, pyotp.TOTP(new['totp']).now(), invite=invitation)
    with pytest.raises(PermissionError):
        a.invite_user(alice, 'unauthorized')


async def test_contents_filenames_and_complete_checkpoints_are_encrypted(people):
    jid = jobs.create_job('https://secret-customer.example/', 30, SECRET)
    job = vault.get_job(jid)
    (vault.VaultPath(job) / 'private-campaign.txt').write_text(SECRET)
    async def step(state):
        return {'message': SECRET}
    builder = StateGraph(dict)
    builder.add_node('step', step)
    builder.add_edge(START, 'step')
    builder.add_edge('step', END)
    config = {'configurable': {'thread_id': jid}, 'metadata': {'private': SECRET}}
    async with checkpoint(job) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({'message': SECRET}, config)
    async with checkpoint(job) as saver:
        saved = await builder.compile(checkpointer=saver).aget_state(config)
        assert saved.values['message'] == SECRET
        assert saved.metadata['private'] == SECRET
    for file in (settings.data_dir / 'secure').rglob('*'):
        if file.is_file():
            content = file.read_bytes()
            for private in (SECRET.encode(), b'secret-customer.example', b'private-campaign.txt', people[1].key, PASSWORD.encode()):
                assert private not in content, file
    assert not (settings.data_dir / 'jobs.sqlite').exists()


def test_owner_binding_and_ciphertext_tampering_fail_closed(people):
    admin, alice, *_ = people
    jid = jobs.create_job('https://example.com', 30)
    job = vault.get_job(jid)
    path = vault.VaultPath(job) / 'secret.txt'
    path.write_text(SECRET)
    token = a.current_identity.set(admin)
    with pytest.raises(FileNotFoundError):
        vault.get_job(jid)
    a.current_identity.reset(token)
    encrypted = job.directory / job.entry('secret.txt')['blob']
    content = bytearray(encrypted.read_bytes())
    content[-1] ^= 1
    encrypted.write_bytes(content)
    with pytest.raises(tink.TinkError):
        path.read_bytes()
    path.write_text(SECRET)
    with a.database() as con:
        con.execute('UPDATE jobs SET owner=? WHERE id=?', (admin.id, jid))
    token = a.current_identity.set(admin)
    with pytest.raises(InvalidTag):
        vault.get_job(jid)
    a.current_identity.reset(token)


def test_all_private_web_routes_enforce_ownership_and_csrf(people):
    admin, alice, *_ = people
    jid = jobs.create_job('https://example.com', 30, SECRET)
    path = jobs.jobs_dir() / jid
    (path / 'site/index.html').write_text('<p>' + SECRET + '</p>')
    (path / 'renders/feed-1080x1350.mp4').write_bytes(SECRET.encode())
    routes = [f'/jobs/{jid}', f'/jobs/{jid}/progress', f'/jobs/{jid}/delete',
              f'/jobs/{jid}/files/renders/feed-1080x1350.mp4', f'/preview/{jid}/index.html']
    with TestClient(app, base_url=ORIGIN) as client:
        for route in routes:
            assert client.get(route).status_code == 401
            response = client.get(route, headers=headers(admin))
            assert response.status_code == 404 and SECRET not in response.text
        credentials = headers(alice)
        assert client.get(routes[-1], headers=credentials).status_code == 200
        assert SECRET not in client.get('/', headers=headers(admin)).text
        assert client.post(f'/jobs/{jid}/delete', headers={'Origin': ORIGIN, 'Cookie': credentials['Cookie']}).status_code == 403
        assert client.post(f'/jobs/{jid}/delete', headers=headers(admin)).status_code == 404
        assert client.get('/admin', headers=credentials).status_code == 403
        assert client.get('/admin', headers=headers(admin)).status_code == 200
        for endpoint in ('review', 'comments', 'design-assets', 'reopen'):
            assert client.post(f'/jobs/{jid}/{endpoint}', headers=headers(admin), json={}).status_code in (404, 422)


def test_range_downloads_never_materialize_plaintext(people):
    jid = jobs.create_job('https://example.com', 30)
    content = bytes(range(256)) * 10000
    path = jobs.jobs_dir() / jid / 'renders/feed-1080x1350.mp4'
    path.write_bytes(content)
    with TestClient(app, base_url=ORIGIN) as client:
        credentials = headers(people[1])
        url = f'/jobs/{jid}/files/renders/feed-1080x1350.mp4'
        for value, expected in [('bytes=1048500-1048700', content[1048500:1048701]), ('bytes=-20', content[-20:]), ('bytes=0-0', content[:1])]:
            response = client.get(url, headers={**credentials, 'Range': value})
            assert response.status_code == 206 and response.content == expected
        assert client.head(url, headers=credentials).headers['content-length'] == str(len(content))
        assert client.get(url, headers={**credentials, 'Range': 'bytes=99999999-'}).status_code == 416
        assert client.get(url, headers={**credentials, 'Range': 'bytes=0-1,3-4'}).status_code == 416
    assert not list(settings.data_dir.rglob('*.mp4'))


def test_login_mfa_logout_and_preview_capability_revocation(people):
    jid = jobs.create_job('https://example.com', 30)
    path = jobs.jobs_dir() / jid
    (path / 'site/index.html').write_text(SECRET)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        page = client.get('/login')
        values = {'csrf': csrf(page), 'username': 'alice', 'password': PASSWORD, 'code': 'invalid'}
        assert 'Invalid username' in client.post('/login', data=values).text
        values['code'] = pyotp.TOTP(people[3]['totp']).now()
        response = client.post('/login', data=values, follow_redirects=False)
        assert response.status_code == 303
        assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
        from app.web import auth
        raw = client.cookies.get('kiehnema-local')
        session = a.get_session(raw)
        token = auth.current_session.set((a.digest(raw), session))
        grant = auth.preview_grant(jid)
        auth.current_session.reset(token)
        url = f'/preview/{jid}/_/{grant}/index.html'
        anonymous = TestClient(app, base_url=ORIGIN)
        assert anonymous.get(url).text == SECRET
        direct = anonymous.get(url, headers={'Sec-Fetch-Dest': 'document'}, follow_redirects=False)
        assert direct.status_code == 303 and direct.headers['location'] == f'/jobs/{jid}'
        assert anonymous.get(f'/preview/another-job/_/{grant}/index.html').status_code == 401
        anonymous.close()
        assert client.post('/logout', data={'csrf': session.csrf}, follow_redirects=False).status_code == 303
        assert client.get(url).status_code == 401


def test_password_change_and_recovery_preserve_data_without_admin_key(people):
    jid = jobs.create_job('https://example.com', 30, SECRET)
    owner = people[1]
    previous_session = a.new_session(owner)
    a.change_password(owner, PASSWORD + ' changed')
    assert a.get_session(previous_session) is None
    with pytest.raises(ValueError):
        a.authenticate('alice', PASSWORD, pyotp.TOTP(people[3]['totp']).now())
    recovered = a.recover('alice', a.recovery_text(people[3]), PASSWORD + ' recovered')
    new_owner = a.finish_recovery(recovered, pyotp.TOTP(recovered['totp']).now())
    token = a.current_identity.set(new_owner)
    assert SECRET in (jobs.jobs_dir() / jid / 'request.json').read_text()
    a.current_identity.reset(token)
    with pytest.raises(ValueError):
        a.recover('alice', a.recovery_text(people[3]), PASSWORD)
    with pytest.raises(ValueError):
        a.recover('alice', a.recovery_text(people[2]), PASSWORD)


async def test_logout_keeps_only_the_started_job_capability(people, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    observed = []
    other = jobs.create_job('https://example.org', 30)
    async def work(jid):
        assert a.current_identity.get() is None
        job = vault.get_job(jid)
        with pytest.raises(PermissionError):
            vault.get_job(other)
        entered.set()
        await release.wait()
        (vault.VaultPath(job) / 'result.txt').write_text(SECRET)
        observed.append(jid)
        return {'status': 'done'}
    monkeypatch.setattr(jobs, 'run_job', work)
    monkeypatch.setattr('app.web.main.UrlGuard.check', lambda self, url: url)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=headers(people[1])) as client:
            response = await client.post('/jobs', data={'url': 'https://example.com'})
            assert response.status_code == 303
            await asyncio.wait_for(entered.wait(), 2)
            assert (await client.post('/logout')).status_code == 303
            assert (await client.get(response.headers['location'])).status_code == 401
            tasks = list(app.state.tasks.values())
            release.set()
            await asyncio.gather(*tasks)
    assert len(observed) == 1
    assert (jobs.jobs_dir() / observed[0] / 'result.txt').read_text() == SECRET


async def test_real_worker_reopens_encrypted_checkpoint_and_removes_workspace(people, tmp_path, monkeypatch):
    from test_pipeline import CONCEPT
    from app.pipeline.graph import build_graph
    jid = jobs.create_job('https://example.com', 6)
    job = vault.get_job(jid)
    (vault.VaultPath(job) / 'concept.json').write_text(json.dumps(CONCEPT))
    async with checkpoint(job) as saver:
        graph = build_graph(saver)
        await graph.aupdate_state({'configurable': {'thread_id': jid}},
                                 {'job_id': jid, 'duration': 6, 'status': 'done'}, as_node='render')
    scratch = tmp_path / 'synthetic-worker'
    scratch.mkdir()
    # Synthetic fixture data only: exercise the real subprocess without mounting host filesystems.
    monkeypatch.setattr('app.pipeline.jobs.ram_root', lambda: scratch)
    monkeypatch.setenv('MG_ADMIN_PASSWORD', PASSWORD)
    monkeypatch.setenv('mg_admin_totp_secret', pyotp.random_base32())
    from app.llm import provider
    provider.save(provider.Provider(provider='openai', model='synthetic-model', api_key='synthetic-worker-key'))
    spawn = asyncio.create_subprocess_exec

    async def check_environment(*args, **kwargs):
        assert not any(key.upper().startswith('MG_ADMIN_') for key in kwargs['env'])
        assert 'synthetic-worker-key' not in str(args) + str(kwargs['env'])
        kwargs['stderr'] = asyncio.subprocess.PIPE
        process = await spawn(*args, **kwargs)
        communicate = process.communicate
        async def checked_communicate(payload):
            assert json.loads(payload)['provider']['api_key'] == 'synthetic-worker-key'
            output = await communicate(payload)
            assert process.returncode == 0, output[1].decode()
            return output
        process.communicate = checked_communicate
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', check_environment)
    result = await jobs.reopen_job(jid)
    assert '__interrupt__' in result
    assert (await jobs.job_state(jid))['next'] == ['review']
    assert not list(scratch.iterdir())
    assert b'Robots that work' not in b''.join(p.read_bytes() for p in job.directory.iterdir())


def test_worker_rejects_plain_disk_workspace(people, tmp_path, monkeypatch):
    import subprocess
    from app.security.workspace import ram_root
    monkeypatch.setattr(settings, 'work_dir', tmp_path / 'disk')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        ram_root()


def test_memory_policy_fails_closed_and_coordinator_lock_is_exclusive(people, monkeypatch):
    from types import SimpleNamespace
    from app.security import workspace
    monkeypatch.setattr(workspace.sys, 'platform', 'darwin')
    monkeypatch.setattr(workspace.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='total = 1024M (encrypted)'))
    workspace.memory_policy()
    monkeypatch.setattr(workspace.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='total = 1024M'))
    with pytest.raises(ValueError, match='Encrypted'):
        workspace.memory_policy()
    with a.service_lock():
        with pytest.raises(ValueError, match='Another Kiehnema'):
            with a.service_lock():
                pass


async def test_legacy_import_verifies_without_deleting_source(people, tmp_path):
    from app.security.__main__ import import_legacy
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    source = tmp_path / 'legacy'
    old_job = source / 'jobs' / 'legacy-job'
    old_job.mkdir(parents=True)
    (old_job / 'request.json').write_text(json.dumps({'url': 'https://example.com', 'description': SECRET}))
    (old_job / 'status.json').write_text(json.dumps({'status': 'done'}))
    builder = StateGraph(dict)
    builder.add_node('step', lambda state: state)
    builder.add_edge(START, 'step')
    builder.add_edge('step', END)
    async with AsyncSqliteSaver.from_conn_string(str(source / 'jobs.sqlite')) as saver:
        await builder.compile(checkpointer=saver).ainvoke({'content': SECRET}, {'configurable': {'thread_id': 'legacy-job'}})
        await builder.compile(checkpointer=saver).ainvoke({'content': 'other private job'}, {'configurable': {'thread_id': 'not-imported'}})
    token = a.current_identity.set(people[0])
    try:
        await import_legacy(source)
        job = vault.get_job('legacy-job')
        assert SECRET in (vault.VaultPath(job) / 'request.json').read_text()
        async with checkpoint(job) as saver:
            state = await builder.compile(checkpointer=saver).aget_state({'configurable': {'thread_id': 'legacy-job'}})
            assert state.values['content'] == SECRET
            assert not (await saver.conn.execute_fetchall('SELECT 1 FROM checkpoints WHERE thread_id=?', ('not-imported',)))
        assert SECRET in (old_job / 'request.json').read_text()
        await import_legacy(source)
    finally:
        a.current_identity.reset(token)


def test_https_cookie_flags_and_disabled_accounts(people, monkeypatch):
    # Cookie assertions must not depend on crossing a real TOTP time window.
    step = int(a.time.time()) // 30
    monkeypatch.setattr(pyotp.TOTP, 'timecode', lambda self, for_time: step)
    monkeypatch.setattr(settings, 'public_origin', 'https://127.0.0.1')
    with TestClient(app, base_url='https://127.0.0.1', headers={'Origin': 'https://127.0.0.1'}) as client:
        token = csrf(client.get('/login'))
        result = client.post('/login', data={'csrf': token, 'username': 'alice', 'password': PASSWORD,
                                           'code': pyotp.TOTP(people[3]['totp']).now()}, follow_redirects=False)
        assert result.status_code == 303
        cookie = result.headers['set-cookie']
        assert '__Host-kiehnema=' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie
        assert 'Domain=' not in cookie
        with a.database() as con:
            con.execute('UPDATE users SET disabled=1 WHERE id=?', (people[1].id,))
        assert client.get('/account', follow_redirects=False).status_code == 303


@pytest.mark.browser
async def test_account_onboarding_and_recovery_in_chromium(people, tmp_path, monkeypatch):
    import socket
    import uvicorn
    from playwright.async_api import async_playwright, expect
    monkeypatch.setattr('app.web.main.enqueue', lambda jid: None)
    monkeypatch.setattr('app.web.main.UrlGuard.check', lambda self, url: url)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(.01)
        assert server.started
        base = f'http://127.0.0.1:{sock.getsockname()[1]}'
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                admin_page = await browser.new_page()
                await admin_page.goto(base + '/login')
                await admin_page.get_by_label('Username', exact=True).fill('admin')
                await admin_page.get_by_label('Password', exact=True).fill(PASSWORD)
                await admin_page.get_by_label('Authenticator code').fill(pyotp.TOTP(people[2]['totp']).now())
                await admin_page.get_by_role('button', name='Unlock workspace').click()
                await admin_page.get_by_role('link', name='Users', exact=True).click()
                await admin_page.get_by_label('Require two-factor authentication for all users', exact=True).check()
                await admin_page.get_by_role('button', name='Save policy', exact=True).click()
                await admin_page.get_by_label('New username').fill('new-user')
                await admin_page.get_by_role('button', name='Create invitation').click()
                invitation = await admin_page.locator('.recovery-key code').inner_text()
                page = await browser.new_page(viewport={'width': 390, 'height': 844})
                await page.goto(base + '/activate')
                await page.get_by_label('Invitation code').fill(invitation)
                await page.get_by_label('Choose a password').fill(PASSWORD)
                await page.get_by_role('button', name='Continue', exact=True).click()
                secret = await page.locator('.auth-card code').first.inner_text()
                recovery = await page.locator('.recovery-key code').inner_text()
                await page.get_by_label('Authenticator code').fill(pyotp.TOTP(secret).now())
                await page.get_by_label('I saved my recovery key').check()
                await page.get_by_role('button', name='Create account', exact=True).click()
                await expect(page.get_by_role('heading', name='New video', exact=True)).to_be_visible()
                assert await page.get_by_role('link', name='Users', exact=True).count() == 0
                await page.get_by_label('Website URL', exact=True).fill('https://example.com')
                await page.get_by_label('Creative direction', exact=False).fill(SECRET)
                await page.get_by_role('button', name='Create preview').click()
                job_url = page.url
                await page.get_by_role('button', name='Sign out').click()
                await page.get_by_role('link', name='Recover your account').click()
                await page.get_by_label('Username', exact=True).fill('new-user')
                await page.get_by_label('Personal recovery key').fill(recovery)
                await page.get_by_label('New password').fill(PASSWORD + ' recovered')
                await page.get_by_role('button', name='Restore workspace').click()
                new_secret = await page.locator('.auth-card code').first.inner_text()
                await page.get_by_label('Authenticator code').fill(pyotp.TOTP(new_secret).now())
                await page.get_by_label('I saved my recovery key').check()
                await page.get_by_role('button', name='Restore access', exact=True).click()
                await page.goto(job_url)
                assert 'https://example.com/' in await page.locator('body').inner_text()
                cookies = await page.context.cookies()
                raw = next(item['value'] for item in cookies if item['name'] == 'kiehnema-local')
                principal = a.current_identity.set(a.get_session(raw).person)
                try:
                    assert SECRET in (jobs.jobs_dir() / job_url.rsplit('/', 1)[1] / 'request.json').read_text()
                finally:
                    a.current_identity.reset(principal)
                await page.get_by_role('button', name='Sign out').click()
                await page.wait_for_url(base + '/login')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.screenshot(path=str(tmp_path / 'login-mobile.png'), full_page=True)
                await page.set_viewport_size({'width': 1440, 'height': 1000})
                await page.screenshot(path=str(tmp_path / 'login-desktop.png'), full_page=True)
                from app.web.screenshot import capture
                report = await capture(job_url, tmp_path / 'private-capture.png',
                    credentials=('new-user', PASSWORD + ' recovered', pyotp.TOTP(new_secret).now()))
                assert report['url'] == job_url and not report['page_errors']
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await task
        sock.close()


def test_public_server_rejects_plain_http_and_insecure_bind(people, monkeypatch):
    monkeypatch.setattr(settings, 'public_origin', 'https://127.0.0.1')
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get('/login').status_code == 400
    monkeypatch.setattr(settings, 'public_origin', ORIGIN)
    monkeypatch.setattr(settings, 'host', '0.0.0.0')
    with pytest.raises(ValueError, match='HTTPS'):
        with TestClient(app, base_url=ORIGIN):
            pass


def test_video_library_and_polling_show_only_the_signed_in_users_jobs(people):
    admin, alice, *_ = people
    alice_job = jobs.create_job('https://alice-private.example', 30)
    token = a.current_identity.set(admin)
    try:
        admin_job = jobs.create_job('https://admin-private.example', 30)
    finally:
        a.current_identity.reset(token)
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.get('/videos', follow_redirects=False).status_code == 303
        for person, own, other, private_url in ((admin, admin_job, alice_job, 'alice-private.example'),
                                                (alice, alice_job, admin_job, 'admin-private.example')):
            credentials = headers(person)
            for route in ('/videos', '/jobs'):
                page = web.get(route, headers=credentials)
                assert page.status_code == 200
                assert f'href="/jobs/{own}"' in page.text
                assert f'href="/jobs/{other}"' not in page.text and private_url not in page.text


def test_expired_video_poll_redirects_whole_page_instead_of_injecting_login(people):
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.get('/jobs', headers={'HX-Request':'true'})
        assert response.status_code == 401
        assert response.headers['HX-Redirect'] == '/login'
        assert '<html' not in response.text
