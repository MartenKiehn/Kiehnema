import copy
import json
import sqlite3
from pathlib import Path

import pytest

from app.updates.runner import Transaction
from app.updates.service import Updater
from app.updates.state import atomic_json, read_json
from tests.test_updates import REPOSITORY, release_data, signer, encrypted_store


class Engine:
    def __init__(self, images):
        self.images = dict(images)
        self.started = set(images)
        self.actions = []

    def snapshot(self, role):
        return {'Image': self.images[role], 'Env': [], 'HostConfig': {}, 'NetworkingConfig': {'EndpointsConfig': {}}}

    def inspect(self, role):
        if role not in self.images:
            return None
        return {'Config': {'Image': self.images[role]}, 'State': {'Running': role in self.started,
                                                               'Health': {'Status': 'healthy'}}}

    def start(self, role):
        self.started.add(role)

    def stop(self, role):
        self.actions.append(('stop', role))
        self.started.discard(role)

    def remove(self, role):
        self.images.pop(role, None)

    def replace(self, role, image, specification):
        self.actions.append(('replace', role, image))
        self.images[role] = image
        self.started.add(role)

    def run_helper(self, role, image, specification):
        self.actions.append(('helper', role))

    def pull(self, image, credentials, progress):
        self.actions.append(('pull', image))
        progress(100, 100)


@pytest.fixture
def installation(encrypted_store, signer, tmp_path, monkeypatch):
    keys, sign = signer
    root = tmp_path / 'updates'
    for name in ('control', 'public'):
        (root / name).mkdir(parents=True)
    baseline = {'version': '0.1.0', 'updater_version': '0.1.0', 'schema': 1, 'keys': keys,
                'images': {role: REPOSITORY + ('/gateway' if role == 'gateway' else '') + '@sha256:' + '0' * 64
                           for role in ('app', 'updater', 'gateway')}}
    atomic_json(root / 'config.json', {'installation': 'test', 'repository': REPOSITORY,
                                    'platform': 'linux/arm64', 'drain_timeout': 0.01})
    atomic_json(root / 'installed.json', baseline)
    atomic_json(root / 'catalog.json', {'path': [sign(release_data()).decode()],
                                      'available': [{'version': '0.2.0', 'notes': 'test'}], 'error': None})
    engine = Engine(baseline['images'])
    service = Updater(root, encrypted_store, engine)
    monkeypatch.setattr('app.updates.service.boot_id', lambda: 'test-boot')
    monkeypatch.setattr(service, 'readiness', lambda: {'ready': True, 'busy': False})
    monkeypatch.setattr(service, 'await_ready', lambda *args: None)
    return service, sign


def launch(service, monkeypatch, migration=None):
    service.install('0.2.0')
    transaction = Transaction(service)
    monkeypatch.setattr(transaction, 'migrate', migration or (lambda *args: None))
    return transaction


def test_success_updates_all_components_and_releases_gate(installation, monkeypatch):
    service, _ = installation
    transaction = launch(service, monkeypatch)
    transaction.run()
    assert service.job()['phase'] == 'succeeded'
    assert service.installed()['version'] == '0.2.0'
    assert read_json(service.control / 'gate.json')['mode'] == 'running'
    assert not (service.public / 'maintenance').exists()
    assert (service.root / 'images.env').read_text().count('@sha256:') == 3
    assert len(list((service.root / 'backups').glob('*/complete.json'))) == 1


def test_migration_failure_restores_data_and_all_digests(installation, monkeypatch):
    service, _ = installation
    original = copy.deepcopy(service.installed())
    def fail(*args):
        (service.data / 'secure/ciphertext').write_bytes(b'partial migration')
        raise ValueError('migration failed')
    launch(service, monkeypatch, fail).run()
    assert service.job()['phase'] == 'failed'
    assert not service.job()['needs_repair']
    assert service.installed() == original
    assert service.docker.images == original['images']
    assert (service.data / 'secure/ciphertext').read_bytes() == b'encrypted fixture bytes'
    assert not (service.public / 'maintenance').exists()


def test_cancel_and_duplicate_install_do_not_stop_jobs(installation, monkeypatch):
    service, _ = installation
    first = service.install('0.2.0')
    assert service.install('0.2.0') == first
    service.cancel()
    Transaction(service).run()
    assert service.job()['phase'] == 'cancelled'
    assert not any(action[0] == 'stop' for action in service.docker.actions)


def test_busy_queue_times_out_without_killing_work(installation, monkeypatch):
    service, _ = installation
    monkeypatch.setattr(service, 'readiness', lambda: {'ready': True, 'busy': True})
    launch(service, monkeypatch).run()
    assert service.job()['phase'] == 'failed'
    assert 'did not finish' in service.job()['error']
    assert not any(action[0] == 'stop' for action in service.docker.actions)
    assert read_json(service.control / 'gate.json')['mode'] == 'running'


