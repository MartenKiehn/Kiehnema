"""Web workflow and boundaries, without Bedrock calls."""

import asyncio
import base64
import io
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


async def preview_ready(frame):
    # Playwright's in-page polling uses eval, which the preview's CSP intentionally rejects.
    async with asyncio.timeout(30):
        while not await frame.evaluate("window.__ready === true"):
            await asyncio.sleep(0.05)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr('app.web.main.memory_policy', lambda: None)
    from app.security import accounts as a
    import pyotp
    draft = a.enrollment('admin', 'test password with fifteen characters', two_factor=True)
    person = a.finish_enrollment(draft, pyotp.TOTP(draft['totp']).now(), setup=True)
    identity_token = a.current_identity.set(person)
    session_token = a.new_session(person)
    session = a.get_session(session_token)
    for cls in (TestClient, httpx.AsyncClient):
        original = cls.__init__
        def initialize(self, *args, _original=original, **kwargs):
            kwargs['headers'] = {'X-CSRF-Token': session.csrf, **kwargs.get('headers', {})}
            kwargs['cookies'] = {'kiehnema-local': session_token, **kwargs.get('cookies', {})}
            _original(self, *args, **kwargs)
        monkeypatch.setattr(cls, '__init__', initialize)
    from playwright.async_api import BrowserContext, Browser
    original_page = BrowserContext.new_page
    async def authenticated_page(context, *args, **kwargs):
        await context.add_cookies([{'name': 'kiehnema-local', 'value': session_token,
                                   'domain': '127.0.0.1', 'path': '/', 'httpOnly': True, 'sameSite': 'Strict'}])
        return await original_page(context, *args, **kwargs)
    monkeypatch.setattr(BrowserContext, 'new_page', authenticated_page)
    original_browser_page = Browser.new_page
    async def authenticated_browser_page(browser, *args, **kwargs):
        page = await original_browser_page(browser, *args, **kwargs)
        await page.context.add_cookies([{'name': 'kiehnema-local', 'value': session_token,
                                       'domain': '127.0.0.1', 'path': '/', 'httpOnly': True, 'sameSite': 'Strict'}])
        return page
    monkeypatch.setattr(Browser, 'new_page', authenticated_browser_page)
    yield tmp_path
    a.current_identity.reset(identity_token)
    a.sessions.clear()


@pytest.fixture
def review_job(workspace, monkeypatch):
    jobs.create_job("https://example.com/", 30, job_id=JOB)
    path = jobs.jobs_dir() / JOB
    (path / "concept.json").write_text(json.dumps(CONCEPT))
    (path / "check.json").write_text(json.dumps({"ok": True, "issues": []}))
    (path / "profile.json").write_text(json.dumps({"facts": [{"id": "f1", "text": "Robots that work.", "quote": "Robots that work.", "source_url": "https://example.com/about"}]}))
    build_site(Path("examples/they"), workspace / "fixture" / "site")
    path.job.import_tree(workspace / "fixture")
    frames = []
    for fmt in ("feed", "story"):
        file = f"keyframes/r0/{fmt}-001.00s.png"
        target = path / file
        target.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        import io
        image = io.BytesIO()
        Image.new("RGB", (8, 10), "green").save(image, "PNG")
        target.write_bytes(image.getvalue())
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
    response = client.get("/")
    assert "img-src 'self' data: blob:;" in response.headers['Content-Security-Policy']
    html = response.text
    assert "Director’s desk · Kiehnema" in html and 'aria-label="Kiehnema home"' in html
    assert "Motion Studio" not in html and "kiehnema.svg" in html
    assert "New video" in html and 'name="url"' in html and 'name="duration"' in html and 'name="description"' in html
    assert 'name="allow_client_logos"' in html
    assert 'checked' not in re.search(r'<input[^>]*name="allow_client_logos"[^>]*>', html)[0]
    assert 'name="source_mode" value="url" checked' in html
    assert 'name="source_mode" value="files"' in html and 'id="source-files"' in html
    assert 'data-source-showcase="url" aria-labelledby="website-showcase-title"' in html
    assert 'data-source-showcase="files" aria-labelledby="showcase-title" hidden' in html
    assert 'YOUR BRAND INGREDIENTS' in html and 'FROM FILES TO VIDEO' in html
    assert client.get('/static/source-landscape.webp').status_code == 200
    assert '<dialog id="source-confirm"' in html and 'aria-labelledby="source-confirm-title"' in html
    assert 'data-confirm-cancel autofocus' in html and 'Process all pages' in html
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


def source_upload(name='Campaign.png', format='PNG'):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (32, 24), '#c8a565').save(buffer, format)
    return {'name': name, 'content': base64.b64encode(buffer.getvalue()).decode()}


