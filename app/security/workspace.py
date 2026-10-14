"""Fail-closed plaintext workspace selection for the isolated pipeline process."""

import os
import plistlib
import resource
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path

from app.config import settings

runtime_root: ContextVar[Path | None] = ContextVar("runtime_root", default=None)
publisher: ContextVar[object | None] = ContextVar("publisher", default=None)


def memory_policy() -> None:
    """Unlocked keys must not leak through core dumps or unencrypted swap."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform == 'linux':
        if len(Path('/proc/swaps').read_text().splitlines()) > 1:
            raise ValueError('Disable swap on the service host before unlocking user keys.')
    elif sys.platform == 'darwin':
        output = subprocess.run(['/usr/sbin/sysctl', '-n', 'vm.swapusage'], capture_output=True, text=True, check=True).stdout
        if '(encrypted)' not in output:
            raise ValueError('Encrypted macOS swap is required before unlocking user keys.')
    else:
        raise ValueError('Secure workspaces currently support Linux and macOS.')


def workspace_dir() -> Path:
    root = runtime_root.get()
    if root is None:
        raise PermissionError("Pipeline execution requires a private worker workspace.")
    return root


def ram_root() -> Path:
    memory_policy()
    root = settings.work_dir.resolve()
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    if sys.platform == "linux":
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields, after = line.split(" - ", 1)
            mount = Path(fields.split()[4].replace("\\040", " "))
            if ancestor.is_relative_to(mount):
                mounts.append((len(str(mount)), after.split()[0]))
        if not mounts or max(mounts)[1] not in {"tmpfs", "ramfs"}:
            raise ValueError("MG_WORK_DIR must be on a RAM filesystem.")
    elif sys.platform == "darwin":
        result = subprocess.run(["/usr/bin/hdiutil", "info", "-plist"], capture_output=True, check=True)
        images = plistlib.loads(result.stdout).get('images', [])
        ram = any(str(image.get('image-path', '')).startswith('ram://')
                  and any('mount-point' in item and ancestor.is_relative_to(Path(item['mount-point']))
                          for item in image.get('system-entities', [])) for image in images)
        if not ram:
            raise ValueError("MG_WORK_DIR must point to a mounted RAM disk on macOS.")
    else:
        raise ValueError("Secure workers require Linux tmpfs or a macOS RAM disk.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError("MG_WORK_DIR must be owned by the service user and have mode 0700.")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return root


def browser_environment() -> dict[str, str]:
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'DISPLAY') if key in os.environ}
    if root := runtime_root.get():
        home = root / 'browser-home'
        home.mkdir(exist_ok=True, mode=0o700)
        env.update(HOME=str(home), TMPDIR=str(root), XDG_CACHE_HOME=str(home))
    return env
