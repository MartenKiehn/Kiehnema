"""Cheap live check of the first two model steps (distill + concept) on an existing crawl/brand."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from app.pipeline.graph import n_concept, n_distill
from app.pipeline.llm import BedrockLLM, estimate_cost

ROOT = Path(__file__).resolve().parents[1]


async def main(description: str) -> None:
    job = ROOT / "data" / "partial" / "they"
    if job.exists():
        shutil.rmtree(job)
    (job).mkdir(parents=True)
    shutil.copytree(ROOT / "data/crawls/they", job / "crawl")
    shutil.copytree(ROOT / "data/brands/they", job / "brand")
    llm = BedrockLLM(log_path=job / "llm.jsonl")
    config: RunnableConfig = {"configurable": {"llm": llm}}
    state = {"job_dir": str(job), "url": "https://www.they-consulting.de/", "duration": 30.0,
             "description": description, "allow_client_logos": True}
    state |= await n_distill(state, config)
    print("distill:", state.get("error"), "notes:", state.get("profile_notes"))
    if not state.get("error"):
        state |= await n_concept(state, config)
        print("concept:", state.get("error"), state.get("concept_errors"))
    print("usage", llm.usage.by_step, "cost ~$", estimate_cost(llm.usage))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
