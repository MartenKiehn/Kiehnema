"""Permanent account deletion uses synthetic users and real encrypted job files."""

import pytest
from fastapi.testclient import TestClient

from app.pipeline import jobs
from app.security import accounts as a
from app.web import auth
from app.web.main import app
from tests.test_security import people, headers, PASSWORD, ORIGIN


def new_person(admin, name, role='user'):
    person = a.finish_enrollment(a.enrollment(name, PASSWORD), invite=a.invite_user(admin, name))
    if role != 'user':
        a.set_role(admin, person.id, role)
    return a.refresh(person)


def saved_job(person, text):
    token = a.current_identity.set(person)
    try:
        jid = jobs.create_job('https://private.example', 30)
        path = jobs.jobs_dir() / jid
        (path / 'private.txt').write_text(text)
        return path
    finally:
        a.current_identity.reset(token)


def disable(person):
    with a.database() as con:
        con.execute('UPDATE users SET disabled=1 WHERE id=?', (person.id,))


def exists(person):
    with a.database() as con:
        return con.execute('SELECT 1 FROM users WHERE id=?', (person.id,)).fetchone() is not None


@pytest.mark.parametrize('viewer_role', ['admin', 'manager', 'user'])
@pytest.mark.parametrize('target_role', ['admin', 'manager', 'user'])
def test_delete_permissions_for_every_viewer_and_target_role(people, viewer_role, target_role):
    admin, member, *_ = people
    if viewer_role == 'manager':
        a.set_role(admin, member.id, 'manager')
    viewer = admin if viewer_role == 'admin' else member
    target = new_person(admin, 'delete-' + target_role, target_role)
    a.require_two_factor(admin, target.id, True)
    target_job = saved_job(target, 'TARGET PRIVATE CONTENT')
    survivor_job = saved_job(admin, 'SURVIVING PRIVATE CONTENT')
    target_token = a.new_session(target)
    disable(target)

    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(viewer)
        page = web.get('/admin/delete/' + target.id, headers=credentials)
        if viewer_role == 'admin':
            assert page.status_code == 200
            assert target.username in page.text and 'name="confirmation"' in page.text
            assert 'TARGET PRIVATE CONTENT' not in page.text
        else:
            assert page.status_code == 403
        response = web.post('/admin/delete', headers=credentials,
                            data={'user_id': target.id, 'confirmation': target.username}, follow_redirects=False)
        if viewer_role != 'admin':
            assert response.status_code == 403
            assert exists(target) and target_job.job.directory.exists()
        else:
            assert response.status_code == 303 and response.headers['location'] == '/admin'
            assert not exists(target) and not target_job.job.directory.exists()
            assert a.digest(target_token) not in a.sessions
            with a.database() as con:
                assert not con.execute('SELECT 1 FROM jobs WHERE owner=?', (target.id,)).fetchone()
                assert not con.execute('SELECT 1 FROM files WHERE job=?', (target_job.job.id,)).fetchone()
                assert not con.execute('SELECT 1 FROM invitation_managers WHERE user_id=?', (target.id,)).fetchone()
                assert not con.execute('SELECT 1 FROM two_factor_policy WHERE user_id=?', (target.id,)).fetchone()
            with pytest.raises(ValueError):
                a.authenticate(target.username, PASSWORD)
        assert exists(admin) and exists(member)
        assert (survivor_job / 'private.txt').read_text() == 'SURVIVING PRIVATE CONTENT'


@pytest.mark.parametrize('viewer_role', ['admin', 'manager', 'user'])
@pytest.mark.parametrize('disabled', [False, True])
def test_delete_link_is_only_shown_to_admin_for_disabled_other_users(people, viewer_role, disabled):
    admin, member, *_ = people
    if viewer_role == 'manager':
        a.set_role(admin, member.id, 'manager')
    target = new_person(admin, 'delete-visible')
    if disabled:
        disable(target)
    with TestClient(app, base_url=ORIGIN) as web:
        page = web.get('/admin', headers=headers(admin if viewer_role == 'admin' else member))
        if viewer_role == 'user':
            assert page.status_code == 403
        else:
            assert page.status_code == 200
            assert ('href="/admin/delete/' + target.id + '"' in page.text) == (viewer_role == 'admin' and disabled)
            assert 'href="/admin/delete/' + admin.id + '"' not in page.text


