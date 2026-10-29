"""Interactive bootstrap for dedicated Linux servers; standard library only (Python 3.9+)."""

import json
import hashlib
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile

if __package__:
    from .install import verify_release
else:
    from install import verify_release


def run(*args):
    subprocess.run(args, check=True)


def host_profile(system):
    name, release = system.get('ID'), system.get('VERSION_ID', '')
    if name == 'ubuntu' and release in {'22.04', '24.04', '26.04'}:
        return 'apt', 'ubuntu', {'22.04': 'jammy', '24.04': 'noble', '26.04': 'resolute'}[release]
    if name == 'debian' and release in {'12', '13'}:
        return 'apt', 'debian', {'12': 'bookworm', '13': 'trixie'}[release]
    major = release.split('.')[0]
    if name == 'fedora' and major in {'43', '44', '45'}:
        return 'dnf', 'fedora', major
    if name in {'rhel', 'rocky', 'almalinux', 'centos'} and major in {'9', '10'}:
        return 'dnf', 'rhel' if name == 'rhel' else 'centos', major
    if name == 'amzn' and release == '2023':
        return 'dnf', 'amzn', release
    raise ValueError('Unsupported distribution/version. See the supported Linux versions in docs/updates.md.')


def operating_system():
    # Python 3.9 on RHEL/Amazon Linux has no platform.freedesktop_os_release yet.
    return dict(line.split('=', 1) for line in shlex.split(Path('/etc/os-release').read_text(), comments=True)
                if '=' in line)


def validate_host(system, architecture, memory_kib, free_bytes, installed):
    host_profile(system)
    if architecture not in {'x86_64', 'aarch64'}:
        raise ValueError('Use an amd64 or arm64 instance.')
    if memory_kib < 15 * 1024 * 1024 or free_bytes < 30 * 1024**3:
        raise ValueError('Use an instance with at least 16 GiB RAM and 30 GiB free disk space.')
    if installed:
        raise ValueError('/opt/kiehnema already exists. Use Updates; existing data will not be overwritten.')


