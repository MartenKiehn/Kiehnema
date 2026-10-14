"""Encrypted object files with authenticated names and owner-bound job keys."""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import tink
from tink import secret_key_access, streaming_aead

from app.config import settings
from app.security.accounts import database, identity, seal, unseal

streaming_aead.register()
active_job: ContextVar[Job | None] = ContextVar("active_job", default=None)


def valid_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise FileNotFoundError("Job not found")
    return value


def valid_name(value: str) -> str:
    if "\\" in value or "\x00" in value or PurePosixPath(value).is_absolute() or ".." in value.split("/"):
        raise FileNotFoundError("File not found")
    return str(PurePosixPath(value)) if value else ""


@dataclass
class Job:
    id: str
    owner: str
    secret: bytes = field(repr=False)

    @property
    def directory(self) -> Path:
        path = settings.data_dir / "secure" / "objects" / valid_id(self.id)
        if path.is_symlink():
            raise ValueError("Invalid object directory")
        return path

    @property
    def index_key(self) -> bytes:
        return hmac.digest(self.secret, b"kiehnema-file-index-v1", "sha256")

    def context(self, name: str) -> str:
        return f"kiehnema:v1:{self.owner}:{self.id}:{valid_name(name)}"

    def tag(self, name: str) -> str:
        key = hmac.digest(self.secret, b'kiehnema-filename-tags-v1', 'sha256')
        return hmac.new(key, valid_name(name).encode(), hashlib.sha256).hexdigest()

    def primitive(self):
        return tink.proto_keyset_format.parse(self.secret, secret_key_access.TOKEN).primitive(streaming_aead.StreamingAead)

    def entries(self) -> list[dict]:
        with database() as con:
            rows = con.execute("SELECT * FROM files WHERE job=?", (self.id,)).fetchall()
        return [self.metadata(row) for row in rows]

    def metadata(self, row) -> dict:
        item = json.loads(unseal(self.index_key, row["name"], self.context(row["tag"])))
        if self.tag(item["name"]) != row["tag"] or not re.fullmatch(r"[a-f0-9]{32}", row["blob"]):
            raise ValueError("Invalid encrypted file metadata")
        return {**item, "blob": row["blob"]}

    def entry(self, name: str) -> dict:
        with database() as con:
            row = con.execute("SELECT * FROM files WHERE job=? AND tag=?", (self.id, self.tag(name))).fetchone()
        if row is None:
            raise FileNotFoundError(name)
        return self.metadata(row)

    def reader(self, name: str):
        return self.open_reader(name)[1]

    def open_reader(self, name: str):
        # Hold the SQLite read lock until the inode is open; replacements may then unlink it safely.
        with database() as con:
            con.execute('BEGIN')
            row = con.execute('SELECT * FROM files WHERE job=? AND tag=?', (self.id, self.tag(name))).fetchone()
            if row is None:
                raise FileNotFoundError(name)
            item = self.metadata(row)
            fd = os.open(self.directory / item['blob'], os.O_RDONLY | os.O_NOFOLLOW)
        source = os.fdopen(fd, 'rb')
        try:
            return item, self.primitive().new_decrypting_stream(source, self.context(name).encode())
        except BaseException:
            source.close()
            raise

    def write(self, name: str, source) -> None:
        name = valid_name(name)
        blob = secrets.token_hex(16)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.directory / blob
        size = 0
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with self.primitive().new_encrypting_stream(os.fdopen(fd, "wb"), self.context(name).encode()) as stream:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    stream.write(chunk)
            with target.open("rb") as persisted:
                os.fsync(persisted.fileno())
            tag = self.tag(name)
            metadata = seal(self.index_key, json.dumps({"name": name, "size": size}).encode(), self.context(tag))
            with database() as con:
                old = con.execute("SELECT blob FROM files WHERE job=? AND tag=?", (self.id, tag)).fetchone()
                if old and not re.fullmatch(r'[a-f0-9]{32}', old[0]):
                    raise ValueError('Invalid object identifier')
                con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)", (self.id, tag, metadata, blob, size))
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        if old:
            (self.directory / old[0]).unlink(missing_ok=True)

    def remove(self, name: str) -> None:
        entry = self.entry(name)
        with database() as con:
            con.execute("DELETE FROM files WHERE job=? AND tag=?", (self.id, self.tag(name)))
        (self.directory / entry["blob"]).unlink(missing_ok=True)

    def import_tree(self, root: Path, *, remove_missing: bool = False, stamps: dict | None = None) -> None:
        present = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlinks are not permitted in jobs")
            if not path.is_file() or path.suffix == ".tmp":
                continue
            name = path.relative_to(root).as_posix()
            present.add(name)
            stamp = (path.stat().st_mtime_ns, path.stat().st_size)
            if stamps is None or stamps.get(name) != stamp:
                with path.open("rb") as source:
                    self.write(name, source)
                if stamps is not None:
                    stamps[name] = stamp
        if remove_missing:
            for item in self.entries():
                if item["name"] not in present and item["name"] != "_checkpoint.sqlite":
                    self.remove(item["name"])

    def export_tree(self, root: Path) -> None:
        for item in self.entries():
            if item["name"] == "_checkpoint.sqlite":
                continue
            target = root / valid_name(item["name"])
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.reader(item["name"]) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


