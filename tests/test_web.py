"""Web workflow and boundaries, without Bedrock calls."""

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.harness.build import build_site
from app.pipeline import jobs
from app.pipeline.status import write_status
from app.web.main import app

ORIGIN = "http://127.0.0.1"
JOB = "20261006-120000-test"
CONCEPT = {"title": "A test video", "scenes": [{"id": "s-hook", "purpose": "hook", "visual": "Large type", "start": 0,
           "duration": 15, "texts": [{"id": "hook-title", "role": "headline", "text": "Robots that work.", "fact_ids": ["f1"]}]}]}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def review_job(workspace, monkeypatch):
    jobs.create_job("https://example.com/", 30, job_id=JOB)
    path = jobs.jobs_dir() / JOB
    (path / "concept.json").write_text(json.dumps(CONCEPT))
    (path / "check.json").write_text(json.dumps({"ok": True, "issues": []}))
    (path / "profile.json").write_text(json.dumps({"facts": [{"id": "f1", "text": "Robots that work.", "quote": "Robots that work.", "source_url": "https://example.com/about"}]}))
    build_site(Path("examples/they"), path / "site")
    frames = []
    for fmt in ("feed", "story"):
        file = f"keyframes/r0/{fmt}-001.00s.png"
        target = path / file
        target.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new("RGB", (8, 10), "green").save(target)
        frames.append({"scene": "s-hook", "format": fmt, "file": file, "t": 1})
    state = {"values": {"check": {"ok": True}, "keyframes": frames}, "next": ["review"], "interrupts": [{}]}
    monkeypatch.setattr(jobs, "job_state", AsyncMock(return_value=state))
    write_status(path, "review", "Waiting for your review", cost_usd=1.25)
    return path


@pytest.fixture
def client(workspace):
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client


def review_form(client):
    html = client.get(f"/jobs/{JOB}").text
    return {"revision": re.search(r'name="revision" value="([a-f0-9]+)"', html)[1],
            "text:hook-title": "Robots that work.", "action": "approve"}


