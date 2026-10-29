# Repository Guidelines

## Project Structure & Module Organization

Kiehnema turns websites into branded motion graphics with human review. Python source lives in `app/`: `crawl/` fetches content, `brand/` extracts branding, `pipeline/` orchestrates generation, `harness/` builds compositions, and `render/` produces videos. `web/` serves the FastAPI UI; `static/` holds frontend assets. Supporting modules include `llm/`, `music/`, and `checks/`.

Tests and fixtures live in `tests/`. `examples/they/` contains a sample composition; `assets/` holds demo assets. Architecture and composition contracts are documented in `docs/`. Root JavaScript, HTML, and CSS files implement the original video demo. Generated jobs and renders belong in ignored `data/` and `out/` directories.

## Build, Test, and Development Commands

- `uv sync` — install Python dependencies (Python 3.13+).
- `uv run playwright install chromium` — install the browser used for crawling and rendering.
- `uv run python -m app` — serve the app at `http://127.0.0.1:8000`.
- `uv run pytest` — run the test suite; live Bedrock tests are skipped by default.
- `uv run pytest tests/test_web.py -m browser -q` — run Chromium UI checks without Bedrock calls.
- `uv run python -m app.harness examples/they data/sites/they` — build the example composition.
- `npm install`, then `npm run preview` — install demo dependencies and preview on port 5173.
- `npm run render` — generate demo music and both video formats in `out/`.

## Coding Style & Naming Conventions

Follow surrounding code: four-space Python indentation, `snake_case` functions and modules, `PascalCase` classes, and type hints where appropriate. JavaScript uses ES modules, two-space indentation, and camelCase names. No formatter or linter is configured. Reuse existing helpers and keep changes focused. Follow `docs/harness-contract.md` when changing compositions.

## Testing Guidelines

CI runs only in GitHub Actions (`.github/workflows/ci.yml`); GitLab pipelines are disabled. GitHub is primary for reviews and releases, GHCR hosts images, and GitLab is only a code backup. Use `docs/updates.md` for release credentials and tag publication.

Use pytest and pytest-asyncio; name files `test_*.py` and functions `test_*`. Reuse fixtures in `tests/conftest.py` and temporary directories for generated files. Add regression tests for behavior changes. No coverage threshold is configured. Enable `MG_LIVE_BEDROCK=1` only intentionally: it makes real, potentially billable model calls.

## Commit & Pull Request Guidelines

For requested branch pushes, use `python3 scripts/push.py` to push the current branch to both GitLab and GitHub. Check its exit status and report partial failures; never force-push to resolve divergence. The script does not push release tags.

Recent commits mostly use `feat:` and `chore:` prefixes with imperative summaries. Follow that pattern. PRs should explain the behavior change, include validation commands/results, link relevant issues, and provide screenshots for UI changes.

## Security & Configuration

Use `.env.example` for local `MG_` settings. Keep credentials, `.env`, databases, crawled content, and rendered videos out of Git. Preserve crawler URL guards and human approval before final rendering.


## UI Design Patterns

Never use “fingernail” borders: no one-sided colored accent stripes on cards, notices or
information boxes (for example, `border-left: 3px solid ...`). Use the shared `info_box`
Jinja macro in `app/web/templates/components/info_box.html` with the `.info-box` styles
in `app/static/app.css` for explanatory boxes. It uses the app’s warm paper palette, a uniform fine border, a small clapperboard motif,
monospace “PRODUCTION NOTES” label and an editorial serif heading. Do not replace it
with a generic circular information icon or a colored sidebar. Reuse this component for future
information boxes; keep domain-specific content and layouts inside its caller block.