class PowerLoss(BaseException):
    pass


@pytest.mark.parametrize('phase', ['backing_up', 'migrating', 'starting', 'components', 'committing'])
def test_restart_at_each_destructive_phase_restores_consistent_state(installation, monkeypatch, phase):
    service, _ = installation
    transaction = launch(service, monkeypatch)
    original_save = transaction.save
    def crash(current, **values):
        original_save(current, **values)
        if current == phase:
            raise PowerLoss()
    monkeypatch.setattr(transaction, 'save', crash)
    with pytest.raises(PowerLoss):
        transaction.run()
    recovered = Transaction(service)
    recovered.run()
    assert service.job()['phase'] == 'failed'
    assert not service.job()['needs_repair']
    assert service.installed()['version'] == '0.1.0'
    assert (service.data / 'secure/ciphertext').read_bytes() == b'encrypted fixture bytes'
    assert read_json(service.control / 'gate.json')['mode'] == 'running'


def test_failed_restore_stays_closed_and_preserves_backup(installation, monkeypatch):
    service, _ = installation
    def fail(*args):
        (service.data / 'secure/ciphertext').write_bytes(b'partial migration')
        raise ValueError('migration failed')
    monkeypatch.setattr('app.updates.runner.restore', lambda *args: (_ for _ in ()).throw(OSError('disk unavailable')))
    launch(service, monkeypatch, fail).run()
    assert service.job()['needs_repair']
    assert read_json(service.control / 'gate.json')['mode'] == 'blocked'
    assert (service.public / 'maintenance').exists()
    assert list((service.root / 'backups').glob('*/complete.json'))
    service.recover_idle()
    assert read_json(service.control / 'gate.json')['mode'] == 'blocked'
    with pytest.raises(ValueError, match='Recovery'):
        service.install('0.2.0')


def test_later_hop_failure_keeps_verified_intermediate_without_user_writes(installation, monkeypatch):
    service, sign = installation
    second = release_data('0.3.0', minimum_app='0.2.0')
    second['images'] = {role: ref.replace('a' * 64, 'c' * 64) for role, ref in second['images'].items()}
    atomic_json(service.root / 'catalog.json', {'path': [sign(release_data()).decode(), sign(second).decode()], 'error': None})
    service.install('0.3.0')
    transaction = Transaction(service)
    calls = []
    def migrate(image, schema):
        calls.append(image)
        assert read_json(service.control / 'gate.json')['mode'] == 'blocked'
        if len(calls) == 2:
            raise ValueError('second hop failed')
        (service.data / 'secure/ciphertext').write_bytes(b'first hop')
    monkeypatch.setattr(transaction, 'migrate', migrate)
    transaction.run()
    assert service.job()['phase'] == 'failed'
    assert service.installed()['version'] == '0.2.0'
    assert (service.data / 'secure/ciphertext').read_bytes() == b'first hop'


def test_recovery_and_new_install_cannot_overlap(installation, monkeypatch):
    import threading
    service, _ = installation
    waiting, resume = threading.Event(), threading.Event()
    def pause(*args):
        waiting.set()
        assert resume.wait(5)
    monkeypatch.setattr(service, 'await_ready', pause)
    thread = threading.Thread(target=service.recover_idle)
    thread.start()
    try:
        assert waiting.wait(5)
        with pytest.raises(BlockingIOError):
            service.install('0.2.0')
    finally:
        resume.set()
        thread.join()
    service.install('0.2.0')
    service.gate('blocked', '0.1.0')
    service.recover_idle()
    assert read_json(service.control / 'gate.json')['mode'] == 'blocked'


def test_runner_start_is_serialized_and_crash_loop_is_bounded(installation):
    from app.updates.state import file_lock
    service, _ = installation
    service.install('0.2.0')
    with file_lock(service.root / 'runner-start.lock'):
        with pytest.raises(BlockingIOError):
            service.ensure_runner()
    service.ensure_runner()
    service.ensure_runner()
    service.ensure_runner()
    assert service.job()['needs_repair']
    assert read_json(service.control / 'gate.json')['mode'] == 'blocked'
    assert len([a for a in service.docker.actions if a[0] == 'helper']) == 3


def test_public_status_distinguishes_recovery_from_terminal_transition(installation):
    service, _ = installation
    service.maintenance(True)
    service.publish('succeeded')
    status = read_json(service.public / 'status.json')
    assert status['maintenance'] and not status['needs_repair']
    atomic_json(service.root / 'job.json', {'phase': 'failed', 'needs_repair': True})
    service.publish('failed')
    assert read_json(service.public / 'status.json')['needs_repair']
