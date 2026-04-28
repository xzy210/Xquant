"""Read-only output widgets for the ETF rotation live panel."""
from __future__ import annotations

from typing import Mapping

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget


class ETFRotationReadOnlyPanel(QWidget):
    """Display ETF scores, runtime logs, and internal statistics.

    This component intentionally receives plain data only. Runtime orchestration,
    broker access, and execution services stay in ``ETFRotationLiveWidget``.
    """

    def __init__(self, theme: Mapping[str, str], parent=None) -> None:
        super().__init__(parent)
        self._theme = dict(theme)
        self._setup_ui()

    def _setup_ui(self) -> None:
        t = self._theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)

        self.tabs = QTabWidget(self)
        table_style = (
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

        self.score_table = QTableWidget(self)
        self.score_table.setColumnCount(3)
        self.score_table.setHorizontalHeaderLabels(["ETF代码", "名称", "综合得分"])
        self.score_table.horizontalHeader().setStretchLastSection(True)
        self.score_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.score_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.score_table.setAlternatingRowColors(True)
        self.score_table.setStyleSheet(table_style)
        self.tabs.addTab(self.score_table, "ETF得分")

        self.log_text = QTextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            f"QTextEdit{{font-family:Consolas,monospace;font-size:11px;"
            f"background:{t['panel_bg']};color:{t['text']};"
            f"border:none;}}"
        )
        self.tabs.addTab(self.log_text, "运行日志")

        self.stat_table = QTableWidget(self)
        self.stat_table.setColumnCount(2)
        self.stat_table.setHorizontalHeaderLabels(["指标", "数值"])
        self.stat_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.stat_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.stat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stat_table.setAlternatingRowColors(True)
        self.stat_table.verticalHeader().setVisible(False)
        self.stat_table.setStyleSheet(table_style)
        self.stat_table.hide()

        layout.addWidget(self.tabs)

    def append_log(self, message: str) -> None:
        self.log_text.append(str(message))
        scroll_bar = self.log_text.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def update_scores(self, scores: Mapping[str, float], *, name_map: Mapping[str, str], holding: str) -> None:
        t = self._theme
        sorted_items = sorted(dict(scores or {}).items(), key=lambda item: item[1], reverse=True)

        self.score_table.setRowCount(len(sorted_items))
        for row, (code, score) in enumerate(sorted_items):
            is_holding = code == holding
            background = QColor(t["holding_bg"]) if is_holding else None

            code_item = QTableWidgetItem(str(code))
            code_item.setForeground(QColor(t["text"]))
            if background:
                code_item.setBackground(background)
            self.score_table.setItem(row, 0, code_item)

            name_item = QTableWidgetItem(str(name_map.get(code, "")))
            name_item.setForeground(QColor(t["text_secondary"]))
            if background:
                name_item.setBackground(background)
            self.score_table.setItem(row, 1, name_item)

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
            self.score_table.setItem(row, 2, score_item)

    def update_statistics(self, rows: list[tuple[str, str]]) -> None:
        t = self._theme
        self.stat_table.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            self.stat_table.setItem(row, 0, QTableWidgetItem(label))
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
            self.stat_table.setItem(row, 1, value_item)
