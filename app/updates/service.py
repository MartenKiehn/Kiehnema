"""Local updater control plane. Durable work is handed to an independent runner."""

import asyncio
import hmac
import json
import random
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.updates.docker import Docker
from app.updates.manifest import resolve
from app.updates.releases import GitHub
from app.updates.state import atomic_bytes, atomic_json, boot_id, file_lock, read_json, sync_directory

TERMINAL = {'succeeded', 'failed', 'cancelled'}
MESSAGES = {
    'idle': 'Kiehnema is ready.', 'queued': 'Preparing the update.',
    'checking': 'Checking update requirements.', 'downloading': 'Downloading and verifying the update.',
    'draining': 'Waiting for current work to finish. New changes are paused.',
    'backing_up': 'Saving a verified backup.', 'migrating': 'Updating the data format.',
    'starting': 'Starting and checking the new version.', 'components': 'Updating installation services.',
    'committing': 'Finishing the update.', 'rolling_back': 'Restoring the previous working version.',
    'succeeded': 'Update complete. Sign in again.', 'failed': 'The update could not be completed.',
    'cancelled': 'Update cancelled. The previous version is available.',
    'recovering': 'Checking the installation after a restart.',
}


def safe_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return 'Access denied. Check the update credentials and their expiry.'
        return f'The update service returned HTTP {exc.response.status_code}.'
    if isinstance(exc, httpx.RequestError):
        return 'The update service could not be reached. Try again later.'
    if isinstance(exc, ValueError):
        return str(exc)[:500]
    return 'The operation failed. The installation has retained its recovery information.'


