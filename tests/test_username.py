"""Self-service sign-in names preserve encrypted content and account boundaries."""

from concurrent.futures import ThreadPoolExecutor

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.security import accounts as a, audit
from app.pipeline import jobs
from app.web import auth
from app.web.main import app
from test_security import people, headers, ORIGIN, PASSWORD, SECRET


@pytest.mark.parametrize('role', ['admin', 'manager', 'user'])
@pytest.mark.parametrize('two_factor', [False, True])
def test_rename_preserves_content_access_and_role_and_replaces_sessions(people, monkeypatch, role, two_factor):
    admin, alice, admin_draft, alice_draft = people
    person, draft = (admin, admin_draft) if role == 'admin' else (alice, alice_draft)
    other = alice if role == 'admin' else admin
    if role == 'manager':
        a.set_role(admin, person.id, 'manager')
    if not two_factor:
        a.require_two_factor(admin, person.id, False)
    identity = a.current_identity.set(person)
    try:
        jid = jobs.create_job('https://example.com', 30, SECRET)
        path = jobs.jobs_dir() / jid
        (path / 'site/index.html').write_text(SECRET)
        (path / 'renders/feed-1080x1350.mp4').write_bytes(b'synthetic private video')
    finally:
        a.current_identity.reset(identity)
    original_name, original_key = person.username, person.key
    credentials = headers(person)
    old_token = credentials['Cookie'].split('=', 1)[1]
    other_session = a.new_session(a.Identity(person.id, person.username, person.admin, person.key, two_factor))
    unrelated_session = a.new_session(other)
    old_csrf = a.get_session(old_token).csrf
    session_context = auth.current_session.set((a.digest(old_token), a.get_session(old_token)))
    try:
        grant = auth.preview_grant(jid)
    finally:
        auth.current_session.reset(session_context)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as web:
        with a.database() as con:
            con.execute('UPDATE jobs SET active=2 WHERE id=?', (jid,))
            before = dict(con.execute('SELECT * FROM users WHERE id=?', (person.id,)).fetchone())
        response = web.post('/account/username', headers=credentials, data={
            'username': '  New.Name-7  ', 'password': PASSWORD,
            'code': pyotp.TOTP(draft['totp']).now() if two_factor else '',
            'user_id': other.id, 'role': 'admin'}, follow_redirects=False)
        assert response.status_code == 303 and response.headers['location'] == '/account?username_changed=1'
        token = web.cookies.get('kiehnema-local')
        assert token and token != old_token
        session = a.get_session(token)
        assert session.csrf != old_csrf and session.provider_test is None
        assert (session.person.id, session.person.username, session.person.role) == (person.id, 'new.name-7', role)
        assert session.person.key == original_key
        assert a.get_session(old_token) is None and a.get_session(other_session) is None
        assert a.get_session(unrelated_session) is not None
        page = web.get(response.headers['location'])
        assert 'Your username is updated.' in page.text and 'new.name-7' in page.text
        video = web.get(f'/jobs/{jid}/files/renders/feed-1080x1350.mp4')
        assert video.status_code == 200 and video.content == b'synthetic private video'
        assert web.get(f'/preview/{jid}/_/{grant}/index.html').status_code == 401
        with a.database() as con:
            after = dict(con.execute('SELECT * FROM users WHERE id=?', (person.id,)).fetchone())
            events = con.execute("SELECT * FROM audit_events WHERE action='team.username_changed'").fetchall()
            assert len(events) == 1
            event = events[0]
            assert (event['actor_id'], event['actor_name'], event['target'], event['detail'], event['result']) == (
                person.id, original_name, original_name, f'{original_name} → new.name-7', 'saved')
            assert event['occurred_at'] <= a.time.time()
            job = con.execute('SELECT owner,active FROM jobs WHERE id=?', (jid,)).fetchone()
            assert tuple(job) == (person.id, 2)
            assert con.execute('SELECT username FROM users WHERE id=?', (other.id,)).fetchone()[0] == other.username
        for column in before.keys() - {'username', 'last_totp'}:
            assert after[column] == before[column], column
    with pytest.raises(ValueError, match='Invalid username'):
        a.authenticate(original_name, PASSWORD)
    # Re-authentication consumed the current MFA code; advance to the next time step.
    step = int(a.time.time()) // 30 + 1
    monkeypatch.setattr(a.time, 'time', lambda: step * 30 + 1)
    monkeypatch.setattr(pyotp.TOTP, 'timecode', lambda self, for_time: step)
    signed_in = a.authenticate('new.name-7', PASSWORD, pyotp.TOTP(draft['totp']).now() if two_factor else '')
    assert signed_in.id == person.id and signed_in.role == role and signed_in.key == original_key
    recovered = a.recover('new.name-7', a.recovery_text(draft), PASSWORD + ' recovered')
    assert recovered['id'] == person.id and recovered['key'] == original_key


@pytest.mark.parametrize(('name', 'message'), [
    ('ab', '3–64'), ('a' * 65, '3–64'), ('a user', '3–64'), ('../admin', '3–64'),
    ('<script>alert(1)</script>', '3–64'), (' ADMIN ', 'already exists'),
    ('reserved', 'pending invitation'), (' ALICE ', 'already using'),
])
def test_invalid_taken_and_invited_names_preserve_existing_account_and_sessions(people, name, message):
    admin, alice, *_ = people
    a.invite_user(admin, 'reserved')
    token = a.new_session(alice)
    with pytest.raises(ValueError, match=message):
        a.change_username(alice, name)
    assert a.get_session(token).person.username == 'alice'
    with a.database() as con:
        assert con.execute('SELECT username FROM users WHERE id=?', (alice.id,)).fetchone()[0] == 'alice'
        assert con.execute("SELECT COUNT(*) FROM audit_events WHERE action='team.username_changed'").fetchone()[0] == 0


