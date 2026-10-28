#!/usr/bin/env bash
# Release publication embeds the tested installer, manifest and public trust key below.
set -euo pipefail
if [[ "${1:-}" == --help ]]; then
  echo 'Kiehnema: interactive installation on a dedicated Ubuntu 24.04 server (amd64/arm64).'
  echo 'Run with sudo from a terminal. Requires a domain and an EC2 Bedrock IAM role.'
  exit 0
fi
[[ $# == 0 ]] || { echo 'Only --help is supported.' >&2; exit 1; }
[[ $EUID == 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || {
  echo 'This installer supports Ubuntu 24.04 only.' >&2; exit 1;
}
exec </dev/tty
umask 077
stage=$(mktemp -d /tmp/kiehnema-install.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
# RELEASE_PAYLOAD
[[ -s "$stage/update.json" ]] || {
  echo 'Download install.sh from a published GitHub release, not the source template.' >&2; exit 1;
}
if ! command -v python3 >/dev/null; then
  apt-get update
  apt-get install -y --no-install-recommends python3
fi
python3 "$stage/setup_server.py"
