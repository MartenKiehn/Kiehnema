import json

import httpx
import pytest

from app.updates.manifest import verify
from scripts import release


@pytest.fixture
def publication(tmp_path, monkeypatch):
    values = {
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF_TYPE': 'tag',
        'GITHUB_REF_NAME': 'v' + release.VERSION, 'GITHUB_SHA': 'a' * 40,
        'GITLAB_API_URL': 'https://git.example.com/api/v4', 'GITLAB_PROJECT_ID': 'team/app',
        'GITLAB_TOKEN': 'test-token', 'GITLAB_REGISTRY_IMAGE': 'registry.example.com/team/app',
        'KIEHNEMA_SIGNING_KEY': str(tmp_path / 'key.pem'), 'KIEHNEMA_SIGNING_KEY_ID': 'test',
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    release.create_keys(tmp_path / 'key.pem', tmp_path / 'keys.json', 'test')
    tag = {'protected': True, 'created_at': '2026-01-02T00:00:00Z',
           'commit': {'id': 'a' * 40, 'committed_date': '2026-01-01T00:00:00Z'}}
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers['PRIVATE-TOKEN'] == 'test-token'
        assert 'JOB-TOKEN' not in request.headers
        assert '/projects/team%2Fapp/' in str(request.url)
        if '/repository/tags/' in str(request.url):
            return httpx.Response(200, json=tag)
        if request.method == 'GET':
            return httpx.Response(404)
        return httpx.Response(201, json={})

    client = httpx.Client
    monkeypatch.setattr(release.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    return tmp_path, tag, requests


@pytest.mark.parametrize('lightweight', [False, True])
def test_github_release_publishes_verified_manifest_to_gitlab(publication, lightweight):
    root, tag, requests = publication
    if lightweight:
        tag['created_at'] = None
    for name, digest in [('app', 'b'), ('gateway', 'c')]:
        (root / f'{name}.json').write_text(json.dumps({'containerimage.digest': 'sha256:' + digest * 64}))
    (root / 'notes.md').write_text('Release test')
    release.publish(root / 'app.json', root / 'gateway.json', root / 'notes.md')
    assert [r.method for r in requests] == ['GET', 'GET', 'PUT', 'POST']
    manifest = verify(requests[2].content, json.loads((root / 'keys.json').read_text()),
                      'registry.example.com/team/app')
    assert manifest.commit == 'a' * 40
    assert manifest.published_at == (tag['created_at'] or tag['commit']['committed_date'])
    assert json.loads(requests[3].content)['tag_name'] == 'v' + release.VERSION


@pytest.mark.parametrize('name,value', [
    ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_REF_TYPE', 'branch'),
    ('GITHUB_REF_NAME', 'v99.0.0'), ('GITLAB_API_URL', 'http://git.example.com/api/v4'),
])
def test_invalid_release_context_never_contacts_gitlab(publication, monkeypatch, name, value):
    _, _, requests = publication
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        release.release_context()
    assert requests == []


@pytest.mark.parametrize('protected,commit', [(False, 'a' * 40), (True, 'b' * 40)])
def test_unprotected_or_different_gitlab_tag_prevents_publication(publication, protected, commit):
    root, tag, requests = publication
    tag.update(protected=protected)
    tag['commit']['id'] = commit
    with pytest.raises(ValueError, match='protected and point to the same commit'):
        release.publish(root / 'missing.json', root / 'missing.json', root / 'missing.md')
    assert len(requests) == 1 and requests[0].method == 'GET'