def test_file_job_upload_stores_private_sources_and_displays_names(client, monkeypatch):
    queued = []
    monkeypatch.setattr('app.web.main.enqueue', queued.append)
    monkeypatch.setattr('app.web.main.UrlGuard.check', lambda *args: pytest.fail('Files must not crawl a URL'))
    uploaded = [source_upload('../Campaign.png'), source_upload('Brand guide.pdf', 'PDF')]
    response = client.post('/jobs', json={'files': uploaded, 'duration': 25,
                                         'description': 'Private launch brief', 'allow_client_logos': True})
    assert response.status_code == 201, response.text
    location = response.json()['location']
    job_id = location.rsplit('/', 1)[1]
    assert queued == [job_id]
    path = jobs.jobs_dir() / job_id
    brief = json.loads((path / 'request.json').read_text())
    assert brief['source_mode'] == 'files' and brief['url'] == ''
    assert brief['duration'] == 25 and brief['allow_client_logos'] is True
    assert brief['description'] == 'Private launch brief'
    assert [item['name'] for item in brief['source_files']] == ['Campaign.png', 'Brand guide.pdf']
    assert [item['file'] for item in brief['source_files']] == ['sources/01.png', 'sources/02.pdf']
    assert (path / 'sources/01.png').read_bytes().startswith(b'\x89PNG')
    assert (path / 'sources/02.pdf').read_bytes() == base64.b64decode(uploaded[1]['content'])
    for source in brief['source_files']:
        download = client.get(location + '/files/' + source['file'])
        assert download.status_code == 200
        assert download.headers['content-disposition'].startswith('attachment;')
        assert download.content == (path / source['file']).read_bytes()
    (path / 'sources/99.pdf').write_bytes(b'%PDF-unlisted')
    assert client.get(location + '/files/sources/99.pdf').status_code == 404
    for blob in path.job.directory.iterdir():
        encrypted = blob.read_bytes()
        assert not encrypted.startswith((b'\x89PNG', b'%PDF-'))
        assert b'Private launch brief' not in encrypted and b'Brand guide.pdf' not in encrypted
    for page in ('/jobs', location):
        html = client.get(page).text
        assert 'Campaign.png' in html and 'Brand guide.pdf' in html
    write_status(path, 'crawl', 'Reading uploaded sources')
    assert 'Reading your files' in client.get(location + '/progress').text


@pytest.mark.parametrize('files', [
    [],
    [{'name': 'broken.png', 'content': 'not-base64!'}],
    [{'name': 'empty.png', 'content': ''}],
    [{'name': 'fake.pdf', 'content': base64.b64encode(b'not a PDF').decode()}],
    [{'name': 'fake.png', 'content': base64.b64encode(b'not an image').decode()}],
])
def test_file_job_rejects_invalid_upload_without_creating_job(client, monkeypatch, files):
    queued = []
    monkeypatch.setattr('app.web.main.enqueue', queued.append)
    response = client.post('/jobs', json={'files': files})
    assert response.status_code == 422 and response.json()['error']
    assert not queued and not list(jobs.jobs_dir().iterdir())


def test_file_job_rejects_entire_batch_and_invalid_brief(client, monkeypatch):
    monkeypatch.setattr('app.web.main.enqueue', lambda _: pytest.fail('Invalid uploads must not be queued'))
    valid = source_upload()
    invalid = {'name': 'script.svg', 'content': base64.b64encode(b'<svg/>').decode()}
    for payload in ({'files': [valid, invalid]}, {'files': [valid] * 11},
                    {'files': [valid], 'duration': 61}, {'files': [valid], 'description': 'x' * 4001}):
        response = client.post('/jobs', json=payload)
        assert response.status_code == 422 and response.json()['error']
        assert not list(jobs.jobs_dir().iterdir())


def test_file_upload_requires_session_csrf_and_same_origin(client, monkeypatch):
    monkeypatch.setattr('app.web.main.enqueue', lambda _: pytest.fail('Rejected requests must not be queued'))
    payload = {'files': [source_upload()]}
    for headers in ({'X-CSRF-Token': ''}, {'X-CSRF-Token': 'invalid'}, {'Origin': 'https://elsewhere.example'}):
        assert client.post('/jobs', json=payload, headers=headers).status_code == 403
    client.cookies.clear()
    assert client.post('/jobs', json=payload).status_code == 401
    assert not list(jobs.jobs_dir().iterdir())


def test_file_job_queue_rejection_cleans_saved_upload(client, monkeypatch):
    from fastapi import HTTPException
    from app.security import accounts

    def full_queue(_):
        raise HTTPException(429, 'The job queue is full.')

    monkeypatch.setattr('app.web.main.enqueue', full_queue)
    response = client.post('/jobs', json={'files': [source_upload()]})
    assert response.status_code == 429
    assert not list(jobs.jobs_dir().iterdir())
    with accounts.database() as con:
        assert con.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0
    assert not list((settings.data_dir / 'secure/objects').iterdir())


def test_uploaded_sources_remain_bound_to_their_owner(client, monkeypatch):
    import pyotp
    from app.security import accounts

    monkeypatch.setattr('app.web.main.enqueue', lambda _: None)
    response = client.post('/jobs', json={'files': [source_upload()]})
    location = response.json()['location']
    invitation = accounts.invite_user(accounts.identity(), 'another-editor')
    draft = accounts.enrollment('another-editor', 'Another synthetic password!', two_factor=True)
    person = accounts.finish_enrollment(draft, pyotp.TOTP(draft['totp']).now(), invite=invitation)
    session_token = accounts.new_session(person)
    session = accounts.get_session(session_token)
    client.cookies.clear()
    client.cookies.set('kiehnema-local', session_token)
    client.headers['X-CSRF-Token'] = session.csrf
    assert client.get(location).status_code == 404
    assert client.get(location + '/files/sources/01.png').status_code == 404
    assert 'Campaign.png' not in client.get('/jobs').text


