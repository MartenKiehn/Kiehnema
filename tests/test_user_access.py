"""Account suspension revokes access without deleting private workspaces."""

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.pipeline import jobs
from app.security import accounts as a
from app.web import auth
from app.web.main import app
from test_security import people, headers, PASSWORD, ORIGIN


def test_disable_revokes_all_sessions_and_keeps_saved_videos(people):
    admin, member, _, draft = people
    jid = jobs.create_job('https://private.example', 30)
    path = jobs.jobs_dir() / jid
    (path / 'private.txt').write_text('PRIVATE SAVED VIDEO')
    with TestClient(app, base_url=ORIGIN) as web:
        first, second = headers(member), headers(member)
        tokens = [credentials['Cookie'].split('=', 1)[1] for credentials in (first, second)]
        auth.grants['old-preview'] = (a.digest(tokens[0]), jid, a.time.time() + 600)
        director = headers(admin)
        values = {'user_id': member.id, 'disabled': '1'}
        response = web.post('/admin/disable', headers=director, data=values, follow_redirects=False)
        assert response.status_code == 303
        for token, credentials in zip(tokens, (first, second)):
            assert a.digest(token) not in a.sessions
            assert web.get('/account', headers=credentials, follow_redirects=False).status_code == 303
            assert web.get('/jobs/' + jid, headers=credentials).status_code == 401
            assert web.post('/jobs', headers=credentials, data={'url': 'https://example.com'}).status_code == 401
        assert web.get('/preview/' + jid + '/_/old-preview/site/index.html',
                       headers=first).status_code == 401
        with pytest.raises(ValueError):
            a.authenticate(member.username, PASSWORD, pyotp.TOTP(draft['totp']).now())
        with pytest.raises(PermissionError):
            jobs.create_job('https://example.com', 30)
        assert (path / 'private.txt').read_text() == 'PRIVATE SAVED VIDEO'
        with a.database() as con:
            assert con.execute('SELECT COUNT(*) FROM jobs WHERE owner=?', (member.id,)).fetchone()[0] == 1
            assert con.execute('SELECT disabled FROM users WHERE id=?', (member.id,)).fetchone()[0] == 1

        response = web.post('/admin/disable', headers=director,
                            data={'user_id': member.id, 'disabled': '0'}, follow_redirects=False)
        assert response.status_code == 303
        assert all(a.get_session(token) is None for token in tokens)
        restored = a.authenticate(member.username, PASSWORD, pyotp.TOTP(draft['totp']).now())
        assert restored.key == member.key
        assert web.get('/account', headers=headers(restored)).status_code == 200
        assert (path / 'private.txt').read_text() == 'PRIVATE SAVED VIDEO'


@pytest.mark.parametrize('role', ['manager', 'user'])
def test_only_directors_can_disable_or_enable_accounts(people, role):
    admin, member, *_ = people
    if role == 'manager':
        a.set_role(admin, member.id, role)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(member)
        for disabled in ('1', '0'):
            assert web.post('/admin/disable', headers=credentials,
                            data={'user_id': admin.id, 'disabled': disabled}).status_code == 403
        with a.database() as con:
            assert con.execute('SELECT disabled FROM users WHERE id=?', (admin.id,)).fetchone()[0] == 0


@pytest.mark.parametrize('invalid', ['', 'true', '2', 'disable'])
def test_invalid_disable_values_cannot_restore_suspended_accounts(people, invalid):
    admin, member, *_ = people
    a.set_disabled(admin, member.id, True)
    with TestClient(app, base_url=ORIGIN) as web:
        page = web.post('/admin/disable', headers=headers(admin),
                        data={'user_id': member.id, 'disabled': invalid})
        assert 'role="alert"' in page.text
        with a.database() as con:
            assert con.execute('SELECT disabled FROM users WHERE id=?', (member.id,)).fetchone()[0] == 1


def test_disable_requires_valid_target_and_protects_current_director(people):
    admin, member, *_ = people
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        for target in ('missing', admin.id):
            page = web.post('/admin/disable', headers=credentials,
                            data={'user_id': target, 'disabled': '1'})
            assert 'role="alert"' in page.text
        assert a.refresh(admin).admin
        assert a.refresh(member).id == member.id


def test_disable_requires_authentication_csrf_and_same_origin(people):
    admin, member, *_ = people
    values = {'user_id': member.id, 'disabled': '1'}
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.post('/admin/disable', data=values).status_code in (401, 403)
        credentials = headers(admin)
        for invalid in ({'Cookie': credentials['Cookie'], 'Origin': ORIGIN},
                        credentials | {'Origin': 'https://other.example'}):
            assert web.post('/admin/disable', headers=invalid, data=values).status_code == 403
        assert a.refresh(member).id == member.id


@pytest.mark.parametrize('change', ['disabled=1', 'admin=0'])
def test_stale_director_cannot_change_account_access(people, change):
    admin, member, *_ = people
    with a.database() as con:
        con.execute('UPDATE users SET ' + change + ' WHERE id=?', (admin.id,))
    assert admin.admin  # The object intentionally predates the permission change.
    with pytest.raises(PermissionError):
        a.set_disabled(admin, member.id, True)
    assert a.refresh(member).id == member.id
