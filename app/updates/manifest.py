"""A deliberately small stable-release format. Sign the original bytes, not reserialized JSON."""

import base64
import json
import re
from datetime import datetime, timezone
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MANIFEST = 128 * 1024
ROLES = {'app', 'updater', 'gateway'}


class UnsupportedRelease(ValueError):
    pass


def version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})', value):
        raise ValueError('Expected a stable MAJOR.MINOR.PATCH version.')
    return tuple(map(int, value.split('.')))


def public_key(value: str):
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(value, validate=True))


class Release(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    format: Literal[1] = 1
    product: Literal['kiehnema'] = 'kiehnema'
    version: str
    minimum_app: str
    minimum_updater: str
    protocol: Literal[1] = 1
    layout: Literal[1] = 1
    source_schemas: list[int] = Field(min_length=1, max_length=20)
    data_schema: int = Field(ge=1, le=10000)
    platforms: list[Literal['linux/amd64', 'linux/arm64']] = Field(min_length=1, max_length=2)
    images: dict[str, str]
    commit: str = Field(pattern=r'^[a-f0-9]{40,64}$')
    published_at: str
    notes: str = Field(default='', max_length=16000)
    trusted_keys: dict[str, str] | None = None

    @field_validator('version', 'minimum_app', 'minimum_updater')
    @classmethod
    def stable_version(cls, value):
        version(value)
        return value

    @field_validator('images')
    @classmethod
    def digest_images(cls, value):
        if set(value) != ROLES:
            raise ValueError('A release must pin app, updater and gateway images.')
        for ref in value.values():
            if not re.fullmatch(r'[a-z0-9][a-z0-9.:-]*/[a-z0-9._/-]+@sha256:[a-f0-9]{64}', ref):
                raise ValueError('Images must use a registry repository and immutable SHA256 digest.')
        return value

    @field_validator('published_at')
    @classmethod
    def published(cls, value):
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError('Release date must include its timezone.')
        return value

    @field_validator('trusted_keys')
    @classmethod
    def keys(cls, value):
        if value is not None:
            if not 1 <= len(value) <= 5:
                raise ValueError('Supply one to five trusted release keys.')
            for key_id, key in value.items():
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', key_id):
                    raise ValueError('Invalid signing key ID.')
                public_key(key)
        return value


def verify(envelope: bytes, keys: dict[str, str], repository: str) -> Release:
    if len(envelope) > MAX_MANIFEST:
        raise ValueError('Release manifest is too large.')
    try:
        signed = json.loads(envelope)
        raw = base64.b64decode(signed['manifest'], validate=True)
        signature = base64.b64decode(signed['signature'], validate=True)
        public_key(keys[signed['key_id']]).verify(signature, raw)
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise ValueError('Release signature or manifest is invalid.') from exc
    metadata = json.loads(raw)
    if any(metadata.get(key, 1) != 1 for key in ('format', 'protocol', 'layout')):
        raise UnsupportedRelease('This release requires a newer updater protocol.')
    release = Release.model_validate_json(raw)
    if datetime.fromisoformat(release.published_at.replace('Z', '+00:00')) > datetime.now(timezone.utc):
        raise ValueError('This release has not been published yet.')
    for ref in release.images.values():
        if ref.split('@')[0] not in {repository, repository + '/gateway'}:
            raise ValueError('Release image is outside the configured repository.')
    return release


def resolve(current: dict, platform: str, envelopes: list[str], repository: str):
    """Include the signed key-transition release before accepting a newly trusted key."""
    candidates = []
    for envelope in envelopes:
        signed = json.loads(envelope)
        metadata = json.loads(base64.b64decode(signed['manifest'], validate=True))
        candidates.append((version(metadata['version']), signed['key_id'], envelope))
    paths = [(current, [], [])]
    for candidate_version, key_id, envelope in sorted(candidates):
        best = {}
        for state, releases, raw_path in list(paths):
            if candidate_version <= version(state['version']) or key_id not in state['keys']:
                continue
            try:
                release = verify(envelope.encode(), state['keys'], repository)
            except UnsupportedRelease:
                continue
            if (platform not in release.platforms or state['schema'] not in release.source_schemas
                    or version(release.minimum_app) > version(state['version'])
                    or version(release.minimum_updater) > version(state['updater_version'])):
                continue
            next_state = {'version': release.version, 'updater_version': release.version, 'schema': release.data_schema,
                          'keys': release.trusted_keys or state['keys']}
            option = (next_state, releases + [release], raw_path + [envelope])
            trust = tuple(sorted(next_state['keys'].items()))
            if trust not in best or len(option[1]) < len(best[trust][1]):
                best[trust] = option
        paths.extend(best.values())
    return max(paths, key=lambda item: (version(item[0]['version']), -len(item[1])))[1:]