def test_rename_audit_failure_preserves_name_sessions_and_history(people, monkeypatch):
    alice = people[1]
    token = a.new_session(alice)
    original = audit.record

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('Audit storage failure')

    monkeypatch.setattr(audit, 'record', fail)
    with pytest.raises(OSError, match='Audit storage failure'):
        a.change_username(alice, 'new-alice')
    assert alice.username == 'alice'
    assert a.get_session(token).person.username == 'alice'
    with a.database() as con:
        assert con.execute('SELECT username FROM users WHERE id=?', (alice.id,)).fetchone()[0] == 'alice'
        assert con.execute("SELECT COUNT(*) FROM audit_events WHERE action='team.username_changed'").fetchone()[0] == 0


def test_crew_can_only_record_own_username_change_and_cannot_read_log(people):
    alice = people[1]
    with pytest.raises(PermissionError):
        audit.record(alice, 'team.username_changed', target='admin', detail='admin → different')
    with pytest.raises(PermissionError):
        audit.record(alice, 'team.role_changed', target='alice')
    a.change_username(alice, 'new-alice')
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.get('/admin/activity', headers=headers(alice)).status_code == 403
        page = web.get('/admin/activity?category=team', headers=headers(people[0]))
        assert page.status_code == 200
        assert 'Changed username' in page.text and 'alice → new-alice' in page.text


def test_expired_invitation_does_not_reserve_name_and_refresh_updates_stale_identities(people):
    admin, alice, *_ = people
    invitation = a.invite_user(admin, 'available')
    draft = a.enrollment('available', PASSWORD)
    with a.database() as con:
        con.execute('UPDATE invites SET expires=0 WHERE username=?', ('available',))
    stale = a.Identity(alice.id, 'alice', False, alice.key, True)
    a.change_username(alice, 'available')
    assert a.refresh(stale).username == 'available'
    with pytest.raises(ValueError, match='invalid or expired'):
        a.finish_enrollment(draft, invite=invitation)


def test_concurrent_renames_cannot_claim_the_same_name(people):
    def rename(person):
        try:
            a.change_username(person, 'one-name')
            return 'renamed'
        except ValueError:
            return 'taken'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rename, people[:2]))
    assert sorted(results) == ['renamed', 'taken']
    with a.database() as con:
        assert con.execute('SELECT count(*) FROM users WHERE username=?', ('one-name',)).fetchone()[0] == 1


def test_recovery_started_before_rename_uses_current_name(people):
    _, alice, _, draft = people
    recovery = a.recover('alice', a.recovery_text(draft), PASSWORD + ' recovered')
    a.change_username(alice, 'new-alice')
    restored = a.finish_recovery(recovery, pyotp.TOTP(recovery['totp']).now())
    assert restored.id == alice.id and restored.username == 'new-alice' and restored.key == alice.key


def test_disabled_user_cannot_rename(people):
    alice = people[1]
    with a.database() as con:
        con.execute('UPDATE users SET disabled=1 WHERE id=?', (alice.id,))
    with pytest.raises(PermissionError, match='unavailable'):
        a.change_username(alice, 'different')


@pytest.mark.parametrize('change', [{'password': 'wrong'}, {'code': ''}, {'code': 'invalid'}])
def test_rename_requires_password_and_fresh_authenticator(people, change):
    alice = people[1]
    credentials = headers(alice)
    values = {'username': 'different', 'password': PASSWORD, 'code': pyotp.TOTP(people[3]['totp']).now()}
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/account/username', headers=credentials, data=values | change)
        assert 'Invalid username, password or authenticator code.' in response.text
        assert 'name="username" value="different"' in response.text
        assert PASSWORD not in response.text
        assert a.get_session(credentials['Cookie'].split('=', 1)[1]).person.username == 'alice'


def test_rename_rejects_reused_authenticator_code(people):
    code = pyotp.TOTP(people[3]['totp']).now()
    alice = a.authenticate('alice', PASSWORD, code)
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/account/username', headers=headers(alice), data={
            'username': 'different', 'password': PASSWORD, 'code': code})
        assert 'Wait for a new authenticator code' in response.text
        assert a.refresh(alice).username == 'alice'


def test_invalid_username_is_escaped_and_password_is_not_redisplayed(people):
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/account/username', headers=headers(people[1]), data={
            'username': '<script>alert(1)</script>', 'password': PASSWORD,
            'code': pyotp.TOTP(people[3]['totp']).now()})
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
        assert '<script>alert(1)</script>' not in response.text and PASSWORD not in response.text


def test_rename_rejects_cross_origin_missing_csrf_anonymous_and_mismatched_principal(people, monkeypatch):
    admin, alice, *_ = people
    credentials = headers(alice)
    values = {'username': 'different', 'password': PASSWORD, 'code': pyotp.TOTP(people[3]['totp']).now()}
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.post('/account/username', headers=credentials | {'Origin': 'https://other.example'}, data=values).status_code == 403
        assert web.post('/account/username', headers={'Origin': ORIGIN, 'Cookie': credentials['Cookie']}, data=values).status_code == 403
        assert web.post('/account/username', headers={'Origin': ORIGIN}, data=values).status_code == 401
        monkeypatch.setattr(a, 'authenticate', lambda *args: admin)
        response = web.post('/account/username', headers=credentials, data=values)
        assert 'Account changed. Reload the page and try again.' in response.text
        assert a.get_session(credentials['Cookie'].split('=', 1)[1]).person.username == 'alice'
        assert admin.username == 'admin'
