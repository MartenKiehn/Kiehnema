"""Password-wrapped user keys; sessions hold unlocked keys only in process memory."""

from __future__ import annotations

import base64
import hashlib
import fcntl
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import pyotp
from cryptography.exceptions import InvalidKey, InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from app.config import settings

ROLE_LABELS = {'admin': 'Regisseur', 'manager': 'Regieassistenz', 'user': 'Crew'}


def seal(key: bytes, value: bytes, context: str) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, value, context.encode())


def unseal(key: bytes, value: bytes, context: str) -> bytes:
    return AESGCM(key).decrypt(value[:12], value[12:], context.encode())


def kdf(salt: bytes) -> Argon2id:
    return Argon2id(salt=salt, length=32, iterations=3, lanes=1, memory_cost=65536)


def password_bytes(password: str) -> bytes:
    if not 15 <= len(password) <= 256:
        raise ValueError("Use a password of 15–256 characters.")
    return password.encode()


def username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise ValueError("Use 3–64 letters, numbers, dots, underscores or hyphens for the username.")
    return value


@contextmanager
def database():
    root = settings.data_dir / "secure"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise ValueError("The secure data directory must not be a symlink.")
    root.chmod(0o700)
    path = root / "accounts.sqlite"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    path.chmod(0o600)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, admin INTEGER NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0, password_hash TEXT NOT NULL,
            salt BLOB NOT NULL, wrapped_key BLOB NOT NULL, recovery_key BLOB NOT NULL,
            totp BLOB NOT NULL, last_totp INTEGER NOT NULL DEFAULT -1);
        CREATE TABLE IF NOT EXISTS invites (
            token TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id), key BLOB NOT NULL,
            active INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS files (
            job TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, tag TEXT NOT NULL,
            name BLOB NOT NULL, blob TEXT NOT NULL, size INTEGER NOT NULL,
            PRIMARY KEY(job, tag));
        CREATE TABLE IF NOT EXISTS failures (id TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS invitation_managers (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS two_factor_policy (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS security_policy (
            id INTEGER PRIMARY KEY CHECK(id=1), require_totp INTEGER NOT NULL CHECK(require_totp IN (0,1)));
    """)
    try:
        with con:
            yield con
    finally:
        con.close()


@contextmanager
def service_lock():
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if settings.data_dir.is_symlink():
        raise ValueError('The data directory must not be a symlink.')
    # Keep the lock inode outside the directory replaced during an update restore.
    fd = os.open(settings.data_dir / '.service.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another Kiehnema process is using this workspace. Stop it first.') from None
        yield
    finally:
        os.close(fd)


@dataclass
class Identity:
    id: str
    username: str
    admin: bool
    key: bytes = field(repr=False)
    two_factor_verified: bool = False
    manager: bool = False

    @property
    def role(self) -> str:
        return 'admin' if self.admin else 'manager' if self.manager else 'user'

    @property
    def role_label(self) -> str:
        return ROLE_LABELS[self.role]

    @property
    def can_invite(self) -> bool:
        return self.admin or self.manager


current_identity: ContextVar[Identity | None] = ContextVar("identity", default=None)


def refresh(person: Identity) -> Identity:
    with database() as con:
        row = con.execute("""SELECT disabled,admin,
            id IN (SELECT user_id FROM invitation_managers) AS manager FROM users WHERE id=?""", (person.id,)).fetchone()
    if row is None or row['disabled']:
        raise PermissionError('Account unavailable.')
    person.admin, person.manager = bool(row['admin']), bool(row['manager'])
    return person


def identity() -> Identity:
    person = current_identity.get()
    if person is None:
        raise PermissionError("Sign in to unlock your workspace.")
    return refresh(person)


def set_role(person: Identity, uid: str, role: str) -> None:
    if role not in ROLE_LABELS:
        raise ValueError('Choose a valid role.')
    with database() as con:
        con.execute('BEGIN IMMEDIATE')
        if not con.execute('SELECT 1 FROM users WHERE id=? AND admin=1 AND disabled=0', (person.id,)).fetchone():
            raise PermissionError('Administrator access required.')
        if uid == person.id:
            raise ValueError('You cannot change your own administrator role.')
        if not con.execute('UPDATE users SET admin=? WHERE id=?', (int(role == 'admin'), uid)).rowcount:
            raise ValueError('Account unavailable.')
        con.execute('DELETE FROM invitation_managers WHERE user_id=?', (uid,))
        if role == 'manager':
            con.execute('INSERT INTO invitation_managers VALUES(?)', (uid,))
    revoke(uid)


def initialized() -> bool:
    with database() as con:
        return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def all_two_factor_required() -> bool:
    with database() as con:
        return con.execute('SELECT 1 FROM security_policy WHERE require_totp=1').fetchone() is not None


def require_all_two_factor(person: Identity, required: bool) -> None:
    if not refresh(person).admin:
        raise PermissionError('Administrator access required.')
    with database() as con:
        con.execute('INSERT INTO security_policy VALUES(1,?) ON CONFLICT(id) DO UPDATE SET require_totp=excluded.require_totp',
                    (int(required),))


def two_factor_required(person: Identity) -> bool:
    with database() as con:
        return con.execute('''SELECT 1 FROM users WHERE id=? AND (length(totp)>0
            OR id IN (SELECT user_id FROM two_factor_policy) OR EXISTS(SELECT 1 FROM security_policy WHERE require_totp=1))''',
                           (person.id,)).fetchone() is not None


def two_factor_enabled(person: Identity) -> bool:
    with database() as con:
        row = con.execute('SELECT totp FROM users WHERE id=? AND disabled=0', (person.id,)).fetchone()
    if row is None:
        raise PermissionError('Account unavailable.')
    return bool(row['totp'])


def require_two_factor(person: Identity, uid: str, required: bool) -> None:
    if not refresh(person).admin:
        raise PermissionError('Administrator access required.')
    with database() as con:
        con.execute('BEGIN IMMEDIATE')
        if not con.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
            raise ValueError('Account unavailable.')
        if required:
            con.execute('INSERT OR IGNORE INTO two_factor_policy VALUES(?)', (uid,))
        else:
            if con.execute('SELECT 1 FROM security_policy WHERE require_totp=1').fetchone():
                raise ValueError('Turn off the requirement for all users before disabling an individual authenticator.')
            con.execute('DELETE FROM two_factor_policy WHERE user_id=?', (uid,))
            con.execute('UPDATE users SET totp=?,last_totp=-1 WHERE id=?', (b'', uid))
    if not required:
        revoke(uid)


def enrollment(name: str, password: str, *, two_factor: bool = False) -> dict:
    return {"id": secrets.token_hex(16), "username": username(name), "password": password_bytes(password),
            "key": secrets.token_bytes(32), "totp": pyotp.random_base32() if two_factor or all_two_factor_required() else "",
            "recovery": secrets.token_bytes(32)}


def finish_enrollment(draft: dict, code: str = "", *, setup: bool = False, invite: str = "") -> Identity:
    if draft['totp'] and not pyotp.TOTP(draft["totp"]).verify(code):
        raise ValueError("Enter the current code from your authenticator.")
    uid, key, password = draft["id"], draft["key"], draft["password"]
    salt = secrets.token_bytes(16)
    values = (uid, draft["username"], int(setup), kdf(secrets.token_bytes(16)).derive_phc_encoded(password), salt,
              seal(kdf(salt).derive(password), key, f"password:{uid}"),
              seal(draft["recovery"], key, f"recovery:{uid}"),
              seal(key, draft["totp"].encode(), f"totp:{uid}") if draft['totp'] else b'')
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        if not draft['totp'] and con.execute('SELECT 1 FROM security_policy WHERE require_totp=1').fetchone():
            raise ValueError('Two-factor authentication is now required. Start account setup again.')
        if setup:
            if con.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("Administrator setup is already complete.")
        else:
            row = con.execute("SELECT * FROM invites WHERE token=? AND expires>?", (digest(invite), time.time())).fetchone()
            if row is None or row["username"] != draft["username"]:
                raise ValueError("This invitation is invalid or expired.")
            con.execute("DELETE FROM invites WHERE token=?", (digest(invite),))
        con.execute("INSERT INTO users(id,username,admin,password_hash,salt,wrapped_key,recovery_key,totp) VALUES(?,?,?,?,?,?,?,?)", values)
    return Identity(uid, draft["username"], setup, key, bool(draft['totp']))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def throttle(scope: str) -> None:
    now = time.time()
    with database() as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute("DELETE FROM failures WHERE until<?", (now,))
        row = con.execute("SELECT count FROM failures WHERE id=?", (digest(scope),)).fetchone()
        if row and row[0] >= 10:
            raise ValueError("Too many attempts. Try again in 15 minutes.")
        con.execute("INSERT INTO failures VALUES(?,1,?) ON CONFLICT(id) DO UPDATE SET count=count+1",
                    (digest(scope), now + 900))


def authenticate(name: str, password: str, code: str = "") -> Identity:
    name = username(name)
    throttle("login:" + name)
    with database() as con:
        row = con.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    try:
        if row is None or row["disabled"]:
            kdf(b"kiehnema-dummy-v1").derive(password[:256].encode())
            raise ValueError("Invalid username, password or authenticator code.")
        Argon2id.verify_phc_encoded(password[:256].encode(), row["password_hash"])
        key = unseal(kdf(row["salt"]).derive(password.encode()), row["wrapped_key"], f"password:{row['id']}")
        if row['totp']:
            totp = pyotp.TOTP(unseal(key, row["totp"], f"totp:{row['id']}").decode())
            step = int(time.time()) // 30
            if not totp.verify(code):
                raise ValueError("Invalid username, password or authenticator code.")
        with database() as con:
            if row['totp']:
                changed = con.execute("UPDATE users SET last_totp=? WHERE id=? AND last_totp<? AND disabled=0 AND totp=?",
                                      (step, row["id"], step, row['totp'])).rowcount
                if not changed:
                    raise ValueError("Wait for a new authenticator code before signing in again.")
            elif not con.execute("SELECT 1 FROM users WHERE id=? AND disabled=0 AND totp=? AND password_hash=?",
                                 (row['id'], b'', row['password_hash'])).fetchone():
                raise ValueError('Account changed. Sign in again.')
            con.execute("DELETE FROM failures WHERE id=?", (digest("login:" + name),))
        return refresh(Identity(row["id"], name, bool(row["admin"]), key, bool(row['totp'])))
    except (InvalidKey, InvalidTag):
        raise ValueError("Invalid username, password or authenticator code.") from None


@dataclass
class Session:
    person: Identity
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created: float = field(default_factory=time.time)
    used: float = field(default_factory=time.time)
    totp_setup: str = field(default="", repr=False)


sessions: dict[str, Session] = {}
session_lock = threading.RLock()


def new_session(person: Identity) -> str:
    token = secrets.token_urlsafe(32)
    with session_lock:
        sessions[digest(token)] = Session(person)
    return token


def get_session(token: str) -> Session | None:
    with session_lock:
        now = time.time()
        for sid, item in list(sessions.items()):
            if now - item.used > 1800 or now - item.created > 43200:
                del sessions[sid]
        item = sessions.get(digest(token))
        if item:
            try:
                refresh(item.person)
            except PermissionError:
                sessions.pop(digest(token), None)
                return None
            item.used = now
        return item


def revoke(uid: str) -> None:
    with session_lock:
        for sid, item in list(sessions.items()):
            if item.person.id == uid:
                del sessions[sid]


def change_password(person: Identity, password: str) -> None:
    password = password_bytes(password)
    salt = secrets.token_bytes(16)
    with database() as con:
        con.execute("UPDATE users SET password_hash=?,salt=?,wrapped_key=? WHERE id=?",
                    (kdf(secrets.token_bytes(16)).derive_phc_encoded(password), salt,
                     seal(kdf(salt).derive(password), person.key, f"password:{person.id}"), person.id))
    revoke(person.id)


def enable_two_factor(person: Identity, secret: str, code: str) -> Identity:
    throttle('totp-setup:' + person.id)
    if not secret or not pyotp.TOTP(secret).verify(code):
        raise ValueError('Enter the current code from your authenticator.')
    with database() as con:
        changed = con.execute('UPDATE users SET totp=?,last_totp=? WHERE id=? AND disabled=0 AND totp=?',
            (seal(person.key, secret.encode(), f'totp:{person.id}'), int(time.time()) // 30, person.id, b'')).rowcount
        if not changed:
            raise ValueError('Account changed. Sign in again.')
    revoke(person.id)
    return refresh(Identity(person.id, person.username, person.admin, person.key, True))


def recover(name: str, recovery: str, password: str) -> dict:
    name = username(name)
    throttle('recovery:' + name)
    with database() as con:
        row = con.execute('SELECT * FROM users WHERE username=? AND disabled=0', (name,)).fetchone()
    try:
        secret = base64.urlsafe_b64decode(recovery.strip() + '=' * (-len(recovery.strip()) % 4))
        if row is None or len(secret) != 32:
            raise ValueError('Invalid recovery credentials.')
        key = unseal(secret, row['recovery_key'], f"recovery:{row['id']}")
    except (ValueError, InvalidTag):
        raise ValueError('Invalid recovery credentials.') from None
    person = Identity(row['id'], name, bool(row['admin']), key)
    draft = enrollment(name, password, two_factor=two_factor_required(person))
    draft.update(id=row['id'], key=key, admin=bool(row['admin']), previous_recovery=row['recovery_key'], previous_totp=row['totp'])
    return draft


def finish_recovery(draft: dict, code: str = "") -> Identity:
    if draft['totp'] and not pyotp.TOTP(draft['totp']).verify(code):
        raise ValueError('Enter the current code from your authenticator.')
    salt = secrets.token_bytes(16)
    uid, key, password = draft['id'], draft['key'], draft['password']
    with database() as con:
        con.execute('BEGIN IMMEDIATE')
        if not draft['totp'] and con.execute('''SELECT 1 FROM two_factor_policy WHERE user_id=?
                UNION ALL SELECT 1 FROM security_policy WHERE require_totp=1''', (uid,)).fetchone():
            raise ValueError('Two-factor authentication is now required. Start recovery again.')
        changed = con.execute('''UPDATE users SET password_hash=?,salt=?,wrapped_key=?,recovery_key=?,totp=?,last_totp=-1
            WHERE id=? AND disabled=0 AND recovery_key=? AND totp=?''',
            (kdf(secrets.token_bytes(16)).derive_phc_encoded(password), salt,
             seal(kdf(salt).derive(password), key, f'password:{uid}'),
             seal(draft['recovery'], key, f'recovery:{uid}'),
             seal(key, draft['totp'].encode(), f'totp:{uid}') if draft['totp'] else b'',
             uid, draft['previous_recovery'], draft['previous_totp'])).rowcount
        if not changed:
            raise ValueError('Recovery has expired or was already used.')
    revoke(uid)
    return refresh(Identity(uid, draft['username'], draft['admin'], key, bool(draft['totp'])))


def recovery_text(draft: dict) -> str:
    return base64.urlsafe_b64encode(draft["recovery"]).decode().rstrip("=")


def invite_user(person: Identity, name: str) -> str:
    name = username(name)
    token = secrets.token_urlsafe(32)
    with database() as con:
        con.execute('BEGIN IMMEDIATE')
        if not con.execute("""SELECT 1 FROM users WHERE id=? AND disabled=0
                AND (admin=1 OR id IN (SELECT user_id FROM invitation_managers))""", (person.id,)).fetchone():
            raise PermissionError('An administrator or manager is required to invite users.')
        if con.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
            raise ValueError("This username already exists.")
        con.execute("DELETE FROM invites WHERE username=?", (name,))
        con.execute("INSERT INTO invites VALUES(?,?,?)", (digest(token), name, time.time() + 86400))
    return token
