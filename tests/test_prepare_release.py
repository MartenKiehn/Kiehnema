import subprocess
import tomllib

import pytest

from scripts import prepare_release


@pytest.fixture
def repository(tmp_path, monkeypatch):
    remote, root = tmp_path / 'remote.git', tmp_path / 'checkout'
    subprocess.run(['git', 'init', '-q', '--bare', '--initial-branch=main', str(remote)], check=True)
    subprocess.run(['git', 'clone', '-q', str(remote), str(root)], check=True)
    monkeypatch.chdir(root)
    for name, value in [('user.name', 'Test'), ('user.email', 'test@example.invalid'), ('commit.gpgsign', 'false')]:
        prepare_release.git('config', name, value)
    (root / 'pyproject.toml').write_text('[project]\nname = "example"\nversion = "0.1.0"\n')
    (root / 'uv.lock').write_text('old lock\n')
    (root / 'README.md').write_text('# Example\n\nCurrent version / Aktuelle Version: **0.1.0**\n')
    (root / 'deploy').mkdir()
    (root / 'deploy/release-notes.md').write_text('Old notes\n')
    prepare_release.git('add', '.')
    prepare_release.git('commit', '-qm', 'Initial')
    prepare_release.git('push', '-q', 'origin', 'main')
    sha = prepare_release.git('rev-parse', 'HEAD')
    for name, value in {
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': sha,
        'GITHUB_OUTPUT': str(tmp_path / 'outputs'), 'GITHUB_STEP_SUMMARY': str(tmp_path / 'summary'),
        'RELEASE_VERSION': '0.2.0', 'RELEASE_NOTES': 'New release\n\nText with `code` and $(literal).',
    }.items():
        monkeypatch.setenv(name, value)
    original_run = subprocess.run

    def run(args, **kwargs):
        if args == ['uv', 'lock']:
            (root / 'uv.lock').write_text('new lock for ' + tomllib.loads((root / 'pyproject.toml').read_text())['project']['version'])
            return subprocess.CompletedProcess(args, 0)
        return original_run(args, **kwargs)

    monkeypatch.setattr(prepare_release.subprocess, 'run', run)
    return root, remote, sha


def test_prepares_commit_tag_notes_lock_and_current_version_display(repository):
    root, _, before = repository
    prepare_release.main()
    after = prepare_release.git('rev-parse', 'HEAD')
    assert after != before
    assert prepare_release.git('ls-remote', 'origin', 'refs/heads/main').split()[0] == after
    assert prepare_release.git('ls-remote', 'origin', 'refs/tags/v0.2.0^{}').split()[0] == after
    assert tomllib.loads((root / 'pyproject.toml').read_text())['project']['version'] == '0.2.0'
    assert 'Current version / Aktuelle Version: **0.2.0**' in (root / 'README.md').read_text()
    assert (root / 'uv.lock').read_text() == 'new lock for 0.2.0'
    assert (root / 'deploy/release-notes.md').read_text() == 'New release\n\nText with `code` and $(literal).\n'
    assert (root.parent / 'outputs').read_text() == f'commit={after}\ntag=v0.2.0\n'
    assert '**0.1.0** → requested version: **0.2.0**' in (root.parent / 'summary').read_text()
    assert prepare_release.git('status', '--porcelain') == ''


@pytest.mark.parametrize('name,value', [
    ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_REF', 'refs/heads/feature/example'),
    ('GITHUB_REF_PROTECTED', 'false'), ('RELEASE_VERSION', '0.0.9'),
    ('RELEASE_VERSION', 'v0.2.0'), ('RELEASE_VERSION', '0.2.0;echo injected'),
    ('RELEASE_NOTES', ''), ('RELEASE_NOTES', 'x' * 16001), ('GITHUB_SHA', 'a' * 40),
])
def test_rejects_invalid_preparation_without_changing_git(repository, monkeypatch, name, value):
    root, _, before = repository
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        prepare_release.main()
    assert prepare_release.git('rev-parse', 'HEAD') == before
    assert prepare_release.git('status', '--porcelain') == ''
    assert not (root.parent / 'outputs').exists()


def test_existing_tag_is_not_overwritten(repository):
    _, _, before = repository
    prepare_release.git('tag', 'v0.2.0')
    prepare_release.git('push', '-q', 'origin', 'refs/tags/v0.2.0')
    with pytest.raises(ValueError, match='already exists'):
        prepare_release.main()
    assert prepare_release.git('rev-parse', 'HEAD') == before
    assert prepare_release.git('status', '--porcelain') == ''


def test_first_release_can_use_current_project_version(repository, monkeypatch):
    _, _, before = repository
    monkeypatch.setenv('RELEASE_VERSION', '0.1.0')
    prepare_release.main()
    after = prepare_release.git('rev-parse', 'HEAD')
    assert after != before
    assert prepare_release.git('ls-remote', 'origin', 'refs/tags/v0.1.0^{}').split()[0] == after


def test_atomic_push_refuses_main_race_without_publishing_tag(repository, monkeypatch):
    root, remote, before = repository
    original_git = prepare_release.git

    def git(*args):
        if args[0] == 'push':
            # Simulate a concurrent main push immediately before the atomic publication.
            other = original_git('commit-tree', 'HEAD^{tree}', '-p', before, '-m', 'Concurrent change')
            subprocess.run(['git', '-C', str(remote), 'fetch', '-q', str(root), other], check=True)
            subprocess.run(['git', '-C', str(remote), 'update-ref', 'refs/heads/main', other], check=True)
        return original_git(*args)

    monkeypatch.setattr(prepare_release, 'git', git)
    with pytest.raises(subprocess.CalledProcessError):
        prepare_release.main()
    assert original_git('ls-remote', 'origin', 'refs/tags/v0.2.0') == ''
    assert not (root.parent / 'outputs').exists()
