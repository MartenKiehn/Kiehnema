"""CLI: build a composition into a site.  uv run python -m app.harness <composition_dir> <site_dir>"""

import sys
from pathlib import Path

from app.harness.build import build_site

if len(sys.argv) != 3:
    sys.exit(__doc__)
print(build_site(Path(sys.argv[1]), Path(sys.argv[2])))
