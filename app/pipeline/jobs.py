"""Authenticated jobs; plaintext execution is confined to a short-lived RAM worker."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import signal
import sys
import tempfile

from app.config import settings, ROOT
from app.pipeline.status import write_status
from app.security import vault
from app.security.accounts import database
from app.security.checkpoints import checkpoint
from app.security.workspace import ram_root


def jobs_dir():
    return vault.VaultRoot()


def create_job(url: str, duration: float, description: str = "", *, allow_client_logos: bool = False,
               max_pages: int = 20, max_depth: int = 2, job_id: str | None = None) -> str:
    job = vault.create(job_id)
    path = vault.VaultPath(job)
    (path / "request.json").write_text(json.dumps({"url": url, "duration": duration, "description": description,
        "allow_client_logos": allow_client_logos, "max_pages": max_pages, "max_depth": max_depth}))
    write_status(path, "queued", "Waiting to start")
    return job.id


async def execute(job_id: str, action: str, decision: dict | None = None) -> dict:
    job = vault.get_job(job_id)
    root = ram_root()
    with database() as con:
        if not con.execute("UPDATE jobs SET active=2 WHERE id=? AND active<2", (job_id,)).rowcount:
            raise ValueError("This job already has work in progress.")
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="job-", dir=root) as folder:
            env = {**os.environ, "PYTHONPATH": str(ROOT), "TMPDIR": folder,
                   "MG_DATA_DIR": str(settings.data_dir.resolve())}
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.pipeline.worker",
                cwd=folder, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            payload = json.dumps({"id": job.id, "owner": job.owner, "secret": base64.b64encode(job.secret).decode(),
                                  "workspace": folder, "action": action, "decision": decision}).encode()
            try:
                await asyncio.wait_for(process.communicate(payload), settings.job_timeout_s)
            finally:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
                # Chromium/ffmpeg must not outlive the key-holding worker.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.returncode:
                raise RuntimeError("The secure worker failed. Check the private job activity.")
        state = await job_state(job_id)
        return {**state["values"], **({"__interrupt__": state["interrupts"]} if state["interrupts"] else {})}
    finally:
        with database() as con:
            con.execute("UPDATE jobs SET active=0 WHERE id=?", (job_id,))


async def run_job(job_id: str) -> dict:
    return await execute(job_id, "run")


async def resume_job(job_id: str, decision: dict) -> dict:
    return await execute(job_id, "resume", decision)


async def reopen_job(job_id: str) -> dict:
    return await execute(job_id, "reopen")


async def start_job(url: str, duration: float, description: str = "", **kwargs):
    job_id = create_job(url, duration, description, **kwargs)
    return job_id, await run_job(job_id)


async def job_state(job_id: str) -> dict:
    from app.pipeline.graph import build_graph
    job = vault.get_job(job_id)
    async with checkpoint(job) as saver:
        graph = build_graph(saver)
        snapshot = await graph.aget_state({"configurable": {"thread_id": job_id}})
        return {"values": snapshot.values, "next": list(snapshot.next),
                "interrupts": [i.value for t in snapshot.tasks for i in t.interrupts]}


async def delete_job(job_id: str) -> None:
    job = vault.get_job(job_id)
    with database() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT active FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row[0]:
            raise ValueError("Wait for this job to finish.")
        con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    if job.directory.exists():
        shutil.rmtree(job.directory)
