"""Managed Linux services; no public administration listener."""

import os
import sys
from pathlib import Path

from app.updates.state import file_lock


def main():
    os.umask(0o077)
    if sys.platform != 'linux':
        raise SystemExit('The managed updater requires Linux.')
    command = sys.argv[1] if len(sys.argv) > 1 else 'serve'
    if command == 'migrate':
        from app.updates.probe import migrate
        migrate(int(sys.argv[2]))
        return
    from app.updates.service import Updater, create_app
    updater = Updater()
    if command == 'runner':
        from app.updates.runner import Transaction
        try:
            Transaction(updater).run()
        except BlockingIOError:
            pass
        return
    if command != 'serve':
        raise SystemExit('Unknown updater command.')
    import uvicorn
    with file_lock(updater.root / 'daemon.lock'):
        socket = updater.control / 'updater.sock'
        socket.unlink(missing_ok=True)
        # Uvicorn creates the socket using the current umask; only the app's group may connect.
        os.umask(0o007)
        os.setgid(1000)
        uvicorn.run(create_app(updater), uds=str(socket), access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
