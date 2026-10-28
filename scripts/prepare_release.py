"""Prepare a release commit and tag from a protected main workflow dispatch."""

import os
from pathlib import Path
import re
import subprocess
import tomllib


def version(value):
    if not re.fullmatch(r'(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})', value):
        raise ValueError('Enter a stable MAJOR.MINOR.PATCH version without a v prefix.')
    return tuple(map(int, value.split('.')))


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def main():
    env = os.environ
    if (env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or env.get('GITHUB_REF') != 'refs/heads/main'
            or env.get('GITHUB_REF_PROTECTED') != 'true'):
        raise ValueError('Prepare releases only by manually running the workflow on protected main.')
    requested = env['RELEASE_VERSION']
    notes = env['RELEASE_NOTES'].strip()
    project = Path('pyproject.toml')
    current = tomllib.loads(project.read_text())['project']['version']
    if version(requested) < version(current):
        raise ValueError('Release version must not be lower than the version on main.')
    if not notes or len(notes) > 16000:
        raise ValueError('Release notes must contain 1–16000 characters.')
    print(f'Current project version: {current}; requested release version: {requested}.', flush=True)
    with Path(env['GITHUB_STEP_SUMMARY']).open('a') as summary:
        summary.write(f'## Release preparation\n\nCurrent version: **{current}** → requested version: **{requested}**\n')
    tag = 'v' + requested
    commit = env['GITHUB_SHA']
    if (git('rev-parse', 'HEAD') != commit or git('status', '--porcelain')
            or git('ls-remote', 'origin', 'refs/heads/main').split()[0] != commit):
        raise ValueError('Start from the unchanged current main commit; restart the workflow if main advanced.')
    if git('ls-remote', 'origin', 'refs/tags/' + tag):
        raise ValueError('This release tag already exists. Existing versions are never overwritten.')
    updated, count = re.subn(r'(?m)^version = "[^"]+"$', f'version = "{requested}"', project.read_text())
    if count != 1:
        raise ValueError('Expected exactly one project version assignment in pyproject.toml.')
    readme = Path('README.md')
    description, count = re.subn(r'(?m)^Current version / Aktuelle Version: \*\*[^\n]+\*\*$',
        f'Current version / Aktuelle Version: **{requested}**', readme.read_text())
    if count != 1:
        raise ValueError('Expected exactly one current-version display in README.md.')
    project.write_text(updated)
    readme.write_text(description)
    Path('deploy/release-notes.md').write_text(notes + '\n')
    subprocess.run(['uv', 'lock'], check=True)
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('add', '--', 'pyproject.toml', 'uv.lock', 'deploy/release-notes.md', 'README.md')
    git('commit', '--allow-empty', '-m', 'chore: prepare release ' + tag)
    git('tag', '-a', tag, '-m', 'Release ' + requested)
    prepared = git('rev-parse', 'HEAD')
    # Atomic, non-forced publication refuses races without leaving only one ref updated.
    git('push', '--atomic', '--no-force', '--no-follow-tags', 'origin', 'HEAD:refs/heads/main', 'refs/tags/' + tag)
    with Path(env['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'commit={prepared}\ntag={tag}\n')
    print(f'Prepared {tag} at {prepared}. Tests and publication continue in this workflow.')


if __name__ == '__main__':
    main()
