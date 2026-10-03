"""CLI: uv run python -m app.crawl <url> <out_dir> [max_pages]"""

import asyncio
import logging
import sys
from pathlib import Path

from app.crawl.crawler import crawl, site_markdown

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
if len(sys.argv) < 3:
    sys.exit(__doc__)
res = asyncio.run(crawl(sys.argv[1], Path(sys.argv[2]), max_pages=int(sys.argv[3]) if len(sys.argv) > 3 else 20,
                        progress=lambda m: print(m, flush=True)))
(Path(sys.argv[2]) / "site.md").write_text(site_markdown(res))
print(f"pages={len(res.pages)} robots_skipped={len(res.skipped_robots)} blocked={len(res.blocked)} errors={len(res.errors)}")
for e in res.errors[:5] + res.blocked[:5]:
    print("  ", e)
