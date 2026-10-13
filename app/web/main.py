"""Local, single-user FastAPI + HTMX interface for the generation pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.crawl.guard import UrlGuard
from app.pipeline import jobs
from app.pipeline.schemas import SceneChange
from app.pipeline.design import DesignDraft
from app.pipeline.status import write_status
from app.web.insights import job_insights
from app.web.review import CommentRequest, saved_comments, save_comments

HERE = Path(__file__).resolve().parent
TERMINAL = {"review", "done", "failed", "interrupted"}
STEPS = [("crawl", "Website"), ("brand", "Brand"), ("distill", "Facts"), ("concept", "Storyboard"),
         ("compose", "Animation"), ("check", "Checks"), ("keyframes", "Keyframes"), ("qa", "Visual review"),
         ("review", "Your review"), ("render", "Render"), ("done", "Ready")]
log = logging.getLogger(__name__)


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.is_file() else ({} if default is None else default)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.tasks = {}
    # ponytail: serialize whole jobs for local use; use a worker queue if concurrent generation is needed.
    app.state.worker_lock = asyncio.Lock()
    for path in jobs.jobs_dir().glob("*/status.json"):
        if read_json(path).get("status") not in TERMINAL:
            write_status(path.parent, "interrupted", "The app stopped before this job finished. Start a new video; saved files are still available.")
    yield
    tasks = list(app.state.tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Kiehnema", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
app.mount("/static", StaticFiles(directory=HERE.parent / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def safe_url(value: str) -> str:
    try:
        p = urlsplit(value)
        return value if p.scheme in ("http", "https") and p.hostname and not p.username and not p.password else "#"
    except ValueError:
        return "#"


templates.env.filters["safe_url"] = safe_url


@app.middleware("http")
async def local_boundary(request: Request, call_next):
    # Browser POSTs carry Origin, including "null" from the isolated preview. Never accept that origin.
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != str(request.base_url).rstrip("/"):
        return HTMLResponse("This action must be submitted from the local app.", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if not request.url.path.startswith("/preview/"):
        # no-referrer turns native form POST Origins into "null", tripping our own guard.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-src 'self'; media-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    return response


def job_path(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise HTTPException(404, "Job not found.")
    path = jobs.jobs_dir() / job_id
    if not path.is_dir() or path.is_symlink():
        raise HTTPException(404, "Job not found.")
    return path


def job_summary(path: Path) -> dict:
    status = read_json(path / "status.json", {"status": "interrupted", "message": "No status is available."})
    active = path.name in app.state.tasks
    if active and status["status"] in TERMINAL:
        status = {**status, "status": "working", "message": "Finishing this step…"}
    return {**read_json(path / "request.json"), **status, "id": path.name,
            "active": active, "poll": status["status"] not in TERMINAL}


def all_jobs() -> list[dict]:
    return [job_summary(p) for p in sorted(jobs.jobs_dir().iterdir(), reverse=True)
            if p.is_dir() and not p.is_symlink() and (p / "request.json").is_file()]


def revision(path: Path) -> str:
    return hashlib.sha256((path / "status.json").read_bytes() + (path / "concept.json").read_bytes()).hexdigest()


async def job_context(job_id: str) -> dict:
    path = job_path(job_id)
    job = job_summary(path)
    context = {"job": job, "steps": STEPS, "seen": {h["status"] for h in job.get("history", [])},
               "insights": job_insights(path) if job["poll"] else [],
               "concept": {}, "frames": {}, "facts": {}, "check": {}, "qa": {}, "can_approve": False}
    if job["status"] in {"review", "done"}:
        state = await jobs.job_state(job_id)
        values = state["values"]
        concept = read_json(path / "concept.json")
        frames = {}
        for frame in values.get("keyframes", []):
            frames.setdefault(frame["scene"], {})[frame["format"]] = frame
        context.update(concept=concept, frames=frames,
                       facts={f["id"]: f for f in read_json(path / "profile.json").get("facts", [])},
                       check=read_json(path / "check.json"), qa=read_json(path / "qa.json"),
                       board=(path / "brand/board.png").is_file(), preview=(path / "site/index.html").is_file(),
                       can_approve=bool(values.get("check", {}).get("ok")),
                       revision=revision(path) if concept else "")
        context["comments"] = saved_comments(path, context["revision"])
        context["preview_duration"] = max((s["start"] + s["duration"] for s in concept.get("scenes", [])), default=job["duration"])
    return context


async def run_background(job_id: str, decision: dict | None):
    path = job_path(job_id)
    try:
        async with app.state.worker_lock:
            result = await (jobs.run_job(job_id) if decision is None else jobs.resume_job(job_id, decision))
            if result.get("error"):
                write_status(path, "failed", result["error"])
    except asyncio.CancelledError:
        write_status(path, "interrupted", "The app stopped before this job finished. Start a new video; saved files are still available.")
        raise
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        write_status(path, "failed", f"{type(exc).__name__}: {exc}")
    finally:
        app.state.tasks.pop(job_id, None)


def enqueue(job_id: str, decision: dict | None = None):
    if job_id in app.state.tasks:
        raise HTTPException(409, "This job already has work in progress.")
    write_status(job_path(job_id), "queued", "Queued for rendering" if decision and decision["action"] == "approve"
                 else "Waiting for the local worker")
    app.state.tasks[job_id] = asyncio.create_task(run_background(job_id, decision))


class JobRequest(BaseModel):
    url: AnyHttpUrl = Field(max_length=2048)
    duration: int = Field(default=30, ge=15, le=60)
    description: str = Field(default="", max_length=4000)
    allow_client_logos: bool = False
    max_pages: int = Field(default=20, ge=1, le=50)
    max_depth: int = Field(default=2, ge=0, le=5)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"jobs": all_jobs(), "values": {}})


@app.get("/jobs", response_class=HTMLResponse)
async def job_list(request: Request):
    return templates.TemplateResponse(request, "partials/jobs.html", {"jobs": all_jobs()})


@app.post("/jobs")
async def create_job(request: Request):
    form = dict(await request.form())
    try:
        data = JobRequest.model_validate(form)
        url = await asyncio.to_thread(UrlGuard().check, str(data.url))
    except (ValidationError, ValueError) as exc:
        error = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors()) if isinstance(exc, ValidationError) else str(exc)
        return templates.TemplateResponse(request, "index.html", {"jobs": all_jobs(), "values": form, "error": error}, status_code=422)
    job_id = jobs.create_job(url, data.duration, data.description, allow_client_logos=data.allow_client_logos,
                             max_pages=data.max_pages, max_depth=data.max_depth)
    enqueue(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str):
    return templates.TemplateResponse(request, "job.html", await job_context(job_id))


@app.get("/jobs/{job_id}/progress", response_class=HTMLResponse)
async def progress(request: Request, job_id: str):
    context = await job_context(job_id)
    if not context["job"]["poll"]:
        return HTMLResponse(headers={"HX-Redirect": f"/jobs/{job_id}"})
    return templates.TemplateResponse(request, "partials/progress.html", {**context, "poll_response": True})


@app.post("/jobs/{job_id}/review")
async def review(request: Request, job_id: str):
    path = job_path(job_id)
    submitted = await request.form()
    form = dict(submitted)
    if job_id in app.state.tasks or job_summary(path)["status"] != "review":
        raise HTTPException(409, "This job is no longer waiting for review. Open it again to see the current state.")
    context = await job_context(job_id)
    if job_id in app.state.tasks or form.get("revision") != revision(path):
        raise HTTPException(409, "This preview has changed. Reload the job before submitting another review.")
    texts = {t["id"]: t["text"] for s in context["concept"].get("scenes", []) for t in s["texts"]}
    draft = {k[5:]: str(v).strip() for k, v in form.items() if k.startswith("text:")}
    feedback = str(form.get("feedback", "")).strip()
    edits = {k: v for k, v in draft.items() if texts.get(k) != v}
    error = None
    action = form.get("action")
    comments = saved_comments(path, context["revision"])
    scenes = context["concept"].get("scenes", [])
    design_raw = str(form.get("advanced", '{"frames":[]}'))
    design = DesignDraft()
    try:
        design = DesignDraft.model_validate_json(design_raw).validate_context(scenes, path)
    except (ValueError, ValidationError):
        error = "Check the advanced frame ideas: use this preview’s scenes, nonempty text and uploaded images."
    scene_changes = []
    scene_draft = {}
    order = submitted.getlist("scene-order")
    if order or any(k.startswith(("scene-duration:", "scene-visual:")) for k in form):
        try:
            if len(order) != len(scenes) or set(order) != {s["id"] for s in scenes}:
                raise ValueError("Keep every scene exactly once in the scene order.")
            scene_changes = [SceneChange(id=sid, duration=form.get(f"scene-duration:{sid}"),
                                         visual=form.get(f"scene-visual:{sid}")).model_dump() for sid in order]
            if not 15 <= round(sum(s["duration"] for s in scene_changes), 3) <= 60:
                raise ValueError("The total video length must be between 15 and 60 seconds.")
            scene_draft = {s["id"]: s for s in scene_changes}
            if scene_changes == [{k: s[k] for k in ("id", "duration", "visual")} for s in scenes]:
                scene_changes = []
        except (ValueError, ValidationError):
            error = "Keep each scene once, its duration between 0.5 and 60 seconds, the total between 15 and 60 seconds, and each visual direction between 1 and 4,000 characters."
            scene_draft = {s["id"]: {"duration": form.get(f"scene-duration:{s['id']}", s["duration"]),
                                     "visual": form.get(f"scene-visual:{s['id']}", s["visual"])} for s in scenes}
    if draft.keys() != texts.keys() or any(not v or len(v) > 500 for v in draft.values()) or len(feedback) > 4000:
        error = "Keep every text between 1 and 500 characters and feedback under 4,000 characters."
    elif action == "approve" and (edits or feedback or comments or scene_changes or design.frames):
        error = "You have unapplied changes. Choose Revise preview, then review the updated version before approving."
    elif action == "approve" and not context["can_approve"]:
        error = "Automatic checks still report errors. Request a revision before rendering."
    elif action == "revise" and not edits and not feedback and not comments and not scene_changes and not design.frames:
        error = error or "Edit a scene, confirm a timeline comment or add feedback to request a revision."
    elif action not in {"approve", "revise"}:
        error = "Choose Revise preview or Approve & render."
    if error:
        return templates.TemplateResponse(request, "job.html", {**context, "error": error, "draft": draft, "feedback": feedback,
                                          "scene_draft": scene_draft, "advanced_draft": design_raw,
                                          "edit_scenes": sorted(scenes, key=lambda s: order.index(s["id"]))
                                          if len(order) == len(scenes) and set(order) == {s["id"] for s in scenes} else scenes}, status_code=422)
    decision = {"action": "approve"} if action == "approve" else (
        {"action": "edit", "texts": edits, "text": feedback} if edits else {"action": "feedback", "text": feedback})
    if comments:
        decision["comments"] = comments
    if scene_changes:
        decision["scenes"] = scene_changes
    if design.frames:
        decision["design"] = design.model_dump()
    enqueue(job_id, decision)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/comments")
async def review_comment(job_id: str, data: CommentRequest):
    path = job_path(job_id)
    if job_id in app.state.tasks or job_summary(path)["status"] != "review" or data.revision != revision(path):
        raise HTTPException(409, "This preview has changed. Reload before editing comments.")
    comments = saved_comments(path, data.revision)
    if data.id and not any(c["id"] == data.id for c in comments):
        raise HTTPException(404, "Comment not found.")
    if data.action == "delete":
        if not data.id:
            raise HTTPException(422, "Choose a comment to delete.")
        comments = [c for c in comments if c["id"] != data.id]
    else:
        scenes = read_json(path / "concept.json").get("scenes", [])
        duration = max((s["start"] + s["duration"] for s in scenes), default=0)
        scene = next((s for s in scenes if s["start"] <= data.time < s["start"] + s["duration"]), None)
        if data.time == duration and scenes:
            scene = scenes[-1]
        if not data.text or scene is None or (not data.id and len(comments) >= 30):
            raise HTTPException(422, "Add a comment at a time within this preview. Up to 30 confirmed comments are allowed.")
        comment = {"id": data.id or uuid4().hex, "time": round(data.time, 3), "text": data.text,
                   "format": data.format, "scene_id": scene["id"]}
        comments = [c for c in comments if c["id"] != data.id] + [comment]
        comments.sort(key=lambda c: c["time"])
    save_comments(path, data.revision, comments)
    return {"comments": comments}


@app.post("/jobs/{job_id}/design-assets")
async def upload_design_asset(request: Request, job_id: str):
    import io
    from PIL import Image, ImageOps, UnidentifiedImageError

    path = job_path(job_id)
    if job_id in app.state.tasks or job_summary(path)["status"] != "review" or request.headers.get("x-preview-revision") != revision(path):
        raise HTTPException(409, "This preview has changed. Reload before uploading images.")
    folder = path / "review-assets"
    if len(list(folder.glob("*.png"))) >= 40:
        raise HTTPException(422, "This job already has 40 uploaded images.")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "Choose an image smaller than 10 MB.")
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or source.width * source.height > 20_000_000:
                raise ValueError("Unsupported image")
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((2048, 2048))
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(422, "Use a valid PNG, JPEG or WebP image, up to 20 megapixels.")
    # The streamed request may overlap a revision; recheck before saving.
    if job_id in app.state.tasks or request.headers.get("x-preview-revision") != revision(path):
        raise HTTPException(409, "The preview changed during upload. Reload and try again.")
    folder.mkdir(exist_ok=True)
    name = uuid4().hex + ".png"
    image.save(folder / name, "PNG")
    return {"asset": name, "width": image.width, "height": image.height}


@app.get("/jobs/{job_id}/delete", response_class=HTMLResponse)
async def confirm_delete(request: Request, job_id: str):
    return templates.TemplateResponse(request, "delete.html", {"job": job_summary(job_path(job_id))})


@app.post("/jobs/{job_id}/delete")
async def delete(request: Request, job_id: str):
    job = job_summary(job_path(job_id))
    if job["active"] or job["poll"]:
        raise HTTPException(409, "Wait for this job to finish before deleting it.")
    # Reserve the job across the checkpoint await, preventing a simultaneous review submission.
    app.state.tasks[job_id] = asyncio.current_task()
    try:
        await jobs.delete_job(job_id)
    finally:
        app.state.tasks.pop(job_id, None)
    return RedirectResponse("/", status_code=303)


def confined_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, "File not found.")
    return path


@app.get("/jobs/{job_id}/files/{name:path}")
async def artifact(job_id: str, name: str, download: bool = False):
    if not (re.fullmatch(r"review-assets/[a-f0-9]{32}\.png", name) or name == "brand/board.png" or re.fullmatch(r"keyframes/r\d+/(feed|story)-[\d.]+s\.png", name)
            or re.fullmatch(r"renders/(feed-1080x1350|story-1080x1920)\.mp4", name)):
        raise HTTPException(404, "File not found.")
    path = confined_file(job_path(job_id), name)
    return FileResponse(path, filename=path.name if download else None)


@app.get("/preview/{job_id}/{name:path}")
async def preview(request: Request, job_id: str, name: str):
    allowed = (name in {"index.html", "theme.css", "composition/composition.css", "composition/composition.js",
                        "harness/harness.css", "harness/harness.js", "harness/design.js", "harness/gsap.min.js"}
               or name.startswith("assets/") and Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".woff", ".woff2", ".ttf", ".otf"})
    if not allowed:
        raise HTTPException(404, "Preview file not found.")
    if name in {"harness/harness.js", "harness/design.js"}:
        path = HERE.parent / name
    elif re.fullmatch(r"assets/user/[a-f0-9]{32}\.png", name):
        path = confined_file(job_path(job_id) / "review-assets", Path(name).name)
    else:
        path = confined_file(job_path(job_id) / "site", name)
    source = f"{str(request.base_url).rstrip('/')}/preview/{job_id}/"
    return FileResponse(path, headers={
        "Access-Control-Allow-Origin": "*",  # fonts loaded from the iframe's opaque origin
        "Content-Security-Policy": (
            f"sandbox allow-scripts; default-src 'none'; script-src {source}; "
            f"style-src {source} 'unsafe-inline'; img-src {source} data:; font-src {source} data:; "
            "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'self'"),
    })


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.endswith('/design-assets'):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request, "error.html", {"error": exc.detail}, status_code=exc.status_code)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(HERE.parent / "static" / "favicon.ico")