def test_index_validation_and_static_assets(client):
    html = client.get("/").text
    assert "Kiehnema · Website to video" in html and 'aria-label="Kiehnema home"' in html
    assert "Motion Studio" not in html and "kiehnema.svg" in html
    assert "New video" in html and 'name="url"' in html and 'name="duration"' in html and 'name="description"' in html
    assert 'name="allow_client_logos"' in html and "checked" not in html
    assert 'name="max_pages"' in html and 'name="max_depth"' in html
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    assert client.get("/static/kiehnema.svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get("/favicon.ico").status_code == 200
    for data in ({"url": "file:///etc/passwd"}, {"url": "https://example.com", "duration": 14},
                 {"url": "https://example.com", "duration": 61}, {"url": "http://127.0.0.1"},
                 {"url": "https://example.com", "description": "x" * 4001}):
        response = client.post("/jobs", data=data)
        assert response.status_code == 422 and 'role="alert"' in response.text
    assert not list(jobs.jobs_dir().iterdir())


@pytest.mark.parametrize("field,value", [
    ("max_pages", "0"), ("max_pages", "51"), ("max_pages", "1.5"),
    ("max_depth", "-1"), ("max_depth", "6"), ("max_depth", "1.5"), ("max_depth", "abc"),
])
def test_scan_settings_validation_preserves_form(client, field, value):
    response = client.post("/jobs", data={"url": "https://example.com/", "max_pages": "7", "max_depth": "3",
                                         field: value})
    assert response.status_code == 422
    assert f'value="{value}"' in response.text
    assert 'class="crawl-settings" open' in response.text
    assert not list(jobs.jobs_dir().iterdir())


def test_form_origin_policy_preserves_local_posts_and_rejects_other_origins(workspace, monkeypatch):
    origin = "http://127.0.0.1:8000"
    monkeypatch.setattr("app.web.main.UrlGuard.check", lambda self, url: url)
    monkeypatch.setattr("app.web.main.enqueue", lambda job_id: None)
    with TestClient(app, base_url=origin) as client:
        # A native form under no-referrer would send Origin: null (Fetch standard §3.2).
        assert client.get("/").headers["referrer-policy"] == "same-origin"
        response = client.post("/jobs", data={"url": "https://example.com"},
                               headers={"Origin": origin}, follow_redirects=False)
        assert response.status_code == 303
        assert client.get(response.headers["location"]).headers["referrer-policy"] == "same-origin"
        for headers in ({}, {"Origin": "null"}, {"Origin": "https://evil.example"},
                        {"Origin": "http://127.0.0.1:8001"}):
            assert client.post("/jobs", data={"url": "https://example.com"}, headers=headers).status_code == 403
        assert len(list(jobs.jobs_dir().iterdir())) == 1


@pytest.mark.parametrize("status,heading,next_text,step", [
    ("queued", "Waiting for the worker", "begin automatically", None),
    ("crawl", "Reading your website", "Build your brand board", 1),
    ("brand", "Finding your visual identity", "Find the facts", 2),
    ("distill", "Finding your story", "scene-by-scene storyboard", 3),
    ("concept", "Planning each scene", "Bring the storyboard to life", 4),
    ("compose", "Bringing your story to life", "Check the animation", 5),
    ("check", "Checking the details", "or revise the animation", 6),
    ("fix", "Refining the animation", "Run the automatic checks again", 5),
    ("keyframes", "Preparing your preview", "Review the frames", 7),
    ("qa", "Taking a closer look", "Your turn to review", 8),
    ("render", "Making the final cut", "Download your feed and story", 10),
    ("working", "Finishing this step", "open automatically", None),
])
def test_progress_explains_current_and_next_step(client, status, heading, next_text, step):
    job_id = jobs.create_job("https://example.com/", 30)
    path = jobs.jobs_dir() / job_id
    for previous in ("crawl", "brand", "distill", "concept", "compose", "check", "keyframes", "qa"):
        write_status(path, previous, "Earlier pass")
    write_status(path, status, "Latest activity")
    html = client.get(f"/jobs/{job_id}/progress").text
    assert heading in html and next_text in html and "Latest activity" in html
    assert html.count('aria-current="step"') == (1 if step else 0)
    if step:
        assert f"STEP {step} / 11" in html
    if status == "fix":
        assert '<li class="current" aria-current="step"><span aria-hidden="true">5</span>Animation' in html
        assert '<li class="" ><span aria-hidden="true">6</span>Checks' in html


async def test_background_create_reload_queue_and_failure(workspace, monkeypatch):
    release = asyncio.Event()
    entered = asyncio.Event()
    calls = []
    monkeypatch.setattr("app.web.main.UrlGuard.check", lambda self, url: url)

    async def fake_run(job_id):
        calls.append(job_id)
        write_status(jobs.jobs_dir() / job_id, "compose", "Writing the composition code – 2,002 characters so far")
        entered.set()
        await release.wait()
        raise RuntimeError("Test failure")

    monkeypatch.setattr(jobs, "run_job", fake_run)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
            first = await client.post("/jobs", data={"url": "https://example.com", "duration": 30,
                                                    "allow_client_logos": "true", "max_pages": 7, "max_depth": 0})
            second = await client.post("/jobs", data={"url": "https://example.org", "duration": 15})
            assert first.status_code == second.status_code == 303
            await asyncio.wait_for(entered.wait(), 2)
            location = first.headers["location"]
            detail = await client.get(location)
            assert "2,002 characters" in detail.text and 'hx-trigger="every 2s"' in detail.text
            assert 'hx-target="#progress-status"' in detail.text and 'hx-select="#progress-status"' in detail.text
            assert 'hx-select-oob="#activity-entries:innerHTML,#job-cost:innerHTML"' in detail.text
            assert detail.text.index('</div>\n  <details class="activity">') > detail.text.index('id="progress-status"')
            assert detail.text.count('id="job-cost"') == 1
            write_status(jobs.jobs_dir() / location.rsplit("/", 1)[1], "compose",
                         "Writing the composition code – 4,004 characters so far", cost_usd=0.75)
            update = (await client.get(location + "/progress")).text
            assert "4,004 characters" in update and '<ol id="activity-entries">' in update
            assert '<span id="job-cost">$0.75' in update
            assert len(calls) == 1  # second job is queued, including its eventual render
            stored = json.loads((jobs.jobs_dir() / location.rsplit("/", 1)[1] / "request.json").read_text())
            assert stored["allow_client_logos"] is True
            assert (stored["max_pages"], stored["max_depth"]) == (7, 0)
            defaults = json.loads((jobs.jobs_dir() / second.headers["location"].rsplit("/", 1)[1] / "request.json").read_text())
            assert (defaults["max_pages"], defaults["max_depth"]) == (20, 2)
            assert (await client.post(location + "/delete")).status_code == 409
            tasks = list(app.state.tasks.values())
            release.set()
            await asyncio.gather(*tasks)
            assert len(calls) == 2
            assert "Test failure" in (await client.get(location)).text
            assert (await client.get(location + "/progress")).headers["hx-redirect"] == location


def test_review_sources_edits_and_stale_submission(review_job, client, monkeypatch):
    html = client.get(f"/jobs/{JOB}").text
    assert 'sandbox="allow-scripts"' in html and "allow-same-origin" not in html
    assert 'data-text-id="hook-title"' in html and "https://example.com/about" in html
    assert "feed-001.00s.png" in html and "story-001.00s.png" in html
    form = review_form(client)
    form["text:hook-title"] = "Better robots"
    response = client.post(f"/jobs/{JOB}/review", data=form)
    assert response.status_code == 422 and "unapplied changes" in response.text and "Better robots" in response.text
    form.update(action="revise", feedback="Bolder opening")
    captured = []
    monkeypatch.setattr("app.web.main.enqueue", lambda job_id, decision: captured.append((job_id, decision)))
    assert client.post(f"/jobs/{JOB}/review", data=form, follow_redirects=False).status_code == 303
    assert captured == [(JOB, {"action": "edit", "texts": {"hook-title": "Better robots"}, "text": "Bolder opening"})]
    write_status(review_job, "review", "A newer revision")
    assert client.post(f"/jobs/{JOB}/review", data=form).status_code == 409


async def test_approve_is_resumed_once_and_results_survive_reload(review_job, monkeypatch):
    release = asyncio.Event()
    decisions = []

    async def fake_resume(job_id, decision):
        decisions.append(decision)
        await release.wait()
        outputs = {fmt: f"renders/{fmt}-{size}.mp4" for fmt, size in (("feed", "1080x1350"), ("story", "1080x1920"))}
        for file in outputs.values():
            path = review_job / file
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"fake video")
        write_status(review_job, "done", "Videos ready", outputs=outputs)
        return {"status": "done"}

    monkeypatch.setattr(jobs, "resume_job", fake_resume)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
            html = (await client.get(f"/jobs/{JOB}")).text
            form = {"revision": re.search(r'name="revision" value="([a-f0-9]+)"', html)[1],
                    "text:hook-title": "Robots that work.", "action": "approve"}
            assert (await client.post(f"/jobs/{JOB}/review", data=form)).status_code == 303
            assert (await client.post(f"/jobs/{JOB}/review", data=form)).status_code == 409
            tasks = list(app.state.tasks.values())
            release.set()
            await asyncio.gather(*tasks)
            assert decisions == [{"action": "approve"}]
            page = await client.get(f"/jobs/{JOB}")
            assert "Your videos are ready" in page.text and page.text.count("<video") == 2
            assert 'hx-trigger="every 2s"' not in page.text
            file = await client.get(f"/jobs/{JOB}/files/renders/feed-1080x1350.mp4?download=true")
            assert "attachment" in file.headers["content-disposition"]
            assert (await client.get(f"/jobs/{JOB}/files/renders/feed-1080x1350.mp4", headers={"Range": "bytes=0-3"})).status_code == 206


