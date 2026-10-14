import uvicorn

from app.config import settings
from app.security.accounts import initialized
from app.security.__main__ import setup_admin

if not initialized():
    setup_admin()
uvicorn.run("app.web.main:app", host=settings.host, port=settings.port, reload=False, access_log=False)
