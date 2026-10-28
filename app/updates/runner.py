"""One durable update transaction, independent of both the app and updater daemon."""

import copy
import shutil
import threading
import time

from app.updates.manifest import verify
from app.updates.service import TERMINAL, safe_error
from app.updates.state import atomic_json, file_lock, read_json
from app.updates.storage import backup, restore, prune_backups


class Cancelled(ValueError):
    pass


class Transaction:
    def __init__(self, updater):
        self.updater = updater
        self.job = updater.job()

    def save(self, phase, **values):
        self.job.update(phase=phase, **values)
        atomic_json(self.updater.root / 'job.json', self.job)
        self.updater.publish(phase)

    def check_cancelled(self):
        if read_json(self.updater.root / 'cancel.json', {}).get('id') == self.job['id']:
            raise Cancelled('The update was cancelled before replacing data.')

    def download(self):
        current = self.job['base']
        keys = dict(current['keys'])
        releases = []
        for raw in self.job['path']:
            release = verify(raw.encode(), keys, self.updater.config['repository'])
            releases.append(release)
            if release.trusted_keys:
                keys = release.trusted_keys
        if shutil.disk_usage(self.updater.root).free < 512 * 1024 * 1024:
            raise ValueError('At least 512 MiB of free space is required before downloading updates.')
        self.save('downloading')
        pulled = set()
        credentials = read_json(self.updater.root / 'credentials.json', {})
        for release in releases:
            for ref in release.images.values():
                self.check_cancelled()
                if ref not in pulled:
                    def progress(received, total):
                        self.check_cancelled()
                        self.updater.publish('downloading', received=received, total=total)
                    self.updater.docker.pull(ref, credentials, progress)
                    pulled.add(ref)
        return releases

    def drain(self):
        self.save('draining')
        self.updater.gate('draining', self.job['base']['version'])
        deadline = time.monotonic() + self.updater.config.get('drain_timeout', 7200)
        while time.monotonic() < deadline:
            self.check_cancelled()
            ready = self.updater.readiness()
            if ready.get('ready') and not ready.get('busy'):
                return
            time.sleep(1)
        raise ValueError('Existing work did not finish in time. Try the update again after the queue is empty.')

    def migrate(self, image, target):
        spec = copy.deepcopy(self.job['specs']['app'])
        spec.update(Cmd=['python', '-m', 'app.updates', 'migrate', str(target)], Entrypoint=None)
        spec['Env'] = [value for value in spec['Env'] if not value.startswith(('AWS_', 'MG_ADMIN_'))]
        spec['HostConfig'].update(RestartPolicy={'Name': 'no'}, NetworkMode='none', PortBindings={})
        spec['NetworkingConfig'] = {'EndpointsConfig': {}}
        engine = self.updater.docker
        engine.replace('migration', image, spec)
        deadline = time.monotonic() + 180
        try:
            while time.monotonic() < deadline:
                state = engine.inspect('migration')['State']
                if not state['Running']:
                    if state['ExitCode'] != 0:
                        raise ValueError('The data migration failed. Restoring the previous version.')
                    return
                time.sleep(1)
            raise ValueError('The data migration did not finish in time.')
        finally:
            engine.stop('migration')
            engine.remove('migration')

    def check_component(self, role, image):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            value = self.updater.docker.inspect(role)
            if value and value['Config']['Image'] == image and value['State'].get('Health', {}).get('Status') == 'healthy':
                return
            time.sleep(1)
        raise ValueError('An installation component did not become healthy in time.')

    def apply(self, release):
        updater = self.updater
        snapshot = updater.root / 'backups' / f"{self.job['id']}-{self.job['index']}"
        snapshot.parent.mkdir(exist_ok=True, mode=0o700)
        self.save('backing_up', backup=str(snapshot))
        updater.maintenance(True)
        updater.gate('blocked', self.job['base']['version'])
        updater.docker.stop('app')
        with file_lock(updater.data / '.service.lock'):
            backup(updater.data, snapshot, self.job['base'])
        self.save('migrating')
        self.migrate(release.images['app'], release.data_schema)
        self.save('starting')
        updater.gate('starting', release.version)
        updater.docker.replace('app', release.images['app'], self.job['specs']['app'])
        updater.await_ready(release.version, release.data_schema)
        self.save('components')
        for role in ('updater', 'gateway'):
            updater.docker.replace(role, release.images[role], self.job['specs'][role])
            self.check_component(role, release.images[role])
        candidate = {**self.job['base'], 'version': release.version, 'updater_version': release.version,
                     'schema': release.data_schema, 'images': release.images,
                     'keys': release.trusted_keys or self.job['base']['keys']}
        self.save('committing')
        updater.save_installation(candidate)
        specs = {role: updater.docker.snapshot(role) for role in ('app', 'updater', 'gateway')}
        self.save('committing', base=candidate, specs=specs, index=self.job['index'] + 1, backup=None)

    def rollback(self, error, cancelled=False):
        updater = self.updater
        destructive = self.job['phase'] not in {'queued', 'checking', 'downloading', 'draining'}
        if destructive:
            self.save('rolling_back', error=error)
            updater.maintenance(True)
            updater.gate('blocked', self.job['base']['version'])
            updater.docker.stop('migration')
            updater.docker.remove('migration')
            updater.docker.stop('app')
            if self.job.get('backup'):
                from pathlib import Path
                snapshot = Path(self.job['backup'])
                if read_json(snapshot / 'complete.json'):
                    with file_lock(updater.data / '.service.lock'):
                        self.job['base'] = restore(updater.data, snapshot)
                elif self.job['phase'] != 'rolling_back':
                    raise ValueError('The required backup is incomplete.')
                # No complete backup is only safe before migration; recorded below in run().
                elif self.job.get('data_changed'):
                    raise ValueError('The required backup is incomplete.')
            current = self.job['base']
            updater.save_installation(current)
            updater.gate('starting', current['version'])
            for role in ('app', 'updater', 'gateway'):
                updater.docker.replace(role, current['images'][role], self.job['specs'][role])
            updater.await_ready(current['version'], current['schema'])
            for role in ('updater', 'gateway'):
                self.check_component(role, current['images'][role])
        else:
            current = self.job['base']
            updater.gate('starting', current['version'])
            updater.docker.start('app')
            updater.await_ready(current['version'], current['schema'])
        self.save('cancelled' if cancelled else 'failed', error=error, needs_repair=False)
        updater.gate('running', current['version'])
        updater.maintenance(False)
        updater.publish(self.job['phase'])

    def run(self):
        with file_lock(self.updater.root / 'operation.lock'):
            if not self.job or self.job['phase'] in TERMINAL:
                return
            stopped = threading.Event()

            def heartbeat():
                while not stopped.wait(2):
                    status = read_json(self.updater.public / 'status.json', {})
                    self.updater.publish(self.job['phase'], received=status.get('received'), total=status.get('total'))

            pulse = threading.Thread(target=heartbeat, daemon=True)
            pulse.start()
            try:
                if self.job['phase'] != 'queued':
                    self.job['data_changed'] = self.job['phase'] not in {'checking', 'downloading', 'draining', 'backing_up'}
                    self.rollback('An interrupted update was safely recovered. Check for updates to retry.')
                    return
                self.save('checking')
                releases = self.download()
                self.drain()
                for release in releases:
                    self.apply(release)
                self.save('succeeded', error=None, needs_repair=False)
                self.updater.gate('running', self.job['base']['version'])
                self.updater.maintenance(False)
                self.updater.publish('succeeded')
                try:
                    prune_backups(self.updater.root / 'backups')
                except OSError:
                    pass  # A retention failure must not roll back a committed update.
            except Exception as exc:
                try:
                    self.job['data_changed'] = self.job['phase'] not in {'queued', 'checking', 'downloading', 'draining', 'backing_up'}
                    self.rollback(safe_error(exc), isinstance(exc, Cancelled))
                except Exception:
                    self.save('failed', error='Automatic recovery could not finish. Keep the backup and contact the server operator.', needs_repair=True)
                    self.updater.gate('blocked', self.job['base']['version'])
                    self.updater.maintenance(True)
                    self.updater.publish('failed')
            finally:
                stopped.set()
                pulse.join()

