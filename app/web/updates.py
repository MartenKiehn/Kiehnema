"""Administrator-only update controls using the existing session and CSRF boundary."""

from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.security.accounts import identity
from app.updates import VERSION
from app.updates import client

router = APIRouter(prefix='/admin/updates')
templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')


def administrator():
    if not identity().admin:
        raise HTTPException(403, 'Only administrators can manage updates.')


async def call(method, path, body=None):
    try:
        return await client.request(method, path, body)
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get('error', 'The updater rejected this action.')
        except ValueError:
            detail = 'The updater rejected this action.'
        raise HTTPException(409, detail) from None
    except (httpx.RequestError, OSError, ValueError):
        raise HTTPException(503, 'The update service is unavailable. Your current application can keep running.') from None


@router.get('')
async def page(request: Request):
    administrator()
    status, error = None, None
    if settings.update_control_dir is not None:
        try:
            status = await call('GET', '/status')
        except HTTPException as exc:
            error = exc.detail
    checked = (datetime.fromtimestamp(status['checked_at'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
               if status and status.get('checked_at') else 'Not checked yet')
    return templates.TemplateResponse(request, 'updates.html', {'status': status, 'error': error,
        'managed': settings.update_control_dir is not None, 'version': VERSION, 'checked': checked})


@router.get('/status')
async def status():
    administrator()
    if settings.update_control_dir is None:
        return {'available': [], 'busy': False}
    return await call('GET', '/status')


@router.post('/check')
async def check():
    administrator()
    await call('POST', '/check')
    return RedirectResponse('/admin/updates', status_code=303)


@router.post('/install')
async def install(request: Request):
    administrator()
    form = await request.form()
    await call('POST', '/install', {'version': form.get('version', '')})
    return RedirectResponse('/admin/updates', status_code=303)


@router.post('/cancel')
async def cancel():
    administrator()
    await call('POST', '/cancel')
    return RedirectResponse('/admin/updates', status_code=303)


@router.post('/credentials')
async def credentials(request: Request):
    administrator()
    form = await request.form()
    await call('POST', '/credentials', {'username': form.get('username', '').strip(), 'token': form.get('token', '').strip()})
    return RedirectResponse('/admin/updates', status_code=303)
