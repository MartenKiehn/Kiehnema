# Phase 2 – Crawler & brand extraction

```bash
uv run python -m app.crawl https://www.example.com/ data/crawls/x     # crawl.json, site.md, screens/
uv run python -m app.brand.extract data/crawls/x data/brands/x        # brand.json, theme.json, logo, fonts/, img/, clients/
uv run python -m app.brand.board data/brands/x                        # board.html + board.png for review
```

## Security model (untrusted URL, untrusted content)
| Threat | Mitigation |
|---|---|
| SSRF to internal hosts / cloud metadata | `UrlGuard`: http(s) only, no credentials, host must resolve **only** to global unicast addresses (blocks loopback, RFC 1918, link-local incl. 169.254.169.254, CGNAT, ULA, multicast, reserved, IPv4-mapped forms, decimal IPs) |
| Redirect to an internal host | **Finding:** Playwright does not route redirect targets (neither `continue_()` nor fulfilling a 3xx). The route handler therefore fetches every request itself with redirects disabled; sub-resource chains are followed hop by hop through the guard, navigations are aborted and re-issued by the crawler after the guard check. HTTP downloads (`fetch.py`) do the same. Tested with redirects to 169.254.169.254 and 127.0.0.2. |
| Other channels | service workers blocked, WebSockets closed, media/beacons skipped, downloads disabled |
| Resource exhaustion | ≤ 20 pages, depth ≤ 2, 240 s budget, 30 s per navigation, size caps (images 15 MB, fonts 5 MB, robots 0.5 MB), text cap per page |
| Malicious SVG (logo, client logos) | parsed with `defusedxml` (no entity expansion), `<script>`, `foreignObject`, animations, event handlers, external `href`s and external CSS `url()`/`@import` removed |
| Malicious raster files | verified and re-encoded with Pillow (PNG/JPEG, metadata stripped, ≤ 2400 px) |
| Prompt injection via page text | page text is data only; it reaches the model in Phase 3 inside clearly delimited blocks, and generated code is sandboxed (CSP + network block) |

Residual risk: DNS rebinding between the guard check and the browser's own connection. Acceptable for a
local single-user tool; a resolving forward proxy would close it if the app is ever deployed.

## Extraction heuristics
- **Content:** visible headings/paragraphs/list items in document order as markdown, header/footer
  boilerplate de-duplicated across pages (`site_markdown`), CTA buttons, links (nav links crawled first).
- **Colours:** heading/link/body colours, hero/header backgrounds, CTA fills, area-weighted backgrounds,
  text-weighted foregrounds, theme-color meta, non-Bootstrap CSS variables and – weighted highest – the
  logo's dominant colours (k-means). Near-duplicates merged. Default theme roles: dark, light, paper,
  primary, accent, accent-2, text.
- **Fonts:** display = first family of the `h1` stack, body = `body` stack. Files are taken from the site's
  own `@font-face` (same site or Google Fonts only – other third-party font hosts are skipped because the
  licence is unclear), else the same family from Google Fonts, else Inter / Source Serif 4.
- **Logo:** inline SVG or image in the header, linked to the home page, "logo" in names, near the top;
  logo walls are excluded. Contrast flags `on_dark_ok` / `on_light_ok` (≥ 85 % of logo pixels ≥ 1.4:1
  against the theme colour) – e.g. they consulting: OK on light, **not** OK on dark petrol.
- **Images:** large content images (photos), ranked by rendered size; small non-JPEG images in logo
  walls / "clients/partners" sections become `client-logo` (only used when the user enables it).

## Result on www.they-consulting.de (20 pages, 37 s crawl, 4 s brand extraction)
Theme dark #164656 · primary #f7b85a · accent #7ec7be · accent-2 #ea5837; fonts chivo-black + Chivo
from the site; logo colours #164656 #7ec7be #ea5837 #f7b85a; 12 photos; the 11 client logos of the site.
