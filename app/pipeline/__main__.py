"""CLI for generation jobs.

  uv run python -m app.pipeline start <url> [--duration 30] [--description "..."] [--clients]
  uv run python -m app.pipeline resume <job_id> (--approve | --feedback "..." | --edit text-id="new copy" ...)
  uv run python -m app.pipeline status <job_id>
"""

import argparse
import asyncio
import json
import logging

from app.pipeline.jobs import job_state, resume_job, start_job


def _summary(result: dict) -> str:
    keep = {k: result.get(k) for k in ("job_id", "status", "error", "check", "qa", "outputs", "cost_usd") if k in result}
    if "__interrupt__" in result:
        keep["waiting_for"] = "review"
    return json.dumps(keep, ensure_ascii=False, indent=1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "botocore", "langchain_aws"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("url")
    s.add_argument("--duration", type=float, default=30)
    s.add_argument("--description", default="")
    s.add_argument("--clients", action="store_true", help="allow client logos")
    r = sub.add_parser("resume")
    r.add_argument("job_id")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--feedback")
    g.add_argument("--edit", nargs="+", metavar="ID=TEXT")
    st = sub.add_parser("status")
    st.add_argument("job_id")
    a = p.parse_args()
    from app.security.__main__ import unlock
    from app.security.accounts import current_identity
    from app.security.accounts import service_lock
    from app.updates.state import assert_start_allowed

    with service_lock():
        assert_start_allowed(cli=True)
        token = current_identity.set(unlock())
        try:
            if a.cmd == "start":
                job_id, result = asyncio.run(start_job(a.url, a.duration, a.description, allow_client_logos=a.clients))
                print(_summary({"job_id": job_id, **result}))
            elif a.cmd == "resume":
                if a.approve:
                    decision = {"action": "approve"}
                elif a.feedback:
                    decision = {"action": "feedback", "text": a.feedback}
                else:
                    decision = {"action": "edit", "texts": dict(e.split("=", 1) for e in a.edit)}
                print(_summary({"job_id": a.job_id, **asyncio.run(resume_job(a.job_id, decision))}))
            else:
                st = asyncio.run(job_state(a.job_id))
                print(json.dumps({"next": st["next"], **{k: st["values"].get(k) for k in ("status", "error", "check", "qa", "outputs", "cost_usd")}}, indent=1))
        finally:
            current_identity.reset(token)


if __name__ == "__main__":
    main()
