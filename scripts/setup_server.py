"""Interactive bootstrap for a fresh, dedicated Ubuntu 24.04 EC2 host; standard library only."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys

if __package__:
    from .install import verify_release
else:
    from install import verify_release


def run(*args):
    subprocess.run(args, check=True)


def validate_host(system, architecture, memory_kib, free_bytes, installed):
    if system.get('ID') != 'ubuntu' or system.get('VERSION_ID') != '24.04':
        raise ValueError('Use Ubuntu 24.04.')
    if architecture not in {'x86_64', 'aarch64'}:
        raise ValueError('Use an amd64 or arm64 instance.')
    if memory_kib < 15 * 1024 * 1024 or free_bytes < 30 * 1024**3:
        raise ValueError('Use an instance with at least 16 GiB RAM and 30 GiB free disk space.')
    if installed:
        raise ValueError('/opt/kiehnema already exists. Use Updates; existing data will not be overwritten.')


def configuration(domain, region, model):
    domain = domain.strip().lower()
    if (len(domain) > 253 or '.' not in domain or not all(
            re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.'))):
        raise ValueError('Enter a DNS hostname such as video.example.com, without https:// or a path.')
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', region):
        raise ValueError('Invalid AWS region.')
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,300}', model):
        raise ValueError('Invalid Bedrock model or inference profile ID.')
    return 'https://' + domain, f'MG_AWS_REGION={region}\nMG_BEDROCK_MODEL_ID={model}\nAWS_EC2_METADATA_V1_DISABLED=true\n'


def without_swap(fstab):
    lines = []
    for line in fstab.splitlines(keepends=True):
        fields = line.split()
        lines.append('# Kiehnema disabled swap: ' + line
                     if fields and not line.lstrip().startswith('#') and len(fields) >= 3 and fields[2] == 'swap'
                     else line)
    return ''.join(lines)


def prepare_host():
    run('apt-get', 'update')
    run('apt-get', 'install', '-y', '--no-install-recommends', 'ca-certificates', 'curl', 'openssl')
    if not shutil.which('docker'):
        architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
        if architecture not in {'amd64', 'arm64'}:
            raise ValueError('Unsupported Docker package architecture.')
        Path('/etc/apt/keyrings').mkdir(mode=0o755, exist_ok=True)
        Path('/etc/apt/keyrings').chmod(0o755)
        run('curl', '--fail', '--silent', '--show-error', '--proto', '=https', '--tlsv1.2',
            'https://download.docker.com/linux/ubuntu/gpg', '-o', '/etc/apt/keyrings/docker.asc')
        Path('/etc/apt/keyrings/docker.asc').chmod(0o644)
        source = Path('/etc/apt/sources.list.d/kiehnema-docker.sources')
        source.write_text('Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\n'
                          f'Components: stable\nArchitectures: {architecture}\nSigned-By: /etc/apt/keyrings/docker.asc\n')
        source.chmod(0o644)
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', '--no-install-recommends', 'docker-ce', 'docker-ce-cli',
            'containerd.io', 'docker-buildx-plugin', 'docker-compose-plugin')
    run('systemctl', 'enable', '--now', 'docker')
    api = subprocess.check_output(['docker', 'version', '--format', '{{.Server.APIVersion}}'], text=True).strip()
    compose = subprocess.check_output(['docker', 'compose', 'version', '--short'], text=True).strip()
    if tuple(map(int, api.split('.'))) < (1, 45) or tuple(map(int, compose.lstrip('v').split('.')[:2])) < (2, 30):
        raise ValueError('Docker API >= 1.45 and Compose >= 2.30 are required. Upgrade the existing installation.')
    fstab = Path('/etc/fstab')
    original = fstab.read_text()
    updated = without_swap(original)
    if updated != original:
        backup = Path('/etc/fstab.kiehnema-before-install')
        with backup.open('x') as stream:
            stream.write(original)
        fstab.write_text(updated)
    run('swapoff', '-a')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'mask', 'swap.target')
    if len(Path('/proc/swaps').read_text().splitlines()) > 1:
        raise ValueError('Host swap is still active.')
    # Ubuntu's extra userns restriction blocks Chromium inside Docker's sandbox.
    if Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns').exists():
        policy = Path('/etc/sysctl.d/90-kiehnema.conf')
        policy.write_text('kernel.apparmor_restrict_unprivileged_userns=0\n')
        run('sysctl', '-p', str(policy))


def main():
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise ValueError('Run with sudo in an interactive terminal.')
    os.umask(0o077)
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    validate_host(platform.freedesktop_os_release(), platform.machine(), int(memory['MemTotal'].split()[0]),
                  shutil.disk_usage('/opt').free, os.path.lexists('/opt/kiehnema'))
    for port in (80, 443):
        with socket.socket() as probe:
            probe.bind(('0.0.0.0', port))
    if shutil.which('docker'):
        if subprocess.check_output(['docker', 'ps', '-aq'], text=True).strip():
            raise ValueError('Existing Docker containers found. Use a dedicated, fresh server.')
    print('Kiehnema will install Docker, disable host swap and allow unprivileged user namespaces.')
    print('This changes host-wide settings. Use a dedicated server; app container sandboxing stays enabled.')
    print('Before continuing: DNS must point here, TCP 80/443 must be open, and outbound HTTPS must work.')
    print('Attach a Bedrock IAM role to EC2; require IMDSv2 with metadata response hop limit 2.')
    origin, content = configuration(input('Public domain (e.g. video.example.com): '),
        input('AWS region [eu-central-1]: ').strip() or 'eu-central-1',
        input('Bedrock model/inference profile ID: ').strip())
    socket.getaddrinfo(origin.removeprefix('https://'), 443)
    if input('Install on this dedicated server? Type INSTALL: ').strip() != 'INSTALL':
        raise ValueError('Installation cancelled; server preparation has not started.')
    install_release(Path(__file__).resolve().parent, origin, content)


def install_release(bundle, origin, content):
    if not shutil.which('openssl'):
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', '--no-install-recommends', 'openssl')
    release = verify_release((bundle / 'update.json').read_bytes(), json.loads((bundle / 'keys.json').read_text()))
    repository = json.loads((bundle / 'repository.json').read_text())
    image_repo = 'ghcr.io/' + repository.lower()
    for role, image in release['images'].items():
        if image.split('@')[0] != image_repo + ('/gateway' if role == 'gateway' else ''):
            raise ValueError('Release images belong to an unexpected registry repository.')
    target = 'linux/' + {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
    if target not in release['platforms']:
        raise ValueError('This release does not support this server architecture.')
    prepare_host()
    image = release['images']['app']
    run('docker', 'pull', image)
    run('docker', 'run', '--rm', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '-e', 'AWS_EC2_METADATA_V1_DISABLED=true', '-e', 'AWS_METADATA_SERVICE_TIMEOUT=5',
        '--entrypoint', 'python', image, '-c',
        'import boto3; c = boto3.Session().get_credentials(); '
        'assert c is not None and c.method == "iam-role", "EC2 IAM role unavailable: check role, IMDSv2 and hop limit 2"; '
        'c.get_frozen_credentials(); print("EC2 role credentials available inside the container.")')
    app_env = bundle / 'app.env'
    app_env.write_text(content)
    run(sys.executable, str(bundle / 'install.py'), '--release', str(bundle / 'update.json'),
        '--keys', str(bundle / 'keys.json'), '--app-env', str(app_env), '--origin', origin, '--repository', repository)
    print('Keep the admin recovery key outside this server. Configure two-factor authentication under Users.')
    print('Checking HTTPS. If this fails, check DNS, security groups and certificate issuance; containers stay running.')
    run('curl', '--fail', '--silent', '--show-error', '--proto', '=https', '--retry', '12',
        '--retry-delay', '5', '--retry-all-errors', '--connect-timeout', '5', '--max-time', '10',
        origin + '/login', '-o', '/dev/null')
    print('Ready: ' + origin)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f'Installation stopped: {exc}\nExisting data is retained. Do not delete /opt/kiehnema to retry blindly.')
