"""Director-only access, safe rendering and pagination of administrative history."""

import re

import pytest
from fastapi.testclient import TestClient

from app.security import accounts as a, audit
from app.web.main import app
from tests.test_security import people, headers, ORIGIN


@pytest.mark.parametrize('role', ['admin', 'manager', 'user'])
def test_activity_is_only_visible_to_current_directors(people, role):
    admin, member, *_ = people
    if role == 'manager':
        a.set_role(admin, member.id, role)
    person = admin if role == 'admin' else member
    a.set_disabled(admin, member.id, True)
    a.set_disabled(admin, member.id, False)
    with TestClient(app, base_url=ORIGIN) as web:
        assert web.get('/admin/activity', follow_redirects=False).status_code == 303
        credentials = headers(person)
        page = web.get('/admin/activity', headers=credentials)
        assert page.status_code == (200 if role == 'admin' else 403)
        assert page.headers['cache-control'] == 'no-store'
        assert ('Disabled account' in page.text) == (role == 'admin')
        home = web.get('/admin' if role != 'user' else '/', headers=credentials)
        assert ('href="/admin/activity"' in home.text) == (role == 'admin')
        context = a.current_identity.set(person)
        try:
            if role == 'admin':
                assert audit.entries()
            else:
                with pytest.raises(PermissionError):
                    audit.entries()
        finally:
            a.current_identity.reset(context)
        # A bookmark and an existing session cannot preserve withdrawn privileges.
        if role == 'admin':
            with a.database() as con:
                con.execute('UPDATE users SET admin=0 WHERE id=?', (person.id,))
            assert web.get('/admin/activity', headers=credentials).status_code == 403


def test_activity_filters_pagination_and_empty_states(people):
    admin, *_ = people
    with a.database() as con:
        con.execute('DELETE FROM audit_events')
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        assert 'No activity recorded yet.' in web.get('/admin/activity', headers=credentials).text
        for i in range(51):
            audit.record(admin, 'team.invite_created', target=f'member-{i:02}')
        audit.record(admin, 'security.policy_changed', target='All accounts', detail='Two-factor authentication required')
        audit.record(admin, 'provider.configuration_saved')
        event = audit.record(admin, 'updates.install_requested', detail='0.3.0', result='requested')
        audit.finish(event, 'accepted')
        first = web.get('/admin/activity?category=team&per_page=50', headers=credentials)
        second = web.get('/admin/activity?category=team&page=2&per_page=50', headers=credentials)
        assert first.text.count('<time datetime=') == 50
        assert second.text.count('<time datetime=') == 1
        assert 'member-50' in first.text and 'member-00' not in first.text
        assert 'member-00' in second.text and 'member-50' not in second.text
        assert 'category=team&amp;page=2' in first.text
        assert 'category=team&amp;page=1' in second.text
        assert 'rel="next"' not in second.text
        for category in ('security', 'provider', 'updates'):
            page = web.get('/admin/activity?category=' + category, headers=credentials)
            assert page.status_code == 200 and page.text.count('<time datetime=') == 1
            assert 'member-50' not in page.text
        latest = web.get('/admin/activity', headers=credentials)
        assert latest.text.index('Requested update installation') < latest.text.index('Updated AI configuration')
        assert 'No more activity.' in web.get('/admin/activity?page=3&per_page=50', headers=credentials).text
        for query in ('page=0', 'page=1000001', 'category=secret', 'category=team%27',
                      'per_page=0', 'per_page=10', 'per_page=51', 'per_page=abc'):
            assert web.get('/admin/activity?' + query, headers=credentials).status_code == 422
        for method in ('post', 'delete', 'patch'):
            assert getattr(web, method)('/admin/activity', headers=credentials).status_code == 405


@pytest.mark.parametrize('size', [5, 25, 50])
def test_activity_page_sizes_keep_filters_and_navigation_boundaries(people, size):
    admin, *_ = people
    with a.database() as con:
        con.execute('DELETE FROM audit_events')
    for i in range(51):
        audit.record(admin, 'team.invite_created', target=f'row-{i:02}')
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin)
        assert web.get('/admin/activity', headers=credentials).text.count('<time datetime=') == 25
        base = f'/admin/activity?category=team&per_page={size}'
        first = web.get(base, headers=credentials).text
        assert first.count('<time datetime=') == size
        assert 'rel="prev"' not in first and 'disabled>← Previous' in first
        assert f'page=2&amp;per_page={size}" rel="next"' in first
        assert f'category=security&amp;per_page={size}' in first
        assert f'per_page={size}" aria-current="true"' in first
        second = web.get(base + '&page=2', headers=credentials).text
        assert second.count('<time datetime=') == min(size, 51 - size)
        assert f'row-{50-size:02}' in second and 'row-50' not in second
        assert f'page=1&amp;per_page={size}" rel="prev"' in second
        last = web.get(base + f'&page={51 // size + 1}', headers=credentials).text
        assert last.count('<time datetime=') == 1
        assert 'rel="next"' not in last and 'disabled>Next →' in last
        sizes = re.search(r'<nav class="activity-size-options".*?</nav>', second, re.S)[0]
        assert 'page=' not in sizes.replace('per_page=', '')
        assert all(f'category=team&amp;per_page={value}' in sizes for value in (5, 25, 50))
        empty = web.get(base + f'&page={51 // size + 2}', headers=credentials).text
        assert 'No more activity.' in empty and 'rel="next"' not in empty


def test_activity_escapes_metadata_and_has_no_edit_actions(people):
    admin, *_ = people
    audit.record(admin, 'team.invite_created', target='<script>alert(1)</script>')
    with TestClient(app, base_url=ORIGIN) as web:
        page = web.get('/admin/activity', headers=headers(admin))
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page.text
        assert '<script>alert(1)</script>' not in page.text
        content = re.search(r'<section class="activity-log".*?</section>', page.text, re.S)[0]
        assert '<form' not in content and 'Read only' in content
        assert 'No passwords, API keys, invitation codes or video content.' in page.text


def test_audit_table_is_added_to_existing_database_without_backfilling(people):
    admin, *_ = people
    with a.database() as con:
        con.execute('DROP TABLE audit_events')
    with a.database() as con:
        assert con.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0] == 0
        assert con.execute('SELECT username FROM users WHERE id=?', (admin.id,)).fetchone()[0] == admin.username
    audit.record(admin, 'team.invite_created', target='new-member')
    with a.database() as con:
        assert con.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0] == 1
