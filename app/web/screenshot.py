"""Capture the running local UI: python -m app.web.screenshot URL --out out/review.png."""

import argparse
import asyncio
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def capture(url: str, output: Path, *, width: int = 1440, height: int = 1000,
                  selector: str | None = None, time: float | None = None,
                  credentials: tuple[str, str, str] | None = None) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password:
        raise ValueError("Use a URL on the running local server.")
    if width < 1 or height < 1 or time is not None and (not math.isfinite(time) or time < 0):
        raise ValueError("Viewport dimensions must be positive and time finite and nonnegative.")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = None
        try:
            page = await browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
            if credentials:
                origin = f'{parsed.scheme}://{parsed.netloc}'
                await page.goto(origin + '/login')
                await page.get_by_label('Username', exact=True).fill(credentials[0])
                await page.get_by_label('Password', exact=True).fill(credentials[1])
                await page.get_by_label('Authenticator code').fill(credentials[2])
                await page.get_by_role('button', name='Unlock workspace').click()
                if urlsplit(page.url).path == '/login':
                    raise RuntimeError('Sign in failed. Check your credentials and use a fresh authenticator code.')
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            response = await page.goto(url, wait_until="networkidle")
            if response is None or not response.ok:
                raise RuntimeError(f"Page could not be loaded: {response.status if response else url}")
            await page.locator(selector or "main").wait_for()
            await page.evaluate("async () => { await document.fonts.ready; await Promise.all([...document.images].map(img => { img.loading = 'eager'; return img.decode().catch(() => {}); })); }")
            previews = [frame for frame in page.frames[1:] if "/preview/" in frame.url]
            for frame in previews:
                await frame.wait_for_function("() => window.__ready === true || Boolean(window.__mgFailed)")
                if failure := await frame.evaluate("window.__mgFailed"):
                    errors.append(f"Preview {frame.url}: {failure}")
            if time is not None:
                slider = page.locator("#timeline-position")
                await slider.wait_for()
                if time > float(await slider.get_attribute("max")):
                    raise ValueError("Time exceeds the preview duration.")
                await slider.evaluate("(el, t) => { el.value = t; el.dispatchEvent(new Event('input', {bubbles:true})); }", time)
                for frame in previews:
                    if not await frame.evaluate("window.__mgFailed"):
                        await frame.wait_for_function("t => Math.abs(Number(document.querySelector('#mg-time').textContent.split(' ')[0]) - t) < .11", arg=time)
            # Offscreen iframes may be unpainted in Chromium's full-page capture.
            for iframe in await page.locator("iframe.preview").all():
                await iframe.scroll_into_view_if_needed()
                await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            await page.evaluate("scrollTo(0, 0)")
            output.parent.mkdir(parents=True, exist_ok=True)
            if selector:
                await page.locator(selector).screenshot(path=str(output), animations="disabled")
            else:
                await page.screenshot(path=str(output), full_page=True, animations="disabled")
            report = {"url": page.url, "screenshot": str(output.resolve()), "width": width, "height": height,
                      "page_errors": errors, "horizontal_overflow": await page.evaluate("document.documentElement.scrollWidth > innerWidth"),
                      "broken_images": await page.locator("img").evaluate_all("imgs => imgs.filter(img => !img.naturalWidth).map(img => img.src)")}
            output.with_suffix(".json").write_text(json.dumps(report, indent=2))
            return report
        finally:
            if credentials and page:
                try:
                    csrf = await page.locator('meta[name="csrf-token"]').get_attribute('content', timeout=2000)
                    await page.request.post(origin + '/logout', headers={'Origin': origin, 'X-CSRF-Token': csrf or ''})
                except Exception:
                    pass  # A stopped server already invalidates its in-memory sessions.
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--out", type=Path, default=Path("out/screenshots/review.png"))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--selector", help="Capture only this CSS selector")
    parser.add_argument("--time", type=float, help="Seek the review timeline to this second before capture")
    parser.add_argument('--login', action='store_true', help='Prompt for credentials to capture a private page')
    args = parser.parse_args()
    credentials = None
    if args.login:
        import getpass
        from app.security.workspace import memory_policy
        memory_policy()
        credentials = (input('Username: '), getpass.getpass('Password: '), input('Authenticator code: ').strip())
    print(json.dumps(asyncio.run(capture(args.url, args.out, width=args.width, height=args.height,
                                         selector=args.selector, time=args.time, credentials=credentials)), indent=2))


if __name__ == "__main__":
    main()
