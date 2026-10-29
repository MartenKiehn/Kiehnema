"""Role boundaries, legacy accounts and session changes with synthetic local accounts."""
import re

import pytest
from fastapi.testclient import TestClient

from app.security import accounts as a
from app.pipeline import jobs
from app.web.main import app
from test_security import people, headers, PASSWORD, ORIGIN


def test_existing_accounts_keep_roles_and_keys(people):
    admin, member, *_ = people
    assert a.refresh(admin).role_label == 'Regisseur'
    assert a.refresh(member).role_label == 'Crew'
    assert not member.can_invite
    original_key = member.key
    a.set_role(admin, member.id, 'manager')
    assert a.refresh(member).role_label == 'Regieassistenz'
    assert member.can_invite and not member.admin and member.key == original_key
    a.set_role(admin, member.id, 'admin')
    assert a.refresh(member).admin
    a.set_role(admin, member.id, 'user')
    assert not a.refresh(member).can_invite and member.key == original_key


def test_manager_can_invite_only_crew_and_cannot_manage_accounts(people):
    admin, manager, *_ = people
    a.set_role(admin, manager.id, 'manager')
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(manager)
        page = web.get('/admin', headers=credentials)
        assert page.status_code == 200
        assert 'Regieassistenz' in page.text and 'Roles &amp; permissions' in page.text
        assert 'action="/admin/role"' not in page.text
        assert 'action="/admin/two-factor-policy"' not in page.text
        assert 'href="/admin/provider"' not in page.text
        for endpoint in ('role', 'two-factor', 'two-factor-policy', 'disable'):
            assert web.post('/admin/' + endpoint, headers=credentials,
                            data={'user_id':admin.id, 'role':'admin', 'required':'1', 'disabled':'1'}).status_code == 403
        for endpoint in ('/admin/provider', '/admin/updates'):
            assert web.get(endpoint, headers=credentials).status_code == 403
        page = web.post('/admin/invite', headers=credentials, data={'username':'new-crew', 'role':'admin'})
        assert page.status_code == 200
        invitation = re.search(r'class="recovery-key"><code>([^<]+)', page.text)[1]
        draft = a.enrollment('new-crew', PASSWORD)
        newcomer = a.finish_enrollment(draft, invite=invitation)
        assert newcomer.role == 'user' and not newcomer.can_invite
        # Membership cannot reveal another user's private videos.
        token = a.current_identity.set(admin)
        try:
            jid = jobs.create_job('https://example.com', 30)
        finally:
            a.current_identity.reset(token)
        assert web.get('/jobs/' + jid, headers=credentials).status_code == 404


def test_role_changes_revoke_sessions_and_stale_objects_cannot_keep_access(people):
    admin, member, *_ = people
    a.set_role(admin, member.id, 'admin')
    a.refresh(member)
    session = a.new_session(member)
    a.set_role(admin, member.id, 'user')
    assert a.get_session(session) is None
    with pytest.raises(PermissionError):
        a.set_role(member, admin.id, 'user')
    with pytest.raises(PermissionError):
        a.invite_user(member, 'forbidden')
    with pytest.raises(PermissionError):
        a.require_all_two_factor(member, True)
    a.set_role(admin, member.id, 'manager')
    a.refresh(member)
    a.set_role(admin, member.id, 'user')
    with pytest.raises(PermissionError):
        a.invite_user(member, 'also-forbidden')


def test_role_form_requires_admin_valid_role_target_csrf_and_origin(people):
    admin, member, *_ = people
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        values = {'user_id':member.id, 'role':'manager'}
        assert web.post('/admin/role', data=values).status_code in (401,403)
        assert web.post('/admin/role', headers={'Cookie':credentials['Cookie'], 'Origin':ORIGIN}, data=values).status_code == 403
        assert web.post('/admin/role', headers=credentials | {'Origin':'https://evil.example'}, data=values).status_code == 403
        assert web.post('/admin/role', headers=headers(member), data=values).status_code == 403
        assert web.post('/admin/role', headers=credentials, data=values, follow_redirects=False).status_code == 303
        assert a.refresh(member).role == 'manager'
        for bad in ({'user_id':member.id, 'role':'root'}, {'user_id':'missing', 'role':'admin'},
                    {'user_id':admin.id, 'role':'user'}):
            page = web.post('/admin/role', headers=credentials, data=bad)
            assert 'role="alert"' in page.text
        assert a.refresh(admin).role == 'admin' and a.refresh(member).role == 'manager'


def test_manager_role_survives_account_recovery_and_two_factor_setup(people):
    import pyotp
    admin, member, _, draft = people
    a.set_role(admin, member.id, 'manager')
    recovery = a.recover(member.username, a.recovery_text(draft), PASSWORD + ' recovered')
    restored = a.finish_recovery(recovery, pyotp.TOTP(recovery['totp']).now())
    assert restored.role == 'manager' and restored.key == member.key
    # A stale recovery draft must not restore a former admin role.
    a.set_role(admin, member.id, 'admin')
    recovery = a.recover(member.username, a.recovery_text(recovery), PASSWORD + ' again')
    a.set_role(admin, member.id, 'user')
    restored = a.finish_recovery(recovery, pyotp.TOTP(recovery['totp']).now())
    assert restored.role == 'user' and not restored.can_invite


@pytest.mark.parametrize('viewer', ['admin', 'manager'])
def test_team_directory_sorts_by_role_then_username_and_updates_after_role_change(people, viewer):
    admin, member, *_ = people
    users = {}
    for name, role in [('zara-admin', 'admin'), ('amy-manager', 'manager'),
                       ('zoe-manager', 'manager'), ('aaron-crew', 'user')]:
        invitation = a.invite_user(admin, name)
        users[name] = a.finish_enrollment(a.enrollment(name, PASSWORD), invite=invitation)
        a.set_role(admin, users[name].id, role)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin if viewer == 'admin' else users['amy-manager'])
        def listed_names():
            page = web.get('/admin', headers=credentials)
            assert page.status_code == 200
            return [name.strip() for name in re.findall(r'<h3>([^<]+)', page.text)]
        assert listed_names() == ['admin', 'zara-admin', 'amy-manager', 'zoe-manager', 'aaron-crew', 'alice']
        a.set_role(admin, users['aaron-crew'].id, 'manager')
        assert listed_names() == ['admin', 'zara-admin', 'aaron-crew', 'amy-manager', 'zoe-manager', 'alice']
