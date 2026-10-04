"""Atomic status snapshots shared by the pipeline and its web worker."""

import json
import time
from pathlib import Path


def write_status(job: Path, status: str, message: str, **extra) -> None:
    path = job / "status.json"
    data = json.loads(path.read_text()) if path.exists() else {"history": []}
    data["history"].append({"t": round(time.time(), 1), "status": status, "message": message})
    data.update(status=status, message=message, **extra)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    temporary.replace(path)
