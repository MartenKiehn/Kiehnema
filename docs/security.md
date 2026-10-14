# Accounts and encrypted workspaces

The shared app stores user content only as authenticated ciphertext under `data/secure/`.
The app administrator manages invitations and account suspension, but cannot open another
user's jobs. This protects against other application users and copies of the database,
files and backups without the user's secrets. It does **not** protect against root, code
execution as the service user, a compromised browser, or live memory snapshots. Bedrock
still receives the content needed for model inference.

## First start

```sh
uv sync --frozen
uv run playwright install chromium
uv run python -m app.security setup
uv run python -m app
```

`python -m app` also runs setup on its first interactive start. Headless deployments must
run the setup command in a terminal first. There is no default password or public admin
registration. Choose a password of at least 15 characters, enroll the displayed TOTP key
in an authenticator, and save the personal recovery key outside the server. Setup can only
create the first administrator. Do not record the setup terminal in shared logs.

Use **Users** to create single-use invitations (24-hour expiry), disable accounts or enable
them again. Invitees choose their own password and authenticator at `/activate`. Disabling
an account revokes its sessions and cancels its jobs. `/recover` requires the user's
personal recovery key, preserves existing content and rotates the password, authenticator
and recovery key. Without a password or recovery key, the old content cannot be recovered.
An administrator has no decryption override.

## RAM processing workspace

The server refuses ordinary disk scratch directories. On Linux, `MG_WORK_DIR` defaults
to `/dev/shm/kiehnema`: use a sufficiently sized tmpfs, disable swap, and run the service as
a dedicated unprivileged OS account without interactive SSH access. The directory must
be owned by that account with mode `0700`. Generation runs Chromium's sandbox; do not run
the service as root or disable the browser sandbox to work around deployment errors.

On macOS, create a RAM disk for development. The following allocates a **new** 4 GiB memory
device; only format the device returned by this allocation, never an existing disk:

```sh
kiehnema_ram_device=$(hdiutil attach -nomount ram://8388608 | awk 'NR==1 {print $1}')
test -n "$kiehnema_ram_device" && diskutil eraseVolume HFS+ KiehnemaRAM "$kiehnema_ram_device"
mkdir -m 700 /Volumes/KiehnemaRAM/work
MG_WORK_DIR=/Volumes/KiehnemaRAM/work uv run python -m app
```

The app verifies RAM backing with `hdiutil` and requires encrypted macOS swap. RAM disks
must be recreated after reboot. Stop the app before ejecting one. Core dumps are disabled.
The application does not create mounts or change the host's swap configuration itself.

Each job runs in a subprocess with its own key and private temporary directory. Browser
profiles and caches use that directory; browser subprocesses do not inherit AWS credentials.
Files are encrypted after each pipeline node; checkpoints, including pending writes and
metadata, are serialized from in-memory SQLite into encrypted objects. Progress snapshots
are encrypted immediately. Incomplete work can lose changes since its last saved node.

Signing out revokes browser access immediately, while already submitted work continues
until review, completion, failure or the two-hour timeout. No user master key is passed to
the worker. Restarting the server invalidates sessions and interrupts unfinished jobs;
it never restarts paid work automatically. Re-enter credentials to unlock stored results.

## Public deployment

Set `MG_PUBLIC_ORIGIN=https://your-host.example`, terminate TLS at a trusted reverse proxy,
and restrict the application port to that proxy. Forward the original Host and scheme;
trust forwarded headers only from the proxy's address. Run one application process per
data directory; an advisory lock rejects a second coordinator. The pipeline CLI prompts
for credentials and requires the web process to be stopped for start/resume operations.

Session cookies are HttpOnly, SameSite=Strict and Secure over HTTPS, with the `__Host-`
prefix. Sessions expire after 30 idle minutes or 12 hours total. Mutations require an
Origin check and CSRF token. Preview code remains sandboxed and receives a job-scoped,
session-bound capability, revoked on logout. Disable URL/body logging at the reverse proxy:
preview URLs contain bearer capabilities. The built-in launcher disables access logging.
Do not enable LLM tracing or prompt/invocation logging without a separate privacy review.

Use OS/container resource limits and network egress controls in a public deployment.
Application crawls validate and pin public IP destinations while preserving TLS hostname
verification; public users cannot opt into localhost access. Internal render servers
require an unguessable path token and do not list directories or follow symlinks.

## Storage, backup and migration

Passwords use Argon2id (64 MiB, three passes); independent salts separate login hashes
from password-derived wrapping keys. Random user keys wrap random job keysets. File data
uses Tink AES-256-GCM-HKDF streaming AEAD; key wrapping and encrypted file indexes use
AES-GCM. Owner, job and file identities are authenticated. HTTP video ranges decrypt a
bounded stream without a plaintext disk copy; Tink's Python API currently reads preceding
segments when seeking. This costs additional CPU for seeks in large exports.

Account names, roles, random IDs, ownership, object sizes and activity remain visible in
the database. Contents and logical filenames do not. This does not conceal usage patterns.

Stop the app before copying the complete `data/secure/` directory for backup; keep its
database and object files together. Do not include live memory, RAM disks or temporary
worker directories in backups. Test a restore with a user's recovery key. Deleting a job
does not erase copies in historical backups, filesystem snapshots or discarded SSD blocks.
Password changes do not revoke old ciphertext/key copies an attacker already obtained.

To import existing jobs into the administrator's encrypted workspace, stop the app and run:

```sh
uv run python -m app.security import-legacy ./data
```

The importer authenticates the administrator, copies job files and only each job's SQLite
checkpoint rows, verifies decryption, and skips IDs already present. **It never deletes
the legacy source.** After checking the imported videos, separately retire old `jobs/`,
`jobs.sqlite*`, exports, snapshots and backups according to your retention requirements.
Do not advertise existing data as encrypted until those plaintext copies are addressed.

Standalone demo, crawl, render and screenshot commands produce explicit plaintext exports.
They are development/export utilities, not the shared app's storage boundary. Downloads
on a user's device likewise fall under that user's device and backup protections.
For a private UI capture, add `--login` to `python -m app.web.screenshot`; it prompts for
credentials without placing passwords in command arguments and signs out after capture.

## Verification

`uv run pytest` includes ownership, admin isolation, MFA/CSRF, recovery, encrypted database
and filename checks, ciphertext tampering, range downloads, logout/worker separation and
a real subprocess checkpoint round trip. Browser tests cover the editing workflow without
Bedrock calls. `MG_LIVE_BEDROCK=1` is a separate, billable opt-in. These tests are not an
independent penetration test or an audit of a deployment's OS, TLS proxy or backup system.
