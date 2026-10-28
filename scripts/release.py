"""Create release signing keys locally, or publish verified CI images to GitLab."""

import argparse
import base64
import json
import os
from pathlib import Path
import re

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.updates import DATA_SCHEMA, VERSION
from app.updates.manifest import Release, verify


def signed_manifest(manifest, key_id, key):
    raw = manifest.model_dump_json(exclude_none=True).encode()
    return json.dumps({'key_id': key_id, 'manifest': base64.b64encode(raw).decode(),
                       'signature': base64.b64encode(key.sign(raw)).decode()}, separators=(',', ':')).encode()


def create_keys(private_path, public_path, key_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key_id):
        raise ValueError('Use a short signing key ID containing letters, numbers, underscores or hyphens.')
    if private_path.exists() or public_path.exists():
        raise ValueError('Key files already exist. They will not be overwritten.')
    key = Ed25519PrivateKey.generate()
    for path, content in (
        (private_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
        (public_path, json.dumps({key_id: base64.b64encode(key.public_key().public_bytes_raw()).decode()}).encode()),
    ):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(content)
    print('Key files created. Keep the private key secret; distribute the public key independently.')


def publish(app_metadata, gateway_metadata, notes):
    env = os.environ
    if env.get('CI_COMMIT_REF_PROTECTED') != 'true' or env.get('CI_COMMIT_TAG') != 'v' + VERSION:
        raise ValueError('Publish only from a protected tag matching pyproject.toml.')
    repo = env['CI_REGISTRY_IMAGE']
    app_image = repo + '@' + json.loads(app_metadata.read_text())['containerimage.digest']
    gateway_image = repo + '/gateway@' + json.loads(gateway_metadata.read_text())['containerimage.digest']
    policy = json.loads(Path('deploy/release.json').read_text())
    manifest = Release(**policy, version=VERSION, data_schema=DATA_SCHEMA, commit=env['CI_COMMIT_SHA'],
                       published_at=env['CI_PIPELINE_CREATED_AT'], notes=notes.read_text(),
                       images={'app': app_image, 'updater': app_image, 'gateway': gateway_image})
    key = serialization.load_pem_private_key(Path(env['KIEHNEMA_SIGNING_KEY']).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('The release key must be Ed25519.')
    key_id = env['KIEHNEMA_SIGNING_KEY_ID']
    envelope = signed_manifest(manifest, key_id, key)
    verify(envelope, {key_id: base64.b64encode(key.public_key().public_bytes_raw()).decode()}, repo)
    api = env['CI_API_V4_URL'].rstrip('/')
    if not api.startswith('https://'):
        raise ValueError('Release publication requires HTTPS.')
    project = api + '/projects/' + env['CI_PROJECT_ID']
    asset = project + '/packages/generic/kiehnema/' + VERSION + '/update.json'
    with httpx.Client(headers={'JOB-TOKEN': env['CI_JOB_TOKEN']}, timeout=60, follow_redirects=False, trust_env=False) as client:
        existing = client.get(asset)
        if existing.status_code == 200:
            if existing.content != envelope:
                raise ValueError('This version already contains different release bytes. Publish a new version.')
        elif existing.status_code == 404:
            client.put(asset, content=envelope, headers={'Content-Type': 'application/json'}).raise_for_status()
        else:
            existing.raise_for_status()
        release = client.post(project + '/releases', json={'name': 'Kiehnema ' + VERSION, 'tag_name': 'v' + VERSION,
            'description': manifest.notes, 'assets': {'links': [{'name': 'update.json', 'url': asset, 'link_type': 'package'}]}})
        release.raise_for_status()
    print('Published signed Kiehnema release ' + VERSION)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    keygen = commands.add_parser('keygen')
    keygen.add_argument('--private', required=True, type=Path)
    keygen.add_argument('--public', required=True, type=Path)
    keygen.add_argument('--key-id', required=True)
    release = commands.add_parser('publish')
    release.add_argument('--app-metadata', required=True, type=Path)
    release.add_argument('--gateway-metadata', required=True, type=Path)
    release.add_argument('--notes', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'keygen':
        create_keys(args.private, args.public, args.key_id)
    else:
        publish(args.app_metadata, args.gateway_metadata, args.notes)


if __name__ == '__main__':
    main()
