"""Shared shell for strategy scheduler settings dialogs.

统一 AI / ETF 等策略的“定时任务设置”对话框外壳：
1. 标题、尺寸和边距一致
2. 内容区可滚动
3. 底部操作区固定在窗口底部
4. 操作按钮（保存 / 取消 / 立即执行）样式一致
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

_PRIMARY_BUTTON_STYLE = (
    "QPushButton{background:#2563EB;color:white;padding:6px 16px;"
    "border-radius:6px;font-size:12px;font-weight:600;}"
    "QPushButton:hover{background:#1D4ED8;}"
    "QPushButton:disabled{background:#93C5FD;color:white;}"
)

_SECONDARY_BUTTON_STYLE = (
    "QPushButton{background:#374151;color:white;padding:6px 16px;"
    "border-radius:6px;font-size:12px;}"
    "QPushButton:hover{background:#1F2937;}"
    "QPushButton:disabled{background:#9CA3AF;color:white;}"
)

_ACTION_BUTTON_STYLE = (
    "QPushButton{background:#0EA5E9;color:white;padding:6px 14px;"
    "border-radius:6px;font-size:12px;}"
    "QPushButton:hover{background:#0284C7;}"
    "QPushButton:disabled{background:#7DD3FC;color:white;}"
)


class BaseSchedulerSettingsDialog(QDialog):
    """Common shell used by strategy scheduler dialogs."""

    def __init__(
        self,
        *,
        title: str = "定时任务设置",
        min_width: int = 560,
        initial_height: int = 460,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(min_width)
        self.resize(min_width, initial_height)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, 1)

        self._content_widget = QWidget(self._scroll)
        self.content_layout = QVBoxLayout(self._content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        self._scroll.setWidget(self._content_widget)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#E5E7EB;")
        root.addWidget(sep)

        self.footer_layout = QHBoxLayout()
        self.footer_layout.setContentsMargins(0, 0, 0, 0)
        self.footer_layout.setSpacing(8)
        root.addLayout(self.footer_layout)

    def make_primary_button(
        self,
        text: str,
        handler: Optional[Callable] = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(34)
        btn.setStyleSheet(_PRIMARY_BUTTON_STYLE)
        if handler is not None:
            btn.clicked.connect(handler)
        return btn

    def make_secondary_button(
        self,
        text: str,
        handler: Optional[Callable] = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(34)
        btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
        if handler is not None:
            btn.clicked.connect(handler)
        return btn

    def make_action_button(
        self,
        text: str,
        handler: Optional[Callable] = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(32)
        btn.setStyleSheet(_ACTION_BUTTON_STYLE)
        if handler is not None:
            btn.clicked.connect(handler)
        return btn

    def make_note_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color:#6B7280;font-size:11px;line-height:1.4;")
        return label

    def setup_footer(
        self,
        *,
        primary_text: str = "保存并关闭",
        primary_handler: Optional[Callable] = None,
        secondary_text: str = "取消",
        secondary_handler: Optional[Callable] = None,
        left_buttons: Optional[Iterable[QPushButton]] = None,
    ) -> tuple[QPushButton, QPushButton]:
        """Build a consistent footer: left actions + right cancel/save."""
        while self.footer_layout.count():
            item = self.footer_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        for btn in left_buttons or ():
            self.footer_layout.addWidget(btn)

        self.footer_layout.addStretch()

        secondary = self.make_secondary_button(
            secondary_text,
            secondary_handler or self.reject,
        )
        primary = self.make_primary_button(primary_text, primary_handler)
        self.footer_layout.addWidget(secondary)
        self.footer_layout.addWidget(primary)
        return primary, secondary
