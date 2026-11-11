"""Role-based update controls using the existing session and CSRF boundary."""

from datetime import datetime, timezone
from pathlib import Path
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.changelog import VERSION_PATTERN
from app.security import audit
from app.security.accounts import identity
from app.updates import VERSION
from app.updates import client

router = APIRouter(prefix='/admin/updates')
templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')


def administrator():
    if not identity().admin:
        raise HTTPException(403, 'Only administrators can manage update access.')


def update_operator():
    if not identity().can_invite:
        raise HTTPException(403, 'Only directors and assistants can manage updates.')


async def call(method, path, body=None):
    try:
        return await client.request(method, path, body)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            raise HTTPException(503, 'The update service is unavailable. Your current application can keep running.') from None
        try:
            detail = exc.response.json().get('error', 'The updater rejected this action.')
        except ValueError:
            detail = 'The updater rejected this action.'
        raise HTTPException(409, detail) from None
    except (httpx.RequestError, OSError, ValueError):
        raise HTTPException(503, 'The update service is unavailable. Your current application can keep running.') from None


async def change(path, action, body=None):
    if path == '/credentials':
        administrator()
    else:
        update_operator()
    version = str(body.get('version', '')) if path == '/install' else ''
    detail = version if re.fullmatch(VERSION_PATTERN, version) else ''
    # Persist before the updater can stop the app or snapshot its data.
    event = audit.record(identity(), action, detail=detail, result='requested')
    try:
        await call('POST', path, body)
    except HTTPException as exc:
        # A transport failure cannot tell us whether the updater accepted the request.
        if exc.status_code == 409:
            audit.finish(event, 'failed')
        raise
    audit.finish(event, 'accepted')


@router.get('')
async def page(request: Request):
    update_operator()
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
    update_operator()
    if settings.update_control_dir is None:
        return {'available': [], 'busy': False}
    return await call('GET', '/status')


@router.post('/check')
async def check():
    update_operator()
    await change('/check', 'updates.check_requested')
    return RedirectResponse('/admin/updates', status_code=303)


@router.post('/install')
async def install(request: Request):
    update_operator()
    form = await request.form()
    await change('/install', 'updates.install_requested', {'version': form.get('version', '')})
    return RedirectResponse('/admin/updates', status_code=303)


@router.post('/cancel')
async def cancel():
    update_operator()
    await change('/cancel', 'updates.cancel_requested')
    return RedirectResponse('/admin/updates', status_code=303)


@router.post('/credentials')
async def credentials(request: Request):
    administrator()
    form = await request.form()
    await change('/credentials', 'updates.credentials_changed',
                 {'username': form.get('username', '').strip(), 'token': form.get('token', '').strip()})
    return RedirectResponse('/admin/updates', status_code=303)