def test_file_upload_body_limit_is_enforced_before_parsing(client, monkeypatch):
    monkeypatch.setattr('app.web.main.enqueue', lambda _: pytest.fail('Oversized requests must not be queued'))
    from app.pipeline.sources import MAX_TOTAL_BYTES
    response = client.post('/jobs', content=b' ' * ((MAX_TOTAL_BYTES * 4 + 2) // 3 + 1024 * 1024 + 1),
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert not list(jobs.jobs_dir().iterdir())


def test_file_job_storage_failure_cleans_partial_sources(workspace, monkeypatch):
    from app.security import vault, accounts
    original = vault.VaultPath.write_bytes

    def failing_write(path, content):
        if path.relative == 'sources/02.png':
            raise OSError('Synthetic write failure')
        return original(path, content)

    monkeypatch.setattr(vault.VaultPath, 'write_bytes', failing_write)
    image = base64.b64decode(source_upload()['content'])
    with pytest.raises(OSError, match='Synthetic write failure'):
        jobs.create_job('', 30, source_files=[('one.png', image), ('two.png', image)])
    assert not list(jobs.jobs_dir().iterdir())
    with accounts.database() as con:
        assert con.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0
    assert not list((settings.data_dir / 'secure/objects').iterdir())


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "http://localhost:3000/", "http://[::1]:8000/"])
def test_create_job_rejects_local_website(client, monkeypatch, url):
    queued = []
    monkeypatch.setattr("app.web.main.enqueue", queued.append)
    response = client.post("/jobs", data={"url": url}, follow_redirects=False)
    assert response.status_code == 422
    assert queued == []


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


@pytest.mark.parametrize(('percent', 'eta', 'message'), [
    (0, None, 'Estimating remaining time'),
    (25, 121, 'about 3 min'),
    (75, 30, 'less than a minute'),
    (99, None, 'Finalizing videos'),
])
def test_render_progress_bar_and_eta(client, percent, eta, message):
    job_id = jobs.create_job('https://example.com/', 30)
    write_status(jobs.jobs_dir() / job_id, 'render', 'Rendering video',
                 render_progress={'percent': percent, 'eta_seconds': eta, 'format': 'feed' if percent < 50 else 'story'})
    html = client.get(f'/jobs/{job_id}/progress').text
    assert f'value="{percent}"' in html and f'{percent}%</strong>' in html
    assert message in html and '<label for="render-completion">' in html
    assert 'Video 2 starts at 50%' in html and 'class="render-midpoint"' in html
    assert ('Rendering video 1 of 2 · Feed (4:5)' if percent < 50 else 'Rendering video 2 of 2 · Story (9:16)') in html
    assert ' frames' not in html and 'frame count' not in html and 'progress-message' not in html


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


