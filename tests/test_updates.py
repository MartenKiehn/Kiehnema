import base64
import json
import os
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.updates.manifest import Release, resolve, verify, version
from app.updates.releases import GitHub
from app.updates.state import atomic_json, file_lock, read_json
from app.updates.storage import backup, restore

REPOSITORY = 'registry.example.com/team/kiehnema'


@pytest.fixture
def signer():
    private = Ed25519PrivateKey.generate()
    keys = {'release-1': base64.b64encode(private.public_key().public_bytes_raw()).decode()}

    def sign(value):
        raw = json.dumps(value).encode()
        return json.dumps({'key_id': 'release-1', 'manifest': base64.b64encode(raw).decode(),
                           'signature': base64.b64encode(private.sign(raw)).decode()}).encode()
    return keys, sign


def release_data(ver='0.2.0', **changes):
    return {'version': ver, 'minimum_app': '0.1.0', 'minimum_updater': '0.1.0', 'format': 1,
            'product': 'kiehnema', 'source_schemas': [1], 'data_schema': 1, 'platforms': ['linux/arm64', 'linux/amd64'],
            'images': {role: REPOSITORY + ('/gateway' if role == 'gateway' else '') + '@sha256:' + 'a' * 64
                       for role in ('app', 'updater', 'gateway')},
            'commit': 'b' * 40, 'published_at': '2026-01-01T00:00:00Z', 'notes': 'A signed release.', **changes}


def test_signed_release_and_image_repository(signer):
    keys, sign = signer
    assert verify(sign(release_data()), keys, REPOSITORY).version == '0.2.0'
    envelope = json.loads(sign(release_data()))
    envelope['manifest'] = base64.b64encode(json.dumps(release_data('9.0.0')).encode()).decode()
    with pytest.raises(ValueError, match='signature'):
        verify(json.dumps(envelope).encode(), keys, REPOSITORY)
    with pytest.raises(ValueError, match='outside'):
        verify(sign(release_data()), keys, 'registry.other.com/team/kiehnema')
    with pytest.raises(ValueError, match='signature'):
        verify(sign(release_data()), {}, REPOSITORY)


@pytest.mark.parametrize('value', ['1.2', '1.2.3-rc1', '01.2.3', '-1.2.3', '1.2.3;sh', '1.2.3\n', None])
def test_stable_versions_only(value):
    with pytest.raises(ValueError):
        version(value)


def test_semver_order_and_required_intermediate(signer):
    keys, sign = signer
    releases = [sign(release_data('0.9.0')).decode(), sign(release_data('0.10.0', minimum_app='0.9.0')).decode(),
                sign(release_data('0.11.0', platforms=['linux/amd64'], minimum_app='0.10.0')).decode()]
    current = {'version': '0.1.0', 'updater_version': '0.1.0', 'schema': 1, 'keys': keys}
    path, _ = resolve(current, 'linux/arm64', releases, REPOSITORY)
    assert [r.version for r in path] == ['0.9.0', '0.10.0']
    assert resolve({**current, 'version': '1.0.0'}, 'linux/arm64', releases, REPOSITORY)[0] == []
    assert resolve({**current, 'schema': 2}, 'linux/arm64', releases, REPOSITORY)[0] == []


@pytest.mark.parametrize('change', [
    {'format': 2}, {'layout': 2}, {'command': 'curl | sh'}, {'images': {'app': REPOSITORY + ':latest'}},
    {'published_at': '2999-01-01T00:00:00Z'}, {'published_at': '2026-01-01'},
    {'trusted_keys': {}}, {'trusted_keys': {'key': 'garbage'}},
])
def test_reject_incompatible_or_unbounded_manifest(signer, change):
    keys, sign = signer
    with pytest.raises(ValueError):
        verify(sign(release_data(**change)), keys, REPOSITORY)


