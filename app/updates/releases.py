"""Bounded, same-origin GitLab downloads. Credentials never follow asset redirects."""

from datetime import datetime, timezone
import base64
from urllib.parse import quote, urlsplit

import httpx

from app.updates.manifest import MAX_MANIFEST, UnsupportedRelease, verify, version


class GitLab:
    def __init__(self, config, credentials):
        self.origin = config['gitlab_url'].rstrip('/')
        parsed = urlsplit(self.origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('GitLab must be an HTTPS origin.')
        self.api = self.origin + '/api/v4/projects/' + quote(str(config['project_id']), safe='')
        self.client = httpx.Client(headers={'PRIVATE-TOKEN': credentials['token']} if credentials.get('token') else {},
                                   timeout=30, follow_redirects=False, trust_env=False)

    def close(self):
        self.client.close()

    def download(self, url, limit=MAX_MANIFEST):
        if urlsplit(url)[:2] != urlsplit(self.origin)[:2] or urlsplit(url).username:
            raise ValueError('Release assets must be hosted on the configured GitLab origin.')
        with self.client.stream('GET', url) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError('GitLab response exceeds the size limit.')
            return bytes(data), response.headers

    def releases(self, keys, repository):
        import json
        found = []
        envelopes = {}
        page = '1'
        seen = set()
        while page:
            if not page.isdigit() or page in seen or len(seen) >= 100:
                raise ValueError('Invalid or excessive release pagination.')
            seen.add(page)
            raw, headers = self.download(f'{self.api}/releases?per_page=100&page={page}', limit=4 * 1024 * 1024)
            entries = json.loads(raw)
            if not isinstance(entries, list):
                raise ValueError('Invalid GitLab release list.')
            for entry in entries:
                tag = entry.get('tag_name', '')
                try:
                    version(tag.removeprefix('v'))
                    date = datetime.fromisoformat(entry['released_at'].replace('Z', '+00:00'))
                    if date.tzinfo is None or date > datetime.now(timezone.utc):
                        continue
                except (ValueError, KeyError, TypeError):
                    continue
                links = [link['url'] for link in entry.get('assets', {}).get('links', []) if link.get('name') == 'update.json']
                if len(links) != 1:
                    continue
                envelope, _ = self.download(links[0])
                signed = json.loads(envelope)
                metadata = json.loads(base64.b64decode(signed['manifest'], validate=True))
                if tag != 'v' + metadata['version']:
                    raise ValueError('Release tag does not match the signed version.')
                if metadata['version'] in envelopes:
                    raise ValueError('Duplicate product version in release feed.')
                envelopes[metadata['version']] = envelope.decode()
                if signed['key_id'] not in keys:
                    continue
                try:
                    release = verify(envelope, keys, repository)
                except UnsupportedRelease:
                    continue
                found.append(release)
            page = headers.get('x-next-page', '')
        return found, envelopes
