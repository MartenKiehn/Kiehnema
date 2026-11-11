"""Administrative account events share the transaction of the change they describe."""

import pytest

from app.pipeline import jobs
from app.security import accounts as a, audit
from tests.test_security import people, PASSWORD


@pytest.fixture
def audited_people(people):
    with a.database() as con:
        con.execute('DELETE FROM audit_events')
    return people


def events():
    with a.database() as con:
        return [dict(row) for row in con.execute('SELECT * FROM audit_events ORDER BY id')]


def test_roles_and_access_log_actual_changes_and_keep_name_snapshots(audited_people):
    admin, member, *_ = audited_people
    a.set_role(admin, member.id, 'manager')
    a.set_role(admin, member.id, 'manager')
    a.set_disabled(admin, member.id, True)
    a.set_disabled(admin, member.id, True)
    a.set_disabled(admin, member.id, False)
    a.set_disabled(admin, member.id, False)
    rows = events()
    assert [row['action'] for row in rows] == ['team.role_changed', 'team.account_disabled', 'team.account_enabled']
    assert rows[0]['detail'] == 'Crew → Assistant director'
    assert all(row['actor_name'] == 'admin' and row['target'] == 'alice' and row['result'] == 'saved' for row in rows)
    assert all(row['occurred_at'] <= a.time.time() for row in rows)
    with a.database() as con:
        con.execute("UPDATE users SET username='renamed-director' WHERE id=?", (admin.id,))
        con.execute("UPDATE users SET username='renamed-crew' WHERE id=?", (member.id,))
    a.set_role(admin, member.id, 'user')
    assert events()[:3] == rows
    assert events()[-1]['actor_name'] == 'renamed-director'
    assert events()[-1]['target'] == 'renamed-crew'


def test_invitation_events_include_assistants_but_never_codes(audited_people):
    admin, member, *_ = audited_people
    a.set_role(admin, member.id, 'manager')
    first = a.invite_user(member, 'new-crew')
    second = a.invite_user(member, 'new-crew')
    with pytest.raises(PermissionError):
        a.revoke_invitation(member, a.digest(second))
    a.revoke_invitation(admin, a.digest(first))
    a.revoke_invitation(admin, a.digest(second))
    a.revoke_invitation(admin, a.digest(second))
    rows = events()[1:]
    assert [row['action'] for row in rows] == ['team.invite_created', 'team.invite_renewed', 'team.invite_revoked']
    assert [row['actor_name'] for row in rows] == ['alice', 'alice', 'admin']
    assert all(row['target'] == 'new-crew' for row in rows)
    for secret in (first, second, a.digest(first), a.digest(second), PASSWORD):
        assert secret not in str(rows)


def test_security_events_distinguish_policy_and_authenticator_removal(audited_people):
    admin, member, _, draft = audited_people
    a.require_all_two_factor(admin, False)
    a.require_all_two_factor(admin, True)
    a.require_all_two_factor(admin, True)
    with pytest.raises(ValueError):
        a.require_two_factor(admin, member.id, False)
    a.require_all_two_factor(admin, False)
    a.require_two_factor(admin, member.id, True)
    a.require_two_factor(admin, member.id, True)
    a.require_two_factor(admin, member.id, False)
    a.require_two_factor(admin, member.id, False)
    rows = events()
    assert [row['action'] for row in rows] == ['security.policy_changed'] * 2 + ['security.two_factor_changed'] * 2
    assert [row['target'] for row in rows] == ['All accounts', 'All accounts', 'alice', 'alice']
    assert [row['detail'] for row in rows] == [
        'Two-factor authentication required', 'Two-factor authentication optional',
        'Two-factor authentication required', 'Two-factor authentication disabled']
    assert draft['totp'] not in str(rows)
    assert a.recovery_text(draft) not in str(rows)


def test_deletion_preserves_events_for_deleted_target_and_actor(audited_people):
    admin, member, *_ = audited_people
    a.set_role(admin, member.id, 'admin')
    token = a.invite_user(member, 'future-crew')
    a.set_disabled(admin, member.id, True)
    a.delete_user(admin, member.id, member.username)
    rows = events()
    assert [row['action'] for row in rows] == [
        'team.role_changed', 'team.invite_created', 'team.account_disabled', 'team.account_deleted']
    assert rows[1]['actor_id'] == member.id and rows[1]['actor_name'] == 'alice'
    assert rows[-1]['target'] == 'alice'
    assert token not in str(rows)


@pytest.mark.parametrize('operation', ['role', 'disable', 'policy', 'factor', 'invite', 'renew', 'revoke', 'delete'])
def test_audit_failure_rolls_back_account_change(audited_people, monkeypatch, operation):
    admin, member, *_ = audited_people
    invitation = a.invite_user(admin, 'pending-crew')
    jid = jobs.create_job('https://private.example', 30)
    path = jobs.jobs_dir() / jid
    (path / 'private.txt').write_text('PRIVATE CONTENT')
    if operation == 'delete':
        a.set_disabled(admin, member.id, True)
    tables = ('users', 'invites', 'invitation_managers', 'security_policy', 'two_factor_policy', 'audit_events', 'jobs')
    with a.database() as con:
        before = {table: [tuple(row) for row in con.execute('SELECT * FROM ' + table)] for table in tables}
    original = audit.record
    def fail_record(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('Synthetic audit storage failure')
    monkeypatch.setattr(audit, 'record', fail_record)
    operations = {
        'role': lambda: a.set_role(admin, member.id, 'manager'),
        'disable': lambda: a.set_disabled(admin, member.id, True),
        'policy': lambda: a.require_all_two_factor(admin, True),
        'factor': lambda: a.require_two_factor(admin, member.id, False),
        'invite': lambda: a.invite_user(admin, 'new-crew'),
        'renew': lambda: a.invite_user(admin, 'pending-crew'),
        'revoke': lambda: a.revoke_invitation(admin, a.digest(invitation)),
        'delete': lambda: a.delete_user(admin, member.id, member.username),
    }
    with pytest.raises(OSError, match='Synthetic audit storage failure'):
        operations[operation]()
    with a.database() as con:
        assert {table: [tuple(row) for row in con.execute('SELECT * FROM ' + table)] for table in tables} == before
    assert (path / 'private.txt').read_text() == 'PRIVATE CONTENT'


def test_rejected_administrative_changes_do_not_log_success(audited_people):
    admin, member, *_ = audited_people
    for operation in (
        lambda: a.set_role(member, admin.id, 'user'),
        lambda: a.set_disabled(member, admin.id, True),
        lambda: a.invite_user(member, 'forbidden'),
        lambda: a.require_all_two_factor(member, True),
        lambda: a.require_two_factor(member, admin.id, False),
    ):
        with pytest.raises(PermissionError):
            operation()
    with pytest.raises(ValueError):
        a.set_role(admin, member.id, 'invalid')
    with pytest.raises(ValueError):
        a.delete_user(admin, member.id, member.username)
    assert events() == []
