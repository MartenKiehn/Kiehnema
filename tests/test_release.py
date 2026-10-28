import json

import httpx
import pytest

from app.updates.manifest import verify
from scripts import release


@pytest.fixture
def publication(tmp_path, monkeypatch):
    values = {
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF_TYPE': 'tag', 'GITHUB_REF_PROTECTED': 'true',
        'GITHUB_REF_NAME': 'v' + release.VERSION, 'GITHUB_SHA': 'a' * 40,
        'GITHUB_REPOSITORY': 'Team/App', 'GITHUB_TOKEN': 'test-token',
        'KIEHNEMA_SIGNING_KEY': str(tmp_path / 'key.pem'), 'KIEHNEMA_SIGNING_KEY_ID': 'test',
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    release.create_keys(tmp_path / 'key.pem', tmp_path / 'keys.json', 'test')
    state = {'sha': 'a' * 40, 'upload_status': 201, 'existing': False, 'installer_status': 201}
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer test-token'
        assert request.url.host in {'api.github.com', 'uploads.github.com'}
        if request.url.path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': state.get('main_sha', state['sha'])})
        if '/commits/' in request.url.path:
            return httpx.Response(200, json={'sha': state['sha'],
                'commit': {'committer': {'date': '2026-01-01T00:00:00Z'}}})
        if '/releases/tags/' in request.url.path:
            return httpx.Response(200 if state['existing'] else 404, json={})
        if request.url.host == 'uploads.github.com':
            assert request.url.params['name'] in {'update.json', 'install.sh'}
            return httpx.Response(state['upload_status'] if request.url.params['name'] == 'update.json'
                                  else state['installer_status'], json={})
        return httpx.Response(201, json={'id': 42})

    client = httpx.Client
    monkeypatch.setattr(release.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    for name, digest in [('app', 'b'), ('gateway', 'c')]:
        (tmp_path / f'{name}.json').write_text(json.dumps({'containerimage.digest': 'sha256:' + digest * 64}))
    (tmp_path / 'notes.md').write_text('Release test')
    return tmp_path, state, requests


def publish(root):
    release.publish(root / 'app.json', root / 'gateway.json', root / 'notes.md')


def test_github_release_is_published_only_after_signed_asset_upload(publication):
    root, _, requests = publication
    publish(root)
    assert [r.method for r in requests] == ['GET', 'GET', 'POST', 'POST', 'POST', 'PATCH']
    assert json.loads(requests[2].content)['draft'] is True
    manifest = verify(requests[3].content, json.loads((root / 'keys.json').read_text()), 'ghcr.io/team/app')
    assert manifest.commit == 'a' * 40
    assert manifest.published_at == '2026-01-01T00:00:00Z'
    assert requests[4].url.params['name'] == 'install.sh'
    assert b'PRIVATE KEY' not in requests[4].content
    assert json.loads(requests[5].content) == {'draft': False}


@pytest.mark.parametrize('failure', ['upload_status', 'installer_status'])
def test_failed_upload_leaves_release_unpublished(publication, failure):
    root, state, requests = publication
    state[failure] = 500
    with pytest.raises(httpx.HTTPStatusError):
        publish(root)
    assert not any(r.method == 'PATCH' for r in requests)


def test_existing_release_is_never_overwritten(publication):
    root, state, requests = publication
    state['existing'] = True
    with pytest.raises(ValueError, match='already exists'):
        publish(root)
    assert all(r.method == 'GET' for r in requests)


@pytest.mark.parametrize('name,value', [
    ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_REF_TYPE', 'branch'),
    ('GITHUB_REF_NAME', 'v99.0.0'), ('GITHUB_REF_PROTECTED', 'false'),
    ('GITHUB_REPOSITORY', 'https://attacker.example'),
])
def test_invalid_release_context_never_contacts_github(publication, monkeypatch, name, value):
    _, _, requests = publication
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        release.release_context()
    assert requests == []


def test_moved_github_tag_prevents_publication(publication):
    root, state, requests = publication
    state['sha'] = 'b' * 40
    with pytest.raises(ValueError, match='tested commit'):
        publish(root)
    assert len(requests) == 1 and requests[0].method == 'GET'


def manual_release(monkeypatch):
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('GITHUB_REF_TYPE', 'branch')
    monkeypatch.setenv('GITHUB_REF_NAME', 'main')
    monkeypatch.setenv('GITHUB_SHA', 'c' * 40)
    monkeypatch.setenv('KIEHNEMA_RELEASE_TAG', 'v' + release.VERSION)
    monkeypatch.setenv('KIEHNEMA_RELEASE_COMMIT', 'a' * 40)


def test_manual_release_publishes_prepared_and_tested_commit(publication, monkeypatch):
    root, _, requests = publication
    manual_release(monkeypatch)
    publish(root)
    assert requests[0].url.path.endswith('/commits/v' + release.VERSION)
    assert requests[1].url.path.endswith('/commits/main')
    assert json.loads(requests[3].content)['target_commitish'] == 'a' * 40
    manifest = verify(requests[4].content, json.loads((root / 'keys.json').read_text()), 'ghcr.io/team/app')
    assert manifest.commit == 'a' * 40


@pytest.mark.parametrize('name,value', [
    ('GITHUB_REF_NAME', 'feature/example'), ('GITHUB_REF_TYPE', 'tag'),
    ('GITHUB_REF_PROTECTED', 'false'), ('KIEHNEMA_RELEASE_TAG', 'v99.0.0'),
    ('KIEHNEMA_RELEASE_COMMIT', ''),
])
def test_invalid_manual_release_never_contacts_github(publication, monkeypatch, name, value):
    _, _, requests = publication
    manual_release(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        release.release_context()
    assert requests == []


def test_main_advancing_after_manual_preparation_prevents_publication(publication, monkeypatch):
    root, state, requests = publication
    manual_release(monkeypatch)
    state['main_sha'] = 'b' * 40
    with pytest.raises(ValueError, match='Main advanced'):
        publish(root)
    assert all(request.method == 'GET' for request in requests)
