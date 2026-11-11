"""Session authentication, CSRF protection and account management routes."""

import asyncio
import hmac
import secrets
import sqlite3
import time
import pyotp
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.security import accounts as a
from app.security import audit
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
    if context.get('mode') == 'admin':
        context['require_all_totp'] = a.all_two_factor_required()
        context['role_labels'] = a.ROLE_LABELS
        now = time.time()
        context['invitations'] = [dict(entry, expired=entry['expires'] <= now,
                                     expires_at=datetime.fromtimestamp(entry['expires'], timezone.utc))
                                  for entry in a.invitations(a.identity())]
    if context.get('mode') == 'account':
        context['totp_enabled'] = a.two_factor_enabled(a.identity())
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
    if session and a.two_factor_enabled(session.person) and not session.person.two_factor_verified:
        with a.session_lock:
            a.sessions.pop(sid, None)
        session = None
    public = path in {'/login', '/activate', '/recover', '/healthz', '/favicon.ico'} or path.startswith('/static/')
    csrf = session.csrf if session else request.cookies.get(csrf_cookie(request)) or secrets.token_urlsafe(32)
    request.state.user = session.person if session else None
    request.state.csrf = csrf
    if not public and session is None:
        if request.headers.get('HX-Request') == 'true':
            return HTMLResponse('Sign in required.', status_code=401, headers={'HX-Redirect': '/login'})
        return RedirectResponse('/login', status_code=303) if request.method == 'GET' and not path.startswith(('/preview/', '/jobs/')) else HTMLResponse('Sign in required.', status_code=401)
    if session and a.two_factor_required(session.person) and not a.two_factor_enabled(session.person):
        if not public and path not in {'/account/two-factor', '/logout'}:
            return (RedirectResponse('/account/two-factor', status_code=303)
                    if request.method == 'GET' and not path.startswith(('/preview/', '/jobs/'))
                    else HTMLResponse('Set up two-factor authentication before continuing.', status_code=403))
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
    target = '/account/two-factor' if a.two_factor_required(person) and not a.two_factor_enabled(person) else '/'
    response = RedirectResponse(target, status_code=303)
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


@router.post('/account/username')
async def username_change(request: Request):
    values = await request.form()
    name = str(values.get('username', ''))
    uid = a.identity().id
    try:
        person = await asyncio.to_thread(a.authenticate, a.identity().username,
            str(values.get('password', '')), str(values.get('code', '')))
        if person.id != uid:
            raise ValueError('Account changed. Reload the page and try again.')
        a.change_username(person, name)
    except ValueError as exc:
        return form(request, mode='account', new_username=name, error=str(exc))
    response = RedirectResponse('/account?username_changed=1', status_code=303)
    set_cookie(response, request, cookie_name(request), a.new_session(person))
    return response


@router.get('/account/two-factor')
async def two_factor_page(request: Request):
    if a.two_factor_enabled(a.identity()):
        return RedirectResponse('/account', status_code=303)
    session = current_session.get()[1]
    if not session.totp_setup:
        session.totp_setup = pyotp.random_base32()
    return form(request, mode='two_factor', secret=session.totp_setup)


@router.post('/account/two-factor')
async def two_factor_setup(request: Request):
    session = current_session.get()[1]
    values = await request.form()
    try:
        person = await asyncio.to_thread(a.authenticate, a.identity().username, str(values.get('password', '')))
        person = await asyncio.to_thread(a.enable_two_factor, person, session.totp_setup, str(values.get('code', '')))
    except ValueError as exc:
        return form(request, mode='two_factor', secret=session.totp_setup, error=str(exc))
    response = RedirectResponse('/account', status_code=303)
    set_cookie(response, request, cookie_name(request), a.new_session(person))
    return response


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


def admin_users(*, allow_invites=False):
    person = a.identity()
    if not (person.admin or (allow_invites and person.can_invite)):
        raise PermissionError('Administrator access required.')
    with a.database() as con:
        return [dict(row) for row in con.execute('''SELECT id,username,admin,disabled,length(totp)>0 AS totp_enabled,
            CASE WHEN admin=1 THEN 'admin' WHEN id IN (SELECT user_id FROM invitation_managers)
                THEN 'manager' ELSE 'user' END AS role,
            id IN (SELECT user_id FROM two_factor_policy) AS totp_required FROM users
            ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END, username''')]


@router.get('/admin')
async def admin_page(request: Request):
    return form(request, mode='admin', users=admin_users(allow_invites=True))


@router.get('/admin/video-requests')
async def video_requests_page(request: Request, page: int = Query(default=1, ge=1, le=1_000_000)):
    entries = a.video_requests(page)
    return form(request, template='video_requests.html', page=page, has_more=len(entries) > 50,
                entries=[{'username': entry['username'],
                          'requested_at': datetime.fromtimestamp(entry['requested_at'], timezone.utc)}
                         for entry in entries[:50]])


