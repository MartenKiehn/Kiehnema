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
                 {"url": "https://example.com", "duration": 61}, {"url": "http://192.168.1.1"},
                 {"url": "https://example.com", "description": "x" * 4001}):
        response = client.post("/jobs", data=data)
        assert response.status_code == 422 and 'role="alert"' in response.text
    assert not list(jobs.jobs_dir().iterdir())


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "http://localhost:3000/", "http://[::1]:8000/"])
def test_create_job_accepts_local_website(client, monkeypatch, url):
    queued = []
    monkeypatch.setattr("app.web.main.enqueue", queued.append)
    response = client.post("/jobs", data={"url": url}, follow_redirects=False)
    assert response.status_code == 303
    assert len(queued) == 1
    request = json.loads((jobs.jobs_dir() / queued[0] / "request.json").read_text())
    assert request["url"] == url


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


def test_reopen_completed_job_and_reject_stale_requests(review_job, client, monkeypatch):
    outputs = {"feed": "renders/feed-1080x1350.mp4"}
    video = review_job / outputs["feed"]
    video.parent.mkdir()
    video.write_bytes(b"last export")
    write_status(review_job, "done", "Videos ready", outputs=outputs)

    async def reopen(job_id):
        assert job_id in app.state.tasks
        write_status(review_job, "review", "Waiting for your review")
    mock = AsyncMock(side_effect=reopen)
    monkeypatch.setattr(jobs, "reopen_job", mock)
    html = client.get(f"/jobs/{JOB}").text
    assert "Continue editing" in html and 'id="review-form"' not in html
    form = {"revision": re.search(r'name="revision" value="([a-f0-9]+)"', html)[1]}
    url = f"/jobs/{JOB}/reopen"
    assert client.post(url, data={"revision": "stale"}).status_code == 409
    assert client.post(url, data=form, headers={"Origin": "https://example.com"}).status_code == 403
    app.state.tasks[JOB] = object()
    try:
        assert client.post(url, data=form).status_code == 409
    finally:
        app.state.tasks.pop(JOB)
    mock.assert_not_awaited()
    response = client.post(url, data=form, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/jobs/{JOB}"
    mock.assert_awaited_once_with(JOB)
    assert JOB not in app.state.tasks
    html = client.get(f"/jobs/{JOB}").text
    assert 'id="review-form"' in html and 'id="advanced-tab"' in html
    assert "Last rendered videos" in html and "Continue editing" not in html
    assert client.get(f"/jobs/{JOB}/files/{outputs['feed']}").content == b"last export"
    assert client.post(url, data=form).status_code == 409


async def test_reopen_button_returns_to_editor_in_browser(review_job, live_web, monkeypatch, tmp_path):
    from playwright.async_api import async_playwright, expect

    write_status(review_job, "done", "Videos ready", outputs={"feed": "renders/feed-1080x1350.mp4"})
    async def reopen(job_id):
        write_status(review_job, "review", "Waiting for your review")
    monkeypatch.setattr(jobs, "reopen_job", reopen)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.goto(f"{live_web}/jobs/{JOB}")
        await expect(page.get_by_role("button", name="Continue editing")).to_be_visible()
        await page.screenshot(path=str(tmp_path / "reopen-desktop.png"))
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.screenshot(path=str(tmp_path / "reopen-mobile.png"))
        await page.get_by_role("button", name="Continue editing").click()
        await expect(page.get_by_role("tab", name="Scene", exact=True)).to_be_visible()
        await page.get_by_role("tab", name="Advanced", exact=True).click()
        await expect(page.locator("#advanced-panel")).to_be_visible()
        await page.locator(".rendered-downloads summary").click()
        await expect(page.get_by_role("link", name="Download 4:5")).to_be_visible()
        await page.reload()
        await expect(page.locator("#review-form")).to_be_visible()
        await browser.close()


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
async def test_browser_form_review_preview_and_results(review_job, monkeypatch, tmp_path, live_web):
    """Real Chromium and native form redirects on a local server; no Bedrock."""
    origin = live_web
    import shutil
    from playwright.async_api import async_playwright, expect

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
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            external = []

            async def route_http(route):
                req = route.request
                if not req.url.startswith(origin + "/"):
                    external.append(req.url)
                    await route.abort()
                    return
                await route.continue_()

            await context.route("**/*", route_http)
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(origin)
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
            await page.locator("iframe.preview.feed").scroll_into_view_if_needed()
            await expect(frame.locator("#mg-controls")).to_be_hidden()
            await page.locator("#timeline-play").click()
            await expect(page.locator("#timeline-play")).to_have_text("Pause both previews")
            preview_frame = next(f for f in page.frames if "/preview/" in f.url and "format=feed" in f.url)
            await preview_frame.wait_for_function("window.__ready === true")
            assert await preview_frame.evaluate("document.fonts.status") == "loaded"
            assert await preview_frame.evaluate("[...document.fonts].every(font => font.status !== 'error')")
            assert await preview_frame.evaluate("() => { try { return !!parent.document.body; } catch { return false; } }") is False
            assert await preview_frame.evaluate("async () => { try { await fetch('/healthz'); return false; } catch { return true; } }")
            await page.locator("#timeline-play").click()
            await expect(page.locator("#timeline-play")).to_have_text("Play both previews")
            await page.locator("#timeline-position").fill("7.5")
            await expect(frame.locator("#mg-time")).to_have_text("7.50 s")
            await page.locator(".scene-stills summary").click()
            await page.locator(".keyframe-pair img").first.wait_for()
            await page.wait_for_function("() => [...document.querySelectorAll('.keyframe-pair img')].every(img => img.complete && img.naturalWidth > 0)")
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


def test_timeline_comments_persist_validate_and_revise(review_job, client, monkeypatch):
    form = review_form(client)
    url = f"/jobs/{JOB}/comments"
    payload = {"revision": form["revision"], "time": 4.2, "text": "Use a brighter background", "format": "story"}
    first = client.post(url, json=payload)
    assert first.status_code == 200
    comment = first.json()["comments"][0]
    assert comment["scene_id"] == "s-hook"
    assert "Use a brighter background" in client.get(f"/jobs/{JOB}").text
    assert client.post(f"/jobs/{JOB}/review", data=form).status_code == 422
    for invalid in ({"time": -1}, {"time": 16}, {"time": "NaN"}, {"text": " "}, {"text": "x" * 2001}, {"format": "bad"}):
        assert client.post(url, json={**payload, **invalid}).status_code == 422
    assert client.post(url, json=payload, headers={"Origin": "null"}).status_code == 403
    assert client.post(url, json={**payload, "revision": "old"}).status_code == 409
    assert client.post(url, json={**payload, "id": "abc"}).status_code == 404
    changed = client.post(url, json={**payload, "id": comment["id"], "time": 15, "text": "Completely change the ending"}).json()["comments"]
    assert len(changed) == 1 and changed[0]["time"] == 15
    captured = []
    monkeypatch.setattr("app.web.main.enqueue", lambda job_id, decision: captured.append(decision))
    assert client.post(f"/jobs/{JOB}/review", data={**form, "action": "revise"}, follow_redirects=False).status_code == 303
    assert captured[0]["comments"] == changed
    assert client.post(url, json={"revision": form["revision"], "action": "delete", "id": comment["id"]}).json() == {"comments": []}
    assert not json.loads((review_job / "review-comments.json").read_text())["comments"]
    write_status(review_job, "review", "New preview")
    assert client.post(url, json=payload).status_code == 409


@pytest.mark.parametrize("duration,order", [("NaN", ["s-hook"]), ("14", ["s-hook"]), ("61", ["s-hook"]), ("30", ["s-hook", "s-hook"])])
def test_scene_edit_validation_preserves_draft(review_job, client, duration, order):
    form = {**review_form(client), "action": "revise", "scene-order": order,
            "scene-duration:s-hook": duration, "scene-visual:s-hook": "A quiet product shot"}
    response = client.post(f"/jobs/{JOB}/review", data=form)
    assert response.status_code == 422
    assert "A quiet product shot" in response.text
    assert "Keep each scene once" in response.text


@pytest.mark.browser
async def test_timeline_browser_on_running_server(review_job, monkeypatch, tmp_path, live_web):
    """Exercise real form redirects, sandbox messages and persisted edits, without a model."""
    from playwright.async_api import async_playwright, expect
    from app.web.screenshot import capture

    concept = json.loads(json.dumps(CONCEPT))
    concept["scenes"].append({"id": "s-end", "purpose": "Ending", "visual": "Logo and closing line", "start": 15,
                               "duration": 15, "texts": [{"id": "end-title", "role": "headline", "text": "Get started", "fact_ids": []}]})
    (review_job / "concept.json").write_text(json.dumps(concept))
    captured = []
    monkeypatch.setattr("app.web.main.enqueue", lambda job_id, decision: captured.append(decision))
    base = live_web
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(f"{base}/jobs/{JOB}")
        for frame in page.frames[1:]:
            await frame.wait_for_function("window.__ready === true")
        await page.locator('#duration-s-hook').fill('16')
        await expect(page.locator('.timeline-frame[data-scene="s-hook"] .frame-edit')).to_be_visible()
        await page.locator('#duration-s-hook').fill('15.0')
        await expect(page.locator('.timeline-frame[data-scene="s-hook"] .frame-edit')).to_be_hidden()
        await expect(page.locator('#planned-order')).to_be_hidden()
        await page.locator('#timeline-position').fill('4.2')
        for frame in page.frames[1:]:
            await expect(frame.locator('#mg-time')).to_have_text('4.20 s')
            assert await frame.evaluate("(() => {try {return !!parent.document.body} catch {return false}})()") is False
        await page.locator('#comment-here').click()
        await page.locator('#comment-text').fill('Make this scene lighter')
        assert not (review_job / 'review-comments.json').exists()
        await page.get_by_role('button', name='Revise preview').click()
        await expect(page.locator('#comment-status')).to_contain_text('Confirm or clear')
        assert not captured
        await page.get_by_role('button', name='Confirm comment', exact=True).click()
        await expect(page.locator('#comment-list li')).to_have_count(1)
        await page.reload()
        await expect(page.locator('#comment-list')).to_contain_text('Make this scene lighter')
        await page.locator('.comment-marker').click()
        await page.locator('#comment-text').fill('Use a close-up instead')
        await page.get_by_role('button', name='Confirm changes').click()
        await expect(page.locator('#comment-list')).to_contain_text('Use a close-up instead')
        await page.locator('#comment-time').fill('18')
        await page.locator('#comment-text').fill('Finish with our logo')
        await page.get_by_role('button', name='Confirm comment', exact=True).click()
        await expect(page.locator('#comment-list li')).to_have_count(2)
        await page.locator('#comment-list li').first.get_by_role('button', name='Delete', exact=True).click()
        await expect(page.locator('#comment-list li')).to_have_count(1)
        await page.locator('#timeline-play').click()
        await expect(page.locator('#timeline-play')).to_have_text('Pause both previews')
        await page.locator('#timeline-play').click()
        await expect(page.locator('#timeline-play')).to_have_text('Play both previews')
        await page.locator('#scene-tab').click()
        await page.locator('.timeline-frame[data-scene="s-end"]').click()
        await expect(page.locator('#scene-s-end')).to_be_visible()
        await expect(page.locator('#scene-s-hook')).to_be_hidden()
        await page.locator('#visual-s-end').fill('Show the product first')
        await expect(page.locator('.timeline-frame[data-scene="s-end"] .frame-edit')).to_be_visible()
        await page.locator('#scene-previous').click()
        await expect(page.locator('#scene-s-hook')).to_be_visible()
        await page.locator('.timeline-frame[data-scene="s-hook"]').focus()
        await page.keyboard.press('ArrowRight')
        await expect(page.locator('#visual-s-end')).to_have_value('Show the product first')
        # A playback update must not steal the scene currently being edited.
        for frame in page.frames[1:]:
            await expect(frame.locator('#mg-time')).to_have_text('22.50 s')
            await frame.evaluate('window.__seek(0)')
        await page.locator('#timeline-play').click()
        await page.wait_for_function("() => Number(document.querySelector('#timeline-position').value) < 5")
        await expect(page.locator('#scene-s-end')).to_be_visible()
        await page.locator('#timeline-play').click()
        # Native validation reveals a required field even in an unselected scene.
        await page.locator('#scene-previous').click()
        await page.locator('[data-text-id="hook-title"]').fill('')
        await page.locator('#scene-next').click()
        await page.get_by_role('button', name='Revise preview').click()
        await expect(page.locator('#scene-s-hook')).to_be_visible()
        await expect(page.locator('[data-text-id="hook-title"]')).to_be_focused()
        await page.locator('[data-text-id="hook-title"]').fill('Robots that work.')
        await page.locator('#scene-next').click()
        await page.locator('#scene-s-end [data-move="-1"]').click()
        await expect(page.locator('#scene-editor > article').first).to_have_attribute('data-scene-id', 's-end')
        await page.locator('#duration-s-end').fill('10')
        await page.locator('#visual-s-end').fill('Show the product first')
        await expect(page.locator('#planned-duration')).to_contain_text('25.0 s')
        await expect(page.locator('#planned-order')).to_be_visible()
        await expect(page.locator('#planned-order button').first).to_contain_text('02 · 10.0 s')
        await expect(page.locator('.timeline-frame').first).to_have_attribute('data-scene', 's-hook')
        await expect(page.locator('#edit-status')).to_contain_text('Draft changes')
        await page.get_by_role('button', name='Revise preview').click()
        await page.wait_for_url(f'{base}/jobs/{JOB}')
        await expect(page.locator('#review-form')).not_to_have_attribute('data-submitting', 'true')
        assert captured[0]['scenes'] == [{'id': 's-end', 'duration': 10, 'visual': 'Show the product first'},
                                          {'id': 's-hook', 'duration': 15, 'visual': 'Large type'}]
        assert captured[0]['comments'][0]['scene_id'] == 's-end'
        assert captured[0]['comments'][0]['time'] == 18
        assert not errors
        await browser.close()
    for width in (1440, 390):
        report = await capture(f'{base}/jobs/{JOB}', tmp_path / f'review-{width}.png', width=width, time=4.2)
        assert not report['page_errors'] and not report['horizontal_overflow'] and not report['broken_images']
        assert Path(report['screenshot']).stat().st_size > 1000


@pytest.mark.browser
async def test_comment_editor_keeps_preview_and_scrubber_visible(review_job, live_web, tmp_path):
    from playwright.async_api import async_playwright, expect

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        for width, height in ((1440, 900), (1280, 720), (390, 844), (390, 600)):
            await page.set_viewport_size({"width": width, "height": height})
            await page.goto(f"{live_web}/jobs/{JOB}")
            for frame in page.frames[1:]:
                await frame.wait_for_function("window.__ready === true")
            await page.locator('#comment-here').click()
            await page.locator('#comment-text').fill('Keep this draft while finding the frame')
            await page.locator('#timeline-position').fill('7.2')
            await expect(page.locator('#comment-text')).to_have_value('Keep this draft while finding the frame')
            await expect(page.locator('#comment-time')).to_have_value('7.2')
            for frame in page.frames[1:]:
                await expect(frame.locator('#mg-time')).to_have_text('7.20 s')
            for selector in ('iframe.preview.feed', 'iframe.preview.story', '#timeline-position', '#comment-text'):
                box = await page.locator(selector).bounding_box()
                assert box and box['y'] >= 0 and box['y'] + box['height'] <= height, (width, height, selector, box)
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.screenshot(path=str(tmp_path / f'comment-{width}-{height}.png'))
            await page.locator('#scene-tab').click()
            await page.locator('#comments-tab').click()
            await expect(page.locator('#comment-text')).to_have_value('Keep this draft while finding the frame')
            await page.locator('#cancel-comment').click()
        # Confirming the selected frame persists the timestamp along with the text.
        await page.locator('#comment-text').fill('Saved at the selected frame')
        await page.locator('#timeline-position').fill('8.4')
        await page.locator('#save-comment').click()
        await expect(page.locator('#comment-list')).to_contain_text('8.4 s')
        await page.reload()
        await expect(page.locator('#comment-list')).to_contain_text('Saved at the selected frame')
        await browser.close()


async def test_screenshot_rejects_invalid_target_and_options(tmp_path):
    from app.web.screenshot import capture
    for url, options in [('https://example.com', {}), ('http://127.0.0.1', {'time': float('nan')}),
                         ('http://127.0.0.1', {'width': 0})]:
        with pytest.raises(ValueError):
            await capture(url, tmp_path / 'invalid.png', **options)


@pytest.fixture
async def live_web(review_job):
    import socket
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(.01)
        assert server.started
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task
        sock.close()


def design_draft(**overrides):
    element = {"id": "note-1", "kind": "text", "text": "A better idea", "x": .2, "y": .3,
               "width": .5, "height": .2, "layer": 3, **overrides}
    return {"frames": [{"scene_id": "s-hook", "format": "feed", "time": 1, "elements": [element]}]}


def test_advanced_images_and_review_validation(review_job, client, monkeypatch):
    import io
    from PIL import Image

    form = review_form(client)
    headers = {"X-Preview-Revision": form["revision"]}
    image = io.BytesIO()
    Image.new("RGB", (50, 30), "orange").save(image, "PNG")
    response = client.post(f"/jobs/{JOB}/design-assets", content=image.getvalue(), headers=headers)
    assert response.status_code == 200
    asset = response.json()["asset"]
    assert (review_job / "review-assets" / asset).is_file()
    assert client.get(f"/preview/{JOB}/assets/user/{asset}").status_code == 200
    assert client.get(f"/jobs/{JOB}/files/review-assets/{asset}").status_code == 200
    invalid_image = client.post(f"/jobs/{JOB}/design-assets", content=b'<svg onload="alert(1)"></svg>', headers=headers)
    assert invalid_image.status_code == 422 and 'valid PNG' in invalid_image.json()['detail']
    assert client.post(f"/jobs/{JOB}/design-assets", content=b'x' * (10 * 1024 * 1024 + 1), headers=headers).status_code == 413
    assert client.post(f"/jobs/{JOB}/design-assets", content=image.getvalue(), headers={**headers, "Origin": "null"}).status_code == 403
    assert client.post(f"/jobs/{JOB}/design-assets", content=image.getvalue(), headers={"X-Preview-Revision": "old"}).status_code == 409
    captured = []
    monkeypatch.setattr("app.web.main.enqueue", lambda job_id, decision: captured.append(decision))
    for invalid in (design_draft(x=float('nan')), design_draft(text=''), design_draft(removed=True), design_draft(comment='x' * 2001),
                    design_draft(kind='existing', selector='#s-hook > :nth-child(1)', removed=True, text_ids=['unrelated-text']),
                    design_draft(kind='image', asset='../secret.png'),
                    design_draft(kind='image', asset='a' * 32 + '.png'), {"frames": [{**design_draft()['frames'][0], 'scene_id': 'missing'}]}):
        response = client.post(f"/jobs/{JOB}/review", data={**form, "action": "revise", "advanced": json.dumps(invalid)})
        assert response.status_code == 422
    valid = design_draft()
    valid['frames'][0]['elements'].append({**valid['frames'][0]['elements'][0], 'id': 'image-1', 'kind': 'image', 'asset': asset, 'text': ''})
    response = client.post(f"/jobs/{JOB}/review", data={**form, "advanced": json.dumps(valid)})
    assert response.status_code == 422 and 'unapplied changes' in response.text
    assert 'A better idea' in response.text
    assert not captured
    response = client.post(f"/jobs/{JOB}/review", data={**form, "action": "revise", "advanced": json.dumps(valid)})
    assert response.status_code == 200
    assert captured[0]['design']['frames'][0]['elements'][1]['asset'] == asset


@pytest.mark.browser
async def test_advanced_editor_drag_layers_images_and_revision(review_job, monkeypatch, live_web, tmp_path):
    import io
    from PIL import Image
    from playwright.async_api import async_playwright, expect

    # Deletion validates contained text IDs against the storyboard, including the example's artwork.
    concept = json.loads((review_job / 'concept.json').read_text())
    concept['scenes'][0]['texts'] += [
        {'id': tid, 'role': 'label', 'text': copy} for tid, copy in [
            ('hook-question', 'Noch ein KI-Prototyp für die Schublade?'),
            ('hook-answer', 'Nicht bei uns.'), ('hook-sub', 'Wir entwickeln KI, die im Tagesgeschäft wirkt.')]]
    (review_job / 'concept.json').write_text(json.dumps(concept))
    captured = []
    monkeypatch.setattr("app.web.main.enqueue", lambda job_id, decision: captured.append(decision))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(f'{live_web}/jobs/{JOB}')
        await page.locator('#advanced-tab').click()
        await page.locator('#design-layers button').first.wait_for()
        await expect(page.locator('#timeline-play')).to_be_disabled()
        frame = page.frame_locator('iframe.preview.feed')
        async def resize_handle(edge, dx, dy):
            handle = frame.locator(f'[data-resize="{edge}"]')
            box = await handle.bounding_box()
            await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            await page.mouse.down()
            await page.mouse.move(box['x'] + box['width'] / 2 + dx, box['y'] + box['height'] / 2 + dy, steps=8)
            await page.mouse.up()
        await page.locator('#design-add-text').click()
        await page.locator('#design-text').fill('Make it memorable')
        note = frame.locator('[data-design-id]').first
        await expect(note).to_have_text('Make it memorable')
        await note.dblclick()
        await page.keyboard.press('Meta+a')
        await page.keyboard.insert_text('Make it memorable')
        await expect(note).to_have_attribute('contenteditable', 'plaintext-only')
        await page.locator('#design-status').click()
        await page.locator('iframe.preview.feed').scroll_into_view_if_needed()
        before = await note.bounding_box()
        await resize_handle('e', 35, 0)
        await page.wait_for_function("() => JSON.parse(document.querySelector('#advanced-draft').value).frames[0].elements[0].width > .55")
        resized = await note.bounding_box()
        assert resized['width'] == pytest.approx(before['width'] + 35, abs=2)
        assert resized['height'] == pytest.approx(before['height'], abs=2)
        box = await note.bounding_box()
        await page.mouse.move(box['x'] + 20, box['y'] + 20)
        await page.mouse.down()
        await page.mouse.move(box['x'] + 80, box['y'] + 65, steps=8)
        await page.mouse.up()
        await page.wait_for_function("() => JSON.parse(document.querySelector('#advanced-draft').value).frames[0].elements[0].x > .2")
        moved = json.loads(await page.locator('#advanced-draft').input_value())
        await page.locator('#design-back').click()
        await page.wait_for_function("() => JSON.parse(document.querySelector('#advanced-draft').value).frames[0].elements.some(e => e.kind === 'existing')")
        # Undo restores both layers, then existing artwork can be moved independently.
        await page.locator('#design-undo').click()
        await page.locator('#design-layers button').last.click()
        await page.locator('#design-geometry summary').click()
        await page.locator('#design-x').fill('12')
        await expect(page.locator('#design-reset')).to_be_visible()
        await page.locator('iframe.preview.feed').scroll_into_view_if_needed()
        selected_box = frame.locator('[data-design-selection]')
        before = await selected_box.bounding_box()
        await resize_handle('nw', 25, 15)
        await page.wait_for_function("() => JSON.parse(document.querySelector('#advanced-draft').value).frames[0].elements.some(e => e.kind === 'existing' && e.x > .12)")
        resized = await selected_box.bounding_box()
        assert resized['width'] < before['width']
        assert resized['width'] / resized['height'] == pytest.approx(before['width'] / before['height'], abs=.02)
        assert resized['x'] + resized['width'] == pytest.approx(before['x'] + before['width'], abs=2)
        assert resized['y'] + resized['height'] == pytest.approx(before['y'] + before['height'], abs=2)
        resized_existing = next(e for e in json.loads(await page.locator('#advanced-draft').input_value())['frames'][0]['elements'] if e['kind'] == 'existing')
        await page.locator('#design-undo').click()
        await page.locator('#design-layers button').last.click()
        assert (await selected_box.bounding_box())['width'] == pytest.approx(before['width'], abs=2)
        await resize_handle('nw', 25, 15)
        image = io.BytesIO()
        Image.new('RGB', (60, 40), 'orange').save(image, 'PNG')
        await page.locator('#design-image').set_input_files({'name': 'idea.png', 'mimeType': 'image/png', 'buffer': image.getvalue()})
        await expect(page.locator('#design-status')).to_contain_text('Image added')
        await expect(frame.locator('img[data-design-id]')).to_have_count(1)
        # Dropped files follow the same upload path, with frame-relative positioning.
        await frame.locator('#stage').evaluate("""(stage, bytes) => {
          const transfer = new DataTransfer();
          transfer.items.add(new File([new Uint8Array(bytes)], 'drop.png', {type: 'image/png'}));
          const box = stage.getBoundingClientRect();
          stage.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: transfer,
            clientX: box.left + box.width * .6, clientY: box.top + box.height * .3}));
        }""", list(image.getvalue()))
        await expect(frame.locator('img[data-design-id]')).to_have_count(2)
        await page.locator('#design-remove').click()
        await expect(frame.locator('img[data-design-id]')).to_have_count(1)
        await page.locator('#design-format').select_option('story')
        await expect(page.locator('iframe.preview.feed')).to_be_hidden()
        await expect(page.locator('iframe.preview.story')).to_be_visible()
        await page.locator('#design-format').select_option('feed')
        await expect(frame.locator('[data-design-id]').first).to_have_text('Make it memorable')
        await page.locator('#scene-tab').click()
        await expect(frame.locator('[data-design-id]')).to_have_count(0)
        await expect(frame.locator('[data-design-selection]')).to_have_count(0)
        await page.locator('#advanced-tab').click()
        await expect(frame.locator('[data-design-id]')).to_have_count(2)
        # Existing elements become removal instructions, survive switching tabs, and support Undo.
        artwork = frame.locator(resized_existing['selector'])
        await page.locator('#design-layers button').last.click()
        await page.locator('#design-remove').click()
        await expect(artwork).to_be_hidden()
        await expect(page.locator('#design-status')).to_contain_text('Undo restores it')
        await page.locator('#scene-tab').click()
        await expect(artwork).to_be_visible()
        await page.locator('#advanced-tab').click()
        await expect(artwork).to_be_hidden()
        await page.locator('#design-undo').click()
        await expect(artwork).to_be_visible()
        await page.locator('#design-layers button').last.click()
        await page.locator('#design-remove').click()
        await expect(artwork).to_be_hidden()
        await page.screenshot(path=str(tmp_path / 'advanced-desktop.png'), full_page=True)
        await page.set_viewport_size({'width': 390, 'height': 844})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.locator('#design-layers').scroll_into_view_if_needed()
        preview_box = await page.locator('iframe.preview.feed').bounding_box()
        assert 0 <= preview_box['y'] < 100
        assert preview_box['y'] + preview_box['height'] < 450
        await page.get_by_role('button', name='Revise preview').click()
        await page.wait_for_url(f'{live_web}/jobs/{JOB}')
        assert captured and len(captured[0]['design']['frames']) == 1
        elements = captured[0]['design']['frames'][0]['elements']
        assert {e['kind'] for e in elements} == {'existing', 'text', 'image'}
        assert next(e for e in elements if e['kind'] == 'text')['text'] == 'Make it memorable'
        assert next(e for e in elements if e['kind'] == 'text')['x'] == moved['frames'][0]['elements'][0]['x']
        assert next(e for e in elements if e['kind'] == 'existing')['width'] == pytest.approx(resized_existing['width'], abs=.01)
        assert next(e for e in elements if e['kind'] == 'existing')['removed'] is True
        assert not errors
        await browser.close()


@pytest.mark.browser
async def test_advanced_element_comment_stays_with_selection(review_job, monkeypatch, live_web):
    from playwright.async_api import async_playwright, expect

    captured = []
    monkeypatch.setattr('app.web.main.enqueue', lambda job_id, decision: captured.append(decision))
    comment = 'Round this drawer’s corners and make its entrance slower.'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(f'{live_web}/jobs/{JOB}')
        await page.locator('#advanced-tab').click()
        frame = page.frame_locator('iframe.preview.feed')
        await frame.locator('.drawer-scene').click()
        await expect(page.locator('#design-comment-target')).to_contain_text('KI-Prototyp')
        await page.locator('#design-comment').fill(comment)
        await expect(page.locator('#design-layers button').filter(has_text='KI-Prototypv0.1')).to_contain_text('Comment')
        await page.locator('#design-comment').fill('')
        await page.locator('#design-undo').click()
        await page.locator('#design-layers button').filter(has_text='KI-Prototypv0.1').click()
        await expect(page.locator('#design-comment')).to_have_value(comment)
        # An animated element's comment survives a seek where the artwork is invisible.
        await page.locator('#timeline-position').fill('0')
        await page.locator('#design-add-text').click()
        await expect(page.locator('#design-comment')).to_have_value('')
        await page.locator('#design-comment').fill('Use a lighter font for this new text.')
        await page.locator('#design-remove').click()
        await page.locator('#timeline-position').fill('1')
        await page.locator('#design-format').select_option('story')
        await page.locator('#design-format').select_option('feed')
        await frame.locator('.drawer-scene').click()
        await expect(page.locator('#design-comment')).to_have_value(comment)
        await page.locator('#scene-tab').click()
        await page.locator('#advanced-tab').click()
        await frame.locator('.drawer-scene').click()
        await expect(page.locator('#design-comment')).to_have_value(comment)
        await page.get_by_role('button', name='Revise preview').click()
        await page.wait_for_url(f'{live_web}/jobs/{JOB}')
        assert len(captured) == 1
        elements = captured[0]['design']['frames'][0]['elements']
        assert len(elements) == 1 and elements[0]['kind'] == 'existing'
        assert elements[0]['comment'] == comment
        assert elements[0]['selector'].startswith('#s-hook > ')
        assert 'KI-Prototypv0.1' in elements[0]['label']
        assert not errors
        await browser.close()
