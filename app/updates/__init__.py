"""Signed releases and the Linux installation updater."""

import tomllib
from pathlib import Path

VERSION = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text())['project']['version']
PROTOCOL = 1
DATA_SCHEMA = 1

