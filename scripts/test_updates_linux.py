"""Destructive tests in isolated, disposable Docker resources; never touches an existing installation.

Run after building the runtime and gateway targets (see docs/updates.md).
Docker Desktop's swap policy may be simulated ONLY with --synthetic-no-swap.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

import httpx
import pyotp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.updates.manifest import Release
from scripts.release import signed_manifest


def docker(*args, input=None):
    result = subprocess.run(['docker', *args], input=input, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'Docker command {args[:2]} failed: {result.stderr[-2000:]}')
    return (result.stdout + (result.stderr if args[0] == 'logs' else '')).strip()


def eventually(function, timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = function()
            if result:
                return result
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last = exc
        time.sleep(0.5)
    raise AssertionError(f'Timed out: {last}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', default='kiehnema-update-dev:0.1.0')
    parser.add_argument('--gateway', default='kiehnema-update-gateway')
    parser.add_argument('--synthetic-no-swap', action='store_true')
    args = parser.parse_args()
    name = 'kmtest-' + uuid.uuid4().hex[:10]
    containers, volumes, images = [], [], []
    key = Ed25519PrivateKey.generate()
    keys = {'test': base64.b64encode(key.public_key().public_bytes_raw()).decode()}
    password, totp = 'Synthetic-test-password-123!', pyotp.random_base32()
    with tempfile.TemporaryDirectory(prefix=name + '-') as temp:
        root = Path(temp)
        mounts = []
        if args.synthetic_no_swap:
            (root / 'swaps').write_text('Filename\tType\tSize\tUsed\tPriority\n')
            mounts = ['--mount', f'type=bind,source={root}/swaps,target=/proc/swaps,readonly']
        try:
            docker('network', 'create', name)
            for role in ('data', 'updates', 'tls', 'caddy', 'bootstrap'):
                volume = name + '-' + role
                docker('volume', 'create', volume)
                volumes.append(volume)

            def run(role, image, *options, command=(), stdin=None):
                container = name + '-' + role
                containers.append(container)
                return docker('run', '-i' if stdin else '-d', '--name', container,
                    '--label', 'org.kiehnema.installation=' + name, *options, image, *command, input=stdin)

            port = str(20000 + int(uuid.uuid4().hex[:6], 16) % 40000)
            run('registry', 'registry:3', '--network', 'host', '-e', 'REGISTRY_HTTP_ADDR=127.0.0.1:' + port)
            repository = '127.0.0.1:' + port + '/kiehnema'
            eventually(lambda: 'listening on' in docker('logs', name + '-registry'), timeout=20)

            def push(image, tag, gateway=False):
                tagged = repository + ('/gateway' if gateway else '') + ':' + tag
                docker('tag', image, tagged)
                images.append(tagged)
                docker('push', tagged)
                return next(ref for ref in json.loads(docker('image', 'inspect', tagged))[0]['RepoDigests']
                            if ref.startswith(repository + ('/gateway' if gateway else '') + '@'))

            baseline = name + ':base'
            # shortcut: synthetic schema-1 releases; use archived real images when changing the storage protocol.
            docker('build', '-t', baseline, '-', input=f'FROM {args.runtime}\nUSER root\nRUN sed -i \'s/^version = .*/version = "0.1.0"/\' pyproject.toml\nUSER 1000:1000\n')
            images.append(baseline)
            app1 = push(baseline, 'base')
            gateway = push(args.gateway, 'base', True)
            print('Base images published to isolated test registry.', flush=True)
            targets = {}
            for version, bad in [('0.2.0', False), ('0.3.0', True)]:
                tag = name + ':' + version
                # Fixtures deliberately delay migration so the independent maintenance route is observable.
                injection = "raise ValueError('synthetic migration failure')" if bad else 'import time; time.sleep(6)'
                content = (f'FROM {baseline}\nUSER root\n'
                    f'RUN sed -i \'s/version = "0.1.0"/version = "{version}"/\' pyproject.toml\n'
                    f'RUN sed -i "/def migrate(target: int):/a\\    {injection}" app/updates/probe.py\nUSER 1000:1000\n')
                docker('build', '-t', tag, '-', input=content)
                images.append(tag)
                targets[version] = push(tag, version)

            def envelope(version, image):
                value = Release(version=version, minimum_app='0.1.0', minimum_updater='0.1.0',
                    source_schemas=[1], data_schema=1, platforms=['linux/arm64', 'linux/amd64'],
                    images={'app': image, 'updater': image, 'gateway': gateway}, commit='a' * 40,
                    published_at='2026-01-01T00:00:00Z', notes='Synthetic integration release.')
                return signed_manifest(value, 'test', key).decode()

            # Reserve the gateway's randomly assigned HTTPS port before bootstrap.
            docker('create', '--name', name + '-gateway', '--label', 'org.kiehnema.installation=' + name,
                '--network', name, '-p', '127.0.0.1::443', '-e', 'KIEHNEMA_ADDRESS=https://localhost',
                '--mount', f'type=volume,source={name}-updates,target=/srv/update,volume-subpath=public,readonly',
                '-v', name + '-tls:/data', '-v', name + '-caddy:/config', args.gateway)
            containers.append(name + '-gateway')
            data_mount = ['-v', name + '-data:/data']
            update_mount = ['-v', name + '-updates:/updates']
            (root / 'update.json').write_text(envelope('0.1.0', app1))
            (root / 'keys.json').write_text(json.dumps(keys))
            (root / 'config.json').write_text(json.dumps({'installation': name,
                'public_origin': 'https://localhost', 'repository': repository,
                'gitlab_url': 'https://unavailable.invalid', 'project_id': 'test'}))
            run('seed', app1, '--rm', '--user', '0:0', '-v', name + '-bootstrap:/bootstrap',
                command=['python', '-c', "import json,sys; from pathlib import Path; [Path('/bootstrap', k).write_text(v) for k,v in json.load(sys.stdin).items()]"],
                stdin=json.dumps({p.name: p.read_text() for p in root.glob('*.json')}))
            run('bootstrap', app1, '--rm', '--user', '0:0', *mounts, *data_mount, *update_mount,
                '-v', name + '-bootstrap:/bootstrap:ro', command=['python', '-m', 'app.updates.bootstrap'], stdin='\n')
            docker('start', name + '-gateway')
            port = json.loads(docker('inspect', name + '-gateway'))[0]['NetworkSettings']['Ports']['443/tcp'][0]['HostPort']
            origin = 'https://localhost:' + port
            app_options = ['--network', name, '--network-alias', 'app', '--user', '1000:1000',
                '--init', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--security-opt', 'seccomp=deploy/chromium-seccomp.json',
                '--tmpfs', '/work:size=1073741824,mode=0700,uid=1000,gid=1000', '--tmpfs', '/tmp',
                '--shm-size', '1g', *mounts, *data_mount,
                '--mount', f'type=volume,source={name}-updates,target=/run/kiehnema,volume-subpath=control,readonly']
            environment = {'MG_DATA_DIR': '/data', 'MG_WORK_DIR': '/work', 'MG_HOST': '0.0.0.0',
                'MG_PUBLIC_ORIGIN': origin, 'MG_TRUSTED_PROXIES': '*', 'MG_UPDATE_CONTROL_DIR': '/run/kiehnema'}
            setup_env = {**environment, 'MG_ADMIN_USERNAME': 'admin', 'MG_ADMIN_PASSWORD': password,
                'MG_ADMIN_TOTP_SECRET': totp, 'MG_ADMIN_RECOVERY_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode()}
            env_args = lambda values: [part for k, v in values.items() for part in ('-e', k + '=' + v)]
            run('setup', app1, '--rm', *app_options, *env_args(setup_env),
                command=['python', '-m', 'app.security', 'setup'], stdin='\n')
            run('app', app1, *app_options, *env_args(environment))
            run('updater', app1, '--network', name, '--user', '0:0', '--read-only', '--tmpfs', '/tmp',
                '-e', 'MG_DATA_DIR=/data', *data_mount, *update_mount, '-v', '/var/run/docker.sock:/var/run/docker.sock',
                '--health-cmd', 'python -m app.updates.health', '--health-interval', '2s',
                command=['python', '-m', 'app.updates', 'serve'])
            containers.extend([name + '-runner', name + '-migration'])
            with httpx.Client(base_url=origin, verify=False, timeout=10, trust_env=False) as client:
                eventually(lambda: client.get('/login').status_code == 200)
                assert client.get('/_internal/update-ready').status_code == 404
                # Fresh page rendering is unavailable during maintenance, but status and assets survive.
                def login():
                    page = client.get('/login')
                    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]
                    response = client.post('/login', headers={'Origin': origin}, data={
                        'csrf': csrf, 'username': 'admin', 'password': password, 'code': pyotp.TOTP(totp).now()})
                    assert response.status_code == 303, response.text[:200]
                    page = client.get('/admin/updates')
                    assert page.status_code == 200
                    return re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]

                # Wait until the automatic check has reported the intentionally unreachable fixture feed.
                csrf = login()
                eventually(lambda: client.get('/admin/updates/status').json().get('error'))
                for target, image in targets.items():
                    catalog = {'path': [envelope(target, image)], 'available': [{'version': target, 'notes': 'Integration test'}],
                               'error': None, 'checked_at': time.time()}
                    docker('exec', '-i', name + '-updater', 'python', '-c',
                        "import json,sys; from pathlib import Path; from app.updates.state import atomic_json; atomic_json(Path('/updates/catalog.json'),json.load(sys.stdin))",
                        input=json.dumps(catalog))
                    response = client.post('/admin/updates/install', headers={'Origin': origin}, data={'csrf': csrf, 'version': target})
                    assert response.status_code == 303, response.text[:300]
                    observed = set()
                    maintenance_seen = False
                    def finished():
                        nonlocal maintenance_seen
                        status = client.get('/_update/status').json()
                        observed.add(status['phase'])
                        if status['maintenance']:
                            page = client.get('/login')
                            assert page.status_code == 503 and 'data-update-progress' in page.text
                            assert client.get('/_update/assets/update-progress.js').status_code == 200
                            maintenance_seen = True
                        return status if status['phase'] in {'succeeded', 'failed'} and not status['maintenance'] else None
                    status = eventually(finished, 240)
                    assert maintenance_seen, observed
                    expected = 'succeeded' if target == '0.2.0' else 'failed'
                    assert status['phase'] == expected and not status['needs_repair'], status
                    # Account decryption after replacement/restore verifies more than a liveness endpoint.
                    time.sleep(31)  # TOTP replay protection also applies across updates.
                    csrf = login()
                    state = client.get('/admin/updates/status').json()
                    assert state['version'] == '0.2.0' and not state['busy'], state
                    print(f'{target}: {expected}, independent maintenance page and account decryption passed', flush=True)
            print('Real-container update, updater replacement and rollback passed.', flush=True)
        finally:
            for container in reversed(containers):
                subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
            subprocess.run(['docker', 'network', 'rm', name], capture_output=True)
            for volume in volumes:
                subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)
            for image in images:
                subprocess.run(['docker', 'image', 'rm', image], capture_output=True)


if __name__ == '__main__':
    main()
