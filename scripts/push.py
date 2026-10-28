#!/usr/bin/env python3
"""Push the current branch to GitLab and GitHub without rewriting remote history."""

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = {
    'GitLab': 'git@git.timeglass.de:mkiehn/kiehnema.git',
    'GitHub': 'git@github.com:MartenKiehn/Kiehnema.git',
}


def main():
    try:
        branch = subprocess.check_output(['git', 'symbolic-ref', '--quiet', 'HEAD'], cwd=ROOT, text=True).strip()
        commit = subprocess.check_output(['git', 'rev-parse', '--verify', 'HEAD'], cwd=ROOT, text=True).strip()
    except subprocess.CalledProcessError:
        print('Check out a branch with at least one commit before pushing.', file=sys.stderr)
        return 1
    failed = []
    for name, url in REPOSITORIES.items():
        print(f'Pushing {branch} ({commit[:12]}) to {name}...', flush=True)
        result = subprocess.run(['git', 'push', '--no-force', '--no-follow-tags', url, f'{commit}:{branch}'], cwd=ROOT)
        if result.returncode:
            failed.append(name)
    if failed:
        print(f'Push failed: {", ".join(failed)}. The other destination may already be updated. '
              'Resolve the Git error and rerun this script.', file=sys.stderr)
        return 1
    print('Both repositories now contain the pushed branch commit.')
    return 0


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    sys.exit(main())
