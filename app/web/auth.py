"""Session authentication, CSRF protection and account management routes."""

import asyncio
import hmac
import secrets
import sqlite3
import time
from contextvars import ContextVar
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.security import accounts as a
from pathlib import Path

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')
current_session = ContextVar('current_session', default=None)
grants = {}
pending = {}


def secure_cookie(request):
    return request.url.scheme == 'https' or settings.public_origin.startswith('https://')


def cookie_name(request):
    return '__Host-kiehnema' if secure_cookie(request) else 'kiehnema-local'


def csrf_cookie(request):
    return '__Host-kiehnema-csrf' if secure_cookie(request) else 'kiehnema-local-csrf'


def form(request, template='auth.html', **context):
    return templates.TemplateResponse(request, template, context)


def set_cookie(response, request, name, value, max_age=43200):
    response.set_cookie(name, value, secure=secure_cookie(request), httponly=True,
                        samesite='strict', path='/', max_age=max_age)


def preview_grant(job_id):
    current = current_session.get()
    if current is None:
        raise PermissionError('Sign in first.')
    sid, session = current
    now = time.time()
    for token, value in list(grants.items()):
        if value[2] < now or value[0] not in a.sessions:
            grants.pop(token, None)
        elif value[:2] == (sid, job_id):
            return token
    token = secrets.token_urlsafe(32)
    grants[token] = (sid, job_id, now + 1800)
    return token


async def guard(request: Request, call_next):
    path = request.url.path
    session_token = request.cookies.get(cookie_name(request), '')
    session = a.get_session(session_token)
    sid = a.digest(session_token)
    parts = path.split('/')
    if len(parts) > 5 and parts[1] == 'preview' and parts[3] == '_':
        grant = grants.get(parts[4])
        if grant and grant[1] == parts[2] and grant[2] > time.time():
            sid = grant[0]
            with a.session_lock:
                session = a.sessions.get(sid)
            if session:
                with a.database() as con:
                    user = con.execute('SELECT disabled FROM users WHERE id=?', (session.person.id,)).fetchone()
                if not user or user[0] or time.time() - session.used > 1800 or time.time() - session.created > 43200:
                    session = None
        else:
            session = None
    public = path in {'/login', '/activate', '/recover', '/healthz', '/favicon.ico'} or path.startswith('/static/')
    csrf = session.csrf if session else request.cookies.get(csrf_cookie(request)) or secrets.token_urlsafe(32)
    request.state.user = session.person if session else None
    request.state.csrf = csrf
    if not public and session is None:
        return RedirectResponse('/login', status_code=303) if request.method == 'GET' and not path.startswith(('/preview/', '/jobs/')) else HTMLResponse('Sign in required.', status_code=401)
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        supplied = request.headers.get('x-csrf-token', '')
        if request.headers.get('content-type', '').startswith('application/x-www-form-urlencoded'):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024:
                    return HTMLResponse('Form too large.', status_code=413)
            # Starlette's cached request forwards this bounded body to the endpoint.
            request._body = bytes(body)
            try:
                supplied = supplied or parse_qs(body.decode(), max_num_fields=2500).get('csrf', [''])[0]
            except (ValueError, UnicodeError):
                return HTMLResponse('Invalid form.', status_code=400)
        if not supplied or not hmac.compare_digest(supplied, csrf):
            return HTMLResponse('Invalid form token. Reload the page.', status_code=403)
    user_context = a.current_identity.set(session.person if session else None)
    session_context = current_session.set((sid, session) if session else None)
    try:
        if session and len(parts) > 2 and parts[1] in {'jobs', 'preview'} and parts[2]:
            from app.security.vault import get_job
            try:
                get_job(parts[2])
            except FileNotFoundError:
                return HTMLResponse('File or job not found.', status_code=404)
        response = await call_next(request)
    finally:
        a.current_identity.reset(user_context)
        current_session.reset(session_context)
    if not session:
        set_cookie(response, request, csrf_cookie(request), csrf, max_age=1800)
    return response


@router.get('/login')
async def login_page(request: Request):
    return form(request, mode='login', ready=a.initialized())


@router.post('/login')
async def login(request: Request):
    values = await request.form()
    try:
        a.throttle('ip:' + (request.client.host if request.client else 'unknown'))
        person = await asyncio.to_thread(a.authenticate, str(values.get('username', '')),
                                        str(values.get('password', '')), str(values.get('code', '')))
    except ValueError as exc:
        return form(request, mode='login', ready=a.initialized(), error=str(exc))
    response = RedirectResponse('/', status_code=303)
    set_cookie(response, request, cookie_name(request), a.new_session(person))
    return response


@router.post('/logout')
async def logout(request: Request):
    sid = a.digest(request.cookies.get(cookie_name(request), ''))
    with a.session_lock:
        a.sessions.pop(sid, None)
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(cookie_name(request), path='/')
    return response


