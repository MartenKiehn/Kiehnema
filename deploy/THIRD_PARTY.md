`chromium-seccomp.json` comes from Microsoft Playwright v1.63.0:
https://github.com/microsoft/playwright/blob/v1.63.0/utils/docker/seccomp_profile.json

Upstream SHA256: `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`.
Playwright is distributed under the Apache License 2.0; see `PLAYWRIGHT-LICENSE`.

Local change: allow `chroot` as a syscall even with all host capabilities dropped;
Chromium needs it inside its unprivileged user namespace. Kernel capability checks
still prevent a host-namespace chroot. The browser sandbox remains enabled.
