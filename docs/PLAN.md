# Kiehnema – Website → Motion Graphic Generator

Python + HTMX app: URL, duration and description in the UI → Claude Opus 5.5 on AWS Bedrock (LangChain /
LangGraph) writes the composition → two videos (4:5 feed 1080×1350, 9:16 story 1080×1920) with music.

## Decisions

Follow-up deployment plan: [GitLab releases and updates through the UI](../plans/update-system.md)
(ECC Blueprint; Linux implementation and local container verification completed;
production GitLab/host acceptance remains). See the [installation and release guide](updates.md).

| Topic | Decision |
|---|---|
| Creative approach | **B** – the model writes the full HTML/CSS/GSAP composition against a fixed harness contract (no scene library) |
| Review | **yes** – human review (edit texts / feedback / approve) before the final render |
| Deployment | Managed **Linux/Docker** with UI updates; local development remains available. One coordinator per data directory. |
| UI language | **English**; video language follows the website unless the description says otherwise |
| Orchestration | **LangChain + LangGraph** (langchain-aws `ChatBedrockConverse`), SQLite checkpoints |
| Live-run budget (Phase 3) | 2–3 full runs |

## Phases
| # | Phase | Status | Result document |
|---|---|---|---|
| 0 | Spike & skeleton: Bedrock × LangChain checks, uv project, FastAPI/HTMX skeleton, Python renderer with parity to render.mjs | ✅ done | `docs/phase0-spike.md` |
| 1 | Harness, golden example, music: page shell + contract, they video ported to `examples/they/`, numpy music synth, runtime checks (contract, determinism, readability, layout) | ✅ done | `docs/harness-contract.md` |
| 2 | Crawler & brand: SSRF-guarded crawl, brand kit (palette/theme, fonts, logo incl. contrast, photos, client logos), brand board | ✅ done | `docs/phase2-crawl.md` |
| 3 | LangGraph pipeline: distill (verified facts) → concept (validated storyboard) → compose → check/fix → keyframes → QA → review interrupt → render; 2 live runs | ✅ done | `docs/phase3-pipeline.md` |
| 4 | HTMX UI (English): new-job form, live progress, review page (brand board, keyframes both formats, sandboxed preview, editable texts with sources, feedback, approve & render), results page, job list/cleanup | 🟡 implemented; browser verification pending (session sandbox) | `docs/phase4-ui.md` |
| 5 | Hardening: more tests (UI, security of preview iframe), golden-site eval on 2–3 sites, logging, `.env` config, README; optional later: retry/cancel jobs, own music upload | ⏳ open | – |

## Phase 4 – scope
- **New job:** URL, duration (15–60 s), description, option "show client logos" (default off).
- **Progress:** HTMX polling of `status.json` – step list, live character count while code is written,
  frame progress while rendering, running cost estimate.
- **Review:** brand board; keyframes 4:5 and 9:16 side by side; playable preview in a sandboxed iframe
  (`sandbox="allow-scripts"`, no same-origin, CSP) with scrubber; scene/text list with source links and inline
  editing (`data-text-id`); feedback box → revise → back to review; **Approve & render**.
- **Results:** both MP4s (player + download), keyframes, sources, cost; job list with delete.
- Jobs run in the background (one render at a time); the UI survives page reloads (state on disk + SQLite).

## Phase 5 – scope
- Broaden web/preview security tests after the Phase 4 browser smoke check (route/action/header tests already implemented).
- Small evaluation on 2–3 further websites with fixed criteria (readability, brand fidelity, factual accuracy).
- Structured logging, error states in the UI, `.env.example`, final README.

## Known risks / open points
- Code generation takes ~11 min per full composition (thinking-heavy); fix rounds ~1 min.
- Costs ≈ $1.5–2 per 30 s video (estimate at Anthropic list price; Bedrock billing may differ).
- DNS rebinding remains a residual crawl risk (acceptable locally, see `docs/phase2-crawl.md`).
- Rights: logos, photos, quotes and fonts come from the crawled site – intended for own/client sites.