@router.get('/admin/activity')
async def activity_page(request: Request, category: str = Query(default='all', pattern='^(all|team|security|provider|updates)$'),
                        page: int = Query(default=1, ge=1, le=1_000_000)):
    entries = audit.entries(category, page)
    return form(request, template='activity.html', category=category, categories=audit.CATEGORIES,
                actions=audit.ACTIONS, results=audit.RESULTS, page=page, has_more=len(entries) > 50,
                entries=[dict(entry, occurred_at=datetime.fromtimestamp(entry['occurred_at'], timezone.utc))
                         for entry in entries[:50]])


@router.post('/admin/role')
async def user_role(request: Request):
    admin_users()
    values = await request.form()
    try:
        a.set_role(a.identity(), str(values.get('user_id', '')), str(values.get('role', '')))
    except ValueError as exc:
        return form(request, mode='admin', users=admin_users(), error=str(exc))
    return RedirectResponse('/admin', status_code=303)


@router.post('/admin/two-factor-policy')
async def two_factor_policy(request: Request):
    admin_users()
    values = await request.form()
    a.require_all_two_factor(a.identity(), values.get('required') == '1')
    return RedirectResponse('/admin', status_code=303)


@router.post('/admin/two-factor')
async def user_two_factor(request: Request):
    admin_users()
    values = await request.form()
    try:
        a.require_two_factor(a.identity(), str(values.get('user_id', '')), values.get('required') == '1')
    except ValueError as exc:
        return form(request, mode='admin', users=admin_users(), error=str(exc))
    return RedirectResponse('/admin', status_code=303)


@router.post('/admin/invite')
async def invite(request: Request):
    users = admin_users(allow_invites=True)
    values = await request.form()
    try:
        token = a.invite_user(a.identity(), str(values.get('username', '')))
        return form(request, mode='admin', users=users, invitation=token)
    except ValueError as exc:
        return form(request, mode='admin', users=users, error=str(exc))


@router.post('/admin/invite/revoke')
async def revoke_invitation(request: Request):
    admin_users()
    values = await request.form()
    a.revoke_invitation(a.identity(), str(values.get('token', '')))
    return RedirectResponse('/admin', status_code=303)


@router.post('/admin/disable')
async def disable(request: Request):
    admin_users()
    values = await request.form()
    uid = str(values.get('user_id', ''))
    try:
        if values.get('disabled') not in {'0', '1'}:
            raise ValueError('Choose whether to enable or disable this account.')
        a.set_disabled(a.identity(), uid, values.get('disabled') == '1')
    except ValueError as exc:
        return form(request, mode='admin', users=admin_users(), error=str(exc))
    if values.get('disabled') == '1':
        from app.web.main import app
        with a.database() as con:
            ids = [row[0] for row in con.execute('SELECT id FROM jobs WHERE owner=?', (uid,))]
        tasks = [app.state.tasks[jid] for jid in ids if jid in app.state.tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return RedirectResponse('/admin', status_code=303)


def deletion_target(uid: str):
    target = next((user for user in admin_users() if user['id'] == uid), None)
    if target is None:
        raise HTTPException(404, 'User not found.')
    if uid == a.identity().id:
        raise HTTPException(409, 'You cannot delete your own administrator account.')
    if not target['disabled']:
        raise HTTPException(409, 'Disable this account before deleting it.')
    from app.web.main import app
    with a.database() as con:
        jobs = con.execute('SELECT id,active FROM jobs WHERE owner=?', (uid,)).fetchall()
    if any(job['active'] or job['id'] in app.state.tasks for job in jobs):
        raise HTTPException(409, 'Wait for this user’s running jobs to stop before deleting the account.')
    return {**target, 'job_count': len(jobs)}


@router.get('/admin/delete/{uid}')
async def confirm_user_delete(request: Request, uid: str):
    return form(request, template='delete_user.html', target=deletion_target(uid))


@router.post('/admin/delete')
async def delete_user(request: Request):
    admin_users()
    values = await request.form()
    uid = str(values.get('user_id', ''))
    target = deletion_target(uid)
    try:
        deleted_jobs = a.delete_user(a.identity(), uid, str(values.get('confirmation', '')))
    except FileNotFoundError:
        raise HTTPException(404, 'User not found.') from None
    except PermissionError:
        raise
    except ValueError as exc:
        return templates.TemplateResponse(request, 'delete_user.html', {'target': target, 'error': str(exc)}, status_code=409)
    except (OSError, sqlite3.Error):
        return templates.TemplateResponse(request, 'delete_user.html', {'target': target,
            'error': 'Deletion did not finish. This account remains disabled. Some files may already be removed. Try again.'}, status_code=409)
    for token, item in list(pending.items()):
        if item['draft']['id'] == uid:
            pending.pop(token, None)
    for token, item in list(grants.items()):
        if item[1] in deleted_jobs:
            grants.pop(token, None)
    from app.web.main import app
    app.state.interrupted.difference_update(deleted_jobs)
    return RedirectResponse('/admin', status_code=303)
