from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_FORBIDDEN_PATTERN = re.compile(r"\b(?:from|import)\s+PyQt6\b|\bQTimer\b|\bQThread\b|\bQObject\b|\bpyqtSignal\b|\bQEventLoop\b")
_TARGETS = [PROJECT_ROOT / "live_rotation"]
_INCLUDE_RE = re.compile(r"rotation_.*_service\.py$")


def _iter_target_files() -> list[Path]:
    files: list[Path] = []
    for target in _TARGETS:
        if target.is_file() and _INCLUDE_RE.search(target.name):
            files.append(target)
            continue
        if target.is_dir():
            files.extend(path for path in target.rglob("*.py") if _INCLUDE_RE.search(path.name))
    return sorted(files)


def main() -> int:
    violations: list[str] = []
    for path in _iter_target_files():
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN_PATTERN.search(line):
                rel = path.relative_to(PROJECT_ROOT)
                violations.append(f"{rel}:{line_no}: {line.strip()}")
    if violations:
        print("layering_check_failed")
        for item in violations:
            print(item)
        return 1
    print("layering_check_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
