import base64
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import release, setup_server
from scripts.install import verify_release


@pytest.fixture
def bundle(tmp_path):
    key = Ed25519PrivateKey.generate()
    keys = {'test': base64.b64encode(key.public_key().public_bytes_raw()).decode()}
    value = release.Release(**json.loads(Path('deploy/release.json').read_text()),
        version=release.VERSION, data_schema=1, commit='a' * 40, published_at='2026-01-01T00:00:00Z',
        images={role: 'ghcr.io/team/app' + ('/gateway' if role == 'gateway' else '') + '@sha256:' + 'b' * 64
                for role in ('app', 'updater', 'gateway')})
    envelope = release.signed_manifest(value, 'test', key)
    return tmp_path, envelope, keys


def test_release_installer_embeds_matching_verified_payload_without_private_key(bundle):
    tmp_path, envelope, keys = bundle
    script = release.installer(envelope, keys, 'Team/App')
    path = tmp_path / 'install.sh'
    path.write_bytes(script)
    subprocess.run(['bash', '-n', str(path)], check=True)
    assert 'Ubuntu 24.04' in subprocess.check_output(['bash', str(path), '--help'], text=True)
    # Execute just the generated extraction commands: no root, apt or host configuration.
    commands = re.findall(r'base64 --decode .*?\nKIEHNEMA_PAYLOAD', script.decode(), re.S)
    subprocess.run(['bash', '-e', '-c', '\n'.join(commands)], env={**os.environ, 'stage': str(tmp_path)}, check=True)
    assert json.loads((tmp_path / 'keys.json').read_text()) == keys
    assert json.loads((tmp_path / 'repository.json').read_text()) == 'Team/App'
    assert (tmp_path / 'update.json').read_bytes() == envelope
    assert verify_release(envelope, keys)['version'] == release.VERSION
    for name in ('setup_server.py', 'install.py'):
        assert (tmp_path / name).read_bytes() == (Path('scripts') / name).read_bytes()
    assert len(commands) == 5


@pytest.mark.parametrize('changes', [
    {'system': {'ID': 'debian', 'VERSION_ID': '12'}},
    {'system': {'ID': 'ubuntu', 'VERSION_ID': '22.04'}},
    {'architecture': 'armv7l'}, {'memory_kib': 8 * 1024**2},
    {'free_bytes': 5 * 1024**3}, {'installed': True},
])
def test_unsupported_hosts_and_existing_data_are_rejected(changes):
    inputs = dict(system={'ID': 'ubuntu', 'VERSION_ID': '24.04'}, architecture='aarch64',
                  memory_kib=16 * 1024**2, free_bytes=40 * 1024**3, installed=False)
    setup_server.validate_host(**inputs)
    with pytest.raises(ValueError):
        setup_server.validate_host(**(inputs | changes))


@pytest.mark.parametrize('domain,region,model', [
    ('https://video.example.com', 'eu-central-1', 'model'),
    ('video.example.com/path', 'eu-central-1', 'model'),
    ('-video.example.com', 'eu-central-1', 'model'),
    ('video.example.com\nEVIL=1', 'eu-central-1', 'model'),
    ('video.example.com', 'eu-central-1\nEVIL=1', 'model'),
    ('video.example.com', 'eu-central-1', 'model\nEVIL=1'),
])
def test_configuration_rejects_invalid_hosts_and_env_injection(domain, region, model):
    with pytest.raises(ValueError):
        setup_server.configuration(domain, region, model)


def test_role_configuration_and_swap_rewrite_preserve_unrelated_settings():
    origin, content = setup_server.configuration('Video.example.com', 'eu-central-1', 'eu.anthropic.model-v1:0')
    assert origin == 'https://video.example.com'
    assert content == ('MG_AWS_REGION=eu-central-1\nMG_BEDROCK_MODEL_ID=eu.anthropic.model-v1:0\n'
                       'AWS_EC2_METADATA_V1_DISABLED=true\n')
    source = '# swap documentation\nUUID=root / ext4 defaults 0 1\n/swapfile none swap sw 0 0\n'
    rewritten = setup_server.without_swap(source)
    assert rewritten == '# swap documentation\nUUID=root / ext4 defaults 0 1\n# Kiehnema disabled swap: /swapfile none swap sw 0 0\n'
    assert setup_server.without_swap(rewritten) == rewritten


