"""Docker health probe for the private updater API."""

from pathlib import Path

import httpx


def main():
    root = Path('/updates/control')
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(root / 'updater.sock')),
                      base_url='http://updater', timeout=3) as client:
        response = client.get('/status', headers={'Authorization': 'Bearer ' + (root / 'token').read_text().strip()})
        response.raise_for_status()


if __name__ == '__main__':
    main()