@pytest.mark.browser
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
        # The redirected editor's deferred scripts must be ready before clicking its tabs.
        async with page.expect_event("domcontentloaded"):
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
    # Encrypted objects expose no filesystem symlink operation.
    assert client.get(f"/preview/{JOB}/composition/composition.js").status_code == 404
    for origin in ("null", "https://evil.example", ""):
        assert client.post("/jobs", headers={"Origin": origin}, data={"url": "https://example.com"}).status_code == 403
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_delete_removes_files_and_checkpoint(review_job, client, monkeypatch):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    deleted = AsyncMock()
    monkeypatch.setattr(AsyncSqliteSaver, "adelete_thread", deleted)
    assert "Delete this video?" in client.get(f"/jobs/{JOB}/delete").text
    response = client.post(f"/jobs/{JOB}/delete", follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/videos'
    with __import__("app.security.accounts", fromlist=["database"]).database() as con:
        assert con.execute("SELECT 1 FROM files WHERE job=?", (JOB,)).fetchone() is None
    assert not review_job.exists()


def test_restart_marks_unfinished_work_without_restarting_it(workspace):
    job_id = jobs.create_job("https://example.com", 30)
    path = jobs.jobs_dir() / job_id
    write_status(path, "compose", "Writing code")
    from app.security.accounts import database
    with database() as con:
        con.execute("UPDATE jobs SET active=1 WHERE id=?", (job_id,))
    with TestClient(app, base_url=ORIGIN) as client:
        assert "app stopped" in client.get(f"/jobs/{job_id}").text
        assert not app.state.tasks
        assert json.loads((path / "status.json").read_text())["status"] == "interrupted"


@pytest.mark.browser
async def test_progress_insight_scroll_survives_polling_as_results_arrive(review_job, tmp_path):
    from playwright.async_api import async_playwright, expect

    async with app.router.lifespan_context(app), async_playwright() as pw:
        (review_job / "profile.json").write_text(json.dumps({"facts": [
            {"text": f"Verified website fact {i}: built for small factories.", "source_url": "https://example.com/about"}
            for i in range(15)]}))
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
                facts_region = page.get_by_role('region', name='What your website tells us')
                assert await facts_region.locator('li').count() == 15
                assert await page.locator('.insight-card details').count() == 0
                assert await facts_region.evaluate('el => el.scrollHeight > el.clientHeight')
                await facts_region.focus()
                await facts_region.evaluate('el => { el.scrollTop = 150; }')
                await expect(facts_region).to_be_focused()
                scroll = await facts_region.evaluate('el => el.scrollTop')
                assert scroll > 0
                facts = await facts_region.element_handle()
                (review_job / "brand").mkdir()
                (review_job / "brand/brand.json").write_text(json.dumps({"colors": [{"hex": "#ffffff"}, {"hex": "#19285a"}]}))
                write_status(review_job, "brand", "Brand assets found")
                await page.get_by_role("heading", name="Finding your visual identity").wait_for(timeout=10000)
                assert await facts.evaluate("el => el.isConnected && el === document.activeElement")
                assert await facts.evaluate('el => el.scrollTop') == scroll
                assert await activity.evaluate("el => el.isConnected && el.open")
                assert "White" in await page.locator(".insight-colours").inner_text()
                assert "#ffffff" not in await page.locator(".discoveries").inner_text()
                await page.screenshot(path=str(tmp_path / "progress-desktop.png"), full_page=True)
                assert "Find the facts" in await page.locator(".progress-next").inner_text()
                assert await page.locator('[aria-current="step"]').text_content() == "2Brand"
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert await facts_region.evaluate('el => el.scrollHeight > el.clientHeight && el.clientHeight <= 352')
                await page.screenshot(path=str(tmp_path / "progress-mobile.png"), full_page=True)
                (review_job / 'profile.json').write_text(json.dumps({'facts': [
                    {'text': 'A long website fact for smaller screens. ' * 10, 'source_url': 'https://example.com/about'}
                    for _ in range(3)]}))
                await expect(facts_region.locator('li')).to_have_count(3)
                await expect(facts_region).to_have_attribute('tabindex', '0')
                assert await facts_region.evaluate('el => el.scrollHeight > el.clientHeight')
                await facts_region.focus()
                await expect(facts_region).to_be_focused()
                await page.emulate_media(reduced_motion="reduce")
                assert await page.locator(".live-dot").evaluate("el => getComputedStyle(el).animationName") == "none"
                write_status(review_job, 'render', 'Rendering feed video',
                             render_progress={'percent': 25, 'eta_seconds': 121, 'format': 'feed'})
                await expect(page.locator('#render-completion')).to_have_attribute('value', '25')
                assert 'about 3 min' in await page.locator('.render-eta').inner_text()
                await expect(page.locator('.render-current')).to_have_text('Rendering video 1 of 2 · Feed (4:5)')
                write_status(review_job, 'render', 'Rendering story video',
                             render_progress={'percent': 75, 'eta_seconds': 30, 'format': 'story'})
                await expect(page.locator('#render-completion')).to_have_attribute('value', '75')
                assert '75%' in await page.locator('.render-progress-heading').inner_text()
                assert 'less than a minute' in await page.locator('.render-eta').inner_text()
                assert await page.get_by_role('progressbar', name='Overall progress').count() == 1
                await expect(page.locator('.render-current')).to_have_text('Rendering video 2 of 2 · Story (9:16)')
                await expect(page.locator('#render-format-boundary')).to_contain_text('Video 2 starts at 50%')
                await page.reload()
                assert await page.locator('#render-completion').get_attribute('value') == '75'
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                bar = await page.locator('#render-completion').bounding_box()
                marker = await page.locator('.render-midpoint').bounding_box()
                assert abs(marker['x'] + marker['width'] / 2 - (bar['x'] + bar['width'] / 2)) < 1
                await page.screenshot(path=str(tmp_path / 'render-progress-mobile.png'), full_page=True)
                await page.set_viewport_size({'width': 1440, 'height': 1000})
                await page.screenshot(path=str(tmp_path / 'render-progress-desktop.png'), full_page=True)
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
            (path / name).write_bytes((review_job / name).read_bytes())
        for name in ("site", "keyframes"):
            for entry in (review_job / name).glob("**"):
                (path / entry.relative).write_bytes(entry.read_bytes())
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
            preview_frame = next(f for f in page.frames if "/preview/" in f.url and "format=feed" in f.url)
            await preview_ready(preview_frame)
            await page.locator("#timeline-play").click()
            await expect(page.locator("#timeline-play")).to_have_text("Pause both previews")
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
            await preview_ready(frame)
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
                await preview_ready(frame)
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
        await page.keyboard.press('ControlOrMeta+a')
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


@pytest.mark.browser
async def test_long_username_account_form_fits_mobile_and_desktop(review_job, live_web, tmp_path):
    from playwright.async_api import async_playwright, expect
    from app.security import accounts

    person = accounts.identity()
    name = 'w' * 64
    accounts.change_username(person, name)
    token = accounts.new_session(person)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 390, 'height': 844})
            await page.context.add_cookies([{'name': 'kiehnema-local', 'value': token,
                                            'domain': '127.0.0.1', 'path': '/', 'httpOnly': True, 'sameSite': 'Strict'}])
            await page.goto(live_web + '/account?username_changed=1')
            form = page.get_by_role('form', name='Change username', exact=True)
            await expect(form.get_by_label('New username')).to_have_value(name)
            await expect(form.get_by_label('Current password')).to_be_visible()
            await expect(form.get_by_label('Authenticator code')).to_be_visible()
            await expect(page.get_by_role('complementary', name='Your username is updated.')).to_be_visible()
            for width, height, label in [(390, 844, 'mobile'), (1440, 1000, 'desktop')]:
                await page.set_viewport_size({'width': width, 'height': height})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.screenshot(path=str(tmp_path / f'account-username-{label}.png'), full_page=True)
        finally:
            await browser.close()


