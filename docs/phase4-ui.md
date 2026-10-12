# Phase 4 – English web interface

The local FastAPI/HTMX app now connects to the Phase 3 pipeline. Start it with:

```bash
uv run python -m app
# http://127.0.0.1:8000
```

## Workflow

- **New video:** public website URL, 15–60 seconds, optional creative direction and client logos (off by default).
- **Website scan settings:** maximum pages (1–50, default 20) and link depth (0–5, default 2), saved per new video. Depth 0 reads only the starting page. The existing 240-second scan budget still applies; older jobs use the defaults.
- **Progress:** persisted job URL, HTMX polling every two seconds, pipeline steps, streamed character counts, render frame counts, activity log and accumulated model cost. Targeted swaps update the status block, log entries and header cost while keeping the activity disclosure and its scroll container mounted. Cost updates when model calls finish; it is not live billing.
- Each active phase explains what is happening and what comes next; crawl activity names the current page. Discovery cards replace the moving bar: early website excerpts, source-linked facts, real colour swatches with everyday names, and draft scene text appear as results are saved. No extra model calls. Unchanged cards (including expanded reading sections) stay mounted during polling. Reduced-motion preferences disable the status pulse. Revision loops return the step marker to Animation and clear later completion marks.
- **Review:** brand board, two sandboxed playable previews with scrubbers, paired 4:5/9:16 scene keyframes, editable copy and linked source quotes. Text edits and feedback can be submitted together. Revisions return to review. Approval rejects unapplied changes, stale submissions and compositions that failed automatic checks.
- **Results:** both MP4 players, downloads with range support, scene keyframes, copy, sources and cost.
- **Workspace:** existing Phase 3 jobs are listed automatically. Deleting an idle job removes its artefacts and SQLite checkpoint after a confirmation page.

One background worker runs jobs sequentially, including renders. Browser navigation/reloads do not stop it. Keep the app process running. If the process stops, unfinished jobs are marked interrupted on the next startup; saved review and completed jobs remain usable. Automatic retry/cancel remains Phase 5 work. No paid work restarts automatically.

The original visual QA notes remain available after a revision, labelled as the initial review. Revisions run the pipeline's automatic layout/readability checks, as in Phase 3.

## Implementation

- `app/web/main.py`: routes, local worker, review validation, confined media/preview delivery.
- `app/web/templates/`, `app/static/app.css`, `app/static/app.js`: responsive English UI, HTMX polling and prevention of duplicate form submissions.
- `app/pipeline/jobs.py`: split job creation from execution; existing CLI entry points continue to work; review-only resume and checkpoint deletion.
- `app/pipeline/status.py`: atomic status replacement prevents polling from reading half-written JSON.
- `app/pipeline/graph.py`: persist early failure states and combine copy edits with feedback.
- `app/harness/`: accessible play/pause and scrubber labels for newly built previews.

No dependencies added. No additional Bedrock run was used.

## Preview boundary

The generated site is served separately under `/preview/<job>/…`. Both the iframe attribute and the response CSP apply `sandbox allow-scripts` without `allow-same-origin`, including when a preview is opened directly. CSP only permits scripts/styles/images/fonts under that job's preview path; connections, child frames, forms, objects and base URLs are disabled. Preview assets send CORS headers so fonts work from the opaque iframe origin.

The main UI never serves generated HTML through its media route. Only the board PNG, keyframe PNGs and named output MP4s are exposed there. File paths are confined; mutations require the local app's Origin, rejecting the preview's `null` origin. Hostnames are restricted to loopback names. This remains a local, single-process application, not a hosted multi-user service.

References: [FastAPI templates](https://fastapi.tiangolo.com/advanced/templates/), [HTMX polling](https://htmx.org/docs/#polling), [CSP sandbox](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/sandbox).

### Native form POST fix (2026-10-07)

The original global `Referrer-Policy: no-referrer` made native form submissions send `Origin: null`,
which the Origin guard rejected with 403 ([Fetch standard](https://fetch.spec.whatwg.org/#origin-header)).
App pages now use `same-origin`; previews retain `no-referrer`, and null/foreign Origins remain blocked.
All **9 non-browser web tests pass**, including the new regression check at port 8000.
Restart the app and reload the page to apply the new response policy.

## Validation and remaining verification

```bash
uv run pytest tests/test_web.py tests/test_pipeline.py -m 'not browser' -k 'not full_pipeline' -q
uv run pytest tests/test_web.py -m browser -q
uv run pytest -q
```

At initial implementation, the first command passed **13 tests**, covering form validation, background queue/progress, reloads, errors, copy + feedback, approval, stale/double submissions, downloads, path/Origin/Host boundaries, deletion, process restart and the real SQLite review interrupt.

The browser test is included and uses real Chromium against an in-memory ASGI transport, with a scripted worker (no paid model calls). It exercises the form → review → edit → approve → results flow, playback/scrubbing, font/image loading, iframe isolation, desktop/mobile screenshots and overflow. **It has not been run successfully in this session:** the execution sandbox blocks Chromium startup and local listening sockets. A request to run outside that sandbox was rejected by the session's permission policy; Firefox computer access was also unavailable. Visual layout, actual iframe CSP behaviour and the complete render regression suite therefore still need verification in an unrestricted local session before Phase 4 is signed off.

The final in-process regression run passed **56 tests**, with two live opt-in tests skipped and 12 socket/browser tests deselected. The earlier full suite attempt confirmed environment permission errors in the socket/browser tests. Existing Phase 3 results were read successfully through the new routes; they were not regenerated.