def configuration(domain):
    domain = domain.strip().lower()
    if (len(domain) > 253 or '.' not in domain or not all(
            re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.'))):
        raise ValueError('Enter a DNS hostname such as video.example.com, without https:// or a path.')
    return 'https://' + domain, 'MG_PROVIDER_SETUP_REQUIRED=true\nAWS_EC2_METADATA_V1_DISABLED=true\n'


def without_swap(fstab):
    lines = []
    for line in fstab.splitlines(keepends=True):
        fields = line.split()
        lines.append('# Kiehnema disabled swap: ' + line
                     if fields and not line.lstrip().startswith('#') and len(fields) >= 3 and fields[2] == 'swap'
                     else line)
    return ''.join(lines)


def prerequisites(profile):
    manager, distribution, _ = profile
    packages = ['ca-certificates', 'openssl', 'util-linux']
    if not shutil.which('curl'):
        packages.append('curl-minimal' if distribution == 'amzn' else 'curl')
    if manager == 'apt':
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', '--no-install-recommends', *packages)
    else:
        run('dnf', 'install', '-y', *packages)


def amazon_compose(architecture):
    hashes = {'x86_64': 'dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372',
              'aarch64': 'd26373b19e89160546d15407516cc59f453030d9bc5b43ba7faf16f7b4980137'}
    expected = hashes[architecture]
    with tempfile.TemporaryDirectory(prefix='kiehnema-compose-') as directory:
        binary = Path(directory) / 'docker-compose'
        run('curl', '--fail', '--location', '--silent', '--show-error', '--proto', '=https',
            f'https://github.com/docker/compose/releases/download/v2.40.3/docker-compose-linux-{architecture}', '-o', str(binary))
        if hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
            raise ValueError('Docker Compose download failed checksum verification.')
        destination = Path('/usr/local/lib/docker/cli-plugins')
        destination.mkdir(parents=True, exist_ok=True)
        destination.chmod(0o755)
        shutil.copyfile(binary, destination / 'docker-compose')
        (destination / 'docker-compose').chmod(0o755)


def prepare_host(profile):
    manager, distribution, release = profile
    if not shutil.which('docker'):
        if manager == 'apt':
            architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
            if architecture not in {'amd64', 'arm64'}:
                raise ValueError('Unsupported Docker package architecture.')
            Path('/etc/apt/keyrings').mkdir(mode=0o755, exist_ok=True)
            Path('/etc/apt/keyrings').chmod(0o755)
            run('curl', '--fail', '--silent', '--show-error', '--proto', '=https',
                f'https://download.docker.com/linux/{distribution}/gpg', '-o', '/etc/apt/keyrings/docker.asc')
            Path('/etc/apt/keyrings/docker.asc').chmod(0o644)
            source = Path('/etc/apt/sources.list.d/kiehnema-docker.sources')
            source.write_text(f'Types: deb\nURIs: https://download.docker.com/linux/{distribution}\nSuites: {release}\n'
                              f'Components: stable\nArchitectures: {architecture}\nSigned-By: /etc/apt/keyrings/docker.asc\n')
            source.chmod(0o644)
            run('apt-get', 'update')
            run('apt-get', 'install', '-y', '--no-install-recommends', 'docker-ce', 'docker-ce-cli',
                'containerd.io', 'docker-buildx-plugin', 'docker-compose-plugin')
        elif distribution == 'amzn':
            run('dnf', 'install', '-y', 'docker')
            amazon_compose(platform.machine())
        else:
            source = Path('/etc/yum.repos.d/kiehnema-docker.repo')
            source.write_text(f'[docker-ce-stable]\nname=Docker CE Stable\n'
                f'baseurl=https://download.docker.com/linux/{distribution}/{release}/$basearch/stable\n'
                f'enabled=1\ngpgcheck=1\ngpgkey=https://download.docker.com/linux/{distribution}/gpg\n')
            source.chmod(0o644)
            run('dnf', 'install', '-y', 'docker-ce', 'docker-ce-cli', 'containerd.io',
                'docker-buildx-plugin', 'docker-compose-plugin')
    run('systemctl', 'enable', '--now', 'docker')
    api = subprocess.check_output(['docker', 'version', '--format', '{{.Server.APIVersion}}'], text=True).strip()
    compose = subprocess.check_output(['docker', 'compose', 'version', '--short'], text=True).strip()
    if tuple(map(int, api.split('.'))) < (1, 44) or tuple(map(int, compose.lstrip('v').split('.')[:2])) < (2, 30):
        raise ValueError('Docker API >= 1.44 and Compose >= 2.30 are required. Upgrade the existing installation.')
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
    system = operating_system()
    validate_host(system, platform.machine(), int(memory['MemTotal'].split()[0]),
                  shutil.disk_usage('/opt').free, os.path.lexists('/opt/kiehnema'))
    if not Path('/run/systemd/system').is_dir():
        raise ValueError('This server installer requires systemd.')
    for port in (80, 443):
        with socket.socket() as probe:
            probe.bind(('0.0.0.0', port))
    if shutil.which('docker'):
        if subprocess.check_output(['docker', 'ps', '-aq'], text=True).strip():
            raise ValueError('Existing Docker containers found. Use a dedicated, fresh server.')
    print('Kiehnema will install Docker, disable host swap and allow unprivileged user namespaces.')
    print('This changes host-wide settings. Use a dedicated server; app container sandboxing stays enabled.')
    print('Before continuing: DNS must point here, TCP 80/443 must be open, and outbound HTTPS must work.')
    print('Configure the AI provider and credentials after installation under AI provider in the application.')
    origin, content = configuration(input('Public domain (e.g. video.example.com): '))
    socket.getaddrinfo(origin.removeprefix('https://'), 443)
    if input('Install on this dedicated server? Type INSTALL: ').strip() != 'INSTALL':
        raise ValueError('Installation cancelled; server preparation has not started.')
    install_release(Path(__file__).resolve().parent, origin, content, host_profile(system))


def install_release(bundle, origin, content, profile):
    prerequisites(profile)
    release = verify_release((bundle / 'update.json').read_bytes(), json.loads((bundle / 'keys.json').read_text()))
    repository = json.loads((bundle / 'repository.json').read_text())
    image_repo = 'ghcr.io/' + repository.lower()
    for role, image in release['images'].items():
        if image.split('@')[0] != image_repo + ('/gateway' if role == 'gateway' else ''):
            raise ValueError('Release images belong to an unexpected registry repository.')
    target = 'linux/' + {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
    if target not in release['platforms']:
        raise ValueError('This release does not support this server architecture.')
    prepare_host(profile)
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
    print('Sign in as administrator and open AI provider to finish AI setup for all users.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f'Installation stopped: {exc}\nExisting data is retained. Do not delete /opt/kiehnema to retry blindly.')
