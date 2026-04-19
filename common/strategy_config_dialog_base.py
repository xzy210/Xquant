"""Shared shell for strategy configuration dialogs.

统一 AI / ETF 等策略的“配置”对话框外壳：
1. 标题、尺寸和边距一致
2. 内容区可滚动
3. 默认只读，底部提供统一的“关闭 / 解锁编辑”操作
4. 可复用现有配置表单，避免复制复杂控件树
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

_CLOSE_BUTTON_STYLE = (
    "QPushButton{background:#374151;color:white;padding:6px 16px;"
    "border-radius:6px;font-size:12px;}"
    "QPushButton:hover{background:#1F2937;}"
    "QPushButton:disabled{background:#9CA3AF;color:white;}"
)

_UNLOCK_BUTTON_STYLE = (
    "QPushButton{background:#D97706;color:white;padding:6px 16px;"
    "border-radius:6px;font-size:12px;font-weight:600;}"
    "QPushButton:hover{background:#B45309;}"
    "QPushButton:disabled{background:#FCD34D;color:#78350F;}"
)


class BaseStrategyConfigDialog(QDialog):
    """Common shell used by strategy configuration dialogs."""

    def __init__(
        self,
        *,
        title: str = "策略配置",
        min_width: int = 760,
        initial_height: int = 660,
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

    def make_note_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color:#6B7280;font-size:11px;line-height:1.4;")
        return label

    def make_close_button(
        self,
        text: str = "关闭",
        handler: Optional[Callable] = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(34)
        btn.setStyleSheet(_CLOSE_BUTTON_STYLE)
        btn.clicked.connect(handler or self.reject)
        return btn

    def make_unlock_button(
        self,
        text: str = "🔓 解锁编辑",
        handler: Optional[Callable] = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(34)
        btn.setStyleSheet(_UNLOCK_BUTTON_STYLE)
        if handler is not None:
            btn.clicked.connect(handler)
        return btn

    def setup_footer(
        self,
        *,
        close_text: str = "关闭",
        close_handler: Optional[Callable] = None,
        unlock_text: str = "🔓 解锁编辑",
        unlock_handler: Optional[Callable] = None,
        left_widgets: Optional[Iterable[QWidget]] = None,
    ) -> tuple[QPushButton, QPushButton]:
        while self.footer_layout.count():
            item = self.footer_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        for widget in left_widgets or ():
            self.footer_layout.addWidget(widget)

        self.footer_layout.addStretch()

        close_btn = self.make_close_button(close_text, close_handler)
        unlock_btn = self.make_unlock_button(unlock_text, unlock_handler)
        self.footer_layout.addWidget(close_btn)
        self.footer_layout.addWidget(unlock_btn)
        return close_btn, unlock_btn
