"""Administrator-only AI settings; the normal session, CSRF and update guards apply."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.llm import provider
from app.security.accounts import identity

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
        'saved': request.query_params.get('saved') == '1', 'configured': configured is not None})


@router.post('')
async def save(request: Request):
    administrator()
    form = await request.form()
    values = {name: str(form.get(name, '')).strip() for name in ('provider', 'model', 'region', 'auth', 'max_output_tokens')}
    if values['provider'] != 'bedrock':
        values['auth'] = 'keys'
        values['region'] = 'eu-central-1'
    credentials = {name: str(form.get(name, '')).strip() for name in provider.SECRET_FIELDS}
    try:
        existing = provider.load()
        if (not any(credentials.values()) and existing is not None
                and (values['provider'], values['auth']) == (existing.provider, existing.auth)):
            credentials = {name: getattr(existing, name).get_secret_value() for name in provider.SECRET_FIELDS}
        configured = provider.Provider.model_validate({**values, **credentials})
        provider.save(configured)
    except ValidationError as exc:
        message = '; '.join(error['msg'] for error in exc.errors(include_input=False))
        return templates.TemplateResponse(request, 'provider.html', {'values': values, 'error': message}, status_code=422)
    except (ValueError, OSError):
        return templates.TemplateResponse(request, 'provider.html', {
            'values': values, 'error': 'AI settings could not be saved. Check the configuration key and storage permissions.'}, status_code=409)
    return RedirectResponse('/admin/provider?saved=1', status_code=303)