def test_active_target_and_self_deletion_are_rejected(people):
    admin, member, *_ = people
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        for person in (admin, member):
            assert web.get('/admin/delete/' + person.id, headers=credentials).status_code in (404, 409)
            assert web.post('/admin/delete', headers=credentials,
                            data={'user_id': person.id, 'confirmation': person.username}).status_code in (404, 409)
            assert exists(person)


@pytest.mark.parametrize('confirmation', ['', 'wrong-name', 'ALICE', ' alice '])
def test_delete_requires_exact_username_confirmation(people, confirmation):
    admin, target, *_ = people
    target_job = saved_job(target, 'KEEP THIS FILE')
    disable(target)
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/admin/delete', headers=headers(admin),
                            data={'user_id': target.id, 'confirmation': confirmation})
        assert response.status_code in (409, 422)
        assert exists(target) and (target_job / 'private.txt').read_text() == 'KEEP THIS FILE'


def test_delete_requires_authentication_csrf_and_same_origin(people):
    admin, target, *_ = people
    disable(target)
    values = {'user_id': target.id, 'confirmation': target.username}
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.get('/admin/delete/' + target.id, follow_redirects=False).status_code == 303
        assert web.post('/admin/delete', data=values).status_code in (401, 403)
        credentials = headers(admin)
        for invalid in ({'Cookie': credentials['Cookie'], 'Origin': ORIGIN},
                        credentials | {'Origin': 'https://other.example'}):
            assert web.post('/admin/delete', headers=invalid, data=values).status_code == 403
            assert exists(target)


def test_active_jobs_block_deletion_and_confirmation_get_is_read_only(people):
    admin, target, *_ = people
    target_job = saved_job(target, 'KEEP THE ACTIVE JOB')
    disable(target)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        page = web.get('/admin/delete/' + target.id, headers=credentials)
        assert page.status_code == 200 and exists(target)
        assert (target_job / 'private.txt').read_text() == 'KEEP THE ACTIVE JOB'
        with a.database() as con:
            con.execute('UPDATE jobs SET active=2 WHERE id=?', (target_job.job.id,))
        response = web.post('/admin/delete', headers=credentials,
                            data={'user_id': target.id, 'confirmation': target.username})
        assert response.status_code == 409
        assert exists(target) and (target_job / 'private.txt').read_text() == 'KEEP THE ACTIVE JOB'


def test_missing_user_and_reenabled_user_are_not_deleted(people):
    admin, target, *_ = people
    disable(target)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        assert web.get('/admin/delete/' + target.id, headers=credentials).status_code == 200
        with a.database() as con:
            con.execute('UPDATE users SET disabled=0 WHERE id=?', (target.id,))
        assert web.post('/admin/delete', headers=credentials,
                        data={'user_id': target.id, 'confirmation': target.username}).status_code == 409
        assert web.get('/admin/delete/missing', headers=credentials).status_code == 404
        assert web.post('/admin/delete', headers=credentials,
                        data={'user_id': 'missing', 'confirmation': target.username}).status_code == 404
        assert exists(target)


def test_job_directory_symlink_cannot_delete_unrelated_files(people, tmp_path):
    admin, target, *_ = people
    target_job = saved_job(target, 'TARGET CONTENT')
    directory = target_job.job.directory
    directory.rename(tmp_path / 'saved-target-objects')
    outside = tmp_path / 'unrelated-directory'
    outside.mkdir()
    protected = outside / 'keep.txt'
    protected.write_text('DO NOT DELETE THIS FILE')
    directory.symlink_to(outside, target_is_directory=True)
    disable(target)
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/admin/delete', headers=headers(admin),
                            data={'user_id': target.id, 'confirmation': target.username})
        assert response.status_code == 409
        assert exists(target)
        assert protected.read_text() == 'DO NOT DELETE THIS FILE'