def test_review_validation_blocks_bad_or_unchecked_copy(review_job, client, monkeypatch):
    form = review_form(client)
    for update in ({"action": "bogus"}, {"text:hook-title": ""}, {"text:hook-title": "x" * 501}, {"feedback": "x" * 4001}):
        assert client.post(f"/jobs/{JOB}/review", data={**form, **update}).status_code == 422
    assert client.post(f"/jobs/{JOB}/review", data={**form, "action": "revise"}).status_code == 422
    state = {"values": {"check": {"ok": False}}, "next": ["review"], "interrupts": [{}]}
    monkeypatch.setattr(jobs, "job_state", AsyncMock(return_value=state))
    assert client.post(f"/jobs/{JOB}/review", data=form).status_code == 422


def test_preview_headers_files_origin_and_traversal(review_job, client, workspace):
    response = client.get(f"/preview/{JOB}/index.html")
    assert response.status_code == 200
    assert response.headers["referrer-policy"] == "no-referrer"
    csp = response.headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp and "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp and "form-action 'none'" in csp
    assert client.get(f"/preview/{JOB}/harness/gsap.min.js").headers["access-control-allow-origin"] == "*"
    assert client.get(f"/jobs/{JOB}/files/keyframes/r0/feed-001.00s.png").status_code == 200
    for url in (f"/preview/{JOB}/%2e%2e%2frequest.json", f"/jobs/{JOB}/files/site/index.html",
                f"/jobs/{JOB}/files/request.json", "/jobs/not-found"):
        assert client.get(url).status_code == 404
    outside = workspace / "secret.js"
    outside.write_text("private")
    (review_job / "site/composition/composition.js").unlink()
    (review_job / "site/composition/composition.js").symlink_to(outside)
    assert client.get(f"/preview/{JOB}/composition/composition.js").status_code == 404
    for origin in ("null", "https://evil.example", ""):
        assert client.post("/jobs", headers={"Origin": origin}, data={"url": "https://example.com"}).status_code == 403
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_delete_removes_files_and_checkpoint(review_job, client, monkeypatch):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    deleted = AsyncMock()
    monkeypatch.setattr(AsyncSqliteSaver, "adelete_thread", deleted)
    assert "Delete this video?" in client.get(f"/jobs/{JOB}/delete").text
    assert client.post(f"/jobs/{JOB}/delete", follow_redirects=False).status_code == 303
    deleted.assert_awaited_once_with(JOB)
    assert not review_job.exists()


