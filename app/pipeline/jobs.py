"""Start / resume generation jobs (LangGraph with an SQLite checkpointer, one thread per job)."""

from __future__ import annotations

import json
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.config import settings
from app.crawl.guard import UrlGuard
from app.llm.client import Usage
from app.pipeline.graph import build_graph
from app.pipeline.llm import LLM, BedrockLLM
from app.pipeline.status import write_status


def jobs_dir() -> Path:
    path = settings.data_dir / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@asynccontextmanager
async def _graph() -> AsyncIterator[Any]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "jobs.sqlite")) as saver:
        yield build_graph(saver)


def _config(job_id: str, llm: LLM, guard: UrlGuard | None) -> dict:
    return {"configurable": {"thread_id": job_id, "llm": llm, "guard": guard}, "recursion_limit": 60}


def _bedrock(job_dir: Path, usage: dict | None = None) -> BedrockLLM:
    llm = BedrockLLM(log_path=job_dir / "llm.jsonl")
    if usage:
        llm.usage = Usage(**usage)
    return llm


def create_job(url: str, duration: float, description: str = "", *, allow_client_logos: bool = False,
               max_pages: int = 20, max_depth: int = 2, job_id: str | None = None) -> str:
    """Persist a job before scheduling it, so it is immediately reloadable."""
    job_id = job_id or time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    job_dir = jobs_dir() / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "request.json").write_text(json.dumps(
        {"url": url, "duration": duration, "description": description, "allow_client_logos": allow_client_logos,
         "max_pages": max_pages, "max_depth": max_depth}, ensure_ascii=False))
    write_status(job_dir, "queued", "Waiting to start")
    return job_id


async def run_job(job_id: str, *, llm: LLM | None = None, guard: UrlGuard | None = None) -> dict:
    job_dir = jobs_dir() / job_id
    state = {**json.loads((job_dir / "request.json").read_text()),
             "job_id": job_id, "job_dir": str(job_dir), "error": None}
    async with _graph() as graph:
        return await graph.ainvoke(state, _config(job_id, llm or _bedrock(job_dir), guard))


async def start_job(url: str, duration: float, description: str = "", *, allow_client_logos: bool = False,
                    llm: LLM | None = None, guard: UrlGuard | None = None, job_id: str | None = None) -> tuple[str, dict]:
    job_id = create_job(url, duration, description, allow_client_logos=allow_client_logos, job_id=job_id)
    return job_id, await run_job(job_id, llm=llm, guard=guard)


async def resume_job(job_id: str, decision: dict, *, llm: LLM | None = None, guard: UrlGuard | None = None) -> dict:
    """Resume with approve, edit or feedback; revisions may include ordered scenes and timeline comments."""
    job_dir = jobs_dir() / job_id
    async with _graph() as graph:
        snapshot = await graph.aget_state({"configurable": {"thread_id": job_id}})
        if snapshot.next != ("review",) or not any(t.interrupts for t in snapshot.tasks):
            raise ValueError(f"job {job_id} is not waiting for review")
        llm = llm or _bedrock(job_dir, snapshot.values.get("usage"))
        return await graph.ainvoke(Command(resume=decision), _config(job_id, llm, guard))


async def job_state(job_id: str) -> dict:
    async with _graph() as graph:
        snapshot = await graph.aget_state({"configurable": {"thread_id": job_id}})
        return {"values": snapshot.values, "next": list(snapshot.next),
                "interrupts": [i.value for t in snapshot.tasks for i in t.interrupts]}


async def reopen_job(job_id: str) -> dict:
    """Return a completed job to its review interrupt, keeping its latest export."""
    job_dir = jobs_dir() / job_id
    config = {"configurable": {"thread_id": job_id}}
    async with _graph() as graph:
        snapshot = await graph.aget_state(config)
        if snapshot.next or snapshot.values.get("status") != "done":
            raise ValueError(f"job {job_id} is not a completed render")
        config = _config(job_id, _bedrock(job_dir, snapshot.values.get("usage")), None)
        # The QA successor is review; clear its routing instruction without rerunning QA.
        await graph.aupdate_state(config, {"status": "review", "review": {}, "fix_instruction": None}, as_node="qa")
        return await graph.ainvoke(None, config)


async def delete_job(job_id: str) -> None:
    """The caller must ensure the job is idle before removing its checkpoint and files."""
    async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "jobs.sqlite")) as saver:
        await saver.adelete_thread(job_id)
    shutil.rmtree(jobs_dir() / job_id)
