from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _check_research_entry() -> None:
    from app.main import XquantMainWindow, create_application
    import run_app

    if not callable(getattr(run_app, "main", None)):
        raise RuntimeError("run_app.main is not callable")

    app = create_application(["entrypoint_smoketest"])
    window = XquantMainWindow()
    try:
        if window.windowTitle() != "Xquant 策略研究台":
            raise RuntimeError(f"unexpected research window title: {window.windowTitle()}")
        if window.workspace.count() != 0:
            raise RuntimeError("research workspace should start without opened tabs")
    finally:
        window.close()
        app.processEvents()


def _check_live_entry() -> None:
    import run_live_strategy_center
    from trading_app.widgets.live_strategy_hub_widget import LiveStrategyHubWidget, LiveStrategyHubWindow

    if not callable(getattr(run_live_strategy_center, "main", None)):
        raise RuntimeError("run_live_strategy_center.main is not callable")
    if LiveStrategyHubWidget.TAB_AI != "ai":
        raise RuntimeError("unexpected live strategy hub tab constant")
    if LiveStrategyHubWindow.__name__ != "LiveStrategyHubWindow":
        raise RuntimeError("live strategy hub window import failed")


def main() -> int:
    _check_research_entry()
    _check_live_entry()
    print("entrypoint_smoketest_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
