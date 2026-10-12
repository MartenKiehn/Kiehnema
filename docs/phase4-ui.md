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
- **Review:** collapsed brand board, two sandboxed playable previews and a shared frame timeline. Select a moment, write a format-specific comment and explicitly confirm it. Confirmed comments persist per preview and appear as numbered markers and editable/deletable cards. Unconfirmed comments block review submission. Scene copy, visual direction, duration and order are editable; the planned duration updates immediately. **Revise preview** submits all edits, confirmed comments and feedback together. Comment timestamps and scene IDs refer to the original preview, even after reordering. The revised storyboard also updates the job duration and sound-cue timing. Approval rejects unapplied changes, stale submissions and compositions that failed automatic checks.
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
- `app/web/review.py`, `app/static/review.js`: validated comment persistence and timeline/scene editing.
- `app/web/screenshot.py`: reusable local screenshot CLI; waits for preview readiness and captures PNG plus a JSON error/overflow/image report. Example commands are in the README; outputs stay in ignored `out/`.
- `app/harness/`: accessible preview controls and a source-checked message bridge for seeking/playback; saved jobs use the current harness script without rebuilding their composition. The opaque iframe sandbox remains intact.

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

The current browser tests use real Chromium and a temporary local FastAPI server for native form
redirects, with scripted workers and no paid model calls. They cover the form → review → edit →
approve → results flow, playback/scrubbing, fonts/images, iframe isolation, comment confirmation,
persistence/editing/deletion, draft protection, scene order/duration, and desktop/mobile capture.

Validation on 2026-10-12: **104 passed, 2 live opt-in tests skipped** in the complete suite.
Desktop (1440 px) and mobile (390 px) screenshots were also inspected using a separate copy of the
existing Golem review job. Both preview formats render correctly; reports show no page errors,
horizontal overflow or broken page images. Screenshot capture scrolls each iframe into view first
to avoid Chromium leaving offscreen iframe content unpainted in full-page images.

The model's interpretation of arbitrary design comments was not tested with a paid generation.
The revision payload, persisted checkpoint, retimed storyboard and automated renderer were tested.

### Background replacement fix (2026-10-12)

A confirmed image-change comment reached the composer, which selected a different allowed photo.
The composition writer copied only the original storyboard assets, so the new photo returned 404.
The automatic repair then reverted to the old photo. Builds now include every permitted asset
advertised to the composer (client-logo preferences still apply). Reviewer requirements are retained
separately from automatic check errors and included in subsequent repair prompts, so repairing a
technical error does not silently discard the requested visual change.

Regression tests cover replacement images surviving rebuilds, client-logo exclusion, and review
requirements surviving automatic repair while resetting for the next review.
