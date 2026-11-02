"""Administrator-only AI settings; the normal session, CSRF and update guards apply."""

import asyncio
import hmac
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.llm import provider
from app.llm.client import LLMError, test_connection
from app.security.accounts import identity, throttle
from app.web.auth import current_session

router = APIRouter(prefix='/admin/provider')
templates = Jinja2Templates(directory=Path(__file__).parent / 'templates')


def administrator():
    if not identity().admin:
        raise HTTPException(403, 'Only administrators can configure the AI provider.')


@router.get('')
async def page(request: Request):
    administrator()
    error, configured = None, None
    try:
        configured = provider.load()
    except (ValueError, OSError):
        error = 'AI settings could not be read. Restore the provider configuration and key together.'
    return templates.TemplateResponse(request, 'provider.html', {
        'values': configured.public() if configured else {}, 'error': error,
        'saved': request.query_params.get('saved') == '1', 'configured': configured.public() if configured else None})


def fingerprint(config, session):
    raw = json.dumps(config.private(), sort_keys=True).encode()
    return hmac.digest(session.csrf.encode(), raw, 'sha256').hex()


@router.post('')
@router.post('/test')
async def save(request: Request):
    administrator()
    testing = request.url.path.endswith('/test')
    session = current_session.get()[1]
    if testing:
        session.provider_test = None
    form = await request.form()
    values = {name: str(form.get(name, '')).strip() for name in ('provider', 'model', 'region', 'auth', 'max_output_tokens')}
    if values['provider'] != 'bedrock':
        values['auth'] = 'keys'
        values['region'] = 'eu-central-1'
    credentials = {name: str(form.get(name, '')).strip() for name in provider.SECRET_FIELDS}
    existing = None
    try:
        existing = provider.load()
        if (not any(credentials.values()) and existing is not None
                and (values['provider'], values['auth']) == (existing.provider, existing.auth)):
            credentials = {name: getattr(existing, name).get_secret_value() for name in provider.SECRET_FIELDS}
        configured = provider.Provider.model_validate({**values, **credentials})
        if testing:
            throttle('provider-test:' + identity().id)
            await asyncio.to_thread(test_connection, configured)
            session.provider_test = (fingerprint(configured, session), time.monotonic() + 300)
            return JSONResponse({'message': 'Connection successful. You can now save these settings within 5 minutes.'})
        proof, session.provider_test = session.provider_test, None
        if proof is None or proof[1] < time.monotonic() or not hmac.compare_digest(proof[0], fingerprint(configured, session)):
            return templates.TemplateResponse(request, 'provider.html', {
                'values': values, 'configured': existing.public() if existing else None,
                'error': 'Test the connection successfully with these settings before saving.'}, status_code=409)
        provider.save(configured)
    except ValidationError as exc:
        message = '; '.join(error['msg'] for error in exc.errors(include_input=False))
        if testing:
            return JSONResponse({'error': message}, status_code=422)
        return templates.TemplateResponse(request, 'provider.html', {
            'values': values, 'error': message, 'configured': existing.public() if existing else None}, status_code=422)
    except LLMError as exc:
        return JSONResponse({'error': str(exc)}, status_code=422)
    except (ValueError, OSError):
        if testing:
            return JSONResponse({'error': 'The test could not start. Check the saved configuration or wait before trying again.'}, status_code=409)
        return templates.TemplateResponse(request, 'provider.html', {
            'values': values, 'configured': existing.public() if existing else None,
            'error': 'AI settings could not be saved. Check the configuration key and storage permissions.'}, status_code=409)
    return RedirectResponse('/admin/provider?saved=1', status_code=303)
