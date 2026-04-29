from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_FORBIDDEN_PATTERN = re.compile(r"\b(?:from|import)\s+PyQt6\b|\bQTimer\b|\bQThread\b|\bQObject\b|\bpyqtSignal\b|\bQEventLoop\b")
_TRADING_APP_IMPORT_PATTERN = re.compile(r"^\s*(?:from|import)\s+trading_app(?:\.|\b)")
_FORBIDDEN_LIVE_ORDER_PATTERN = re.compile(
    r"(?<!query_stock_)\border_stock\s*\("
    r"|\.order_stock\s*\("
    r"|\bpassorder\s*\("
    r"|\bstock_buy\s*\("
    r"|\bstock_sell\s*\("
)
_TARGETS = [PROJECT_ROOT / "live_rotation"]
_INCLUDE_RE = re.compile(r"rotation_.*_service\.py$")
_LAYER_DEPENDENCY_TARGETS = [
    PROJECT_ROOT / "strategy_app",
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "common",
]
_PRIMARY_ENTRYPOINTS = {
    "run_app.py",
    "run_live_strategy_center.py",
}
_LEGACY_ENTRYPOINT_BASELINE = {
    # Phase A will either remove these entries or turn them into compatibility shims.
    "main.py",
    "run.bat",
}

# Phase 1 is a guardrail step. These historical reverse dependencies are
# tolerated only as a baseline until Phase 2 moves the registry/service code.
_TRADING_APP_IMPORT_BASELINE = {
    "common/data_portal.py::from trading_app.services.index_service import get_index_list",
    "common/strategy_trade_panel.py::from trading_app.services.strategy_trade_view_service import get_strategy_trade_view_service",
    "common/strategy_trade_panel.py::from trading_app.services.trade_record_service import get_trade_record_service",
}


def _iter_target_files() -> list[Path]:
    files: list[Path] = []
    for target in _TARGETS:
        if target.is_file() and _INCLUDE_RE.search(target.name):
            files.append(target)
            continue
        if target.is_dir():
            files.extend(path for path in target.rglob("*.py") if _INCLUDE_RE.search(path.name))
    return sorted(files)


def _iter_python_files(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            files.append(target)
            continue
        if target.is_dir():
            files.extend(path for path in target.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def _rel_key(path: Path, line: str) -> str:
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    return f"{rel}::{line.strip()}"


def _check_entrypoints() -> int:
    missing_primary = sorted(name for name in _PRIMARY_ENTRYPOINTS if not (PROJECT_ROOT / name).exists())
    if missing_primary:
        print("primary_entrypoint_check_failed")
        print("Missing primary entrypoints:")
        for item in missing_primary:
            print(item)
        return 1

    root_entrypoints = {
        path.name
        for path in PROJECT_ROOT.iterdir()
        if path.is_file()
        and (
            path.name == "main.py"
            or path.name == "run.bat"
            or (path.suffix == ".py" and (path.name.startswith("run_") or path.name.startswith("launch_")))
        )
    }
    unexpected = sorted(root_entrypoints - _PRIMARY_ENTRYPOINTS - _LEGACY_ENTRYPOINT_BASELINE)
    if unexpected:
        print("entrypoint_check_failed")
        print("Only run_app.py and run_live_strategy_center.py are primary entrypoints. Unexpected root entrypoints:")
        for item in unexpected:
            print(item)
        return 1

    missing_legacy = sorted(_LEGACY_ENTRYPOINT_BASELINE - root_entrypoints)
    if missing_legacy:
        print("legacy_entrypoint_baseline_changed")
        print("Remove obsolete legacy entrypoint baseline entries from scripts/check_layering.py:")
        for item in missing_legacy:
            print(item)
        return 1
    return 0


def _check_removed_legacy_dirs() -> int:
    legacy_dirs = [
        PROJECT_ROOT / "app" / "perspectives" / "legacy",
    ]
    existing = [path.relative_to(PROJECT_ROOT).as_posix() for path in legacy_dirs if path.exists()]
    if existing:
        print("legacy_directory_check_failed")
        for item in existing:
            print(item)
        return 1
    return 0


def main() -> int:
    entrypoint_status = _check_entrypoints()
    if entrypoint_status:
        return entrypoint_status
    legacy_dir_status = _check_removed_legacy_dirs()
    if legacy_dir_status:
        return legacy_dir_status

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
    order_violations: list[str] = []
    for path in sorted((PROJECT_ROOT / "live_rotation").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN_LIVE_ORDER_PATTERN.search(line):
                rel = path.relative_to(PROJECT_ROOT)
                order_violations.append(f"{rel}:{line_no}: {line.strip()}")
    if order_violations:
        print("live_order_gateway_check_failed")
        for item in order_violations:
            print(item)
        return 1
    dependency_violations: list[str] = []
    baseline_hits: set[str] = set()
    for path in _iter_python_files(_LAYER_DEPENDENCY_TARGETS):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not _TRADING_APP_IMPORT_PATTERN.search(line):
                continue
            key = _rel_key(path, line)
            if key in _TRADING_APP_IMPORT_BASELINE:
                baseline_hits.add(key)
                continue
            rel = path.relative_to(PROJECT_ROOT)
            dependency_violations.append(f"{rel}:{line_no}: {line.strip()}")
    if dependency_violations:
        print("layer_dependency_check_failed")
        print("strategy_app/, app/, and common/ must not add new trading_app imports.")
        for item in dependency_violations:
            print(item)
        return 1
    missing_baseline = sorted(_TRADING_APP_IMPORT_BASELINE - baseline_hits)
    if missing_baseline:
        print("layer_dependency_baseline_changed")
        print("Remove obsolete baseline entries from scripts/check_layering.py:")
        for item in missing_baseline:
            print(item)
        return 1
    print("layering_check_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
