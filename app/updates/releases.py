"""Bounded GitHub release downloads; credentials never reach the asset CDN."""

from datetime import datetime, timezone
import base64
import re
from urllib.parse import urlsplit

import httpx

from app.updates.manifest import MAX_MANIFEST, UnsupportedRelease, verify, version


class GitHub:
    def __init__(self, config, credentials):
        repository = config['github_repository']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('GitHub repository must use owner/name.')
        self.api = 'https://api.github.com/repos/' + repository
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        if credentials.get('token'):
            headers['Authorization'] = 'Bearer ' + credentials['token']
        self.client = httpx.Client(headers=headers, timeout=30, follow_redirects=False, trust_env=False)

    def close(self):
        self.client.close()

    def download(self, url, limit=MAX_MANIFEST, *, asset=False):
        if not url.startswith(self.api + '/') or urlsplit(url).username:
            raise ValueError('Release URLs must belong to the configured GitHub repository.')
        request = self.client.build_request('GET', url,
            headers={'Accept': 'application/octet-stream'} if asset else {})
        for attempt in range(4):
            response = self.client.send(request, stream=True)
            try:
                if asset and response.status_code in (301, 302, 303, 307, 308):
                    target = response.headers.get('location', '')
                    parsed = urlsplit(target)
                    if (parsed.scheme != 'https' or parsed.hostname not in
                            {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
                            or parsed.username or parsed.port not in (None, 443)):
                        raise ValueError('Unexpected GitHub asset redirect.')
                    # A fresh request deliberately carries no API credentials or cookies to the CDN.
                    request = httpx.Request('GET', target)
                    continue
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError('GitHub response exceeds the size limit.')
                return bytes(data), response.headers
            finally:
                response.close()
        raise ValueError('Too many GitHub asset redirects.')

    def releases(self, keys, repository):
        import json
        found = []
        envelopes = {}
        for page in range(1, 101):
            raw, _ = self.download(f'{self.api}/releases?per_page=100&page={page}', limit=4 * 1024 * 1024)
            entries = json.loads(raw)
            if not isinstance(entries, list):
                raise ValueError('Invalid GitHub release list.')
            for entry in entries:
                if entry.get('draft') or entry.get('prerelease'):
                    continue
                tag = entry.get('tag_name', '')
                try:
                    version(tag.removeprefix('v'))
                    date = datetime.fromisoformat(entry['published_at'].replace('Z', '+00:00'))
                    if date.tzinfo is None or date > datetime.now(timezone.utc):
                        continue
                except (ValueError, KeyError, TypeError):
                    continue
                links = [link['url'] for link in entry.get('assets', []) if link.get('name') == 'update.json']
                if len(links) != 1:
                    continue
                envelope, _ = self.download(links[0], asset=True)
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
            if len(entries) < 100:
                break
        else:
            raise ValueError('Excessive GitHub release pagination.')
        return found, envelopes