def test_github_pagination_skips_drafts_and_prereleases(signer):
    keys, sign = signer
    calls = []
    api = 'https://api.github.com/repos/team/app'
    def handle(request):
        calls.append(request)
        if '/assets/' in request.url.path:
            assert request.headers['accept'] == 'application/octet-stream'
            return httpx.Response(200, content=sign(release_data()))
        if request.url.params.get('page') == '1':
            return httpx.Response(200, json=[{'draft': True}] * 99 + [{'prerelease': True}])
        return httpx.Response(200, json=[{'tag_name': 'v0.2.0', 'published_at': '2026-01-01T00:00:00Z',
            'assets': [{'name': 'update.json', 'url': api + '/releases/assets/1'}]}])
    client = GitHub({'github_repository': 'team/app'}, {})
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        releases, _ = client.releases(keys, REPOSITORY)
        assert [r.version for r in releases] == ['0.2.0']
        assert len(calls) == 3
        with pytest.raises(ValueError, match='configured GitHub repository'):
            client.download('https://attacker.example/update.json')
        assert len(calls) == 3
    finally:
        client.close()


@pytest.mark.parametrize('location,allowed', [
    ('https://release-assets.githubusercontent.com/asset?signature=test', True),
    ('https://objects.githubusercontent.com/asset', True),
    ('https://attacker.example/asset', False),
    ('http://objects.githubusercontent.com/asset', False),
    ('https://objects.githubusercontent.com.attacker.example/asset', False),
])
def test_github_asset_redirect_never_forwards_credentials(location, allowed):
    calls = []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            assert request.headers['Authorization'] == 'Bearer private'
            return httpx.Response(302, headers={'Location': location})
        assert 'Authorization' not in request.headers
        assert 'Cookie' not in request.headers
        return httpx.Response(200, content=b'signed bytes')
    client = GitHub({'github_repository': 'team/app'}, {})
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handle),
                                headers={'Authorization': 'Bearer private', 'Cookie': 'private=secret'})
    try:
        url = client.api + '/releases/assets/1'
        if allowed:
            assert client.download(url, asset=True)[0] == b'signed bytes'
            assert len(calls) == 2
        else:
            with pytest.raises(ValueError, match='redirect'):
                client.download(url, asset=True)
            assert len(calls) == 1
    finally:
        client.close()


def test_github_download_enforces_size_and_redirect_limits():
    client = GitHub({'github_repository': 'team/app'}, {})
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'12345')))
    with pytest.raises(ValueError, match='size limit'):
        client.download(client.api + '/releases/assets/1', limit=4, asset=True)
    client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(302, headers={'Location': 'https://objects.githubusercontent.com/asset'})))
    with pytest.raises(ValueError, match='Too many'):
        client.download(client.api + '/releases/assets/1', asset=True)
    client.close()


def test_key_rotation_requires_verified_transition(signer):
    from app.updates.manifest import resolve
    from scripts.release import signed_manifest
    keys, sign = signer
    next_key = Ed25519PrivateKey.generate()
    next_keys = {'release-2': base64.b64encode(next_key.public_key().public_bytes_raw()).decode()}
    current = {'version': '0.1.0', 'updater_version': '0.1.0', 'schema': 1, 'keys': keys}
    transition = sign(release_data(trusted_keys=next_keys)).decode()
    latest = signed_manifest(Release(**release_data('0.3.0')), 'release-2', next_key).decode()
    assert resolve(current, 'linux/arm64', [latest], REPOSITORY) == ([], [])
    path, _ = resolve(current, 'linux/arm64', [latest, transition], REPOSITORY)
    assert [item.version for item in path] == ['0.2.0', '0.3.0']
    # A shorter path to the same version must not discard a needed inherited key.
    transition = sign(release_data(trusted_keys={**keys, **next_keys})).decode()
    middle = sign(release_data('0.3.0')).decode()
    latest = signed_manifest(Release(**release_data('0.4.0', minimum_app='0.3.0')), 'release-2', next_key).decode()
    path, _ = resolve(current, 'linux/arm64', [latest, middle, transition], REPOSITORY)
    assert [item.version for item in path] == ['0.2.0', '0.3.0', '0.4.0']


