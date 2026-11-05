"""One release-note source for the release pipeline and every workspace member."""

import pytest
from fastapi.testclient import TestClient

from app import changelog
from app.security import accounts
from app.updates import VERSION
from app.web.main import app
from tests.test_security import people, headers, ORIGIN


def test_notes_are_selected_by_exact_version_and_sorted_numerically(tmp_path):
    source = tmp_path / 'CHANGELOG.md'
    source.write_text('# Changes\n\n## 0.2.0\n\n- First change.\n\n- Another change.\n\n## 0.10.0\n- Newer change.\n')
    entries = changelog.read_entries(source)
    assert [entry['version'] for entry in entries] == ['0.10.0', '0.2.0']
    assert entries[1]['changes'] == ['First change.', 'Another change.']
    assert changelog.release_notes('0.2.0', source) == '- First change.\n- Another change.'
    with pytest.raises(ValueError, match='Missing CHANGELOG.md entry for version 0.2.1'):
        changelog.release_notes('0.2.1', source)


@pytest.mark.parametrize(('content', 'message'), [
    ('## 0.2.0\n- One.\n## 0.2.0\n- Two.', 'Duplicate'),
    ('## 0.2.0\n\n', '1–16000 characters'),
    ('## 0.2.0\n- ' + 'x' * 16000, '1–16000 characters'),
    ('## v0.2.0\n- Change.', 'Invalid'),
    ('## 01.2.0\n- Change.', 'Invalid'),
    ('## 0.2.0\nNot a bullet.', 'non-empty'),
    ('## 0.2.0\n- ', 'non-empty'),
])
def test_invalid_change_logs_fail_with_an_actionable_error(tmp_path, content, message):
    source = tmp_path / 'CHANGELOG.md'
    source.write_text(content)
    with pytest.raises(ValueError, match=message):
        changelog.release_notes('0.2.0', source)


def test_missing_file_and_missing_version_fail_in_ci(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'CHANGELOG.md'
    monkeypatch.setattr(changelog, 'CHANGELOG', source)
    monkeypatch.setattr('sys.argv', ['changelog', '--version', '0.2.0'])
    with pytest.raises(SystemExit) as failure:
        changelog.main()
    assert failure.value.code == 1
    assert '::error::CHANGELOG.md is missing' in capsys.readouterr().err
    source.write_text('## 0.1.0\n- Earlier change.\n')
    with pytest.raises(SystemExit) as failure:
        changelog.main()
    assert failure.value.code == 1
    error = capsys.readouterr().err
    assert '::error::Missing CHANGELOG.md entry for version 0.2.0' in error
    assert '## 0.2.0' in error


def test_current_project_version_has_release_notes():
    assert changelog.release_notes(VERSION)


@pytest.mark.parametrize('role', ['admin', 'manager', 'user'])
def test_change_log_is_visible_to_all_members_and_hides_future_entries(people, tmp_path, monkeypatch, role):
    source = tmp_path / 'CHANGELOG.md'
    source.write_text(f'## 99.0.0\n- Not released here.\n## {VERSION}\n- A visible change.\n- <script>bad()</script>\n## 0.0.1\n- An earlier change.\n')
    monkeypatch.setattr(changelog, 'CHANGELOG', source)
    admin, member, *_ = people
    if role == 'manager':
        accounts.set_role(admin, member.id, 'manager')
    with TestClient(app, base_url=ORIGIN) as web:
        credentials = headers(admin if role == 'admin' else member)
        home = web.get('/', headers=credentials)
        assert 'href="/changelog"' in home.text
        page = web.get('/changelog', headers=credentials)
        assert page.status_code == 200
        assert page.headers['cache-control'] == 'no-store'
        assert 'What’s new' in page.text and 'A visible change.' in page.text
        assert 'An earlier change.' in page.text
        assert 'Not released here.' not in page.text and '99.0.0' not in page.text
        assert '<span class="badge">Installed</span>' in page.text
        assert '<li>&lt;script&gt;bad()&lt;/script&gt;</li>' in page.text
        assert 'href="/changelog" aria-current="page"' in page.text
        assert page.text.index('A visible change.') < page.text.index('An earlier change.')
        assert 'action="/admin/updates/install"' not in page.text


def test_change_log_requires_sign_in(people):
    with TestClient(app, base_url=ORIGIN) as web:
        page = web.get('/changelog', follow_redirects=False)
        assert page.status_code == 303 and page.headers['location'] == '/login'
        assert 'href="/changelog"' not in web.get('/login').text
