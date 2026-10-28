import re

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.security import accounts as a
from app.web import auth
from app.web.main import app
from tests.test_security import admin_setup, admin_env, people, headers, csrf, PASSWORD, ORIGIN


def password_only_user(admin, name='bob'):
    draft = a.enrollment(name, PASSWORD)
    return a.finish_enrollment(draft, invite=a.invite_user(admin, name)), draft


def test_unattended_setup_without_authenticator(admin_setup, admin_env, monkeypatch, capsys):
    monkeypatch.delenv('MG_ADMIN_TOTP_SECRET')
    admin_setup.setup_admin()
    person = a.authenticate('marten', PASSWORD)
    assert person.admin and not a.two_factor_enabled(person)
    assert not a.two_factor_required(person)
    assert a.recover('marten', admin_env['MG_ADMIN_RECOVERY_KEY'], PASSWORD)['key'] == person.key
    output = capsys.readouterr().out
    assert PASSWORD not in output and admin_env['MG_ADMIN_RECOVERY_KEY'] not in output


def test_invitation_and_recovery_without_two_factor(people):
    invitation = a.invite_user(people[0], 'bob')
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        page = client.get('/activate')
        page = client.post('/activate', data={'csrf': csrf(page), 'invitation': invitation, 'password': PASSWORD})
        assert 'name="code"' not in page.text
        proof = re.search(r'name="proof" value="([^"]+)"', page.text)[1]
        recovery = re.search(r'class="recovery-key"><code>([^<]+)', page.text)[1]
        response = client.post('/activate', data={'csrf': csrf(page), 'proof': proof}, follow_redirects=False)
        assert response.status_code == 303
        person = a.get_session(client.cookies['kiehnema-local']).person
        assert not a.two_factor_enabled(person)
        assert client.get('/').status_code == 200
        assert 'name="code"' not in client.get('/account').text
        page = client.get('/recover')
        page = client.post('/recover', data={'csrf': csrf(page), 'username': 'bob', 'recovery': recovery,
                                           'password': PASSWORD + ' recovered'})
        assert 'name="code"' not in page.text
        proof = re.search(r'name="proof" value="([^"]+)"', page.text)[1]
        response = client.post('/activate', data={'csrf': csrf(page), 'proof': proof}, follow_redirects=False)
        assert response.status_code == 303
        restored = a.get_session(client.cookies['kiehnema-local']).person
        assert restored.key == person.key
        page = client.get('/account')
        response = client.post('/account/password', data={'csrf': csrf(page), 'old_password': PASSWORD + ' recovered',
                                                         'password': PASSWORD + ' changed'}, follow_redirects=False)
        assert response.status_code == 303
        assert a.authenticate('bob', PASSWORD + ' changed').key == person.key


@pytest.mark.parametrize('global_policy', [False, True])
def test_requirement_blocks_existing_sessions_and_preview_grants(people, global_policy):
    person, _ = password_only_user(people[0])
    with TestClient(app, base_url=ORIGIN) as client:
        credentials = headers(person)
        raw = credentials['Cookie'].split('=', 1)[1]
        context = auth.current_session.set((a.digest(raw), a.get_session(raw)))
        grant = auth.preview_grant('synthetic-job')
        auth.current_session.reset(context)
        admin = headers(people[0])
        assert client.get('/', headers=credentials).status_code == 200
        route = '/admin/two-factor-policy' if global_policy else '/admin/two-factor'
        assert client.post(route, headers=admin, data={'user_id': person.id, 'required': '1'},
                           follow_redirects=False).status_code == 303
        for path in ('/', '/account', '/admin', '/admin/updates/status'):
            response = client.get(path, headers=credentials, follow_redirects=False)
            assert response.status_code == 303 and response.headers['location'] == '/account/two-factor'
        assert client.post('/jobs', headers=credentials).status_code == 403
        assert client.get('/jobs/synthetic-job', headers=credentials).status_code == 403
        assert client.get(f'/preview/synthetic-job/_/{grant}/index.html').status_code == 403
        assert client.get('/account/two-factor', headers=credentials).status_code == 200
        assert client.post('/logout', headers=credentials, follow_redirects=False).status_code == 303
        page = client.get('/login')
        response = client.post('/login', headers={'Origin': ORIGIN}, data={'csrf': csrf(page), 'username': 'bob',
                              'password': PASSWORD}, follow_redirects=False)
        assert response.headers['location'] == '/account/two-factor'


