"""RAM-only swap is allowed; disk-backed and unverifiable swap fail closed."""

from pathlib import Path

import pytest

from app.security import workspace


HEADER = 'Filename\tType\tSize\tUsed\tPriority\n'
ZRAM = '/dev/zram0 partition 1048576 524288 100\n'
BACKING = '/sys/devices/virtual/block/zram0/backing_dev'


@pytest.fixture
def linux_memory(monkeypatch):
    files = {'/proc/swaps': HEADER}

    def read_text(path, *args, **kwargs):
        value = files.get(str(path), FileNotFoundError(str(path)))
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(workspace.sys, 'platform', 'linux')
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(workspace.resource, 'setrlimit', lambda *args: None)
    return files


def test_no_swap_is_allowed(linux_memory):
    workspace.memory_policy()


def test_active_ram_only_zram_is_allowed_without_device_nodes(linux_memory):
    linux_memory.update({'/proc/swaps': HEADER + ZRAM, BACKING: 'none\n'})
    workspace.memory_policy()


def test_multiple_ram_only_zram_devices_are_allowed(linux_memory):
    linux_memory.update({'/proc/swaps': HEADER + ZRAM + ZRAM.replace('zram0', 'zram1'),
                         BACKING: 'none\n', '/sys/devices/virtual/block/zram1/backing_dev': 'none\n'})
    workspace.memory_policy()


@pytest.mark.parametrize('entry', [
    '/swapfile file 1048576 0 -2\n',
    '/dev/sda2 partition 1048576 0 -2\n',
    '/dev/mapper/encrypted-swap partition 1048576 0 -2\n',
    '/dev/zram0 file 1048576 0 -2\n',
    '/tmp/zram0 partition 1048576 0 -2\n',
    '/dev/zram0-extra partition 1048576 0 -2\n',
    'malformed\n',
])
def test_other_swap_is_rejected_even_with_no_pages_used(linux_memory, entry):
    linux_memory.update({'/proc/swaps': HEADER + entry, BACKING: 'none\n'})
    with pytest.raises(ValueError, match='disk-backed or unverifiable swap'):
        workspace.memory_policy()


@pytest.mark.parametrize('backing', ['/dev/sda2\n', '', FileNotFoundError(), PermissionError()])
def test_zram_writeback_or_unverifiable_metadata_is_rejected(linux_memory, backing):
    linux_memory.update({'/proc/swaps': HEADER + ZRAM, BACKING: backing})
    with pytest.raises(ValueError, match='disk-backed or unverifiable swap'):
        workspace.memory_policy()


@pytest.mark.parametrize('other', ['/swapfile file 1048576 0 -2\n', ZRAM.replace('zram0', 'zram1')])
def test_ram_only_zram_does_not_hide_other_unsafe_swap(linux_memory, other):
    linux_memory.update({'/proc/swaps': HEADER + ZRAM + other, BACKING: 'none\n',
                         '/sys/devices/virtual/block/zram1/backing_dev': '/dev/sda2\n'})
    with pytest.raises(ValueError, match='disk-backed or unverifiable swap'):
        workspace.memory_policy()


@pytest.mark.parametrize('swaps', ['', 'unknown format\n', FileNotFoundError(), PermissionError()])
def test_unverifiable_swap_list_is_rejected(linux_memory, swaps):
    linux_memory['/proc/swaps'] = swaps
    with pytest.raises(ValueError, match='disk-backed or unverifiable swap'):
        workspace.memory_policy()
