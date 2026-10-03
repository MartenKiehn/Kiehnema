"""Brand kit from a crawl: colour palette + default theme, fonts (local files), logo, images.

All downloads go through the SSRF guard. Downloaded files are re-encoded / sanitised before use:
raster images via Pillow (strips metadata, normalises format), SVGs via defusedxml with
scripts, event handlers, foreign objects and external references removed.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit

import httpx
import numpy as np
from defusedxml import ElementTree as SafeET
from PIL import Image
from playwright.async_api import async_playwright

from app.crawl.crawler import CrawlResult, _site_key
from app.crawl.fetch import Fetched, fetch_ok, make_client
from app.crawl.guard import UrlGuard
from app.harness.build import FontSpec, Theme

log = logging.getLogger(__name__)

RGB = tuple[int, int, int]
CLIENT_SECTION = re.compile(r"kunde|partner|client|customer|trusted|referenz|vertrauen|projekte, kunden", re.I)
GOOGLE_FALLBACK = {"sans": "Inter", "serif": "Source Serif 4"}
LOGO_MIN_SCORE = 3.0  # below this a candidate is a guess (og:image, favicon, random image) – better no logo than a wrong one
FONT_MAGIC = (b"wOF2", b"wOFF", b"\x00\x01\x00\x00", b"OTTO", b"true")


# ---------------------------------------------------------------- colour helpers

def parse_color(value: str | None) -> tuple[RGB, float] | None:
    if not value:
        return None
    v = value.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})([0-9a-f]{2})?", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        alpha = int(m.group(2), 16) / 255 if m.group(2) else 1.0
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), alpha
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)", v)
    if m:
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return (round(float(m.group(1))), round(float(m.group(2))), round(float(m.group(3)))), alpha
    return None


def hex_of(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def luminance(rgb: RGB) -> float:
    def ch(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def saturation(rgb: RGB) -> float:
    mx, mn = max(rgb) / 255, min(rgb) / 255
    return 0.0 if mx == 0 else (mx - mn) / mx


def _dist(a: RGB, b: RGB) -> float:
    return math.dist(a, b)


# ---------------------------------------------------------------- data model

@dataclass
class BrandColor:
    hex: str
    score: float
    sources: list[str] = field(default_factory=list)
    neutral: bool = False


@dataclass
class BrandFont:
    role: str  # display | body
    site_family: str  # family name used on the site
    family: str  # family name registered in the theme
    file: str | None  # path relative to the brand dir
    source: str  # site | google | fallback
    weight: int = 400


@dataclass
class BrandLogo:
    file: str
    kind: str  # svg | raster
    width: int
    height: int
    source_url: str | None
    colors: list[str] = field(default_factory=list)
    on_dark_ok: bool = False
    on_light_ok: bool = True
    shapes: int | None = None


@dataclass
class BrandImage:
    file: str
    url: str
    kind: str  # photo | client-logo
    alt: str
    page: str
    section: str | None
    width: int
    height: int


@dataclass
class BrandKit:
    site_name: str
    language: str
    start_url: str
    colors: list[BrandColor]
    theme: Theme
    fonts: list[BrandFont]
    logo: BrandLogo | None
    images: list[BrandImage]
    notes: list[str] = field(default_factory=list)
    wordmark: dict | None = None  # header brand name set as text: {text, font, weight, color, letterSpacing, ...}

    def save(self, out: Path) -> Path:
        data = asdict(self)
        data["theme"] = self.theme.model_dump()
        path = out / "brand.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        (out / "theme.json").write_text(self.theme.model_dump_json(indent=2))
        return path


# ---------------------------------------------------------------- palette

class _Palette:
    def __init__(self) -> None:
        self.items: list[list] = []  # [rgb, score, sources]

    def add(self, value: str | RGB | None, score: float, source: str) -> None:
        if value is None:
            return
        if isinstance(value, tuple):
            rgb, alpha = value, 1.0
        else:
            parsed = parse_color(value)
            if not parsed:
                return
            rgb, alpha = parsed
        if alpha < 0.6:
            return
        for item in self.items:
            if _dist(item[0], rgb) < 18:
                item[1] += score
                if source not in item[2]:
                    item[2].append(source)
                return
        self.items.append([rgb, score, [source]])

    def ranked(self) -> list[BrandColor]:
        out = [BrandColor(hex_of(rgb), round(score, 2), src, neutral=saturation(rgb) < 0.12)
               for rgb, score, src in sorted(self.items, key=lambda i: -i[1])]
        return out[:24]


def _collect_palette(crawl: CrawlResult, logo_colors: list[tuple[str, float]]) -> _Palette:
    pal = _Palette()
    for n, page in enumerate(crawl.pages):
        w = 1.0 if n == 0 else 0.25
        d = page.data
        st = d["styles"]
        for key, role, score in (("h1", "heading", 3), ("h2", "heading", 3), ("h3", "heading", 1.5), ("a", "link", 3), ("body", "text", 2)):
            if st.get(key):
                pal.add(st[key]["color"], score * w, f"{role}:{key}")
        for key, score in (("hero", 4), ("header", 1)):
            if st.get(key):
                pal.add(st[key]["bg"], score * w, f"background:{key}")
        for cta in d.get("ctas", []):
            pal.add(cta["bg"], 2.5 * w, "cta")
        total_bg = sum(d["bgUse"].values()) or 1
        for color, used in sorted(d["bgUse"].items(), key=lambda kv: -kv[1])[:8]:
            pal.add(color, 6 * used / total_bg * w, "background:area")
        total_fg = sum(d["fgUse"].values()) or 1
        for color, used in sorted(d["fgUse"].items(), key=lambda kv: -kv[1])[:6]:
            pal.add(color, 3 * used / total_fg * w, "text:area")
        if n == 0:
            pal.add(d.get("themeColor"), 3, "meta:theme-color")
            for name, value in d["cssVars"].items():
                if not name.startswith("--bs-"):  # Bootstrap defaults are noise
                    pal.add(value, 0.5, f"css:{name}")
    for hexv, share in logo_colors:  # the logo is the strongest brand signal
        pal.add(hexv, 25 * share, "logo")
    return pal


def default_theme(colors: list[BrandColor], fonts: list[BrandFont]) -> Theme:
    rgb = {c.hex: parse_color(c.hex)[0] for c in colors}
    chromatic = [c for c in colors if not c.neutral]

    def pick(pred, avoid=(), default=None):
        for c in chromatic + [c for c in colors if c.neutral]:
            if pred(rgb[c.hex]) and all(_dist(rgb[c.hex], rgb.get(a, parse_color(a)[0])) > 60 for a in avoid):
                return c.hex
        return default

    dark = pick(lambda x: luminance(x) < 0.08, default="#111827")
    light = "#ffffff"
    paper = next((c.hex for c in colors if c.neutral and 0.85 < luminance(rgb[c.hex]) < 0.99), "#f7f7f5")
    primary = pick(lambda x: 0.08 <= luminance(x) <= 0.8 and saturation(x) > 0.25, avoid=(dark,), default="#3b82f6")
    accent = pick(lambda x: 0.08 <= luminance(x) <= 0.85 and saturation(x) > 0.25, avoid=(dark, primary), default=primary)
    accent2 = pick(lambda x: 0.05 <= luminance(x) <= 0.85 and saturation(x) > 0.3, avoid=(dark, primary, accent), default=accent)
    text = next((c.hex for c in colors if "text:body" in c.sources and luminance(rgb[c.hex]) < 0.3),
                next((c.hex for c in colors if any(s.startswith("text") for s in c.sources) and luminance(rgb[c.hex]) < 0.2), "#1f2937"))
    theme_fonts = [FontSpec(family=f.family, src=f.file, role=f.role, weight=f.weight) for f in fonts if f.file]
    return Theme(colors={"dark": dark, "light": light, "paper": paper, "primary": primary, "accent": accent,
                         "accent-2": accent2, "text": text}, fonts=theme_fonts)


# ---------------------------------------------------------------- downloads

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


def _font_family_name(stack: str) -> str:
    first = stack.split(",")[0].strip().strip("'\"")
    return first


def _safe_font_family(name: str) -> str:
    cleaned = re.sub(r"[^\w \-]", "", name).strip()
    return cleaned[:60] or "Brand Font"


async def _google_font(client: httpx.AsyncClient, guard: UrlGuard, family: str, weight: int) -> bytes | None:
    url = f"https://fonts.googleapis.com/css2?family={family.replace(' ', '+')}:wght@{weight}&display=block"
    # a modern browser UA makes Google serve woff2
    client.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/140 Safari/537.36"
    css = await fetch_ok(client, url, guard, max_bytes=200_000)
    if not css:
        return None
    text = css.body.decode("utf-8", "replace")
    blocks = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*{([^}]*)}", text)
    block = next((b for name, b in blocks if name == "latin"), blocks[0][1] if blocks else text)
    m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", block)
    if not m:
        return None
    res = await fetch_ok(client, m.group(1), guard, max_bytes=5_000_000)
    return res.body if res and res.body[:4] in FONT_MAGIC else None


async def _resolve_font(client, guard, crawl: CrawlResult, role: str, stack: str, weight: int, out: Path, notes: list[str]) -> BrandFont:
    site_family = _font_family_name(stack)
    faces = [f for p in crawl.pages[:1] for f in p.data["fontFaces"] if f["family"].lower() == site_family.lower()]
    faces.sort(key=lambda f: abs(int(re.sub(r"\D", "", str(f["weight"])) or 400) - weight))
    family = _safe_font_family(site_family)
    site = _site_key(crawl.start_url)
    for face in faces:
        urls = sorted(face["urls"], key=lambda u: [".woff2", ".ttf", ".otf", ".woff"].index(next((e for e in (".woff2", ".ttf", ".otf", ".woff") if e in u.lower()), ".woff")))
        for url in urls:
            host = urlsplit(url).hostname or ""
            if _site_key(url) != site and not host.endswith("fonts.gstatic.com"):
                notes.append(f"font {site_family}: third-party host {host} skipped (licence unclear)")
                continue
            res = await fetch_ok(client, url, guard, max_bytes=5_000_000)
            if res and res.body[:4] in FONT_MAGIC:
                ext = {b"wOF2": ".woff2", b"wOFF": ".woff", b"OTTO": ".otf"}.get(res.body[:4], ".ttf")
                path = out / "fonts" / f"{_slug(site_family)}-{weight}{ext}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(res.body)
                return BrandFont(role, site_family, family, str(path.relative_to(out)), "site", weight)
    body = await _google_font(client, guard, site_family, weight)
    source = "google"
    if body is None:
        serif = "serif" in stack.lower() and "sans-serif" not in stack.lower()
        family = GOOGLE_FALLBACK["serif" if serif else "sans"]
        body = await _google_font(client, guard, family, weight)
        source = "fallback"
        notes.append(f"font {site_family} not downloadable – using {family} {weight} instead")
    if body is None:
        return BrandFont(role, site_family, family, None, "none", weight)
    path = out / "fonts" / f"{_slug(family)}-{weight}.woff2"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return BrandFont(role, site_family, family, str(path.relative_to(out)), source, weight)


def sanitize_svg(text: str) -> str | None:
    """Strip active content from an SVG; returns None if it is not a usable SVG."""
    # Inline SVG from HTML has no namespace declarations; standalone files need them.
    m = re.search(r"<svg\b[^>]*>", text)
    if m:
        tag = m.group(0)
        extra = ""
        if "xmlns=" not in tag:
            extra += ' xmlns="http://www.w3.org/2000/svg"'
        if "xmlns:xlink" not in tag and "xlink:" in text:
            extra += ' xmlns:xlink="http://www.w3.org/1999/xlink"'
        if extra:
            text = text[: m.start()] + tag[:4] + extra + tag[4:] + text[m.end():]
    try:
        root = SafeET.fromstring(text.encode())
    except Exception:
        return None
    if not root.tag.endswith("svg"):
        return None
    bad_tags = {"script", "foreignObject", "iframe", "object", "embed", "audio", "video", "animate", "set", "handler"}

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def clean(el) -> None:
        for child in list(el):
            if local(child.tag) in bad_tags:
                el.remove(child)
                continue
            clean(child)
        for attr in list(el.attrib):
            name = local(attr).lower()
            value = el.attrib[attr]
            if name.startswith("on") or (name == "href" and not value.startswith("#")) \
                    or (name == "style" and re.search(r"url\(\s*['\"]?(?!#)|expression|@import", value, re.I)):
                del el.attrib[attr]
        if local(el.tag) == "style" and el.text:
            el.text = re.sub(r"@import[^;]*;|url\(\s*['\"]?(?!#)[^)]*\)", "", el.text)

    clean(root)
    if "viewBox" not in root.attrib and {"width", "height"} <= set(root.attrib):
        w, h = (re.sub(r"[^\d.]", "", root.attrib[k]) for k in ("width", "height"))
        if w and h:
            root.set("viewBox", f"0 0 {w} {h}")
    from xml.etree import ElementTree as ET  # serialisation only (input was parsed safely)

    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return ET.tostring(root, encoding="unicode")


def decode_data_url(url: str, max_bytes: int = 5_000_000) -> Fetched | None:
    """Decode a data: URL locally (bundlers inline logos this way) – never touches the network."""
    m = re.match(r"data:([\w.+-]+/[\w.+-]+)?((?:;[\w-]+=[^;,]*)*)(;base64)?,(.*)", url, re.S)
    if not m:
        return None
    try:
        body = base64.b64decode(m.group(4), validate=False) if m.group(3) else unquote_to_bytes(m.group(4))
    except Exception:
        return None
    if len(body) > max_bytes:
        return None
    return Fetched(url="data:", status=200, content_type=m.group(1) or "", body=body)


async def _get(client, url: str, guard: UrlGuard, max_bytes: int) -> Fetched | None:
    if url.startswith("data:"):
        return decode_data_url(url, max_bytes)
    return await fetch_ok(client, url, guard, max_bytes=max_bytes)


def _reencode(body: bytes, max_side: int, *, keep_alpha: bool) -> tuple[bytes, str, int, int] | None:
    try:
        Image.open(io.BytesIO(body)).verify()
        img = Image.open(io.BytesIO(body))
        img.load()
    except Exception:
        return None
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    if keep_alpha and (img.mode in ("RGBA", "LA", "P")):
        img.convert("RGBA").save(buf, "PNG", optimize=True)
        ext = ".png"
    else:
        img.convert("RGB").save(buf, "JPEG", quality=88, optimize=True)
        ext = ".jpg"
    return buf.getvalue(), ext, img.width, img.height


def _dominant_colors(pixels: np.ndarray, min_share: float = 0.015) -> list[tuple[str, float]]:
    """Dominant colours of opaque pixels as (hex, share): coarse histogram + merge of near-duplicates.

    Deterministic and exact for flat-colour logos (small accent shapes are kept), unlike k-means."""
    if len(pixels) == 0:
        return []
    px = pixels.astype(np.int64)
    keys = (px[:, 0] // 16) * 256 + (px[:, 1] // 16) * 16 + (px[:, 2] // 16)
    uniq, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    order = np.argsort(-counts)[:24]
    merged: list[list] = []  # [rgb(float array), count]
    for i in order:
        mean = px[inverse == i].mean(0)
        for m in merged:
            if np.linalg.norm(m[0] - mean) < 28:
                m[0] = (m[0] * m[1] + mean * counts[i]) / (m[1] + counts[i])
                m[1] += counts[i]
                break
        else:
            merged.append([mean, counts[i]])
    total = len(px)
    out = [(hex_of(tuple(int(round(v)) for v in m[0])), float(m[1] / total)) for m in merged if m[1] / total >= min_share]
    return sorted(out, key=lambda x: -x[1])


async def _rasterize_svg(svg: str, size: int = 600) -> Image.Image:
    data = base64.b64encode(svg.encode()).decode()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": size, "height": size})
        await page.set_content(f'<html><body style="margin:0;background:transparent">'
                               f'<img id="l" src="data:image/svg+xml;base64,{data}" style="max-width:{size}px;max-height:{size}px"></body></html>')
        await page.wait_for_function("() => document.getElementById('l').complete")
        if not await page.evaluate("() => document.getElementById('l').naturalWidth > 0"):
            await browser.close()
            raise ValueError("SVG could not be rendered")
        png = await page.locator("#l").screenshot(omit_background=True)
        await browser.close()
    return Image.open(io.BytesIO(png)).convert("RGBA")


# ---------------------------------------------------------------- logo + images

def _logo_candidates(crawl: CrawlResult) -> list[tuple[float, str, dict]]:
    home = crawl.home.data
    root = crawl.start_url.rstrip("/")
    cands: list[tuple[float, str, dict]] = []

    def home_link(link: str | None) -> bool:
        return bool(link) and link.rstrip("/") in (root, root.split("://", 1)[0] + "://" + urlsplit(root).netloc)

    for s in home["svgs"]:
        score = 0.0
        score += 5 if s["area"] == "header" else 0
        score += 5 if "logo" in s["desc"].lower() else 0
        score += 5 if home_link(s["link"]) else 0
        score += 2 if s["y"] < 200 else 0
        aspect = s["rw"] / max(1, s["rh"])
        if s["rw"] * s["rh"] < 1500 or not 0.4 <= aspect <= 10 or not s.get("html"):
            continue
        cands.append((score, "svg-inline", s))
    for i in home["images"]:
        if i.get("background"):
            continue
        hint = f"{i['src']} {i['alt']} {i['desc']}".lower()
        score = 0.0
        score += 4 if i["area"] == "header" else 0
        score += 4 if "logo" in hint else 0
        score += 5 if home_link(i["link"]) else 0
        score += 3 if i["y"] < 200 else 0
        score += 1 if 16 <= i["rh"] <= 200 else -2
        if i["area"] == "main" and i["siblings"] >= 4:
            score -= 8  # logo walls are client logos
        cands.append((score, "img", i))
    if home.get("ogImage"):
        cands.append((1.0, "img", {"src": home["ogImage"], "alt": "og:image"}))
    for icon in home.get("icons", []):
        if icon.get("href"):
            cands.append((0.5, "img", {"src": icon["href"], "alt": "icon"}))
    return sorted(cands, key=lambda c: -c[0])


async def _save_logo(client, guard, crawl: CrawlResult, out: Path, notes: list[str]) -> tuple[BrandLogo | None, Image.Image | None]:
    candidates = [c for c in _logo_candidates(crawl) if c[0] >= LOGO_MIN_SCORE]
    if not candidates:
        notes.append("no reliable logo image found – set the brand name as text (see wordmark)")
        return None, None
    for score, kind, cand in candidates[:6]:
        svg_text: str | None = None
        raster: bytes | None = None
        src = cand.get("src")
        if kind == "svg-inline":
            svg_text = cand["html"]
        else:
            res: Fetched | None = await _get(client, src, guard, 5_000_000)
            if not res:
                continue
            if "svg" in res.content_type or src.lower().split("?")[0].endswith(".svg"):
                svg_text = res.body.decode("utf-8", "replace")
            else:
                raster = res.body
        if svg_text is not None:
            clean = sanitize_svg(svg_text)
            if not clean:
                continue
            try:
                img = await _rasterize_svg(clean)
            except ValueError:
                continue
            path = out / "logo.svg"
            path.write_text(clean)
            shapes = len(re.findall(r"<(?:\w+:)?(?:path|circle|rect|polygon|ellipse|polyline|line)\b", clean))
            logo = BrandLogo("logo.svg", "svg", img.width, img.height, None if src and src.startswith("data:") else src, shapes=shapes)
        else:
            enc = _reencode(raster, 1600, keep_alpha=True)
            if not enc:
                continue
            body, ext, w, h = enc
            path = out / f"logo{ext}"
            path.write_bytes(body)
            img = Image.open(io.BytesIO(body)).convert("RGBA")
            logo = BrandLogo(f"logo{ext}", "raster", w, h, None if src and src.startswith("data:") else src)
        return logo, img
    notes.append("no usable logo image found – set the brand name as text (see wordmark)")
    return None, None


def _logo_src(crawl: CrawlResult, logo: BrandLogo | None) -> str | None:
    if logo is None:
        return None
    if logo.source_url:
        return logo.source_url
    return next((c[2].get("src") for c in _logo_candidates(crawl) if c[0] >= LOGO_MIN_SCORE and c[1] == "img"), None)


def _analyse_logo(logo: BrandLogo, img: Image.Image) -> list[tuple[str, float]]:
    arr = np.asarray(img)
    opaque = arr[arr[..., 3] > 160][:, :3]
    colors = _dominant_colors(opaque)
    logo.colors = [c for c, _ in colors]
    return colors


def _set_logo_contrast(logo: BrandLogo, img: Image.Image, theme: Theme) -> None:
    arr = np.asarray(img)
    opaque = arr[arr[..., 3] > 160][:, :3].astype(float)
    if not len(opaque):
        return

    def ok(bg_hex: str) -> bool:
        bg = parse_color(bg_hex)[0]
        lum = np.array([luminance(tuple(int(v) for v in px)) for px in opaque[:: max(1, len(opaque) // 4000)]])
        lb = luminance(bg)
        ratio = (np.maximum(lum, lb) + 0.05) / (np.minimum(lum, lb) + 0.05)
        # light brand colours (mint/yellow on white ≈ 1.7:1) are fine; same-colour-on-same-colour is not
        return float((ratio >= 1.4).mean()) >= 0.85

    logo.on_dark_ok = ok(theme.colors["dark"])
    logo.on_light_ok = ok(theme.colors["light"])


async def _save_images(client, guard, crawl: CrawlResult, logo_url: str | None, out: Path, *, max_photos: int = 12, max_clients: int = 16) -> list[BrandImage]:
    photos: dict[str, tuple[float, dict, str]] = {}
    clients: dict[str, tuple[dict, str]] = {}
    for n, page in enumerate(crawl.pages):
        for i in page.data["images"]:
            src = i["src"]
            if not src or src == logo_url or src.lower().split("?")[0].endswith((".ico", ".gif")):
                continue
            hint = f"{src} {i['alt']} {i['desc']}".lower()
            photo_like = src.lower().split("?")[0].endswith((".jpg", ".jpeg")) and "logo" not in hint  # e.g. testimonial avatars
            is_client = not photo_like and (
                (i["area"] == "main" and i["siblings"] >= 4 and 0 < i["rh"] <= 140)
                or bool(i.get("section") and CLIENT_SECTION.search(i["section"]) and 0 < i["rh"] <= 160))
            if is_client:
                clients.setdefault(src, (i, page.url))
                continue
            big = (i["w"] >= 600 and i["h"] >= 400) or (i.get("background") and i["rw"] * i["rh"] >= 400_000)
            if not big or src.lower().split("?")[0].endswith(".svg"):
                continue
            score = (i["rw"] * i["rh"] / 1e6) * (1.0 if n == 0 else 0.6) + (0.3 if i["alt"] else 0)
            if src not in photos or photos[src][0] < score:
                photos[src] = (score, i, page.url)

    images: list[BrandImage] = []
    (out / "img").mkdir(parents=True, exist_ok=True)
    ranked = sorted(photos.items(), key=lambda kv: -kv[1][0])[:max_photos]
    for k, (src, (_, info, page_url)) in enumerate(ranked):
        res = await _get(client, src, guard, 15_000_000)
        enc = _reencode(res.body, 2400, keep_alpha=False) if res else None
        if not enc:
            continue
        body, ext, w, h = enc
        path = out / "img" / f"photo-{k:02d}{ext}"
        path.write_bytes(body)
        images.append(BrandImage(str(path.relative_to(out)), src, "photo", info["alt"], page_url, info.get("section"), w, h))

    (out / "clients").mkdir(exist_ok=True)
    for k, (src, (info, page_url)) in enumerate(list(clients.items())[:max_clients]):
        res = await _get(client, src, guard, 3_000_000)
        if not res:
            continue
        if "svg" in res.content_type or src.lower().split("?")[0].endswith(".svg"):
            clean = sanitize_svg(res.body.decode("utf-8", "replace"))
            if not clean:
                continue
            path = out / "clients" / f"client-{k:02d}.svg"
            path.write_text(clean)
            w, h = int(info["rw"]), int(info["rh"])
        else:
            enc = _reencode(res.body, 800, keep_alpha=True)
            if not enc:
                continue
            body, ext, w, h = enc
            path = out / "clients" / f"client-{k:02d}{ext}"
            path.write_bytes(body)
        images.append(BrandImage(str(path.relative_to(out)), src, "client-logo", info["alt"], page_url, info.get("section"), w, h))
    return images


# ---------------------------------------------------------------- entry point

async def extract_brand(crawl: CrawlResult, out: Path, guard: UrlGuard | None = None) -> BrandKit:
    guard = guard or UrlGuard()
    out.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    home = crawl.home.data
    async with make_client() as client:
        logo, logo_img = await _save_logo(client, guard, crawl, out, notes)
        logo_colors = _analyse_logo(logo, logo_img) if logo else []
        styles = home["styles"]
        h1 = styles.get("h1") or styles.get("h2") or styles["body"]
        display_weight = min(900, max(100, int(round(int(re.sub(r"\D", "", h1["weight"]) or 700) / 100) * 100)))
        fonts = [
            await _resolve_font(client, guard, crawl, "display", h1["font"], display_weight, out, notes),
            await _resolve_font(client, guard, crawl, "body", styles["body"]["font"], 400, out, notes),
        ]
        if fonts[0].family == fonts[1].family and fonts[0].file == fonts[1].file:
            fonts = fonts[:1]
        images = await _save_images(client, guard, crawl, _logo_src(crawl, logo), out)

    colors = _collect_palette(crawl, logo_colors).ranked()
    theme = default_theme(colors, fonts)
    if logo and logo_img is not None:
        _set_logo_contrast(logo, logo_img, theme)
        if not logo.on_dark_ok:
            notes.append("logo has low contrast on the dark brand colour – place it on a light background")
    site_name = home.get("siteName") or (home.get("title") or "").split("|")[-1].strip() or _site_key(crawl.start_url)
    wordmark = home.get("wordmark")
    if wordmark and not wordmark.get("hasImage"):
        rgb = parse_color(wordmark.get("color"))
        wordmark = {**wordmark, "color": hex_of(rgb[0]) if rgb else None, "font_family": _font_family_name(wordmark.get("font") or "")}
    else:
        wordmark = None
    kit = BrandKit(site_name, (home.get("lang") or "en")[:2], crawl.start_url, colors, theme, fonts, logo, images, notes, wordmark)
    kit.save(out)
    return kit


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    crawl_dir, brand_dir = Path(sys.argv[1]), Path(sys.argv[2])
    kit = asyncio.run(extract_brand(CrawlResult.load(crawl_dir), brand_dir))
    print(json.dumps({"site": kit.site_name, "lang": kit.language, "theme": kit.theme.colors,
                      "top_colors": [(c.hex, c.score, c.sources[:3]) for c in kit.colors[:10]],
                      "fonts": [asdict(f) for f in kit.fonts], "logo": asdict(kit.logo) if kit.logo else None,
                      "images": [(i.kind, i.file, i.width, i.height, i.alt[:30]) for i in kit.images], "notes": kit.notes},
                     ensure_ascii=False, indent=1))
