"""Durable updater files; the application only reads the control directory."""

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return default


def sync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path: Path, value: bytes, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_json(path: Path, value, mode=0o600):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), mode)


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def gate():
    from app.config import settings
    if settings.update_control_dir is None:
        return None
    return read_json(settings.update_control_dir / 'gate.json', {})


def assert_start_allowed(*, cli=False):
    from app.updates import VERSION
    value = gate()
    if value is None:
        return
    if (value.get('boot_id') != boot_id() or value.get('version') != VERSION
            or value.get('mode') not in ({'running'} if cli else {'running', 'starting'})):
        raise ValueError('An update is in progress. Wait for the updater to release this installation.')


def wait_for_start():
    from app.config import settings
    if settings.update_control_dir is None:
        return
    while True:
        try:
            assert_start_allowed()
            return
        except (ValueError, OSError):
            time.sleep(1)


def writes_allowed():
    value = gate()
    return value is None or (value.get('mode') == 'running' and value.get('boot_id') == boot_id())

