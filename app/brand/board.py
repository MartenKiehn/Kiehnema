"""Brand board: one HTML page (and PNG) summarising an extracted brand kit, for review."""

from __future__ import annotations

from app.security.workspace import browser_environment

import asyncio
import html
import json
from pathlib import Path

from playwright.async_api import async_playwright

from app.render.server import serve_directory


def board_html(kit: dict) -> str:
    esc = html.escape
    theme = kit["theme"]
    faces = "\n".join(
        f"@font-face {{ font-family: 'B-{f['role']}'; src: url({esc(f['src'])}); }}" for f in theme["fonts"]
    )
    swatches = "".join(
        f'<div class="sw"><i style="background:{esc(v)}"></i><b>{esc(k)}</b><span>{esc(v)}</span></div>'
        for k, v in theme["colors"].items()
    )
    palette = "".join(
        f'<i title="{esc(c["hex"])} · {c["score"]} · {esc(", ".join(c["sources"][:3]))}" style="background:{esc(c["hex"])}"></i>'
        for c in kit["colors"][:16]
    )
    logo = kit.get("logo")
    logo_html = ""
    if logo:
        dark_note = "" if logo["on_dark_ok"] else '<em>low contrast</em>'
        logo_html = (
            f'<div class="logo light"><img src="{esc(logo["file"])}"></div>'
            f'<div class="logo dark" style="background:{esc(theme["colors"]["dark"])}"><img src="{esc(logo["file"])}">{dark_note}</div>'
        )
    fonts = "".join(
        f'<p style="font-family:\'B-{esc(f["role"])}\'"><small>{esc(f["role"])} · {esc(f["family"])} · {esc(f["source"])}</small>'
        f'<br>{"Klarheit. Struktur. Wirkung." if kit["language"] == "de" else "Clarity. Structure. Impact."}</p>'
        for f in kit["fonts"]
    )
    photos = "".join(f'<img src="{esc(i["file"])}" title="{esc(i["alt"])}">' for i in kit["images"] if i["kind"] == "photo")
    clients = "".join(f'<img src="{esc(i["file"])}">' for i in kit["images"] if i["kind"] == "client-logo")
    notes = "".join(f"<li>{esc(n)}</li>" for n in kit["notes"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{faces}
body {{ margin: 0; padding: 32px; font: 14px system-ui; color: #1d2b33; background: #f4f5f5; width: 1336px; }}
h1 {{ margin: 0 0 4px; font-size: 22px; }} h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: #66757c; margin: 28px 0 10px; }}
.row {{ display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }}
.sw {{ width: 150px; }} .sw i {{ display: block; height: 70px; border-radius: 10px; box-shadow: inset 0 0 0 1px rgba(0,0,0,.08); }}
.sw b {{ display: block; margin-top: 6px; }} .sw span {{ color: #66757c; font-family: ui-monospace, monospace; }}
.pal i {{ width: 44px; height: 28px; border-radius: 6px; box-shadow: inset 0 0 0 1px rgba(0,0,0,.08); }}
.logo {{ position: relative; width: 420px; height: 180px; border-radius: 14px; display: flex; align-items: center; justify-content: center; background: #fff; }}
.logo img {{ max-width: 320px; max-height: 120px; }} .logo em {{ position: absolute; bottom: 10px; right: 14px; color: #fff; background: #c0392b; padding: 2px 8px; border-radius: 6px; font-style: normal; }}
.fonts p {{ margin: 0 0 12px; font-size: 44px; line-height: 1.1; }} .fonts small {{ font: 12px system-ui; color: #66757c; }}
.photos img {{ height: 150px; border-radius: 8px; }} .clients img {{ height: 40px; background: #fff; padding: 8px; border-radius: 6px; }}
ul {{ margin: 0; padding-left: 18px; color: #8a4b00; }}
</style></head><body>
<h1>{esc(kit["site_name"])}</h1><div style="color:#66757c">{esc(kit["start_url"])} · language {esc(kit["language"])}</div>
<h2>Theme</h2><div class="row">{swatches}</div>
<h2>Palette (ranked)</h2><div class="row pal">{palette}</div>
<h2>Logo</h2><div class="row">{logo_html}</div>
<h2>Fonts</h2><div class="fonts">{fonts}</div>
<h2>Photos</h2><div class="row photos">{photos}</div>
<h2>Client logos</h2><div class="row clients">{clients}</div>
{f"<h2>Notes</h2><ul>{notes}</ul>" if notes else ""}
</body></html>"""


async def render_board(brand_dir: Path) -> Path:
    kit = json.loads((brand_dir / "brand.json").read_text())
    (brand_dir / "board.html").write_text(board_html(kit))
    out = brand_dir / "board.png"
    with serve_directory(brand_dir) as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(env=browser_environment(), chromium_sandbox=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            await page.goto(f"{base}/board.html")
            await page.wait_for_load_state("networkidle")
            await page.evaluate("document.fonts.ready")
            await page.screenshot(path=str(out), full_page=True)
            await browser.close()
    return out


if __name__ == "__main__":
    import sys

    print(asyncio.run(render_board(Path(sys.argv[1]))))