def test_restart_marks_unfinished_work_without_restarting_it(workspace):
    job_id = jobs.create_job("https://example.com", 30)
    path = jobs.jobs_dir() / job_id
    write_status(path, "compose", "Writing code")
    with TestClient(app, base_url=ORIGIN) as client:
        assert "app stopped" in client.get(f"/jobs/{job_id}").text
        assert not app.state.tasks
        assert json.loads((path / "status.json").read_text())["status"] == "interrupted"


@pytest.mark.browser
async def test_progress_insights_survive_polling_and_expand_as_results_arrive(review_job, tmp_path):
    from playwright.async_api import async_playwright

    async with app.router.lifespan_context(app), async_playwright() as pw:
        (review_job / "profile.json").write_text(json.dumps({"facts": [
            {"text": text, "source_url": "https://example.com/about"} for text in
            ("Robots that work.", "Founded in 2019.", "Fixed-price projects.", "Built for small factories.")]}))
        write_status(review_job, "crawl", "Reading page 1 (limit 20): https://example.com/")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})

                async def route_http(route):
                    response = await client.get(route.request.url)
                    await route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

                await page.route(ORIGIN + "/**", route_http)
                await page.goto(f"{ORIGIN}/jobs/{JOB}")
                await page.get_by_role("heading", name="Reading your website").wait_for()
                await page.get_by_text("Activity log", exact=True).click()
                activity = await page.locator("details.activity").element_handle()
                assert await page.locator(".progress-motion").count() == 0
                await page.get_by_text("Read 1 more", exact=True).click()
                facts = await page.locator(".insight-card").first.element_handle()
                (review_job / "brand").mkdir()
                (review_job / "brand/brand.json").write_text(json.dumps({"colors": [{"hex": "#ffffff"}, {"hex": "#19285a"}]}))
                write_status(review_job, "brand", "Brand assets found")
                await page.get_by_role("heading", name="Finding your visual identity").wait_for(timeout=10000)
                assert await facts.evaluate("el => el.isConnected && el.querySelector('details').open")
                assert await activity.evaluate("el => el.isConnected && el.open")
                assert "White" in await page.locator(".insight-colours").inner_text()
                assert "#ffffff" not in await page.locator(".discoveries").inner_text()
                await page.screenshot(path=str(tmp_path / "progress-desktop.png"), full_page=True)
                assert "Find the facts" in await page.locator(".progress-next").inner_text()
                assert await page.locator('[aria-current="step"]').text_content() == "2Brand"
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.screenshot(path=str(tmp_path / "progress-mobile.png"), full_page=True)
                await page.emulate_media(reduced_motion="reduce")
                assert await page.locator(".live-dot").evaluate("el => getComputedStyle(el).animationName") == "none"
            finally:
                await browser.close()


