"""Local bootstrap and explicit, non-destructive import of legacy jobs."""

import argparse
import asyncio
import base64
import getpass
import json
import sqlite3
import sys
from pathlib import Path

import pyotp

from app.config import AdminSetupSettings
from app.security import accounts as a, vault
from app.security.checkpoints import checkpoint


def setup_admin():
    from app.security.workspace import memory_policy
    memory_policy()
    if a.initialized():
        raise ValueError('Administrator setup is already complete.')
    config = AdminSetupSettings()
    credentials = (config.username, config.password, config.recovery_key)
    if any(value is not None for value in (*credentials, config.totp_secret)):
        if not all(credentials):
            raise ValueError('Set MG_ADMIN_USERNAME, MG_ADMIN_PASSWORD and MG_ADMIN_RECOVERY_KEY together for unattended setup.')
        draft = a.enrollment(config.username, config.password.get_secret_value())
        code = ''
        if config.totp_secret is not None:
            totp = pyotp.TOTP(config.totp_secret.get_secret_value().strip())
            try:
                if len(totp.byte_secret()) < 20:
                    raise ValueError
            except ValueError:
                raise ValueError('MG_ADMIN_TOTP_SECRET must be a Base32 key containing at least 20 bytes (160 bits).') from None
            draft['totp'], code = totp.secret, totp.now()
        recovery = config.recovery_key.get_secret_value().strip()
        try:
            recovery = base64.b64decode(recovery + '=' * (-len(recovery) % 4), altchars=b'-_', validate=True)
            if len(recovery) != 32:
                raise ValueError
        except ValueError:
            raise ValueError('MG_ADMIN_RECOVERY_KEY must be a URL-safe Base64 key containing exactly 32 bytes.') from None
        draft['recovery'] = recovery
        a.finish_enrollment(draft, code, setup=True)
        print('Administrator created from deployment configuration.')
        return
    if not sys.stdin.isatty():
        raise ValueError('Set MG_ADMIN_USERNAME, MG_ADMIN_PASSWORD and MG_ADMIN_RECOVERY_KEY, or run uv run python -m app.security setup in a terminal.')
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
    print('Save this personal recovery key outside the server:', a.recovery_text(draft))
    a.finish_enrollment(draft, setup=True)
    print('Administrator created. Keep the recovery key: it is not shown again.')
    print('Two-factor authentication can be configured later in Users.')


def unlock():
    from app.security.workspace import memory_policy
    memory_policy()
    person = a.authenticate(input('Username: '), getpass.getpass('Password: '),
                            input('Authenticator code (leave blank if not configured): ').strip())
    if a.two_factor_required(person) and not a.two_factor_enabled(person):
        raise ValueError('Set up two-factor authentication in the web app before using the CLI.')
    return person


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
        from app.updates.state import assert_start_allowed
        with a.service_lock():
            assert_start_allowed(cli=True)
            if args.command == 'setup':
                setup_admin()
            else:
                token = a.current_identity.set(unlock())
                try:
                    asyncio.run(import_legacy(args.source))
                finally:
                    a.current_identity.reset(token)
    except (ValueError, PermissionError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
