import uvicorn

from app.config import settings

uvicorn.run("app.web.main:app", host=settings.host, port=settings.port, reload=False)
