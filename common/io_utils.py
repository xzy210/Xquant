"""
Common IO utilities shared across apps.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Write text atomically to avoid partial file corruption."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def atomic_write_json(path: str | Path, data: Any, encoding: str = "utf-8") -> None:
    """Serialize JSON and write it atomically."""
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding=encoding,
    )
