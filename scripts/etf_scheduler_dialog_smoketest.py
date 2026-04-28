"""Headless smoketest for ETF scheduler settings dialog.

Run::
    conda run -n stock --no-capture-output python scripts/etf_scheduler_dialog_smoketest.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from live_rotation.scheduler_settings_dialog import ETFSchedulerSettingsDialog  # noqa: E402


@dataclass
class FakeConfig:
    auto_enabled: bool = True
    auto_signal_enabled: bool = True
    auto_execute_enabled: bool = False
    data_update_time: str = "14:40"
    check_time: str = "14:50"
    notify_on_signal: bool = True
    notify_on_trade: bool = True


class FakeEngine:
    def __init__(self) -> None:
        self.config = FakeConfig()
        self.state = SimpleNamespace(
            last_check_date="2026-04-19",
            last_check_time="14:50:00",
            last_signal="SWITCH",
        )
        self.updated_config = None
        self.start_auto_calls = 0
        self.stop_auto_calls = 0
        self.data_fresh = False
        self.update_data_calls = []
        self.run_signal_calls = []

    def update_config(self, cfg) -> None:
        self.updated_config = cfg

    def start_auto(self) -> None:
        self.start_auto_calls += 1

    def stop_auto(self) -> None:
        self.stop_auto_calls += 1

    def is_data_fresh(self) -> bool:
        return self.data_fresh

    def update_data(self, **kwargs) -> None:
        self.update_data_calls.append(kwargs)

    def run_signal_check(self, **kwargs) -> None:
        self.run_signal_calls.append(kwargs)


def test_load_from_engine() -> None:
    engine = FakeEngine()
    dialog = ETFSchedulerSettingsDialog(engine)
    assert dialog.chk_auto_enabled.isChecked() is True
    assert dialog.chk_auto_signal.isChecked() is True
    assert dialog.chk_auto_execute.isChecked() is False
    assert dialog.edit_update_time.text() == "14:40"
    assert dialog.edit_time.text() == "14:50"
    assert dialog.chk_notify.isChecked() is True
    assert "2026-04-19" in dialog.lbl_schedule_last_run.text()
    assert dialog.lbl_schedule_last_result.text() == "SWITCH"
    print("[load_from_engine] OK")


def test_save_updates_engine_and_accepts() -> None:
    engine = FakeEngine()
    refresh_calls: list[str] = []
    dialog = ETFSchedulerSettingsDialog(
        engine,
        refresh_callback=lambda: refresh_calls.append("refresh"),
    )
    dialog.chk_auto_enabled.setChecked(False)
    dialog.chk_auto_signal.setChecked(False)
    dialog.chk_auto_execute.setChecked(True)
    dialog.chk_notify.setChecked(False)
    dialog.edit_update_time.setText("14:35")
    dialog.edit_time.setText("14:55")

    with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
        dialog._save()

    assert engine.updated_config is engine.config
    assert engine.config.auto_enabled is False
    assert engine.config.auto_signal_enabled is False
    assert engine.config.auto_execute_enabled is True
    assert engine.config.notify_on_signal is False
    assert engine.config.notify_on_trade is False
    assert engine.config.data_update_time == "14:35"
    assert engine.config.check_time == "14:55"
    assert engine.stop_auto_calls == 1
    assert refresh_calls == ["refresh"]
    assert dialog.result() == dialog.DialogCode.Accepted
    print("[save_updates_engine_and_accepts] OK")


def test_run_now_updates_data_when_stale() -> None:
    engine = FakeEngine()
    engine.data_fresh = False
    logs: list[str] = []
    dialog = ETFSchedulerSettingsDialog(engine, log_callback=logs.append)
    dialog.chk_auto_signal.setChecked(False)
    dialog.edit_update_time.setText("14:41")

    dialog._run_now()

    assert len(engine.update_data_calls) == 1
    call = engine.update_data_calls[0]
    assert call["run_signal_check_after"] is False
    assert call["schedule_context"]["trigger"] == "manual_scan"
    assert call["schedule_context"]["schedule_time"] == "14:41"
    assert not engine.run_signal_calls
    assert logs and "先更新数据" in logs[-1]
    print("[run_now_updates_data_when_stale] OK")


def test_run_now_checks_signal_when_fresh() -> None:
    engine = FakeEngine()
    engine.data_fresh = True
    logs: list[str] = []
    dialog = ETFSchedulerSettingsDialog(engine, log_callback=logs.append)
    dialog.chk_auto_signal.setChecked(True)
    dialog.chk_auto_execute.setChecked(True)
    dialog.edit_time.setText("14:58")

    dialog._run_now()

    assert len(engine.run_signal_calls) == 1
    call = engine.run_signal_calls[0]
    assert "auto_execute" not in call
    assert call["schedule_context"]["trigger"] == "manual"
    assert call["schedule_context"]["schedule_time"] == "14:58"
    assert not engine.update_data_calls
    assert logs and "直接检查信号" in logs[-1]
    print("[run_now_checks_signal_when_fresh] OK")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    test_load_from_engine()
    test_save_updates_engine_and_accepts()
    test_run_now_updates_data_when_stale()
    test_run_now_checks_signal_when_fresh()
    print("ALL_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
