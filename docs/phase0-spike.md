# Phase 0 – Spike results (2026-10-06)

Environment: `langchain-aws 1.8.0`, `langchain-core 1.6.6`, `langgraph 1.2.13`, boto3 1.43, Python 3.13,
AWS profile `bedrock`, region `eu-central-1`. Script: `spikes/bedrock_langchain_spike.py`.

## Model access
| Inference profile | Status |
|---|---|
| `eu.anthropic.claude-opus-5-5` | ACTIVE → **used** (EU data residency) |
| `global.anthropic.claude-opus-5-5` | ACTIVE |

## LangChain × Claude Opus 5.5 (ChatBedrockConverse)
| Check | Result | Decision |
|---|---|---|
| Basic invoke, usage, stop reason | PASS | `usage_metadata` + `response_metadata["stopReason"]` |
| Streaming | PASS | all generation calls stream |
| `reasoning_effort` → adaptive thinking + `output_config.effort` | PASS | set effort per step |
| Structured output `method="json_schema"` | **FAIL** – Bedrock: `output_config.format: Extra inputs are not permitted` | not used |
| Structured output `method="function_calling"` | PASS – langchain-aws auto-selects `tool_choice: auto` for Opus 5.5 (forced tool use would 400) | **used** for JSON steps, validated by Pydantic |
| Image input (base64 PNG) | PASS | vision QA feasible |
| Prompt caching (`cache_control={"type":"ephemeral"}`) | PASS – 2nd call: 14,910 of 14,927 input tokens read from cache | cache system prompt + golden example + profile |
| Long streamed output | PASS (no timeout, `read_timeout=1800`) | see throughput below |

### Throughput / budget finding (important for "compose")
| effort | output tokens | wall time | first text token | stop |
|---|---|---|---|---|
| low | 6,085 | 60 s | 1.2 s | end_turn |
| medium | 16,000 | 188 s | 185.9 s | **max_tokens** (all budget spent thinking) |

≈ 85–100 output tokens/s. Consequences:
- code generation needs `max_tokens` ≥ 64k and will take ~5–12 min per full composition;
- fix/revise rounds use small search/replace edits instead of full regeneration;
- a `max_tokens` stop is raised as `TruncatedOutput` (never silently use truncated code).

## Renderer parity (Python port of render.mjs)
- Stills at 9 timestamps: **pixel-identical** to the Node renderer (same Chromium headless shell 1243),
  with the new CSP and network sandbox enabled.
- Full 30 s video: PSNR 57.8 dB vs. the Node output (x264 version difference only), 900 frames in ~2:15.
- Bug found and fixed: bundled ffmpeg 7.1 dropped primaries/transfer tags (`bt709/unknown/unknown`);
  colour properties are now also set via `setparams` in the filter chain → `bt709/bt709/iec61966-2-1`.

## Sandbox
- Pages are served with a strict CSP (`connect-src 'none'`, `self` only) from a 127.0.0.1 server confined to
  the job directory; Playwright additionally aborts every request to another origin.
- Test fixture proves `fetch()` and external `<img>` are blocked.