def test_installer_checks_signature_independently_and_refuses_live_import(signer, encrypted_store, tmp_path):
    from scripts.install import verify_release, import_store
    keys, sign = signer
    assert verify_release(sign(release_data()), keys)['version'] == '0.2.0'
    changed = json.loads(sign(release_data()))
    changed['manifest'] = base64.b64encode(json.dumps(release_data('0.9.0')).encode()).decode()
    import subprocess
    with pytest.raises(subprocess.CalledProcessError):
        verify_release(json.dumps(changed).encode(), keys)
    (encrypted_store / 'secure/.service.lock').touch()
    with file_lock(encrypted_store / '.service.lock'):
        with pytest.raises(BlockingIOError):
            import_store(encrypted_store, tmp_path / 'copy')
    assert not (tmp_path / 'copy').exists()
    import_store(encrypted_store, tmp_path / 'copy')
    assert (tmp_path / 'copy/ciphertext').read_bytes() == b'encrypted fixture bytes'


def test_release_key_files_are_private_and_never_overwritten(tmp_path):
    from scripts.release import create_keys
    private, public = tmp_path / 'private.pem', tmp_path / 'public.json'
    create_keys(private, public, 'release-1')
    assert private.stat().st_mode & 0o777 == 0o600
    before = private.read_bytes()
    with pytest.raises(ValueError):
        create_keys(private, public, 'release-1')
    assert private.read_bytes() == before


def test_retention_keeps_two_latest_complete_backups_and_incomplete_evidence(tmp_path):
    from app.updates.storage import prune_backups
    for index in range(4):
        path = tmp_path / ('a' * 32 + '-' + str(index))
        path.mkdir()
        if index < 3:
            atomic_json(path / 'complete.json', {})
        os.utime(path, (index + 1, index + 1))
    prune_backups(tmp_path)
    assert sorted(p.name[-1] for p in tmp_path.iterdir()) == ['1', '2', '3']


@pytest.fixture
def encrypted_store(tmp_path):
    data = tmp_path / 'data'
    secure = data / 'secure'
    secure.mkdir(parents=True)
    with sqlite3.connect(secure / 'accounts.sqlite') as con:
        con.execute('CREATE TABLE example (value TEXT)')
        con.execute("INSERT INTO example VALUES ('original')")
    (secure / 'ciphertext').write_bytes(b'encrypted fixture bytes')
    return data


def test_complete_backup_and_repeatable_restore(encrypted_store, tmp_path):
    snapshot = tmp_path / 'backup'
    with file_lock(encrypted_store / '.service.lock'):
        inode = (encrypted_store / '.service.lock').stat().st_ino
        backup(encrypted_store, snapshot, {'version': '0.1.0'})
        (encrypted_store / 'secure/ciphertext').write_bytes(b'new version data')
        assert restore(encrypted_store, snapshot) == {'version': '0.1.0'}
        restore(encrypted_store, snapshot)
        assert (encrypted_store / 'secure/ciphertext').read_bytes() == b'encrypted fixture bytes'
        assert (encrypted_store / '.service.lock').stat().st_ino == inode
        with pytest.raises(BlockingIOError):
            with file_lock(encrypted_store / '.service.lock'):
                pass


def test_backup_corruption_and_links_fail_closed(encrypted_store, tmp_path):
    snapshot = tmp_path / 'backup'
    backup(encrypted_store, snapshot, {})
    (snapshot / 'secure/ciphertext').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='verified backup'):
        restore(encrypted_store, snapshot)
    assert (encrypted_store / 'secure/ciphertext').read_bytes() == b'encrypted fixture bytes'
    (encrypted_store / 'secure/link').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='regular files'):
        backup(encrypted_store, tmp_path / 'other-backup', {})


