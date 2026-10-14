"""One job, one key, one process. Secrets arrive over a pipe, never argv or environment."""

import asyncio
import base64
import json
import os
import resource
import sys
from pathlib import Path


async def main():
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    payload = json.loads(sys.stdin.buffer.read())
    root = Path(payload.pop("workspace"))
    os.environ["TMPDIR"] = str(root)
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    from app.security.vault import Job, active_job
    from app.security.workspace import publisher, runtime_root
    from app.pipeline import runtime
    from app.pipeline.status import write_status
    from app.crawl.guard import UrlGuard

    job = Job(payload["id"], payload["owner"], base64.b64decode(payload.pop("secret")))
    active_job.set(job)
    runtime_root.set(root)
    directory = root / "jobs" / job.id
    directory.mkdir(parents=True)
    job.export_tree(directory)
    stamps = {}
    def publish():
        job.import_tree(directory, remove_missing=True, stamps=stamps)
    publisher.set(publish)
    try:
        if payload["action"] == "run":
            await runtime.run_job(job.id, guard=UrlGuard())
        elif payload["action"] == "resume":
            await runtime.resume_job(job.id, payload["decision"], guard=UrlGuard())
        elif payload["action"] == "reopen":
            await runtime.reopen_job(job.id)
        else:
            raise ValueError("Invalid worker action")
    except Exception as exc:
        write_status(directory, "failed", f"{type(exc).__name__}: {exc}")
        publish()
        raise
    publish()


if __name__ == "__main__":
    asyncio.run(main())