@pytest.mark.browser
async def test_provider_connection_survives_late_page_load_and_resets_on_history_restore(review_job, live_web, monkeypatch):
    from playwright.async_api import async_playwright, expect
    monkeypatch.setattr(settings, 'provider_setup_required', True)
    monkeypatch.setattr('app.web.provider.test_connection', lambda config: None)
    image_release, test_release, test_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def delayed_image(route):
        await image_release.wait()
        await route.continue_()

    async def delayed_test(route):
        test_started.set()
        await test_release.wait()
        await route.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.add_init_script("window.providerPageShown = false; addEventListener('pageshow', () => { window.providerPageShown = true; });")
        await page.route('**/static/kiehnema.svg', delayed_image)
        await page.route('**/admin/provider/test', delayed_test)
        try:
            await page.goto(live_web + '/admin/provider', wait_until='domcontentloaded')
            await page.get_by_label('AWS authentication').select_option('environment')
            await page.get_by_label('Model / inference profile ID').fill('test-model')
            assert not await page.evaluate('window.providerPageShown')
            await page.get_by_role('button', name='Test connection').click()
            await asyncio.wait_for(test_started.wait(), 5)
            image_release.set()
            await asyncio.wait_for(page.evaluate("""() => window.providerPageShown || new Promise(resolve => {
                addEventListener('pageshow', () => resolve(true), {once: true});
            })"""), 5)
            test_release.set()
            feedback = page.locator('[data-provider-test-status]')
            save = page.get_by_role('button', name='Save AI settings')
            await expect(feedback).to_contain_text('Connection successful')
            await expect(save).to_be_visible()
            await page.evaluate("dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
            await expect(save).to_be_hidden()
            await expect(feedback).to_contain_text('Test the connection before saving')
        finally:
            image_release.set()
            test_release.set()
            await browser.close()


@pytest.mark.browser
async def test_admin_provider_form_switching_and_secret_preservation(review_job, live_web, monkeypatch, tmp_path):
    from playwright.async_api import async_playwright, expect
    from app.llm import provider
    monkeypatch.setattr(settings, 'provider_setup_required', True)
    tested = []
    def probe(config):
        from app.llm.client import LLMError
        tested.append(config.public())
        if config.api_key.get_secret_value() == 'invalid-synthetic-key':
            raise LLMError('Connection test failed. Check credentials and model access.')
    monkeypatch.setattr('app.web.provider.test_connection', probe)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page.clock.install()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(live_web)
        await expect(page.get_by_role('button', name='Create preview')).to_be_disabled()
        await expect(page.get_by_label('Website URL')).to_be_disabled()
        await expect(page.get_by_label('Creative direction optional')).to_be_disabled()
        await expect(page.get_by_label('Duration seconds', exact=True)).to_be_disabled()
        await expect(page.get_by_role('heading', name='Closed set.')).to_be_visible()
        await expect(page.get_by_role('link', name='Set up AI provider')).to_be_visible()
        await page.screenshot(path=str(tmp_path / 'provider-lock-mobile.png'), full_page=True)
        await page.get_by_role('link', name='Backstage', exact=True).click()
        await page.get_by_role('link', name='AI provider', exact=True).click()
        await expect(page.get_by_role('button', name='Save AI settings')).to_be_hidden()
        await page.get_by_label('Provider', exact=True).select_option('openai')
        await expect(page.get_by_label('API key', exact=True)).to_be_visible()
        await expect(page.get_by_label('AWS region')).to_be_hidden()
        await page.get_by_label('Model / inference profile ID').fill('test-model')
        await page.get_by_role('button', name='Test connection').click()
        assert not tested and provider.load() is None
        await expect(page.get_by_role('button', name='Save AI settings')).to_be_hidden()
        await page.get_by_label('API key', exact=True).fill('invalid-synthetic-key')
        await page.get_by_role('button', name='Test connection').click()
        feedback = page.locator('[data-provider-test-status]')
        await expect(feedback).to_contain_text('Connection test failed')
        await expect(page.get_by_role('button', name='Save AI settings')).to_be_hidden()
        await page.get_by_label('API key', exact=True).fill('synthetic-browser-key')
        await page.get_by_role('button', name='Test connection').click()
        await expect(feedback).to_contain_text('Connection successful')
        assert provider.load() is None
        await expect(page.get_by_role('button', name='Save AI settings')).to_be_visible()
        await page.clock.fast_forward(300001)
        await expect(page.get_by_role('button', name='Save AI settings')).to_be_hidden()
        await expect(feedback).to_contain_text('expired')
        await page.get_by_label('Model / inference profile ID').fill('changed-model')
        await expect(page.get_by_role('button', name='Save AI settings')).to_be_hidden()
        await page.get_by_label('Model / inference profile ID').fill('test-model')
        await page.get_by_role('button', name='Test connection').click()
        await expect(feedback).to_contain_text('Connection successful')
        await page.get_by_role('button', name='Save AI settings').click()
        summary = page.get_by_role('complementary', name='Your AI setup is saved.')
        await expect(summary.get_by_role('status')).to_contain_text('Settings saved')
        await expect(summary).to_contain_text('OpenAI')
        await expect(summary).to_contain_text('test-model')
        await expect(summary).to_contain_text('64,000 tokens')
        await expect(page.get_by_label('API key', exact=True)).to_have_value('')
        assert 'synthetic-browser-key' not in await page.content()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(tmp_path / 'provider-mobile.png'), full_page=True)
        await page.set_viewport_size({'width': 1440, 'height': 1000})
        await page.screenshot(path=str(tmp_path / 'provider-desktop.png'), full_page=True)
        await page.goto(live_web + '/admin/provider')
        await expect(summary).to_be_visible()
        await expect(summary.get_by_role('status')).not_to_contain_text('Settings saved')
        await expect(page.get_by_label('Provider', exact=True)).to_be_hidden()
        await page.locator('summary', has_text='Edit provider settings').click()
        await page.get_by_label('Provider', exact=True).select_option('anthropic')
        await expect(summary).to_contain_text('OpenAI')
        await page.get_by_label('Provider', exact=True).select_option('openai')
        await page.get_by_role('button', name='Test connection').click()
        await expect(feedback).to_contain_text('Connection successful')
        await page.get_by_role('button', name='Save AI settings').click()
        await expect(summary.get_by_role('status')).to_contain_text('Settings saved')
        assert provider.load().api_key.get_secret_value() == 'synthetic-browser-key'
        await page.locator('summary', has_text='Edit provider settings').click()
        await page.get_by_label('Provider', exact=True).select_option('bedrock')
        await page.get_by_label('AWS authentication').select_option('environment')
        await expect(page.get_by_label('AWS access key ID')).to_be_hidden()
        await expect(page.get_by_role('button', name='Save AI settings')).to_be_hidden()
        await page.get_by_role('button', name='Test connection').click()
        await expect(feedback).to_contain_text('Connection successful')
        await page.get_by_role('button', name='Save AI settings').click()
        await expect(summary.get_by_role('status')).to_contain_text('Settings saved')
        assert provider.load().provider == 'bedrock' and not provider.load().public()['credentials_saved']
        await page.goto(live_web)
        await expect(page.get_by_role('button', name='Create preview')).to_be_enabled()
        await expect(page.get_by_label('Website URL')).to_be_enabled()
        await expect(page.get_by_role('heading', name='Closed set.')).to_have_count(0)
        assert not errors
        await browser.close()


