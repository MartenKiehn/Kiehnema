"""Local readiness checks: no credentials, external websites or model calls."""

import asyncio
import sqlite3
import tempfile
from pathlib import Path

from app.config import settings
from app.security.workspace import ram_root, runtime_root, browser_environment
from app.updates import DATA_SCHEMA


def schema():
    with sqlite3.connect((settings.data_dir / 'secure/accounts.sqlite').as_uri() + '?mode=ro', uri=True) as con:
        if con.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('The account database is not ready.')
        stored = con.execute('PRAGMA user_version').fetchone()[0]
        if stored not in (0, DATA_SCHEMA):
            raise ValueError('Unsupported account database schema.')
    return DATA_SCHEMA


async def runtime():
    import imageio_ffmpeg
    from playwright.async_api import async_playwright
    root = ram_root()
    with tempfile.TemporaryDirectory(dir=root, prefix='readiness-') as directory:
        token = runtime_root.set(Path(directory))
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(chromium_sandbox=True, env=browser_environment())
                await browser.close()
            process = await asyncio.create_subprocess_exec(imageio_ffmpeg.get_ffmpeg_exe(), '-version',
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                if await asyncio.wait_for(process.wait(), 15):
                    raise ValueError('FFmpeg is not ready.')
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        finally:
            runtime_root.reset(token)
    return schema()


def migrate(target: int):
    from app.security.accounts import service_lock
    if target != DATA_SCHEMA:
        raise ValueError('This image cannot migrate to the requested schema.')
    with service_lock():
        schema()
        with sqlite3.connect(settings.data_dir / 'secure/accounts.sqlite') as con:
            con.execute(f'PRAGMA user_version={DATA_SCHEMA}')

