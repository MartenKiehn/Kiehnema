#!/usr/bin/env python3
"""Install a signed release on a Linux Docker host. Only the first setup needs a terminal."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from urllib.parse import urlsplit


def import_store(source, destination):
    import fcntl
    if (source / 'secure').is_symlink():
        raise ValueError('Imported data must not contain symlinks.')
    with ExitStack() as stack:
        for lock in (source / '.service.lock', source / 'secure/.service.lock'):
            if lock.parent == source or lock.exists():
                fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                stack.callback(os.close, fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        secure = source / 'secure'
        for path in [secure, *secure.rglob('*')]:
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError('Imported data must contain only regular files and directories.')
        shutil.copytree(secure, destination, symlinks=True)


def verify_release(envelope, keys):
    signed = json.loads(envelope)
    raw = base64.b64decode(signed['manifest'], validate=True)
    key = base64.b64decode(keys[signed['key_id']], validate=True)
    if len(key) != 32 or len(envelope) > 128 * 1024:
        raise ValueError('Invalid release or trust key.')
    # RFC 8410 SubjectPublicKeyInfo for an Ed25519 public key; OpenSSL ships with Ubuntu.
    der = bytes.fromhex('302a300506032b6570032100') + key
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / 'key.der').write_bytes(der)
        (root / 'manifest').write_bytes(raw)
        (root / 'signature').write_bytes(base64.b64decode(signed['signature'], validate=True))
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(root / 'key.der'), '-keyform', 'DER',
                        '-rawin', '-in', str(root / 'manifest'), '-sigfile', str(root / 'signature')],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    value = json.loads(raw)
    if value.get('product') != 'kiehnema' or value.get('format') != 1 or set(value['images']) != {'app', 'updater', 'gateway'}:
        raise ValueError('Unsupported release.')
    for image in value['images'].values():
        if not re.fullmatch(r'[a-z0-9][a-z0-9.:-]*/[a-z0-9._/-]+@sha256:[a-f0-9]{64}', image):
            raise ValueError('Release images must be pinned by digest.')
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release', required=True, type=Path)
    p.add_argument('--keys', required=True, type=Path, help='Independently trusted public signing keys JSON')
    p.add_argument('--app-env', required=True, type=Path, help='Private MG_/AWS_ configuration file')
    p.add_argument('--credentials', type=Path, help='Read-only registry username/token JSON, if private')
    p.add_argument('--origin', required=True, help='Public HTTPS origin')
    p.add_argument('--repository', default='MartenKiehn/Kiehnema', help='GitHub owner/repository for releases')
    p.add_argument('--directory', default='/opt/kiehnema', type=Path)
    p.add_argument('--name', default='kiehnema')
    p.add_argument('--import-data', type=Path, help='Stopped existing data directory; originals are kept')
    args = p.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        p.error('Run the one-time installer as root on Linux.')
    if len(Path('/proc/swaps').read_text().splitlines()) > 1:
        p.error('Disable host swap before installing Kiehnema.')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,40}', args.name):
        p.error('Choose a lowercase installation name containing letters, numbers, hyphens or underscores.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        p.error('Use owner/name for the GitHub repository.')
    root = args.directory.absolute()
    if root.exists():
        p.error('Choose a new installation directory. Existing data is never overwritten.')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(root)) or not re.fullmatch(r'https://[A-Za-z0-9.:-]+', args.origin):
        p.error('Use an absolute installation path without spaces and an HTTPS origin without a path.')
    if urlsplit(args.origin).port not in (None, 443):
        p.error('The managed installer uses standard HTTPS port 443 and HTTP port 80.')
    os.umask(0o077)
    release = verify_release(args.release.read_bytes(), json.loads(args.keys.read_text()))
    image = release['images']['updater']
    repository = release['images']['app'].split('@')[0]
    if args.credentials:
        credentials = json.loads(args.credentials.read_text())
        subprocess.run(['docker', 'login', repository.split('/')[0], '-u', credentials['username'], '--password-stdin'],
                       input=credentials['token'].encode(), check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'pull', image], check=True)
    root.mkdir(parents=True, mode=0o700)
    bootstrap = root / 'bootstrap'
    bootstrap.mkdir(mode=0o700)
    try:
        for name in ('compose.yaml', 'chromium-seccomp.json'):
            content = subprocess.check_output(['docker', 'run', '--rm', '--entrypoint', 'cat', image, '/opt/kiehnema/deploy/' + name])
            (root / name).write_bytes(content)
        shutil.copyfile(args.app_env, root / 'app.env')
        for source, name in [(args.release, 'update.json'), (args.keys, 'keys.json')]:
            shutil.copyfile(source, bootstrap / name)
        if args.credentials:
            shutil.copyfile(args.credentials, bootstrap / 'credentials.json')
        (bootstrap / 'config.json').write_text(json.dumps({'installation': args.name, 'public_origin': args.origin,
            'github_repository': args.repository, 'repository': repository}))
        if args.import_data:
            from_source = args.import_data.resolve(strict=True)
            if not (from_source / 'secure/accounts.sqlite').is_file():
                p.error('Choose the stopped installation data directory containing secure/accounts.sqlite.')
            import_store(from_source, root / 'data/secure')
        for directory in ('data', 'updates', 'updates/control', 'updates/public'):
            (root / directory).mkdir(parents=True, exist_ok=True)
        hostname = urlsplit(args.origin).hostname
        env = {'KIEHNEMA_HOME': str(root), 'KIEHNEMA_INSTALLATION': args.name, 'KIEHNEMA_ORIGIN': args.origin,
               'KIEHNEMA_ADDRESS': 'https://' + ('[' + hostname + ']' if ':' in hostname else hostname),
               **{'KIEHNEMA_' + role.upper() + '_IMAGE': ref for role, ref in release['images'].items()}}
        (root / 'compose.env').write_text(''.join(f'{key}={value}\n' for key, value in env.items()))
        compose = ['docker', 'compose', '--env-file', str(root / 'compose.env'), '-f', str(root / 'compose.yaml')]
        subprocess.run(compose + ['config', '--quiet'], check=True)
        subprocess.run(compose + ['run', '--rm', '--no-deps', '--volume', str(bootstrap) + ':/bootstrap:ro,z',
                                 'updater', 'python', '-m', 'app.updates.bootstrap'], check=True)
        compose[4:4] = ['--env-file', str(root / 'updates/images.env')]
        if not (root / 'data/secure/accounts.sqlite').exists():
            subprocess.run(compose + ['run', '--rm', '--no-deps', 'app', 'python', '-m', 'app.security', 'setup'], check=True)
        # Bootstrap secrets have served their purpose; runtime containers never need them.
        app_env = root / 'app.env'
        app_env.write_text(''.join(line for line in app_env.read_text().splitlines(keepends=True)
                                  if not line.lstrip().upper().startswith('MG_ADMIN_')))
        subprocess.run(compose + ['up', '-d'], check=True)
        subprocess.run(compose + ['exec', '-T', 'updater', 'python', '-c',
            'from app.updates import VERSION, DATA_SCHEMA; from app.updates.service import Updater; Updater().await_ready(VERSION, DATA_SCHEMA)'], check=True)
        print('Kiehnema installed at ' + args.origin + '. Future updates are available under Updates.')
    finally:
        shutil.rmtree(bootstrap)


if __name__ == '__main__':
    main()
