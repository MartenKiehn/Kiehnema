"""Runtime settings (env vars or .env). Defaults match the Phase-0 spike results."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="MG_", extra="ignore")

    # EU cross-region inference profile; verified ACTIVE in eu-central-1 (Phase 0).
    bedrock_model_id: str = "eu.anthropic.claude-opus-5-5"
    aws_region: str = "eu-central-1"
    aws_profile: str | None = None
    provider_setup_required: bool = False

    # Spike: effort=medium can spend >16k tokens thinking before the first text token,
    # so generous output budgets are required for code generation.
    max_tokens_default: int = 16_000
    max_tokens_compose: int = 64_000
    read_timeout_s: int = 1_800

    data_dir: Path = ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8000
    public_origin: str = "http://127.0.0.1:8000"
    # Plaintext processing requires a verified RAM filesystem; never fall back to /tmp.
    work_dir: Path = Path("/dev/shm/kiehnema")
    job_timeout_s: int = 7200
    update_control_dir: Path | None = None
    trusted_proxies: str = '127.0.0.1'


class AdminSetupSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="MG_ADMIN_", extra="ignore")

    username: str | None = None
    password: SecretStr | None = None
    totp_secret: SecretStr | None = None
    recovery_key: SecretStr | None = None


settings = Settings()