def create(job_id: str | None = None) -> Job:
    person = identity()
    jid = valid_id(job_id or secrets.token_hex(16))
    handle = tink.new_keyset_handle(streaming_aead.streaming_aead_key_templates.AES256_GCM_HKDF_1MB)
    secret = tink.proto_keyset_format.serialize(handle, secret_key_access.TOKEN)
    wrapped = seal(person.key, secret, f"job:{person.id}:{jid}")
    with database() as con:
        con.execute("INSERT INTO jobs(id,owner,key) VALUES(?,?,?)", (jid, person.id, wrapped))
    return Job(jid, person.id, secret)


def get_job(job_id: str) -> Job:
    valid_id(job_id)
    capability = active_job.get()
    if capability is not None:
        if capability.id != job_id:
            raise PermissionError("Worker is restricted to its own job")
        return capability
    person = identity()
    with database() as con:
        row = con.execute("SELECT key FROM jobs WHERE id=? AND owner=?", (job_id, person.id)).fetchone()
    if row is None:
        raise FileNotFoundError("Job not found")
    return Job(job_id, person.id, unseal(person.key, row[0], f"job:{person.id}:{job_id}"))


class VaultPath:
    """The small file interface used by review views; plaintext never has an OS path."""

    def __init__(self, job: Job, name: str = ""):
        self.job, self.relative = job, valid_name(name)

    def __truediv__(self, name):
        return VaultPath(self.job, f"{self.relative}/{name}" if self.relative else str(name))

    @property
    def name(self):
        return PurePosixPath(self.relative).name if self.relative else self.job.id

    @property
    def parent(self):
        name = str(PurePosixPath(self.relative).parent)
        return VaultPath(self.job, "" if name == "." else name)

    def with_suffix(self, suffix):
        return VaultPath(self.job, str(PurePosixPath(self.relative).with_suffix(suffix)))

    def read_bytes(self):
        with self.job.reader(self.relative) as stream:
            return stream.read()

    def read_text(self):
        return self.read_bytes().decode()

    def write_bytes(self, content):
        self.job.write(self.relative, io.BytesIO(content))
        return len(content)

    def write_text(self, content):
        return self.write_bytes(content.encode())

    def is_file(self):
        try:
            self.job.entry(self.relative)
            return True
        except FileNotFoundError:
            return False

    def is_dir(self):
        if not self.relative:
            with database() as con:
                return con.execute("SELECT 1 FROM jobs WHERE id=?", (self.job.id,)).fetchone() is not None
        return any(x["name"].startswith(self.relative + "/") for x in self.job.entries())

    def exists(self):
        return self.is_file() or self.is_dir()

    def is_symlink(self):
        return False

    def mkdir(self, **kwargs):
        pass  # Object storage has no empty directories.

    def glob(self, pattern):
        prefix = self.relative + "/" if self.relative else ""
        return [VaultPath(self.job, item["name"]) for item in self.job.entries()
                if item["name"].startswith(prefix) and fnmatch.fnmatchcase(item["name"][len(prefix):], pattern)]

    def replace(self, target):
        if target.job.id != self.job.id:
            raise PermissionError("Cross-job file move")
        target.write_bytes(self.read_bytes())
        self.unlink()

    def unlink(self):
        self.job.remove(self.relative)


class VaultRoot:
    def __truediv__(self, job_id):
        return VaultPath(get_job(str(job_id)))

    def iterdir(self):
        person = identity()
        with database() as con:
            rows = con.execute("SELECT id FROM jobs WHERE owner=? ORDER BY rowid DESC", (person.id,)).fetchall()
        return [self / row[0] for row in rows]