@pytest.mark.parametrize(('route', 'active'), [
    ('/', '/'), ('/videos', '/videos'), ('/jobs/' + JOB, '/videos'), ('/account', '/account'),
    ('/admin', '/admin'), ('/admin/provider', '/admin/provider'), ('/admin/updates', '/admin/updates'),
    ('/admin/video-requests', '/admin/video-requests'),
])
def test_navigation_identifies_one_current_section(review_job, client, route, active):
    response = client.get(route)
    assert response.status_code == 200
    nav = re.search(r'<nav aria-label="Main navigation">(.*?)</nav>', response.text, re.S)[1]
    assert re.findall(r'<a href="([^"]+)" aria-current="page"', nav) == ['/admin' if active.startswith('/admin') else active]
    if active.startswith('/admin'):
        tabs = re.search(r'<nav class="backstage-tabs".*?</nav>', response.text, re.S)[0]
        assert re.findall(r'<a href="([^"]+)" aria-current="page"', tabs) == [active]


def test_user_form_error_keeps_users_navigation_active(client):
    response = client.post('/admin/invite', data={'username':'admin'})
    assert 'role="alert"' in response.text
    nav = re.search(r'<nav aria-label="Main navigation">(.*?)</nav>', response.text, re.S)[1]
    assert re.findall(r'<a href="([^"]+)" aria-current="page"', nav) == ['/admin']


def test_video_library_is_separate_from_new_video_form(review_job, client):
    home = client.get('/').text
    assert 'name="url"' in home and 'id="jobs"' not in home
    page = client.get('/videos')
    assert page.status_code == 200 and '<title>Your videos · Kiehnema</title>' in page.text
    assert 'YOUR SCREENING ROOM' in page.text and f'href="/jobs/{JOB}"' in page.text
    assert 'hx-get="/jobs"' in page.text and 'name="url"' not in page.text
    assert 'href="/videos">← All videos' in client.get(f'/jobs/{JOB}').text
    fragment = client.get('/jobs')
    assert '<html' not in fragment.text and f'href="/jobs/{JOB}"' in fragment.text


def test_empty_video_library_links_back_to_directors_desk(client):
    page = client.get('/videos')
    assert page.status_code == 200
    assert 'A blank canvas, for now.' in page.text
    assert '<a href="/">Create your first video</a>' in page.text
    assert 'website above' not in page.text
    error = client.post('/jobs', data={'url':'not-a-website'})
    nav = re.search(r'<nav aria-label="Main navigation">(.*?)</nav>', error.text, re.S)[1]
    assert re.findall(r'<a href="([^"]+)" aria-current="page"', nav) == ['/']