class Updater:
    def __init__(self, root=Path('/updates'), data=Path('/data'), docker=None):
        self.root, self.data = root, data
        self.config = read_json(root / 'config.json')
        self.docker = docker or Docker(self.config['installation'])
        self.control, self.public = root / 'control', root / 'public'
        self.checking = False

    def installed(self):
        return read_json(self.root / 'installed.json')

    def job(self):
        return read_json(self.root / 'job.json', {})

    def busy(self):
        job = self.job()
        return bool(job) and job['phase'] not in TERMINAL

    def gate(self, mode, version):
        atomic_json(self.control / 'gate.json', {'mode': mode, 'version': version, 'boot_id': boot_id()}, 0o644)

    def maintenance(self, enabled):
        marker = self.public / 'maintenance'
        if enabled:
            marker.touch(mode=0o644)
        else:
            marker.unlink(missing_ok=True)
        sync_directory(self.public)

    def publish(self, phase, *, received=None, total=None):
        atomic_json(self.public / 'status.json', {'phase': phase, 'message': MESSAGES.get(phase, 'Updating Kiehnema.'),
            'updated_at': time.time(), 'maintenance': (self.public / 'maintenance').exists(),
            'needs_repair': bool(self.job().get('needs_repair')),
            'received': received, 'total': total}, 0o644)

    def readiness(self):
        token = (self.control / 'token').read_text().strip()
        origin = self.config['public_origin']
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get('http://app:8000/_internal/update-ready', headers={
                'Authorization': 'Bearer ' + token, 'Host': httpx.URL(origin).netloc.decode(),
                'X-Forwarded-Proto': httpx.URL(origin).scheme,
            })
            response.raise_for_status()
            return response.json()

    def await_ready(self, version, schema, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ready = self.readiness()
                if ready.get('ready') and ready.get('version') == version and ready.get('schema') == schema:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(1)
        raise ValueError('The replacement application did not become ready in time.')

    def save_installation(self, value):
        atomic_json(self.root / 'installed.json', value)
        atomic_bytes(self.root / 'images.env', ''.join(f'KIEHNEMA_{role.upper()}_IMAGE={ref}\n' for role, ref in value['images'].items()).encode())

    def check(self, credentials=None):
        with file_lock(self.root / 'check.lock'):
            if self.busy():
                return
            credentials = credentials if credentials is not None else read_json(self.root / 'credentials.json', {})
            current = self.installed()
            client = GitHub(self.config, credentials)
            self.checking = True
            try:
                releases, envelopes = client.releases(current['keys'], self.config['repository'])
                path, signed_path = resolve(current, self.config['platform'], list(envelopes.values()), self.config['repository'])
                catalog = {'checked_at': time.time(), 'error': None,
                           'path': signed_path,
                           'available': [{'version': item.version, 'notes': item.notes} for item in path]}
                atomic_json(self.root / 'catalog.json', catalog)
            finally:
                self.checking = False
                client.close()

    def record_check_error(self, exc):
        previous = read_json(self.root / 'catalog.json', {})
        atomic_json(self.root / 'catalog.json', {**previous, 'error': safe_error(exc), 'attempted_at': time.time()})

    def status(self):
        current = self.installed()
        catalog = read_json(self.root / 'catalog.json', {})
        job = self.job()
        return {'version': current['version'], 'updater_version': current['updater_version'],
                'checked_at': catalog.get('checked_at'), 'checking': self.checking, 'error': catalog.get('error'),
                'available': catalog.get('available', []), 'busy': self.busy(),
                'operation': {key: job[key] for key in ('id', 'phase', 'error', 'target') if key in job},
                'progress': read_json(self.public / 'status.json', {}),
                'credentials_configured': bool(read_json(self.root / 'credentials.json', {}).get('token'))}

    def install(self, target):
        with file_lock(self.root / 'command.lock'):
            if self.job().get('needs_repair'):
                raise ValueError('Recovery must finish before another update can start.')
            if self.busy():
                return self.job()['id']
            catalog = read_json(self.root / 'catalog.json', {})
            if catalog.get('error') or not catalog.get('path'):
                raise ValueError('Check for an available update before installing.')
            current = self.installed()
            path, signed_path = resolve(current, self.config['platform'], catalog['path'], self.config['repository'])
            if not path or path[-1].version != target:
                raise ValueError('The selected update changed. Check for updates again.')
            specs = {role: self.docker.snapshot(role) for role in ('app', 'updater', 'gateway')}
            operation = secrets.token_hex(16)
            atomic_json(self.root / 'job.json', {'id': operation, 'phase': 'queued', 'target': target,
                'path': signed_path, 'index': 0, 'base': current, 'specs': specs, 'backup': None,
                'created_at': time.time(), 'cancel': False, 'error': None})
            self.publish('queued')
            self.ensure_runner()
            return operation

    def cancel(self):
        with file_lock(self.root / 'command.lock'):
            job = self.job()
            if job.get('phase') not in {'queued', 'checking', 'downloading', 'draining'}:
                raise ValueError('The update can no longer be cancelled while data is being replaced.')
            # A separate marker avoids racing the runner's durable state transitions.
            atomic_json(self.root / 'cancel.json', {'id': job['id']})

    def ensure_runner(self):
        with file_lock(self.root / 'runner-start.lock'):
            job = self.job()
            if not job or job['phase'] in TERMINAL:
                return
            existing = self.docker.inspect('runner')
            if existing and (existing['State']['Running'] or existing['State'].get('Restarting')):
                return
            attempts = read_json(self.root / 'runner-attempts.json', {})
            count = attempts.get('count', 0) if attempts.get('id') == job['id'] else 0
            if count >= 3:
                with file_lock(self.root / 'operation.lock'):
                    atomic_json(self.root / 'job.json', {**job, 'phase': 'failed', 'needs_repair': True,
                        'error': 'The recovery process repeatedly stopped. Keep the backups and contact the server operator.'})
                    self.gate('blocked', job['base']['version'])
                    self.maintenance(True)
                    self.publish('failed')
                return
            atomic_json(self.root / 'runner-attempts.json', {'id': job['id'], 'count': count + 1})
            self.docker.run_helper('runner', job['base']['images']['updater'], job['specs']['updater'])

    def recover_idle(self):
        with file_lock(self.root / 'command.lock'), file_lock(self.root / 'operation.lock'):
            if not self.busy():
                self._recover_idle()

    def _recover_idle(self):
        if self.job().get('needs_repair'):
            self.maintenance(True)
            self.publish('failed')
            return
        current = self.installed()
        self.maintenance(True)
        self.publish('recovering')
        self.gate('starting', current['version'])
        self.docker.start('app')
        self.await_ready(current['version'], current['schema'])
        self.gate('running', current['version'])
        self.maintenance(False)
        self.publish(self.job().get('phase', 'idle'))


def create_app(updater):
    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()

        async def supervise():
            next_check = 0
            recovered = False
            while not stop.is_set():
                try:
                    if updater.busy():
                        await asyncio.to_thread(updater.ensure_runner)
                        recovered = False
                    else:
                        if not recovered:
                            await asyncio.to_thread(updater.recover_idle)
                            recovered = True
                        updater.publish(updater.job().get('phase', 'idle'))
                        if time.time() >= next_check and not updater.checking:
                            try:
                                await asyncio.to_thread(updater.check)
                                next_check = time.time() + 21600 + random.uniform(-1800, 1800)
                            except Exception as exc:
                                updater.record_check_error(exc)
                                next_check = time.time() + random.uniform(300, 600)
                except Exception as exc:
                    updater.record_check_error(exc)
                try:
                    await asyncio.wait_for(stop.wait(), 2)
                except asyncio.TimeoutError:
                    pass

        task = asyncio.create_task(supervise())
        yield
        stop.set()
        await task
        check_task = getattr(app.state, 'check_task', None)
        if check_task:
            await check_task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def authenticate(request, call_next):
        token = (updater.control / 'token').read_text().strip()
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
            return JSONResponse({'error': 'Not authorized.'}, status_code=403)
        try:
            return await call_next(request)
        except (ValueError, BlockingIOError, httpx.HTTPError) as exc:
            return JSONResponse({'error': safe_error(exc)}, status_code=409)

    async def body(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384:
                raise ValueError('Request too large.')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Invalid request.')
        return value

    @app.get('/status')
    async def status():
        return updater.status()

    @app.post('/check')
    async def check():
        if updater.checking:
            return {'checking': True}
        async def perform():
            try:
                await asyncio.to_thread(updater.check)
            except Exception as exc:
                updater.record_check_error(exc)
            finally:
                updater.checking = False
        task = getattr(app.state, 'check_task', None)
        if task is None or task.done():
            updater.checking = True
            app.state.check_task = asyncio.create_task(perform())
        return {'checking': True}

    @app.post('/install', status_code=202)
    async def install(request: Request):
        data = await body(request)
        return {'id': await asyncio.to_thread(updater.install, data.get('version'))}

    @app.post('/cancel')
    async def cancel():
        await asyncio.to_thread(updater.cancel)
        return {'ok': True}

    @app.post('/credentials')
    async def credentials(request: Request):
        data = await body(request)
        if updater.busy():
            raise ValueError('Wait for the current update to finish.')
        if set(data) != {'username', 'token'} or not all(isinstance(v, str) and len(v) <= 8192 for v in data.values()):
            raise ValueError('Invalid update credentials.')
        def test_access():
            github = GitHub(updater.config, data)
            try:
                github.download(github.api + '/releases?per_page=1', limit=1024 * 1024)
            finally:
                github.close()
        await asyncio.to_thread(test_access)
        atomic_json(updater.root / 'credentials.json', data)
        await check()
        return {'ok': True}

    return app