@pytest.mark.browser
async def test_browser_form_review_preview_and_results(review_job, monkeypatch, tmp_path):
    """Real Chromium, with HTTP routed to ASGI in memory: no listener and no Bedrock."""
    import shutil
    from playwright.async_api import async_playwright

    monkeypatch.setattr("app.web.main.UrlGuard.check", lambda self, url: url)
    ready_for_review = asyncio.Event()

    async def fake_run(job_id):
        path = jobs.jobs_dir() / job_id
        for name in ("concept.json", "check.json", "profile.json"):
            shutil.copy(review_job / name, path / name)
        for name in ("site", "keyframes"):
            shutil.copytree(review_job / name, path / name)
        write_status(path, "compose", "Writing the composition code")
        await ready_for_review.wait()
        write_status(path, "review", "Waiting for your review")
        return {}

    async def fake_resume(job_id, decision):
        path = jobs.jobs_dir() / job_id
        if decision["action"] == "approve":
            write_status(path, "done", "Videos ready", outputs={
                "feed": "renders/feed-1080x1350.mp4", "story": "renders/story-1080x1920.mp4"})
        else:
            concept = json.loads((path / "concept.json").read_text())
            concept["scenes"][0]["texts"][0]["text"] = decision["texts"]["hook-title"]
            (path / "concept.json").write_text(json.dumps(concept))
            write_status(path, "review", "Waiting for your review")
        return {}

    monkeypatch.setattr(jobs, "run_job", fake_run)
    monkeypatch.setattr(jobs, "resume_job", fake_resume)
    async with app.router.lifespan_context(app), async_playwright() as pw:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            browser = await pw.chromium.launch()
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                external = []

                async def route_http(route):
                    req = route.request
                    if not req.url.startswith(ORIGIN + "/"):
                        external.append(req.url)
                        await route.abort()
                        return
                    response = await client.request(req.method, req.url, headers=await req.all_headers(), content=req.post_data_buffer)
                    await route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

                await context.route("**/*", route_http)
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(ORIGIN)
                await page.get_by_label("Website URL", exact=True).fill("https://example.com/")
                await page.get_by_label("Creative direction", exact=False).fill("A brief browser test")
                await page.get_by_role("button", name="Create preview").click()
                await page.get_by_text("Activity log", exact=True).click()
                activity = await page.locator("details.activity").element_handle()
                entries = await page.locator("#activity-entries").element_handle()
                job_id = page.url.rsplit("/", 1)[1]
                for count in (2002, 4004):
                    message = f"Writing the composition code – {count:,} characters so far"
                    write_status(jobs.jobs_dir() / job_id, "compose", message, cost_usd=0.75)
                    await page.wait_for_function("text => document.querySelector('.progress-message')?.textContent === text", arg=message)
                    assert await activity.evaluate("el => el.isConnected && el.open")
                    assert await entries.evaluate("el => el.isConnected")
                    assert message in await page.locator("#activity-entries").inner_text()
                    assert "$0.75" in await page.locator("#job-cost").inner_text()
                ready_for_review.set()
                await page.get_by_role("heading", name="Fine-tune the details.").wait_for(timeout=15000)
                frame = page.frame_locator('iframe[title="4:5 · Feed animation preview"]')
                await frame.get_by_role("button", name="Play preview", exact=True).wait_for()
                await frame.get_by_role("button", name="Play preview", exact=True).click()
                await frame.get_by_role("button", name="Pause preview", exact=True).wait_for()
                preview_frame = next(f for f in page.frames if "/preview/" in f.url and "format=feed" in f.url)
                await preview_frame.wait_for_function("window.__ready === true")
                assert await preview_frame.evaluate("document.fonts.status") == "loaded"
                assert await preview_frame.evaluate("[...document.fonts].every(font => font.status !== 'error')")
                assert await preview_frame.evaluate("() => { try { return !!parent.document.body; } catch { return false; } }") is False
                assert await preview_frame.evaluate("async () => { try { await fetch('/healthz'); return false; } catch { return true; } }")
                await frame.get_by_role("button", name="Pause preview", exact=True).click()
                await frame.get_by_label("Preview position").evaluate("el => { el.value = '0.5'; el.dispatchEvent(new Event('input')); }")
                assert "15.00 s" in await frame.locator("#mg-time").inner_text()
                assert await page.locator(".keyframe-pair img").evaluate_all("imgs => imgs.every(img => img.complete && img.naturalWidth > 0)")
                await page.locator('[data-text-id="hook-title"]').fill("Better robots")
                await page.get_by_role("button", name="Approve & render").click()
                assert "unapplied changes" in await page.get_by_role("alert").inner_text()
                await page.get_by_role("button", name="Revise preview").click()
                await page.get_by_role("heading", name="Fine-tune the details.").wait_for(timeout=15000)
                assert await page.locator('[data-text-id="hook-title"]').input_value() == "Better robots"
                await page.screenshot(path=str(tmp_path / "review-desktop.png"), full_page=True)
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.screenshot(path=str(tmp_path / "review-mobile.png"), full_page=True)
                await page.get_by_role("button", name="Approve & render").click()
                await page.get_by_role("heading", name="Your videos are ready.").wait_for(timeout=15000)
                await page.reload()
                assert await page.locator("video").count() == 2
                assert not errors
                assert not external
            finally:
                await browser.close()