@pytest.mark.browser
async def test_directors_desk_and_video_library_navigation(review_job, live_web, tmp_path):
    from playwright.async_api import async_playwright, expect
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={'width':1440, 'height':1000})
        await page.goto(live_web)
        await expect(page.get_by_role('link', name='Director’s desk', exact=True)).to_have_attribute('aria-current', 'page')
        await expect(page.locator('#jobs')).to_have_count(0)
        await page.get_by_role('link', name='Your videos', exact=True).click()
        await expect(page).to_have_url(live_web + '/videos')
        await expect(page.locator('#jobs .job-row')).to_have_count(1)
        await expect(page.get_by_label('Website URL')).to_have_count(0)
        await expect(page.get_by_role('link', name='Your videos', exact=True)).to_have_attribute('aria-current', 'page')
        await page.screenshot(path=str(tmp_path / 'video-library-desktop.png'), full_page=True)
        await page.locator('.job-link').click()
        await expect(page).to_have_url(f'{live_web}/jobs/{JOB}')
        await page.get_by_role('link', name='All videos').click()
        await expect(page).to_have_url(live_web + '/videos')
        await page.set_viewport_size({'width':390, 'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.get_by_role('link', name='Director’s desk', exact=True).click()
        await expect(page).to_have_url(live_web + '/')
        await page.get_by_role('link', name='Your videos', exact=True).click()
        await expect(page).to_have_url(live_web + '/videos')
        await page.locator('nav a[href="/account"]').click()
        await expect(page).to_have_url(live_web + '/account')
        await page.get_by_role('link', name='Your videos', exact=True).click()
        await expect(page).to_have_url(live_web + '/videos')
        await page.get_by_role('link', name='Backstage', exact=True).click()
        tabs = page.get_by_role('navigation', name='Backstage sections')
        await expect(tabs.get_by_role('link', name='Users', exact=True)).to_have_attribute('aria-current', 'page')
        await tabs.get_by_role('link', name='AI provider', exact=True).click()
        await expect(page).to_have_url(live_web + '/admin/provider')
        await tabs.get_by_role('link', name='Updates', exact=True).click()
        await expect(page).to_have_url(live_web + '/admin/updates')
        await page.route('**/admin/updates/status', lambda route: route.fulfill(
            json={'busy': False, 'available': [{'version': '0.2.0'}]}))
        await page.reload()
        await expect(page.get_by_role('navigation', name='Main navigation').get_by_role(
            'link', name='Backstage · Update available', exact=True)).to_be_visible()
        await expect(tabs.get_by_role('link', name='Updates', exact=True)).to_have_attribute(
            'href', '/admin/updates')
        await page.get_by_role('link', name='Your videos', exact=True).click()
        await page.screenshot(path=str(tmp_path / 'video-library-mobile.png'), full_page=True)
        await page.get_by_role('link', name='New video', exact=True).click()
        await expect(page.get_by_role('heading', name='New video', exact=True)).to_be_visible()
        await expect(page.get_by_role('link', name='Director’s desk', exact=True)).to_be_visible()
        # Visible HTML can precede the stylesheet after a navigation.
        await page.wait_for_load_state('load')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await browser.close()


@pytest.mark.browser
@pytest.mark.parametrize('role', ['admin', 'manager', 'user'])
async def test_delete_disabled_account_in_browser(review_job, live_web, tmp_path, role):
    from playwright.async_api import async_playwright, expect
    from app.security import accounts as a
    admin = a.identity()
    target = a.finish_enrollment(a.enrollment('delete-fixture', 'Synthetic deletion test password!'),
                                invite=a.invite_user(admin, 'delete-fixture'))
    a.set_role(admin, target.id, role)
    token = a.current_identity.set(target)
    try:
        jid = jobs.create_job('https://example.com', 30)
        target_path = jobs.jobs_dir() / jid
        (target_path / 'private.txt').write_text('Synthetic target content')
    finally:
        a.current_identity.reset(token)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 390, 'height': 844})
            await page.goto(live_web + '/admin')
            row = page.locator('.users-account').filter(has=page.get_by_role('heading', name=target.username, exact=True))
            await row.locator('summary').click()
            await expect(row.get_by_role('link', name='Delete permanently')).to_have_count(0)
            await row.get_by_role('button', name='Disable ' + target.username, exact=True).click()
            await row.locator('summary').click()
            await row.get_by_role('link', name='Delete permanently', exact=True).click()
            await expect(page.get_by_role('heading', name='Delete ' + target.username + '?', exact=True)).to_be_visible()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.get_by_label('Type ' + target.username + ' to confirm', exact=True).fill('wrong-name')
            await page.get_by_role('button', name='Delete account permanently', exact=True).click()
            await expect(page.get_by_role('alert')).to_contain_text('exact username')
            await page.screenshot(path=str(tmp_path / ('delete-account-' + role + '.png')), full_page=True)
            await page.get_by_label('Type ' + target.username + ' to confirm', exact=True).fill(target.username)
            await page.get_by_role('button', name='Delete account permanently', exact=True).click()
            await expect(page).to_have_url(live_web + '/admin')
            await expect(page.get_by_role('heading', name=target.username, exact=True)).to_have_count(0)
            assert not target_path.job.directory.exists()
            assert review_job.job.directory.exists()
            with a.database() as con:
                assert not con.execute('SELECT 1 FROM users WHERE id=?', (target.id,)).fetchone()
        finally:
            await browser.close()