@router.get('/activate')
async def activate_page(request: Request):
    return form(request, mode='activate')


@router.post('/activate')
async def activate(request: Request):
    values = await request.form()
    try:
        a.throttle('activate:' + (request.client.host if request.client else 'unknown'))
        for token, item in list(pending.items()):
            if item['expires'] < time.time():
                del pending[token]
        proof = str(values.get('proof', ''))
        if proof:
            item = pending.get(a.digest(proof))
            if not item or not hmac.compare_digest(item['csrf'], request.state.csrf):
                raise ValueError('Setup expired. Start again with your invitation.')
            try:
                if item.get('recovery'):
                    person = await asyncio.to_thread(a.finish_recovery, item['draft'], str(values.get('code', '')))
                else:
                    person = await asyncio.to_thread(a.finish_enrollment, item['draft'], str(values.get('code', '')), invite=item['invite'])
            except ValueError as exc:
                return form(request, mode='enroll', proof=proof, secret=item['draft']['totp'],
                            recovery=a.recovery_text(item['draft']), recovering=item.get('recovery'), error=str(exc))
            del pending[a.digest(proof)]
            response = RedirectResponse('/', status_code=303)
            set_cookie(response, request, cookie_name(request), a.new_session(person))
            return response
        invitation = str(values.get('invitation', ''))
        with a.database() as con:
            row = con.execute('SELECT username FROM invites WHERE token=? AND expires>?', (a.digest(invitation), time.time())).fetchone()
        if not row:
            raise ValueError('Invalid or expired invitation.')
        draft = await asyncio.to_thread(a.enrollment, row[0], str(values.get('password', '')))
        proof = secrets.token_urlsafe(32)
        pending[a.digest(proof)] = {'draft': draft, 'invite': invitation, 'expires': time.time() + 600, 'csrf': request.state.csrf}
        return form(request, mode='enroll', proof=proof, secret=draft['totp'], recovery=a.recovery_text(draft))
    except (ValueError, sqlite3.IntegrityError) as exc:
        return form(request, mode='activate', error=str(exc) if isinstance(exc, ValueError) else 'Account already exists.')


@router.get('/recover')
async def recover_page(request: Request):
    return form(request, mode='recover')


@router.post('/recover')
async def recover_account(request: Request):
    values = await request.form()
    try:
        a.throttle('recover-ip:' + (request.client.host if request.client else 'unknown'))
        draft = await asyncio.to_thread(a.recover, str(values.get('username', '')),
            str(values.get('recovery', '')), str(values.get('password', '')))
        proof = secrets.token_urlsafe(32)
        pending[a.digest(proof)] = {'draft': draft, 'recovery': True, 'expires': time.time() + 600, 'csrf': request.state.csrf}
        return form(request, mode='enroll', proof=proof, secret=draft['totp'], recovery=a.recovery_text(draft), recovering=True)
    except ValueError as exc:
        return form(request, mode='recover', error=str(exc))


@router.get('/account')
async def account_page(request: Request):
    return form(request, mode='account')


@router.post('/account/password')
async def password_change(request: Request):
    values = await request.form()
    try:
        person = await asyncio.to_thread(a.authenticate, a.identity().username,
            str(values.get('old_password', '')), str(values.get('code', '')))
        await asyncio.to_thread(a.change_password, person, str(values.get('password', '')))
    except ValueError as exc:
        return form(request, mode='account', error=str(exc))
    return RedirectResponse('/login', status_code=303)


def admin_users():
    if not a.identity().admin:
        raise PermissionError('Administrator access required.')
    with a.database() as con:
        return [dict(row) for row in con.execute('SELECT id,username,admin,disabled FROM users ORDER BY username')]


@router.get('/admin')
async def admin_page(request: Request):
    return form(request, mode='admin', users=admin_users())


@router.post('/admin/invite')
async def invite(request: Request):
    users = admin_users()
    values = await request.form()
    try:
        token = a.invite_user(a.identity(), str(values.get('username', '')))
        return form(request, mode='admin', users=users, invitation=token)
    except ValueError as exc:
        return form(request, mode='admin', users=users, error=str(exc))


@router.post('/admin/disable')
async def disable(request: Request):
    admin_users()
    values = await request.form()
    uid = str(values.get('user_id', ''))
    if uid == a.identity().id:
        return form(request, mode='admin', users=admin_users(), error='You cannot disable your own administrator account.')
    with a.database() as con:
        con.execute('UPDATE users SET disabled=? WHERE id=?', (int(values.get('disabled') == '1'), uid))
    a.revoke(uid)
    if values.get('disabled') == '1':
        from app.web.main import app
        with a.database() as con:
            ids = [row[0] for row in con.execute('SELECT id FROM jobs WHERE owner=?', (uid,))]
        tasks = [app.state.tasks[jid] for jid in ids if jid in app.state.tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return RedirectResponse('/admin', status_code=303)