def test_two_factor_setup_checks_password_code_and_revokes_old_sessions(people, monkeypatch):
    person, _ = password_only_user(people[0])
    now = int(a.time.time()) // 30 * 30
    monkeypatch.setattr(a.time, 'time', lambda: now)
    monkeypatch.setattr(pyotp.TOTP, 'timecode', lambda self, for_time: now // 30)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        old = headers(person)
        other = headers(person)
        a.require_two_factor(people[0], person.id, True)
        page = client.get('/account/two-factor', headers=old)
        secret = re.search(r'<code>([^<]+)', page.text)[1]
        code = pyotp.TOTP(secret).now()
        assert secret not in client.get('/account/two-factor', headers=other).text
        assert client.post('/account/two-factor', headers={**old, 'X-CSRF-Token': ''},
                           data={'password': PASSWORD, 'code': code}).status_code == 403
        assert 'Invalid username' in client.post('/account/two-factor', headers=old,
                                                data={'password': 'wrong', 'code': code}).text
        assert 'Enter the current code' in client.post('/account/two-factor', headers=old,
                                                      data={'password': PASSWORD, 'code': 'invalid'}).text
        assert not a.two_factor_enabled(person)
        response = client.post('/account/two-factor', headers=old, data={'password': PASSWORD, 'code': code},
                               follow_redirects=False)
        assert response.status_code == 303
        assert not a.get_session(old['Cookie'].split('=', 1)[1])
        assert not a.get_session(other['Cookie'].split('=', 1)[1])
        assert a.get_session(client.cookies['kiehnema-local']).person.two_factor_verified
        assert client.get('/').status_code == 200
        with a.database() as con:
            assert secret.encode() not in con.execute('SELECT totp FROM users WHERE id=?', (person.id,)).fetchone()[0]
        with pytest.raises(ValueError, match='Invalid username'):
            a.authenticate('bob', PASSWORD)
        with pytest.raises(ValueError, match='Wait for a new'):
            a.authenticate('bob', PASSWORD, code)
        # A delayed password-only login must not create a usable session after setup.
        assert client.get('/', headers=headers(person), follow_redirects=False).headers['location'] == '/login'
        now += 30
        assert a.authenticate('bob', PASSWORD, pyotp.TOTP(secret).now()).key == person.key


def test_admin_can_disable_two_factor_and_preserves_workspace_key(people):
    admin, alice, *_ = people
    with TestClient(app, base_url=ORIGIN) as client:
        old = headers(alice)
        credentials = headers(admin)
        response = client.post('/admin/two-factor', headers=credentials, data={'user_id': alice.id}, follow_redirects=False)
        assert response.status_code == 303
        assert not a.two_factor_enabled(alice) and not a.two_factor_required(alice)
        assert not a.get_session(old['Cookie'].split('=', 1)[1])
        assert a.authenticate('alice', PASSWORD).key == alice.key
        assert not a.all_two_factor_required()
        a.require_two_factor(admin, alice.id, True)
        assert a.two_factor_required(alice) and not a.two_factor_enabled(alice)
        a.require_two_factor(admin, alice.id, False)
        assert not a.two_factor_required(alice)


@pytest.mark.parametrize('route', ['/admin/two-factor', '/admin/two-factor-policy'])
def test_policy_changes_require_admin_csrf_and_origin(people, route):
    admin, alice, *_ = people
    with TestClient(app, base_url=ORIGIN) as client:
        credentials = headers(admin)
        payload = {'user_id': alice.id, 'required': '1'}
        assert client.post(route, headers=headers(alice), data=payload).status_code == 403
        assert client.post(route, headers={**credentials, 'X-CSRF-Token': ''}, data=payload).status_code == 403
        assert client.post(route, headers={**credentials, 'Origin': 'https://example.com'}, data=payload).status_code == 403
        with pytest.raises(PermissionError):
            a.require_two_factor(alice, admin.id, False)
        with pytest.raises(PermissionError):
            a.require_all_two_factor(alice, True)
        with pytest.raises(ValueError, match='unavailable'):
            a.require_two_factor(admin, 'missing-user', True)


def test_global_requirement_cannot_be_overridden_and_keeps_existing_authenticators(people):
    admin, alice, *_ = people
    bob, _ = password_only_user(admin)
    a.require_two_factor(admin, bob.id, True)
    a.require_all_two_factor(admin, True)
    with pytest.raises(ValueError, match='all users'):
        a.require_two_factor(admin, alice.id, False)
    assert a.two_factor_enabled(alice)
    a.require_all_two_factor(admin, False)
    assert a.two_factor_required(alice) and a.two_factor_required(bob)
    with pytest.raises(ValueError):
        a.authenticate('alice', PASSWORD)


def test_global_requirement_applies_to_invitations_and_policy_changes_during_setup(people):
    admin = people[0]
    invitation = a.invite_user(admin, 'bob')
    draft = a.enrollment('bob', PASSWORD)
    a.require_all_two_factor(admin, True)
    with pytest.raises(ValueError, match='Start account setup again'):
        a.finish_enrollment(draft, invite=invitation)
    draft = a.enrollment('bob', PASSWORD)
    assert draft['totp']
    with pytest.raises(ValueError, match='authenticator'):
        a.finish_enrollment(draft, invite=invitation)
    assert a.finish_enrollment(draft, pyotp.TOTP(draft['totp']).now(), invite=invitation).two_factor_verified


@pytest.mark.parametrize('global_policy', [False, True])
def test_recovery_respects_new_requirement(people, global_policy):
    person, original = password_only_user(people[0])
    recovery = a.recovery_text(original)
    draft = a.recover('bob', recovery, PASSWORD)
    if global_policy:
        a.require_all_two_factor(people[0], True)
    else:
        a.require_two_factor(people[0], person.id, True)
    with pytest.raises(ValueError, match='Start recovery again'):
        a.finish_recovery(draft)
    draft = a.recover('bob', recovery, PASSWORD)
    assert draft['totp']
    with pytest.raises(ValueError, match='authenticator'):
        a.finish_recovery(draft)
    assert a.finish_recovery(draft, pyotp.TOTP(draft['totp']).now()).key == person.key


def test_existing_database_preserves_authenticator(people):
    with a.database() as con:
        before = dict(con.execute('SELECT * FROM users WHERE id=?', (people[1].id,)).fetchone())
        con.execute('DROP TABLE two_factor_policy')
        con.execute('DROP TABLE security_policy')
    assert not a.all_two_factor_required()
    assert a.two_factor_required(people[1]) and a.two_factor_enabled(people[1])
    with a.database() as con:
        assert dict(con.execute('SELECT * FROM users WHERE id=?', (people[1].id,)).fetchone()) == before


def test_cli_requires_web_setup_when_policy_enabled(people, monkeypatch):
    from app.security import __main__ as cli
    person, _ = password_only_user(people[0])
    monkeypatch.setattr('app.security.workspace.memory_policy', lambda: None)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: PASSWORD)
    monkeypatch.setattr('builtins.input', lambda prompt: 'bob' if prompt == 'Username: ' else '')
    assert cli.unlock().key == person.key
    a.require_two_factor(people[0], person.id, True)
    with pytest.raises(ValueError, match='web app'):
        cli.unlock()
