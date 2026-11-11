"""Invitation lifecycle with synthetic accounts and no provider calls."""

import re

import pytest
from fastapi.testclient import TestClient

from app.security import accounts as a
from app.web.main import app
from tests.test_security import people, headers, csrf, PASSWORD, ORIGIN


def test_invitation_overview_expiry_renewal_and_acceptance(people):
    admin, *_ = people
    old = a.invite_user(admin, 'new-crew')
    expired = a.invite_user(admin, 'expired-crew')
    with a.database() as con:
        expires = con.execute('SELECT expires FROM invites WHERE token=?', (a.digest(old),)).fetchone()[0]
        assert 86390 < expires - a.time.time() <= 86400
        con.execute('UPDATE invites SET expires=? WHERE token=?', (a.time.time() - 1, a.digest(expired)))
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        page = web.get('/admin', headers=credentials)
        assert '1 pending invitation' in page.text
        assert 'new-crew' in page.text and 'expired-crew' in page.text
        assert '>Pending</span>' in page.text and '>Expired</span>' in page.text
        assert 'UTC</time>' in page.text and '2 active' in page.text
        assert old not in page.text and expired not in page.text
        page = web.post('/admin/invite', headers=credentials, data={'username': 'new-crew'})
        renewed = re.search(r'class="recovery-key"><code>([^<]+)', page.text)[1]
        draft = a.enrollment('new-crew', PASSWORD)
        with pytest.raises(ValueError, match='invalid or expired'):
            a.finish_enrollment(draft, invite=old)
        a.finish_enrollment(draft, invite=renewed)
        with pytest.raises(ValueError, match='invalid or expired'):
            a.finish_enrollment(draft, invite=renewed)
        page = web.get('/admin', headers=credentials)
        assert '0 pending invitations' in page.text and '3 active' in page.text
        assert 'Renew invitation for new-crew' not in page.text
        assert 'new-crew' in page.text


@pytest.mark.parametrize('invalidate', ['expire', 'revoke', 'renew'])
def test_invalidated_invitation_cannot_finish_started_enrollment(people, invalidate):
    admin, *_ = people
    invitation = a.invite_user(admin, 'onboarding')
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as web:
        page = web.get('/activate')
        token = csrf(page)
        page = web.post('/activate', data={'csrf': token, 'invitation': invitation, 'password': PASSWORD})
        proof = re.search(r'name="proof" value="([^"]+)"', page.text)[1]
        if invalidate == 'expire':
            with a.database() as con:
                con.execute('UPDATE invites SET expires=? WHERE token=?', (a.time.time() - 1, a.digest(invitation)))
        elif invalidate == 'revoke':
            response = web.post('/admin/invite/revoke', headers=headers(admin),
                                data={'token': a.digest(invitation)}, follow_redirects=False)
            assert response.status_code == 303
        else:
            a.invite_user(admin, 'onboarding')
        page = web.post('/activate', data={'csrf': token, 'proof': proof})
        assert 'This invitation is invalid or expired.' in page.text
        with a.database() as con:
            assert not con.execute('SELECT 1 FROM users WHERE username=?', ('onboarding',)).fetchone()
        page = web.post('/activate', data={'csrf': token, 'invitation': invitation, 'password': PASSWORD})
        assert 'Invalid or expired invitation.' in page.text


@pytest.mark.parametrize('role', ['admin', 'manager', 'user'])
def test_invitation_directory_and_revocation_permissions(people, role):
    admin, member, *_ = people
    if role == 'manager':
        a.set_role(admin, member.id, role)
    person = admin if role == 'admin' else member
    invitation = a.invite_user(admin, 'invited-crew')
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(person)
        page = web.get('/admin', headers=credentials)
        assert page.status_code == (403 if role == 'user' else 200)
        assert ('action="/admin/invite/revoke"' in page.text) == (role == 'admin')
        response = web.post('/admin/invite/revoke', headers=credentials,
                            data={'token': a.digest(invitation)}, follow_redirects=False)
        assert response.status_code == (303 if role == 'admin' else 403)
        assert bool(a.invitations(admin)) == (role != 'admin')


def test_revoke_requires_csrf_origin_and_current_admin_and_preserves_new_invitation(people):
    admin, *_ = people
    old = a.invite_user(admin, 'invited-crew')
    replacement = a.invite_user(admin, 'invited-crew')
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        values = {'token': a.digest(replacement)}
        assert web.post('/admin/invite/revoke', data=values).status_code in (401, 403)
        for invalid in ({'Cookie': credentials['Cookie'], 'Origin': ORIGIN},
                        credentials | {'Origin': 'https://other.example'}):
            assert web.post('/admin/invite/revoke', headers=invalid, data=values).status_code == 403
        assert web.post('/admin/invite/revoke', headers=credentials,
                        data={'token': a.digest(old)}, follow_redirects=False).status_code == 303
        assert a.invitations(admin)[0]['token'] == a.digest(replacement)
        with a.database() as con:
            con.execute('UPDATE users SET admin=0 WHERE id=?', (admin.id,))
        with pytest.raises(PermissionError):
            a.revoke_invitation(admin, a.digest(replacement))
        with pytest.raises(PermissionError):
            a.invitations(admin)