@pytest.mark.parametrize('failure', [None, 'signature', 'role'])
def test_installation_checks_signature_and_role_before_creating_app(bundle, monkeypatch, failure):
    root, envelope, keys = bundle
    if failure == 'signature':
        value = json.loads(envelope)
        value['signature'] = base64.b64encode(bytes(64)).decode()
        envelope = json.dumps(value).encode()
    (root / 'update.json').write_bytes(envelope)
    (root / 'keys.json').write_text(json.dumps(keys))
    (root / 'repository.json').write_text('"Team/App"')
    monkeypatch.setattr(setup_server.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(setup_server.platform, 'machine', lambda: 'aarch64')
    actions = []
    monkeypatch.setattr(setup_server, 'prepare_host', lambda: actions.append(('prepare',)))

    def run(*args):
        actions.append(args)
        if failure == 'role' and args[:2] == ('docker', 'run'):
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(setup_server, 'run', run)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            setup_server.install_release(root, 'https://video.example.com', 'MG_AWS_REGION=eu-central-1\n')
        assert not any(str(root / 'install.py') in action for action in actions)
        if failure == 'signature':
            assert actions == []
    else:
        setup_server.install_release(root, 'https://video.example.com', 'MG_AWS_REGION=eu-central-1\n')
        assert [action[:2] for action in actions[:3]] == [('prepare',), ('docker', 'pull'), ('docker', 'run')]
        assert str(root / 'install.py') in actions[3]
        assert actions[4][0] == 'curl' and 'https://video.example.com/login' in actions[4]
        assert (root / 'app.env').read_text() == 'MG_AWS_REGION=eu-central-1\n'


def test_host_preparation_uses_official_packages_and_preserves_fstab(tmp_path, monkeypatch):
    def mapped_path(path):
        return tmp_path / str(path).lstrip('/')

    for directory in ('etc/apt/sources.list.d', 'etc/sysctl.d', 'proc/sys/kernel'):
        (tmp_path / directory).mkdir(parents=True)
    mapped_path('/etc/fstab').write_text('UUID=root / ext4 defaults 0 1\n/swapfile none swap sw 0 0\n')
    mapped_path('/proc/swaps').write_text('Filename Type Size Used Priority\n')
    mapped_path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns').touch()
    mapped_path('/etc/apt/keyrings').mkdir()
    mapped_path('/etc/apt/keyrings/docker.asc').touch()
    monkeypatch.setattr(setup_server, 'Path', mapped_path)
    monkeypatch.setattr(setup_server.shutil, 'which', lambda _: None)
    commands = []
    monkeypatch.setattr(setup_server, 'run', lambda *args: commands.append(args))
    monkeypatch.setattr(setup_server.subprocess, 'check_output',
        lambda args, **kwargs: 'arm64' if args[0] == 'dpkg' else '1.45' if args[1] == 'version' else '2.30.0')
    setup_server.prepare_host()
    assert ('apt-get', 'install', '-y', '--no-install-recommends', 'docker-ce', 'docker-ce-cli',
            'containerd.io', 'docker-buildx-plugin', 'docker-compose-plugin') in commands
    assert 'Architectures: arm64' in mapped_path('/etc/apt/sources.list.d/kiehnema-docker.sources').read_text()
    assert mapped_path('/etc/apt/keyrings').stat().st_mode & 0o777 == 0o755
    assert mapped_path('/etc/fstab.kiehnema-before-install').read_text().endswith('/swapfile none swap sw 0 0\n')
    assert '# Kiehnema disabled swap:' in mapped_path('/etc/fstab').read_text()
    assert ('swapoff', '-a') in commands
    assert ('systemctl', 'enable', '--now', 'docker') in commands
