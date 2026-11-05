"""The versioned change log shared by the UI and release pipeline."""

import argparse
from pathlib import Path
import re
import sys


CHANGELOG = Path(__file__).resolve().parents[1] / 'CHANGELOG.md'
VERSION_PATTERN = r'(?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8})'


def read_entries(path: Path | None = None) -> list[dict]:
    source = path if path is not None else CHANGELOG
    try:
        text = source.read_text(encoding='utf-8')
    except OSError as exc:
        raise ValueError('CHANGELOG.md is missing or unreadable. Add a change log entry before releasing.') from exc
    entries = []
    seen = set()
    current = None
    for line in text.splitlines():
        if line.startswith('## '):
            version = line[3:].strip()
            if not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError(f'Invalid CHANGELOG.md version heading: {line}. Use ## MAJOR.MINOR.PATCH.')
            if version in seen:
                raise ValueError(f'Duplicate CHANGELOG.md entry for version {version}.')
            seen.add(version)
            current = {'version': version, 'changes': []}
            entries.append(current)
        elif current is not None and line.strip():
            if not line.startswith('- ') or not line[2:].strip():
                raise ValueError(f'CHANGELOG.md entry for version {current["version"]} must use non-empty "- Change" bullets.')
            current['changes'].append(line[2:].strip())
    for entry in entries:
        entry['notes'] = '\n'.join('- ' + change for change in entry['changes'])
        if not entry['notes'] or len(entry['notes']) > 16000:
            raise ValueError(f'CHANGELOG.md entry for version {entry["version"]} must contain 1–16000 characters of release notes.')
    return sorted(entries, key=lambda entry: tuple(map(int, entry['version'].split('.'))), reverse=True)


def release_notes(version: str, path: Path | None = None) -> str:
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError('Use a stable MAJOR.MINOR.PATCH version without a v prefix.')
    for entry in read_entries(path):
        if entry['version'] == version:
            return entry['notes']
    raise ValueError(f'Missing CHANGELOG.md entry for version {version}. Add a "## {version}" section with release notes before releasing.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version')
    args = parser.parse_args()
    if args.version is None:
        from app.updates import VERSION
        args.version = VERSION
    try:
        release_notes(args.version)
    except ValueError as exc:
        print(f'::error::{exc}', file=sys.stderr)
        raise SystemExit(1)
    print(f'CHANGELOG.md entry for version {args.version} verified.')


if __name__ == '__main__':
    main()
