"""Offline backup and repeatable restore of the complete encrypted store."""

import hashlib
import os
import shutil
import sqlite3
import re
from pathlib import Path

from app.updates.state import atomic_json, read_json, sync_directory


def inventory(root: Path):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('The data store must contain only regular files and directories.')
        if path.is_file():
            with path.open('rb') as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def durable_tree(root: Path):
    for path in root.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    for path in sorted((p for p in root.rglob('*') if p.is_dir()), reverse=True):
        sync_directory(path)
    sync_directory(root)


def check_database(root: Path):
    path = root / 'accounts.sqlite'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as con:
        if con.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('The account database failed its integrity check.')


def backup(data: Path, destination: Path, installation):
    source = data / 'secure'
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Missing or unsafe encrypted data directory.')
    # Validate before copying: copytree must never follow a link outside the encrypted store.
    hashes = inventory(source)
    check_database(source)
    size = sum((source / name).stat().st_size for name in hashes)
    if shutil.disk_usage(destination.parent).free < size * 3 + 256 * 1024 * 1024:
        raise ValueError('Not enough free space for a verified backup and restoration.')
    if destination.exists():
        raise ValueError('Backup destination already exists.')
    destination.mkdir(mode=0o700)
    shutil.copytree(source, destination / 'secure', symlinks=True)
    if inventory(destination / 'secure') != hashes:
        raise ValueError('Backup verification failed.')
    durable_tree(destination / 'secure')
    atomic_json(destination / 'complete.json', {'files': hashes, 'installation': installation})
    sync_directory(destination.parent)


def restore(data: Path, source: Path):
    complete = read_json(source / 'complete.json')
    if not complete or inventory(source / 'secure') != complete['files']:
        raise ValueError('No complete, verified backup is available.')
    check_database(source / 'secure')
    live, staging, discarded = (data / name for name in ('secure', '.restore-new', '.restore-old'))
    if any(path.is_symlink() for path in (live, staging, discarded)):
        raise ValueError('Unsafe restore directory.')
    # The source backup is immutable; rebuilding staging also recovers an interrupted copy.
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source / 'secure', staging, symlinks=True)
    if inventory(staging) != complete['files']:
        raise ValueError('Restored files failed verification.')
    if os.geteuid() == 0:
        for path in [staging, *staging.rglob('*')]:
            os.chown(path, 1000, 1000)
    durable_tree(staging)
    if live.exists():
        if discarded.exists():
            shutil.rmtree(discarded)
        os.replace(live, discarded)
        sync_directory(data)
    os.replace(staging, live)
    sync_directory(data)
    check_database(live)
    return complete['installation']


def prune_backups(root: Path):
    complete = [p for p in root.iterdir() if re.fullmatch(r'[a-f0-9]{32}-\d+', p.name)
                and not p.is_symlink() and (p / 'complete.json').is_file()]
    for path in sorted(complete, key=lambda p: p.stat().st_mtime, reverse=True)[2:]:
        shutil.rmtree(path)
    sync_directory(root)
