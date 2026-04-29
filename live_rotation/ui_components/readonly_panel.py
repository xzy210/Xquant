"""Read-only output widgets for ETF rotation panels."""
from __future__ import annotations

from typing import Mapping

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QMenu, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget

from live_rotation.ui_components.theme import ETF_ROTATION_DARK_THEME


def _table_style(theme: Mapping[str, str]) -> str:
    t = {**ETF_ROTATION_DARK_THEME, **dict(theme or {})}
    return (
        f"QTableWidget{{"
        f"  background-color:{t['panel_bg']}; color:{t['text']};"
        f"  gridline-color:{t['table_grid']}; border:none;"
        f"  font-size:12px;"
        f"}}"
        f"QTableWidget::item{{"
        f"  padding:4px 6px;"
        f"}}"
        f"QTableWidget::item:alternate{{"
        f"  background-color:{t['table_alt']};"
        f"}}"
        f"QTableWidget::item:selected{{"
        f"  background-color:{t['selected']}; color:{t['text']};"
        f"}}"
        f"QHeaderView::section{{"
        f"  background-color:{t['table_header']}; color:{t['text_secondary']};"
        f"  border:none; border-bottom:1px solid {t['border']};"
        f"  padding:5px 6px; font-weight:bold; font-size:11px;"
        f"}}"
    )


class ETFRotationScoreTable(QTableWidget):
    """Pure ETF score table shared by research and live views."""

    market_view_requested = pyqtSignal(str, str)

    def __init__(self, theme: Mapping[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._theme = {**ETF_ROTATION_DARK_THEME, **dict(theme or {})}
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["ETF代码", "名称", "综合得分"])
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setStyleSheet(_table_style(self._theme))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def update_scores(self, scores: Mapping[str, float], *, name_map: Mapping[str, str], holding: str = "") -> None:
        t = self._theme
        sorted_items = sorted(dict(scores or {}).items(), key=lambda item: item[1], reverse=True)
        normalized_holding = str(holding or "").strip()

        self.setRowCount(len(sorted_items))
        for row, (code, score) in enumerate(sorted_items):
            code_text = str(code)
            is_holding = code_text == normalized_holding
            background = QColor(t["holding_bg"]) if is_holding else None

            code_item = QTableWidgetItem(code_text)
            code_item.setForeground(QColor(t["text"]))
            if background:
                code_item.setBackground(background)
            self.setItem(row, 0, code_item)

            name_item = QTableWidgetItem(str(name_map.get(code_text, "")))
            name_item.setForeground(QColor(t["text_secondary"]))
            if background:
                name_item.setBackground(background)
            self.setItem(row, 1, name_item)

            numeric_score = float(score or 0.0)
            score_item = QTableWidgetItem(f"{numeric_score:+.4f}")
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if numeric_score > 0:
                score_item.setForeground(QColor(t["red"]))
            elif numeric_score < 0:
                score_item.setForeground(QColor(t["green"]))
            else:
                score_item.setForeground(QColor(t["text_secondary"]))
            if background:
                score_item.setBackground(background)
            self.setItem(row, 2, score_item)

    def _on_context_menu(self, pos) -> None:
        row = self.rowAt(pos.y())
        if row < 0:
            return
        self.selectRow(row)
        code_item = self.item(row, 0)
        name_item = self.item(row, 1)
        code = code_item.text().strip() if code_item else ""
        name = name_item.text().strip() if name_item else ""
        if not code:
            return
        menu = QMenu(self)
        view_action = menu.addAction(f"查看K线 {name}({code})" if name else f"查看K线 {code}")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen == view_action:
            self.market_view_requested.emit(code, name)


class ETFRotationLogView(QTextEdit):
    """Plain read-only ETF runtime/research log view."""

    def __init__(self, theme: Mapping[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._theme = {**ETF_ROTATION_DARK_THEME, **dict(theme or {})}
        self.setReadOnly(True)
        self.setStyleSheet(
            f"QTextEdit{{font-family:Consolas,monospace;font-size:11px;"
            f"background:{self._theme['panel_bg']};color:{self._theme['text']};"
            f"border:none;}}"
        )

    def append_log(self, message: str) -> None:
        self.append(str(message))
        scroll_bar = self.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())


class ETFRotationStatisticsTable(QTableWidget):
    """Pure ETF statistics table shared by read-only panels."""

    def __init__(self, theme: Mapping[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._theme = {**ETF_ROTATION_DARK_THEME, **dict(theme or {})}
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["指标", "数值"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setStyleSheet(_table_style(self._theme))

    def update_statistics(self, rows: list[tuple[str, str]]) -> None:
        t = self._theme
        self.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            self.setItem(row, 0, QTableWidgetItem(label))
            value_item = QTableWidgetItem(value)
            value_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if any(key in label for key in ("盈亏", "收益", "单笔", "胜率", "回撤")):
                raw = value.replace(",", "").replace("%", "").replace("元", "").strip()
                try:
                    number = float(raw)
                    if number > 0:
                        value_item.setForeground(QColor(t["red"]))
                    elif number < 0:
                        value_item.setForeground(QColor(t["green"]))
                except ValueError:
                    pass
            self.setItem(row, 1, value_item)


class ETFRotationReadOnlyPanel(QWidget):
    """Display ETF scores, runtime logs, and internal statistics.

    This component intentionally receives plain data only. Runtime orchestration,
    broker access, and execution services stay in ``ETFRotationLiveWidget``.
    """

    market_view_requested = pyqtSignal(str, str)

    def __init__(self, theme: Mapping[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._theme = {**ETF_ROTATION_DARK_THEME, **dict(theme or {})}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)

        self.tabs = QTabWidget(self)
        self.score_table = ETFRotationScoreTable(self._theme, self)
        self.score_table.market_view_requested.connect(self.market_view_requested)
        self.tabs.addTab(self.score_table, "ETF得分")

        self.log_text = ETFRotationLogView(self._theme, self)
        self.tabs.addTab(self.log_text, "运行日志")

        self.stat_table = ETFRotationStatisticsTable(self._theme, self)
        self.stat_table.hide()

        layout.addWidget(self.tabs)

    def append_log(self, message: str) -> None:
        self.log_text.append_log(message)

    def update_scores(self, scores: Mapping[str, float], *, name_map: Mapping[str, str], holding: str) -> None:
        self.score_table.update_scores(scores, name_map=name_map, holding=holding)

    def update_statistics(self, rows: list[tuple[str, str]]) -> None:
        self.stat_table.update_statistics(rows)


__all__ = [
    "ETFRotationLogView",
    "ETFRotationReadOnlyPanel",
    "ETFRotationScoreTable",
    "ETFRotationStatisticsTable",
]