@pytest.mark.parametrize('passed', [False, True])
def test_review_check_guidance_explains_feedback_and_separates_visual_notes(review_job, client, monkeypatch, passed):
    jobs.job_state.return_value['values']['check']['ok'] = passed
    (review_job / 'check.json').write_text(json.dumps({'ok': passed, 'issues': [] if passed else [
        {'severity': 'error', 'code': 'nondeterministic', 'message': 'Frame differs depending on seek order.', 'format': 'feed', 't': 18.3}]}))
    (review_job / 'qa.json').write_text(json.dumps({'summary': 'Nothing here blocks the render.', 'issues': []}))
    html = client.get(f'/jobs/{JOB}').text
    assert ('One more take before rendering' in html) == (not passed)
    assert ('Write correction feedback' in html) == (not passed)
    assert ('Nothing here blocks the render.' in html) == passed
    if not passed:
        assert 'class="info-box" aria-labelledby="review-checks-title"' in html
        assert 'Describe what needs fixing in <a href="#feedback">Feedback</a>' in html
        assert 'Choose <strong>Revise preview</strong>' in html
        assert 'feed · 18.3 s' in html
        assert 'value="approve" disabled' in html
        assert 'id="feedback" name="feedback" required' not in html
        captured = []
        monkeypatch.setattr('app.web.main.enqueue', lambda jid, decision: captured.append(decision))
        form = {**review_form(client), 'action': 'revise', 'feedback': 'Fix the seek-order error at 18.3 seconds.'}
        assert client.post(f'/jobs/{JOB}/review', data=form, follow_redirects=False).status_code == 303
        assert captured == [{'action': 'feedback', 'text': form['feedback']}]


@pytest.mark.browser
async def test_blocked_review_feedback_jump_and_mobile_layout(review_job, live_web, tmp_path):
    from playwright.async_api import async_playwright, expect
    jobs.job_state.return_value['values']['check']['ok'] = False
    (review_job / 'check.json').write_text(json.dumps({'ok': False, 'issues': [
        {'severity': 'error', 'code': 'nondeterministic', 'message': 'Frame differs depending on seek order.', 'format': 'feed', 't': 18.3}]}))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            for width, height in [(1440, 1000), (390, 844)]:
                await page.set_viewport_size({'width': width, 'height': height})
                await page.goto(f'{live_web}/jobs/{JOB}')
                await expect(page.get_by_role('heading', name='One more take before rendering')).to_be_visible()
                await page.get_by_role('link', name='Write correction feedback').click()
                await expect(page.locator('#feedback')).to_be_focused()
                await expect(page.get_by_role('button', name='Approve & render')).to_be_disabled()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.screenshot(path=str(tmp_path / f'correction-feedback-{width}.png'), full_page=True)
            jobs.job_state.return_value['values']['check']['ok'] = True
            (review_job / 'check.json').write_text(json.dumps({'ok': True, 'issues': []}))
            await page.reload()
            await expect(page.locator('#review-checks-title')).to_have_count(0)
            await expect(page.get_by_role('button', name='Approve & render')).to_be_enabled()
        finally:
            await browser.close()


@pytest.mark.parametrize('pages', [20, 21])
def test_pdf_page_confirmation_precedes_job_creation(client, monkeypatch, pages):
    from app.security import accounts
    from app.web.main import enqueue
    import pypdfium2 as pdfium
    from app.pipeline.sources import PDF_LOCK
    buffer = io.BytesIO()
    with PDF_LOCK, pdfium.PdfDocument.new() as pdf:
        for _ in range(pages):
            pdf.new_page(100, 100).close()
        pdf.save(buffer)
    queued = []
    def queue(job_id):
        enqueue(job_id)
        queued.append(job_id)
    monkeypatch.setattr('app.web.main.enqueue', queue)
    monkeypatch.setattr(jobs, 'run_job', AsyncMock(return_value={}))
    payload = {'files': [{'name': 'guide.pdf', 'content': base64.b64encode(buffer.getvalue()).decode()}]}
    response = client.post('/jobs', json=payload)
    if pages > 20:
        assert response.status_code == 409
        assert response.json() == {'confirmation_required': True, 'pdf_pages': pages}
        assert not queued and not list(jobs.jobs_dir().iterdir())
        assert client.post('/jobs', json={**payload, 'confirm_pdf_pages': False}).status_code == 409
        assert client.post('/jobs', json={**payload, 'confirm_pdf_pages': 'false'}).status_code == 422
        assert accounts.video_requests() == []
        response = client.post('/jobs', json={**payload, 'confirm_pdf_pages': True})
    assert response.status_code == 201
    assert len(queued) == 1
    assert len(accounts.video_requests()) == 1
    brief = json.loads((jobs.jobs_dir() / queued[0] / 'request.json').read_text())
    assert brief['source_files'][0]['pages'] == pages


def test_single_source_file_can_use_entire_100_mb(client, monkeypatch):
    from app.pipeline.sources import MAX_TOTAL_BYTES
    queued = []
    monkeypatch.setattr('app.web.main.enqueue', queued.append)
    pdf = base64.b64decode(source_upload('large.pdf', 'PDF')['content']).ljust(MAX_TOTAL_BYTES, b' ')
    response = client.post('/jobs', json={'files': [{'name': 'large.pdf', 'content': base64.b64encode(pdf).decode()}]})
    assert response.status_code == 201, response.text
    assert len(queued) == 1
    path = jobs.jobs_dir() / queued[0]
    assert path.job.entry('sources/01.pdf')['size'] == MAX_TOTAL_BYTES
