from __future__ import annotations

import re
from datetime import datetime

from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


_FULL_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(:\d{2})?$")
_TIME_ONLY_PATTERN = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _display_time(value: str) -> str:
    text = str(value or "").strip()
    if not text or text == "-":
        return "-"
    if _FULL_TIME_PATTERN.match(text):
        return text
    if _TIME_ONLY_PATTERN.match(text):
        return f"{datetime.now().strftime('%Y-%m-%d')} {text}"
    if _DATE_ONLY_PATTERN.match(text):
        return text
    return text


_TASK_STATUS_LABELS = {
    "idle": "待命",
    "scheduled": "已计划",
    "triggered": "已触发",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "enabled": "已启用",
    "disabled": "已停用",
    "skipped": "已跳过",
}

_TASK_STATUS_FG_COLORS = {
    "running": QColor("#ffb454"),
    "failed": QColor("#ff6b6b"),
    "completed": QColor("#7ed957"),
    "enabled": QColor("#7ed957"),
    "disabled": QColor("#9e9e9e"),
    "skipped": QColor("#9e9e9e"),
    "triggered": QColor("#6ec1e4"),
    "scheduled": QColor("#6ec1e4"),
}

_TASK_TYPE_LABELS = {
    "system": "系统",
    "eod": "日终",
    "ai": "AI",
    "etf": "ETF",
    "etf_rotation": "ETF轮动",
}


def _display_task_status(status: str) -> str:
    value = str(status or "").strip().lower()
    return _TASK_STATUS_LABELS.get(value, status or "-")


def _display_task_type(task_type: str) -> str:
    value = str(task_type or "").strip().lower()
    return _TASK_TYPE_LABELS.get(value, task_type or "-")


class LiveStrategyTaskCenterWidget(QWidget):
    def __init__(self, task_service, parent=None) -> None:
        super().__init__(parent)
        self.task_service = task_service
        self._rows: list[dict] = []
        self._visible_rows: list[dict] = []
        self._type_filter: str = ""
        self._setup_ui()
        self.task_service.tasks_changed.connect(self._on_tasks_changed)
        self._on_tasks_changed(self.task_service.list_tasks())

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("类型"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("全部", "")
        for k, v in _TASK_TYPE_LABELS.items():
            self.type_combo.addItem(v, k)
        self.type_combo.currentIndexChanged.connect(self._on_type_filter_changed)
        filter_row.addWidget(self.type_combo)

        filter_row.addWidget(QLabel("动作"))
        self.action_combo = QComboBox()
        self.action_combo.setMinimumWidth(140)
        filter_row.addWidget(self.action_combo)

        execute_btn = QPushButton("执行动作")
        execute_btn.clicked.connect(self._execute_action)
        filter_row.addWidget(execute_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(lambda: self._on_tasks_changed(self.task_service.list_tasks()))
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch()

        self.lbl_count = QLabel("共 0 条")
        filter_row.addWidget(self.lbl_count)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["任务", "类型", "状态", "计划时间", "最近执行", "消息"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._refresh_actions_for_selection)
        layout.addWidget(self.table, 1)

    def _on_type_filter_changed(self, _idx: int) -> None:
        self._type_filter = str(self.type_combo.currentData() or "")
        self._render_table()

    def _on_tasks_changed(self, tasks: list[dict]) -> None:
        self._rows = list(tasks or [])
        self._render_table()

    def _render_table(self) -> None:
        filtered = [
            item for item in self._rows
            if not self._type_filter
            or str(item.get("task_type", "") or "").strip().lower() == self._type_filter
        ]
        self.table.setRowCount(len(filtered))
        self._visible_rows = filtered
        for row, item in enumerate(filtered):
            last_run_raw = (
                item.get("last_run")
                or item.get("finished_at")
                or item.get("started_at")
                or ""
            )
            status_raw = str(item.get("status", "") or "").strip().lower()
            values = [
                str(item.get("title", "") or item.get("task_key", "")),
                _display_task_type(str(item.get("task_type", "") or "")),
                _display_task_status(status_raw),
                str(item.get("schedule_time", "") or "-"),
                _display_time(str(last_run_raw)),
                str(item.get("message", "") or "-"),
            ]
            fg = _TASK_STATUS_FG_COLORS.get(status_raw)
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if fg is not None and col == 2:
                    cell.setForeground(QBrush(fg))
                self.table.setItem(row, col, cell)
        self.lbl_count.setText(f"共 {len(filtered)} 条")
        self._refresh_actions_for_selection()

    def _selected_task(self) -> dict:
        row = self.table.currentRow()
        rows = self._visible_rows or self._rows
        if row < 0 or row >= len(rows):
            return {}
        return dict(rows[row] or {})

    def _refresh_actions_for_selection(self) -> None:
        self.action_combo.clear()
        task = self._selected_task()
        for action in list(task.get("available_actions", []) or []):
            self.action_combo.addItem(action, action)

    def _execute_action(self) -> None:
        task = self._selected_task()
        action = str(self.action_combo.currentData() or "")
        if not task or not action:
            QMessageBox.information(self, "任务中心", "请先选择任务和动作。")
            return
        ok, message = self.task_service.run_action(str(task.get("task_key", "") or ""), action)
        if ok:
            QMessageBox.information(self, "任务中心", message)
        else:
            QMessageBox.warning(self, "任务中心", message)
        self._on_tasks_changed(self.task_service.list_tasks())
