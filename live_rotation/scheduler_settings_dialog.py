"""ETF 定时任务设置对话框。

把原先嵌在 `live_rotation.widget` 左侧栏里的定时任务表单抽成独立 `QDialog`，
与 AI 策略当前的“按钮 -> 独立设置面板”交互方式对齐。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from common.scheduler_dialog_base import BaseSchedulerSettingsDialog


class ETFSchedulerSettingsDialog(BaseSchedulerSettingsDialog):
    """Configure ETF scheduled tasks in a standalone dialog."""

    def __init__(
        self,
        engine,
        *,
        log_callback: Optional[Callable[[str], None]] = None,
        refresh_callback: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(title="定时任务设置", min_width=560, initial_height=430, parent=parent)
        self.engine = engine
        self._log = log_callback or (lambda _msg: None)
        self._refresh_callback = refresh_callback
        self._setup_ui()
        self.load_from_engine()

    def _setup_ui(self) -> None:
        self.content_layout.addWidget(
            self.make_note_label(
                "说明：修改后点击底部“保存并关闭”生效。ETF 会先按数据更新时间补数据，再按信号检查时间执行巡检。"
            )
        )

        group = QGroupBox("调度任务：ETF 轮动")
        form = QFormLayout(group)
        form.setSpacing(6)
        self.content_layout.addWidget(group)

        self.chk_auto_enabled = QCheckBox("启用任务")
        form.addRow("", self.chk_auto_enabled)

        self.chk_auto_signal = QCheckBox("到点后自动生成信号")
        form.addRow("", self.chk_auto_signal)

        self.edit_update_time = QLineEdit()
        self.edit_update_time.setPlaceholderText("HH:MM")
        self.edit_update_time.setMaximumWidth(90)
        self.edit_update_time.setToolTip("ETF 数据自动更新时间")
        form.addRow("数据更新时间:", self.edit_update_time)

        self.edit_time = QLineEdit()
        self.edit_time.setPlaceholderText("HH:MM")
        self.edit_time.setMaximumWidth(90)
        self.edit_time.setToolTip("ETF 信号检查时间")
        form.addRow("信号检查时间:", self.edit_time)

        self.chk_notify = QCheckBox("完成后发送通知")
        form.addRow("", self.chk_notify)

        self.lbl_schedule_last_run = QLabel("从未执行")
        self.lbl_schedule_last_run.setStyleSheet("color:#888888;")
        form.addRow("最近执行:", self.lbl_schedule_last_run)

        self.lbl_schedule_last_result = QLabel("-")
        self.lbl_schedule_last_result.setStyleSheet("color:#888888;")
        form.addRow("最近结果:", self.lbl_schedule_last_result)

        self.lbl_schedule_tip = QLabel(
            "流程说明：数据更新时间用于自动补数据，信号检查时间用于生成轮动信号；交易下单统一由实盘策略中心执行。"
        )
        self.lbl_schedule_tip.setWordWrap(True)
        self.lbl_schedule_tip.setStyleSheet("color:#888888;font-size:11px;")
        form.addRow("", self.lbl_schedule_tip)

        self.btn_update_now = self.make_action_button("立即更新数据", self._update_now)
        self.btn_run_now = self.make_action_button("立即执行一次", self._run_now)
        self.btn_save, self.btn_cancel = self.setup_footer(
            primary_text="保存并关闭",
            primary_handler=self._save,
            secondary_text="取消",
            secondary_handler=self.reject,
            left_buttons=[self.btn_update_now, self.btn_run_now],
        )

    def load_from_engine(self) -> None:
        """Reload current persisted scheduler config from the engine."""
        cfg = self.engine.config
        self.chk_auto_enabled.setChecked(bool(cfg.auto_enabled))
        self.chk_auto_signal.setChecked(bool(getattr(cfg, "auto_signal_enabled", True)))
        self.edit_update_time.setText(str(cfg.data_update_time or "14:30"))
        self.edit_time.setText(str(cfg.check_time or "14:50"))
        self.chk_notify.setChecked(bool(cfg.notify_on_signal))
        self.refresh_runtime_status()

    def refresh_runtime_status(self) -> None:
        state = self.engine.state
        last_date = str(getattr(state, "last_check_date", "") or "").strip()
        last_time = str(getattr(state, "last_check_time", "") or "").strip()
        if last_date and last_time:
            self.lbl_schedule_last_run.setText(f"{last_date} {last_time}")
        else:
            self.lbl_schedule_last_run.setText("从未执行")

        signal = str(getattr(state, "last_signal", "") or "").strip()
        if signal:
            signal_map = {
                "HOLD": "HOLD",
                "SWITCH": "SWITCH",
                "SELL_ALL": "SELL_ALL",
                "BUY": "BUY",
                "NO_ACTION": "NO_ACTION",
                "COOLDOWN": "COOLDOWN",
                "TRAILING_STOP": "TRAILING_STOP",
                "DRAWDOWN_STOP": "DRAWDOWN_STOP",
            }
            self.lbl_schedule_last_result.setText(signal_map.get(signal, signal))
            color = {
                "BUY": "#16A34A",
                "SWITCH": "#D97706",
                "SELL_ALL": "#DC2626",
                "TRAILING_STOP": "#EA580C",
                "DRAWDOWN_STOP": "#DC2626",
                "COOLDOWN": "#6B7280",
            }.get(signal, "#2563EB")
            self.lbl_schedule_last_result.setStyleSheet(f"color:{color};font-weight:bold;")
        else:
            self.lbl_schedule_last_result.setText("-")
            self.lbl_schedule_last_result.setStyleSheet("color:#6B7B8D;")

    def _save(self) -> None:
        cfg = self.engine.config
        previous_auto_enabled = bool(cfg.auto_enabled)
        cfg.data_update_time = self.edit_update_time.text().strip() or "14:30"
        cfg.check_time = self.edit_time.text().strip() or "14:50"
        cfg.notify_on_signal = self.chk_notify.isChecked()
        cfg.notify_on_trade = self.chk_notify.isChecked()
        cfg.auto_enabled = self.chk_auto_enabled.isChecked()
        cfg.auto_signal_enabled = self.chk_auto_signal.isChecked()
        self.engine.update_config(cfg)

        if cfg.auto_enabled and not previous_auto_enabled:
            self.engine.start_auto()
        elif not cfg.auto_enabled and previous_auto_enabled:
            self.engine.stop_auto()
        elif cfg.auto_enabled and previous_auto_enabled:
            self.engine.stop_auto()
            self.engine.start_auto()
        else:
            self.engine.stop_auto()

        if self._refresh_callback:
            self._refresh_callback()

        QMessageBox.information(
            self,
            "提示",
            f"定时任务配置已保存（更新 {cfg.data_update_time} / 检查 {cfg.check_time} / "
            f"{'自动生成信号' if cfg.auto_signal_enabled else '仅手动检查'}）",
        )
        self.accept()

    def _run_now(self) -> None:
        run_signal_check = self.chk_auto_signal.isChecked()
        update_time = self.edit_update_time.text().strip() or "14:30"
        check_time = self.edit_time.text().strip() or "14:50"

        self.btn_run_now.setEnabled(False)
        self.btn_run_now.setText("执行中...")
        try:
            if not self.engine.is_data_fresh():
                self._log("🕒 手动触发定时任务：先更新数据")
                self.engine.update_data(
                    run_signal_check_after=run_signal_check,
                    schedule_context={
                        "trigger": "manual",
                        "task_date": datetime.now().strftime("%Y-%m-%d"),
                        "schedule_time": update_time,
                    },
                )
            else:
                self._log("🕒 手动触发定时任务：直接检查信号")
                if run_signal_check:
                    self.engine.run_signal_check(
                        schedule_context={
                            "trigger": "manual",
                            "task_date": datetime.now().strftime("%Y-%m-%d"),
                            "schedule_time": check_time,
                        },
                    )
        finally:
            self.btn_run_now.setEnabled(True)
            self.btn_run_now.setText("立即执行一次")
            self.refresh_runtime_status()
            if self._refresh_callback:
                self._refresh_callback()

    def _update_now(self) -> None:
        self.btn_update_now.setEnabled(False)
        self.btn_update_now.setText("更新中...")
        try:
            self._log("🕒 手动触发数据更新：仅更新 ETF 数据")
            self.engine.update_data(run_signal_check_after=False)
            QMessageBox.information(self, "提示", "已开始后台更新 ETF 数据。")
        finally:
            self.btn_update_now.setEnabled(True)
            self.btn_update_now.setText("立即更新数据")
            if self._refresh_callback:
                self._refresh_callback()
