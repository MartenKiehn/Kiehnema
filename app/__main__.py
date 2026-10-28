import uvicorn

from app.config import settings
from app.security.accounts import initialized, service_lock
from app.security.__main__ import setup_admin
from app.updates.state import assert_start_allowed, wait_for_start

wait_for_start()
with service_lock():
    assert_start_allowed()
    if not initialized():
        setup_admin()
uvicorn.run("app.web.main:app", host=settings.host, port=settings.port, reload=False, access_log=False,
            forwarded_allow_ips=settings.trusted_proxies)
