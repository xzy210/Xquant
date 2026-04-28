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

# Phase 1 is a guardrail step. These historical reverse dependencies are
# tolerated only as a baseline until Phase 2 moves the registry/service code.
_TRADING_APP_IMPORT_BASELINE = {
    "common/data_portal.py::from trading_app.services.index_service import get_index_list",
    "common/strategy_trade_panel.py::from trading_app.services.strategy_trade_view_service import get_strategy_trade_view_service",
    "common/strategy_trade_panel.py::from trading_app.services.trade_record_service import get_trade_record_service",
    "strategy_app/backtest/broker.py::from trading_app.services.trade_record_service import TradeRecordService",
    "strategy_app/backtest/engine.py::from trading_app.services.auto_trade_config_service import AutoTradeConfig",
    "strategy_app/backtest/engine.py::from trading_app.services.trade_execution_service import TradeExecutionService",
    "strategy_app/factors/financial_data.py::from trading_app.factors.financial_data import FinancialDataLoader",
    "strategy_app/factors/__init__.py::from trading_app.factors import factor_registry",
    "strategy_app/factors/__init__.py::from trading_app.factors import FactorPreprocessor, preprocess_factors",
    "strategy_app/factors/preprocessor.py::from trading_app.factors.preprocessor import FactorPreprocessor",
    "strategy_app/factors/registry.py::from trading_app.factors import factor_registry",
    "strategy_app/strategies/__init__.py::from trading_app.services.strategy_registry_service import get_strategy_registry_service",
    "strategy_app/widgets/stock_screener_widget.py::from trading_app.notifier import get_notification_manager",
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
