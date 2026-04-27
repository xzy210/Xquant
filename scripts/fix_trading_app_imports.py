#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一次性修复 trading_app 包内部的 import：
  将 `from {prefix}[.xxx] import ...` 改为 `from trading_app.{prefix}[.xxx] import ...`
其中 prefix ∈ TOP_LEVEL_MODULES。

原地修改 .py 文件；不会接触 __pycache__、非 .py 文件。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TRADING_APP_ROOT = Path(__file__).resolve().parent.parent / "trading_app"

# trading_app 下的顶层子模块/子包（需要加 `trading_app.` 前缀的目标）
TOP_LEVEL_MODULES = {
    "widgets",
    "services",
    "controllers",
    "indicators",
    "data_updater",
    "scheduler",
    "notifier",
    "watchlist_manager",
    "trading_simulator",
    "styles",
}

# 匹配：  ^(indent)(from|import)(space)(prefix)(dot or space or EOL)
# group1=前导空白, group2=from/import, group3=空白, group4=prefix, group5=分隔符
LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kw>from|import)(?P<sp>\s+)(?P<mod>[A-Za-z_][A-Za-z_0-9]*)(?P<tail>[\.\s].*)$"
)


def should_rewrite(mod: str) -> bool:
    return mod in TOP_LEVEL_MODULES


def rewrite_line(line: str) -> str | None:
    # 不触碰多行延续的空/数据行
    # 先做快速过滤：不包含 from/import 的行跳过
    if "import " not in line and not line.lstrip().startswith("from "):
        return None

    m = LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    mod = m.group("mod")
    if not should_rewrite(mod):
        return None

    newline_suffix = "\n" if line.endswith("\n") else ""
    new_line = f"{m.group('indent')}{m.group('kw')}{m.group('sp')}trading_app.{mod}{m.group('tail')}{newline_suffix}"
    return new_line


def process_file(path: Path) -> tuple[int, int]:
    """Return (changed_count, total_lines)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    changed = 0
    for i, line in enumerate(lines):
        new_line = rewrite_line(line)
        if new_line is not None and new_line != line:
            lines[i] = new_line
            changed += 1
    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    return changed, len(lines)


def main() -> int:
    if not TRADING_APP_ROOT.exists():
        print(f"[error] {TRADING_APP_ROOT} not found", file=sys.stderr)
        return 2

    total_changed = 0
    total_files = 0
    changed_files = 0
    for py_file in TRADING_APP_ROOT.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        total_files += 1
        changed, _ = process_file(py_file)
        if changed:
            changed_files += 1
            total_changed += changed
            rel = py_file.relative_to(TRADING_APP_ROOT.parent)
            print(f"  {rel}: +{changed} import(s)")

    print(
        f"\n[done] scanned={total_files} files, "
        f"changed={changed_files} files, "
        f"rewrites={total_changed} lines"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