def test_coordinator_lock_precedes_database_initialization(tmp_path, monkeypatch):
    from app.security import accounts
    from app.config import settings
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    with accounts.service_lock():
        with pytest.raises(ValueError, match='Another Kiehnema'):
            with accounts.service_lock():
                pass
        assert not (tmp_path / 'secure').exists()


def test_start_gate_rejects_old_boot_and_cli_during_migration(tmp_path, monkeypatch):
    from app.config import settings
    from app.updates import VERSION
    from app.updates import state
    monkeypatch.setattr(settings, 'update_control_dir', tmp_path)
    monkeypatch.setattr(state, 'boot_id', lambda: 'boot-now')
    for gate in ({}, {'mode': 'running', 'version': VERSION, 'boot_id': 'old'}):
        atomic_json(tmp_path / 'gate.json', gate)
        with pytest.raises(ValueError):
            state.assert_start_allowed()
        assert not state.writes_allowed()
    atomic_json(tmp_path / 'gate.json', {'mode': 'starting', 'version': VERSION, 'boot_id': 'boot-now'})
    state.assert_start_allowed()
    with pytest.raises(ValueError):
        state.assert_start_allowed(cli=True)
    assert not state.writes_allowed()


def test_docker_adapter_refuses_other_installations():
    from app.updates.docker import Docker
    engine = Docker('ours', socket='/unused')
    engine.client.close()
    engine.client = httpx.Client(base_url='http://docker/v1.44', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'Config': {'Labels': {'org.kiehnema.installation': 'theirs'}}})))
    try:
        with pytest.raises(ValueError, match='belong'):
            engine.stop('app')
        with pytest.raises(ValueError, match='component'):
            engine.stop('../../other')
    finally:
        engine.close()


@pytest.mark.parametrize('subpath', [None, 'control'])
def test_container_replacement_uses_required_mount_api(subpath, monkeypatch):
    from app.updates.docker import Docker
    engine = Docker('ours', socket='/unused')
    engine.client.close()
    requests = []
    engine.client = httpx.Client(base_url='http://docker/v1.44', transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(201, json={'Id':'replacement'})))
    for action in ('stop', 'remove', 'start'):
        monkeypatch.setattr(engine, action, lambda role: None)
    mount = {'Type':'volume', 'Source':'updates', 'Target':'/run/kiehnema', 'ReadOnly':True}
    if subpath:
        mount['VolumeOptions'] = {'Subpath':subpath}
    specification = {'Image':'old', 'HostConfig':{'Mounts':[mount]}}
    try:
        engine.replace('app', 'new', specification)
        request, = requests
        assert request.url.path == f'/v{"1.45" if subpath else "1.44"}/containers/create'
        assert json.loads(request.content)['HostConfig']['Mounts'] == [mount]
        assert specification['Image'] == 'old'
    finally:
        engine.close()


def test_container_snapshot_preserves_assigned_ports_and_runtime_configuration():
    from app.updates.docker import Docker
    engine = Docker('ours', socket='/unused')
    value = {'Id': 'a' * 64, 'Config': {'Image': 'old', 'Env': ['PATH=old', 'MG_DATA_DIR=/data', 'KIEHNEMA_ADDRESS=https://example.com']},
             'HostConfig': {'PortBindings': {'443/tcp': [{'HostIp': '127.0.0.1', 'HostPort': ''}]}},
             'NetworkSettings': {'Networks': {'private': {'Aliases': ['app', 'a' * 12]}},
                                 'Ports': {'443/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '54321'}]}}}
    engine.inspect = lambda role: value
    try:
        snapshot = engine.snapshot('app')
        assert snapshot['HostConfig']['PortBindings']['443/tcp'][0]['HostPort'] == '54321'
        assert snapshot['Env'] == ['MG_DATA_DIR=/data', 'KIEHNEMA_ADDRESS=https://example.com']
        assert snapshot['NetworkingConfig']['EndpointsConfig']['private']['Aliases'] == ['app']
        assert value['HostConfig']['PortBindings']['443/tcp'][0]['HostPort'] == ''
    finally:
        engine.close()
