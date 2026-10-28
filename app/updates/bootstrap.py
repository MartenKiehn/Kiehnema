"""One-time initialization, run from the verified release image by scripts/install.py."""

import json
import os
import platform
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from app.security.workspace import memory_policy
from app.updates import VERSION
from app.updates.manifest import verify
from app.updates.state import atomic_bytes, atomic_json, boot_id, file_lock


def main():
    os.umask(0o077)
    memory_policy()
    root, data, source = Path('/updates'), Path('/data'), Path('/bootstrap')
    if (root / 'config.json').exists():
        raise ValueError('This installation is already configured. Use Updates in the application.')
    config = json.loads((source / 'config.json').read_text())
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,40}', config['installation']):
        raise ValueError('Invalid installation name.')
    origin = urlsplit(config['public_origin'])
    if (origin.scheme != 'https' or not origin.hostname or origin.path or origin.query or origin.fragment
            or origin.username or origin.password):
        raise ValueError('A public HTTPS origin without a path is required.')
    keys = json.loads((source / 'keys.json').read_text())
    release = verify((source / 'update.json').read_bytes(), keys, config['repository'])
    target = {'x86_64': 'linux/amd64', 'aarch64': 'linux/arm64'}.get(platform.machine())
    if target not in release.platforms or release.version != VERSION:
        raise ValueError('The installer image does not match this release or platform.')
    root.mkdir(exist_ok=True, mode=0o700)
    for name, mode, group in [('control', 0o750, 1000), ('public', 0o755, 0), ('backups', 0o700, 0)]:
        directory = root / name
        directory.mkdir(exist_ok=True)
        directory.chmod(mode)
        os.chown(directory, 0, group)
    token = root / 'control/token'
    token.write_text(secrets.token_urlsafe(48))
    token.chmod(0o640)
    os.chown(token, 0, 1000)
    data.mkdir(exist_ok=True, mode=0o700)
    if data.is_symlink():
        raise ValueError('The data directory must not be a symlink.')
    with file_lock(data / '.service.lock'):
        for path in [data, *data.rglob('*')]:
            if path.is_symlink():
                raise ValueError('Imported data must not contain symlinks.')
            os.chown(path, 1000, 1000)
        config['platform'] = target
        atomic_json(root / 'config.json', config)
        atomic_json(root / 'installed.json', {'version': release.version, 'updater_version': release.version,
            'schema': release.data_schema, 'images': release.images, 'keys': release.trusted_keys or keys})
        atomic_bytes(root / 'images.env', ''.join(f'KIEHNEMA_{role.upper()}_IMAGE={ref}\n' for role, ref in release.images.items()).encode())
        if (source / 'credentials.json').exists():
            atomic_json(root / 'credentials.json', json.loads((source / 'credentials.json').read_text()))
        atomic_json(root / 'control/gate.json', {'mode': 'running', 'version': release.version, 'boot_id': boot_id()}, 0o644)
        atomic_json(root / 'public/status.json', {'phase': 'recovering', 'message': 'Preparing Kiehnema.',
                    'updated_at': 0, 'maintenance': True, 'received': None, 'total': None}, 0o644)
        (root / 'public/maintenance').touch(mode=0o644)
    print('Installation initialized. Keep the signing key and account recovery keys outside the server.')


if __name__ == '__main__':
    main()