@pytest.mark.parametrize('storage_error', [OSError, PermissionError])
def test_file_removal_failure_keeps_disabled_account_and_allows_retry(people, monkeypatch, storage_error):
    admin, target, *_ = people
    target_job = saved_job(target, 'TARGET CONTENT')
    survivor_job = saved_job(admin, 'UNRELATED CONTENT')
    disable(target)
    def cannot_remove(*args, **kwargs):
        raise storage_error('Synthetic storage failure')
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        values = {'user_id': target.id, 'confirmation': target.username}
        with monkeypatch.context() as patch:
            patch.setattr('app.security.accounts.shutil.rmtree', cannot_remove)
            assert web.post('/admin/delete', headers=credentials, data=values).status_code == 409
        with a.database() as con:
            assert con.execute('SELECT disabled FROM users WHERE id=?', (target.id,)).fetchone()[0] == 1
        assert (survivor_job / 'private.txt').read_text() == 'UNRELATED CONTENT'
        response = web.post('/admin/delete', headers=credentials, data=values, follow_redirects=False)
        assert response.status_code == 303
        assert not exists(target) and not target_job.job.directory.exists()
        assert (survivor_job / 'private.txt').read_text() == 'UNRELATED CONTENT'


def test_in_memory_task_blocks_deletion_even_when_database_job_is_idle(people):
    admin, target, *_ = people
    target_job = saved_job(target, 'KEEP THE SCHEDULED JOB')
    disable(target)
    with TestClient(app, base_url=ORIGIN) as web:
        with a.database() as con:
            assert con.execute('SELECT active FROM jobs WHERE id=?', (target_job.job.id,)).fetchone()[0] == 0
        app.state.tasks[target_job.job.id] = object()
        try:
            response = web.post('/admin/delete', headers=headers(admin),
                                data={'user_id': target.id, 'confirmation': target.username})
            assert response.status_code == 409
            assert exists(target)
            assert (target_job / 'private.txt').read_text() == 'KEEP THE SCHEDULED JOB'
        finally:
            app.state.tasks.pop(target_job.job.id, None)


def test_admin_rights_revoked_immediately_before_deletion_are_rechecked(people, monkeypatch):
    admin, target, *_ = people
    target_job = saved_job(target, 'KEEP THE TARGET CONTENT')
    disable(target)
    original = a.delete_user
    def revoked_before_delete(person, uid, confirmation):
        with a.database() as con:
            con.execute('UPDATE users SET admin=0 WHERE id=?', (person.id,))
        return original(person, uid, confirmation)
    monkeypatch.setattr(a, 'delete_user', revoked_before_delete)
    with TestClient(app, base_url=ORIGIN) as web:
        response = web.post('/admin/delete', headers=headers(admin),
                            data={'user_id': target.id, 'confirmation': target.username})
        assert response.status_code == 403
        assert exists(target) and (target_job / 'private.txt').read_text() == 'KEEP THE TARGET CONTENT'


def test_delete_removes_only_target_recovery_preview_and_interruption_state(people):
    admin, target, admin_draft, target_draft = people
    target_job = saved_job(target, 'TARGET CONTENT')
    survivor_job = saved_job(admin, 'UNRELATED CONTENT')
    target_recovery = a.recover(target.username, a.recovery_text(target_draft), PASSWORD + ' target recovery')
    survivor_recovery = a.recover(admin.username, a.recovery_text(admin_draft), PASSWORD + ' admin recovery')
    target_token = a.new_session(target)
    disable(target)
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        admin_token = credentials['Cookie'].split('=', 1)[1]
        expires = a.time.time() + 600
        auth.pending['target-recovery'] = {'draft': target_recovery, 'recovery': True, 'expires': expires}
        auth.pending['survivor-recovery'] = {'draft': survivor_recovery, 'recovery': True, 'expires': expires}
        auth.grants['target-preview'] = (a.digest(target_token), target_job.job.id, expires)
        auth.grants['survivor-preview'] = (a.digest(admin_token), survivor_job.job.id, expires)
        app.state.interrupted.update([target_job.job.id, survivor_job.job.id])
        response = web.post('/admin/delete', headers=credentials,
                            data={'user_id': target.id, 'confirmation': target.username}, follow_redirects=False)
        assert response.status_code == 303
        assert 'target-recovery' not in auth.pending and 'survivor-recovery' in auth.pending
        assert 'target-preview' not in auth.grants and 'survivor-preview' in auth.grants
        assert target_job.job.id not in app.state.interrupted
        assert survivor_job.job.id in app.state.interrupted
        assert a.digest(target_token) not in a.sessions and a.digest(admin_token) in a.sessions
        assert exists(admin) and (survivor_job / 'private.txt').read_text() == 'UNRELATED CONTENT'
