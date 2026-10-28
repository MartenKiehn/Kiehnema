"""Exercise dual pushes against disposable local repositories, without network access."""

import subprocess

import pytest

from scripts import push


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'feature/test')
    git(source, 'config', 'user.name', 'Test')
    git(source, 'config', 'user.email', 'test@example.invalid')
    git(source, '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'initial')
    targets = {}
    for name in ('GitLab', 'GitHub'):
        target = tmp_path / name
        target.mkdir()
        git(target, 'init', '--bare')
        targets[name] = str(target)
    monkeypatch.setattr(push, 'ROOT', source)
    monkeypatch.setattr(push, 'REPOSITORIES', targets)
    return source, targets


def test_pushes_same_commit_to_both_without_other_branches_or_tags(repositories):
    source, targets = repositories
    git(source, 'branch', 'unpublished')
    git(source, 'tag', '-a', 'v1.0.0', '-m', 'unpublished')
    git(source, 'config', 'push.followTags', 'true')
    assert push.main() == 0
    assert push.main() == 0
    for target in targets.values():
        assert git(target, 'rev-parse', 'refs/heads/feature/test') == git(source, 'rev-parse', 'HEAD')
        assert git(target, 'for-each-ref', '--format=%(refname)') == 'refs/heads/feature/test'


@pytest.mark.parametrize('failed', ['GitLab', 'GitHub'])
def test_divergence_is_preserved_and_other_destination_is_attempted(repositories, failed):
    source, targets = repositories
    assert push.main() == 0
    git(source, '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'remote change')
    git(source, 'push', targets[failed], 'HEAD:refs/heads/feature/test')
    remote_commit = git(source, 'rev-parse', 'HEAD')
    git(source, 'reset', '--hard', 'HEAD~1')
    git(source, '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'local change')
    assert push.main() == 1
    assert git(targets[failed], 'rev-parse', 'refs/heads/feature/test') == remote_commit
    other = 'GitHub' if failed == 'GitLab' else 'GitLab'
    assert git(targets[other], 'rev-parse', 'refs/heads/feature/test') == git(source, 'rev-parse', 'HEAD')


def test_detached_head_does_not_push(repositories):
    source, targets = repositories
    git(source, 'checkout', '--detach')
    assert push.main() == 1
    assert all(not git(target, 'for-each-ref') for target in targets.values())
