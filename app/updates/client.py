"""Application-side access to the local updater; no registry credentials enter the app."""

import httpx

from app.config import settings


async def request(method, path, body=None):
    root = settings.update_control_dir
    if root is None:
        raise ValueError('Updates require the managed Linux installation.')
    token = (root / 'token').read_text().strip()
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(root / 'updater.sock')),
                                 base_url='http://updater', timeout=40, trust_env=False) as client:
        response = await client.request(method, path, json=body, headers={'Authorization': 'Bearer ' + token})
        response.raise_for_status()
        return response.json()
