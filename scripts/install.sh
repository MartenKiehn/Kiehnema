#!/usr/bin/env bash
# Release publication embeds the tested installer, manifest and public trust key below.
set -euo pipefail
if [[ "${1:-}" == --help ]]; then
  echo 'Kiehnema: interactive installation on supported Linux servers (amd64/arm64, systemd).'
  echo 'Ubuntu, Debian, Fedora, RHEL, Rocky, AlmaLinux, CentOS Stream and Amazon Linux 2023.'
  echo 'Run with sudo from a terminal. Configure the AI provider later in the application.'
  exit 0
fi
[[ $# == 0 ]] || { echo 'Only --help is supported.' >&2; exit 1; }
[[ $EUID == 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
source /etc/os-release
case "$ID:$VERSION_ID" in
  ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) manager=apt ;;
  fedora:43|fedora:44|fedora:45|amzn:2023|rhel:9|rhel:9.*|rhel:10|rhel:10.*|rocky:9|rocky:9.*|rocky:10|rocky:10.*|almalinux:9|almalinux:9.*|almalinux:10|almalinux:10.*|centos:9|centos:10) manager=dnf ;;
  *) echo 'Unsupported Linux version. See the supported versions in docs/updates.md.' >&2; exit 1 ;;
esac
exec </dev/tty
umask 077
stage=$(mktemp -d /tmp/kiehnema-install.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
# RELEASE_PAYLOAD
[[ -s "$stage/update.json" ]] || {
  echo 'Download install.sh from a published GitHub release, not the source template.' >&2; exit 1;
}
if ! command -v python3 >/dev/null; then
  if [[ "$manager" == apt ]]; then
    apt-get update
    apt-get install -y --no-install-recommends python3
  else
    dnf install -y python3
  fi
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "Python 3.9 or newer is required.")'
python3 "$stage/setup_server.py"
