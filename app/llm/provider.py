"""Instance-wide AI configuration. Credentials are sealed and never returned to browsers."""

import json
import re
import secrets
from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.config import settings
from app.security.accounts import seal, unseal
from app.updates.state import atomic_bytes, file_lock


class Provider(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['bedrock', 'openai', 'anthropic']
    model: str = Field(min_length=1, max_length=300)
    max_output_tokens: int = Field(default=64000, ge=1024, le=128000)
    region: str = 'eu-central-1'
    auth: Literal['environment', 'keys'] = 'keys'
    api_key: SecretStr = Field(default=SecretStr(''), repr=False)
    access_key: SecretStr = Field(default=SecretStr(''), repr=False)
    secret_key: SecretStr = Field(default=SecretStr(''), repr=False)
    session_token: SecretStr = Field(default=SecretStr(''), repr=False)

    @model_validator(mode='after')
    def valid(self):
        if not re.fullmatch(r'[A-Za-z0-9._:/-]+', self.model):
            raise ValueError('Invalid model or inference profile ID.')
        for name in SECRET_FIELDS:
            value = getattr(self, name).get_secret_value()
            if len(value) > 8192 or any(ord(char) < 32 for char in value):
                raise ValueError('Invalid credential format.')
        if self.provider == 'bedrock':
            if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', self.region):
                raise ValueError('Invalid AWS region.')
            if self.auth == 'keys' and not (self.access_key.get_secret_value() and self.secret_key.get_secret_value()):
                raise ValueError('AWS access key and secret key are required.')
            self.api_key = SecretStr('')
            if self.auth == 'environment':
                self.access_key = self.secret_key = self.session_token = SecretStr('')
        else:
            if not self.api_key.get_secret_value():
                raise ValueError('An API key is required.')
            self.auth = 'keys'
            self.access_key = self.secret_key = self.session_token = SecretStr('')
        return self

    def private(self):
        value = self.model_dump(mode='json')
        value.update({name: getattr(self, name).get_secret_value() for name in SECRET_FIELDS})
        return value

    def public(self):
        return {**self.model_dump(exclude=SECRET_FIELDS),
                'credentials_saved': any(getattr(self, field).get_secret_value() for field in SECRET_FIELDS)}


SECRET_FIELDS = {'api_key', 'access_key', 'secret_key', 'session_token'}
current: ContextVar[Provider | None] = ContextVar('ai_provider', default=None)


def load() -> Provider | None:
    path = settings.data_dir / 'secure/provider.enc'
    if path.exists():
        try:
            if path.is_symlink() or path.parent.is_symlink() or (path.parent / 'provider.key').is_symlink():
                raise ValueError('Invalid configuration path.')
            raw = unseal((path.parent / 'provider.key').read_bytes(), path.read_bytes(), 'kiehnema:provider:1')
            return Provider.model_validate_json(raw)
        except Exception:
            raise ValueError('AI settings could not be unlocked. Restore provider.enc and provider.key together.') from None
    if settings.provider_setup_required:
        return None
    return Provider(provider='bedrock', model=settings.bedrock_model_id, region=settings.aws_region, auth='environment')


def require() -> Provider:
    value = current.get() or load()
    if value is None:
        raise ValueError('An administrator must configure the AI provider before creating a video.')
    return value


def save(value: Provider):
    root = settings.data_dir / 'secure'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise ValueError('The secure directory must not be a symlink.')
    with file_lock(root / 'provider.lock'):
        keyfile = root / 'provider.key'
        if not keyfile.exists():
            if (root / 'provider.enc').exists():
                raise ValueError('Provider key is missing. Restore it before changing AI settings.')
            atomic_bytes(keyfile, secrets.token_bytes(32))
        if keyfile.is_symlink() or len(keyfile.read_bytes()) != 32:
            raise ValueError('Invalid provider key file.')
        atomic_bytes(root / 'provider.enc', seal(keyfile.read_bytes(), json.dumps(value.private()).encode(), 'kiehnema:provider:1'))
