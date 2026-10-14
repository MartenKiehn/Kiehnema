"""Local bootstrap and explicit, non-destructive import of legacy jobs."""

import argparse
import asyncio
import getpass
import json
import sqlite3
import sys
from pathlib import Path

import pyotp

from app.security import accounts as a, vault
from app.security.checkpoints import checkpoint


def setup_admin():
    from app.security.workspace import memory_policy
    memory_policy()
    if a.initialized():
        raise ValueError('Administrator setup is already complete.')
    if not sys.stdin.isatty():
        raise ValueError('Run uv run python -m app.security setup in a terminal to create the first administrator.')
    while True:
        try:
            name = a.username(input('Administrator username [admin]: ').strip() or 'admin')
            break
        except ValueError as exc:
            print(str(exc))
    while True:
        password = getpass.getpass('Password (15–256 characters): ')
        try:
            a.password_bytes(password)
        except ValueError as exc:
            print(str(exc))
            continue
        if password == getpass.getpass('Repeat password: '):
            break
        print('Passwords do not match. Please try again.')
    draft = a.enrollment(name, password)
    print('Add this time-based setup key to your authenticator:', draft['totp'])
    print('Save this personal recovery key outside the server:', a.recovery_text(draft))
    code = input('Current authenticator code: ').strip()
    a.finish_enrollment(draft, code, setup=True)
    print('Administrator created. Keep the recovery key: it is not shown again.')


def unlock():
    from app.security.workspace import memory_policy
    memory_policy()
    return a.authenticate(input('Username: '), getpass.getpass('Password: '), input('Authenticator code: ').strip())


async def import_legacy(source: Path):
    if not a.identity().admin:
        raise PermissionError('Only the administrator can import legacy jobs.')
    source = source.resolve(strict=True)
    jobs = source / 'jobs'
    if not jobs.is_dir():
        raise ValueError('Choose the old data directory containing jobs/.')
    for directory in sorted(jobs.iterdir()):
        if not directory.is_dir() or directory.is_symlink() or not (directory / 'request.json').is_file():
            continue
        with a.database() as con:
            exists = con.execute('SELECT 1 FROM jobs WHERE id=?', (directory.name,)).fetchone()
        if exists:
            print(f'Skipped existing job {directory.name}')
            continue
        job = vault.create(directory.name)
        try:
            job.import_tree(directory)
            old_db = source / 'jobs.sqlite'
            if old_db.is_file():
                with sqlite3.connect(old_db.as_uri() + '?mode=ro', uri=True) as old:
                    async with checkpoint(job) as saver:
                        await saver.setup()
                        for table in ('checkpoints', 'writes'):
                            rows = old.execute(f'SELECT * FROM {table} WHERE thread_id=?', (directory.name,)).fetchall()
                            if rows:
                                placeholders = ','.join('?' for _ in rows[0])
                                await saver.conn.executemany(f'INSERT INTO {table} VALUES({placeholders})', rows)
                        await saver.conn.commit()
                        await saver.persist()
            for item in job.entries():
                with job.reader(item['name']) as stream:
                    while stream.read(1024 * 1024):
                        pass
        except BaseException:
            from app.pipeline.jobs import delete_job
            await delete_job(job.id)
            raise
        print(f'Imported and verified {job.id}')
    print('Legacy files were NOT deleted. Retire old plaintext files, snapshots and backups separately.')


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('setup')
    migration = commands.add_parser('import-legacy')
    migration.add_argument('source', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'setup':
            setup_admin()
        else:
            token = a.current_identity.set(unlock())
            try:
                with a.service_lock():
                    asyncio.run(import_legacy(args.source))
            finally:
                a.current_identity.reset(token)
    except (ValueError, PermissionError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
