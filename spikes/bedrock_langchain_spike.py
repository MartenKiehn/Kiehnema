"""Phase-0 spike: verify LangChain (langchain-aws ChatBedrockConverse) against Claude Opus 5.5 on Bedrock.

Run: uv run python spikes/bedrock_langchain_spike.py
Each check prints PASS/FAIL plus the evidence (usage, stop reason, parsed output).
"""

from __future__ import annotations

import base64
import functools
import os
import sys
import time
import traceback
from pathlib import Path

from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "eu.anthropic.claude-opus-5-5")
REGION = os.environ.get("AWS_REGION", "eu-central-1")
BOTO_CONFIG = Config(read_timeout=900, connect_timeout=30, retries={"max_attempts": 3, "mode": "adaptive"})

results: list[tuple[str, bool, str]] = []


def llm(**kwargs) -> ChatBedrockConverse:
    return ChatBedrockConverse(model=MODEL_ID, region_name=REGION, config=BOTO_CONFIG, **kwargs)


def check(name):
    def wrap(fn):
        @functools.wraps(fn)
        def run():
            t0 = time.time()
            try:
                ok, info = fn()
            except Exception as exc:  # spike: report every failure, keep going
                ok, info = False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
            results.append((name, ok, info))
            print(f"[{'PASS' if ok else 'FAIL'}] {name} ({time.time() - t0:.1f}s)\n    {info}\n", flush=True)
        return run
    return wrap


@check("basic invoke + usage + stop reason")
def basic():
    msg = llm(max_tokens=2000).invoke([HumanMessage("Reply with exactly: OK")])
    return "OK" in msg.text, f"text={msg.text!r} usage={msg.usage_metadata} stop={msg.response_metadata.get('stopReason')}"


@check("streaming")
def streaming():
    chunks = 0
    text = ""
    for chunk in llm(max_tokens=2000).stream([HumanMessage("Count from 1 to 20, comma separated.")]):
        chunks += 1
        text += chunk.text
    return chunks > 1 and "20" in text, f"chunks={chunks} tail={text[-40:]!r}"


@check("reasoning_effort=high (adaptive thinking + output_config.effort)")
def effort():
    model = llm(max_tokens=4000, reasoning_effort="high")
    msg = model.invoke([HumanMessage("What is 17*23? Answer with the number only.")])
    kinds = [b.get("type") for b in msg.content] if isinstance(msg.content, list) else ["str"]
    return "391" in msg.text, f"text={msg.text!r} blocks={kinds} usage={msg.usage_metadata}"


class Claim(BaseModel):
    text: str = Field(description="Short marketing claim")
    source_quote: str = Field(description="Verbatim quote supporting the claim")


class Profile(BaseModel):
    company: str
    claims: list[Claim]


SAMPLE = (
    "they consulting – KI- und Cloud-Beratung. they make AI work. Von der Idee zur produktiven KI-Lösung. "
    "Wir entwickeln und betreiben alle Systeme ausschließlich auf EU-Servern, konform mit DSGVO und TISAX."
)


@check("structured output method=json_schema (Bedrock outputConfig)")
def structured_json_schema():
    out = llm(max_tokens=4000).with_structured_output(Profile, method="json_schema").invoke(
        [HumanMessage(f"Extract a company profile with 2 claims from this text:\n\n{SAMPLE}")]
    )
    return isinstance(out, Profile) and len(out.claims) >= 1, repr(out)[:300]


@check("structured output method=function_calling (tool_choice auto on Opus 5.5)")
def structured_function_calling():
    out = llm(max_tokens=4000).with_structured_output(Profile, method="function_calling").invoke(
        [HumanMessage(f"Extract a company profile with 2 claims from this text:\n\n{SAMPLE}")]
    )
    return isinstance(out, Profile), repr(out)[:300]


@check("image input (base64 PNG)")
def image_input():
    data = base64.b64encode((ROOT / "assets/img/title.png").read_bytes()).decode()
    msg = llm(max_tokens=2000).invoke([
        HumanMessage(content=[
            {"type": "image", "source_type": "base64", "mime_type": "image/png", "data": data},
            {"type": "text", "text": "Which company name is written in this logo? Answer in 3 words max."},
        ])
    ])
    return "they" in msg.text.lower(), f"text={msg.text!r} usage={msg.usage_metadata}"


@check("prompt caching (cache_control on a long system prompt, 2 calls)")
def caching():
    golden = (ROOT / "main.js").read_text() + (ROOT / "style.css").read_text()
    system = SystemMessage(f"You review GSAP motion-graphics code. Reference composition:\n\n{golden}")
    model = llm(max_tokens=1000)
    usages = []
    for q in ("How many scenes does the reference have? Number only.", "Which easing is the default? One word."):
        msg = model.invoke([system, HumanMessage(q)], cache_control={"type": "ephemeral"})
        usages.append(msg.usage_metadata)
    read = (usages[1].get("input_token_details") or {}).get("cache_read", 0)
    return read > 0, f"call1={usages[0]} call2={usages[1]}"


@check("long streamed output (~6k tokens, no timeout)")
def long_output():
    model = llm(max_tokens=16000)
    text = ""
    for chunk in model.stream([HumanMessage(
        "Write a complete standalone HTML file (inline CSS+JS, no external URLs) with 40 distinct, "
        "richly commented CSS keyframe-free animated cards driven by JS. Output only the HTML."
    )]):
        text += chunk.text
    return len(text) > 15000, f"chars={len(text)} ends_with_html={text.rstrip().endswith('</html>') or '</html>' in text[-200:]}"


if __name__ == "__main__":
    print(f"model={MODEL_ID} region={REGION}\n")
    selected = sys.argv[1:]
    for fn in (basic, streaming, effort, structured_json_schema, structured_function_calling, image_input, caching, long_output):
        if not selected or fn.__name__ in selected:
            fn()
    print("SUMMARY")
    for name, ok, _ in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
