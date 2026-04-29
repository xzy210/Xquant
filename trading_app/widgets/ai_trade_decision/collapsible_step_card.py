# -*- coding: utf-8 -*-
"""???? AI ???????"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFontMetrics, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextEdit, QToolButton, QVBoxLayout, QWidget


class CollapsibleStepCard(QWidget):
    """A small collapsible card used to display one summarized progress step."""

    STATUS_STYLES = {
        "pending": ("●", "#888888", "#242424"),
        "running": ("◔", "#0078d4", "#1c2733"),
        "done": ("●", "#107c10", "#1f2a1f"),
        "warning": ("●", "#d8a300", "#322b17"),
    }

    def __init__(
        self,
        title: str,
        detail: str = "",
        status: str = "pending",
        parent=None,
        *,
        action_label: str = "",
        action_callback=None,
        preview_path: str = "",
    ):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._action_callback = None
        self._preview_path = ""
        self._setup_ui()
        self.set_content(
            title,
            detail,
            status=status,
            action_label=action_label,
            action_callback=action_callback,
            preview_path=preview_path,
        )

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header_btn = QToolButton()
        self.header_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header_btn.setArrowType(Qt.ArrowType.RightArrow)
        self.header_btn.setCheckable(True)
        self.header_btn.setChecked(False)
        self.header_btn.clicked.connect(self._toggle_expanded)
        self.header_btn.setStyleSheet(
            """
            QToolButton {
                text-align: left;
                padding: 5px 8px;
                border: 1px solid #333333;
                border-bottom: none;
                font-weight: bold;
                color: #f0f0f0;
            }
            """
        )
        layout.addWidget(self.header_btn)

        self.detail_label = QTextEdit()
        self.detail_label.setReadOnly(True)
        self.detail_label.setVisible(False)
        self.detail_label.setMinimumHeight(0)
        self.detail_label.setMaximumHeight(320)
        self.detail_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.detail_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.detail_label.document().setDocumentMargin(2)
        self.detail_label.setStyleSheet(
            """
            QTextEdit {
                color: #d0d0d0;
                padding: 3px 8px;
                border: 1px solid #333333;
                border-top: none;
                background-color: #171717;
                selection-background-color: #264f78;
            }
            """
        )
        layout.addWidget(self.detail_label)

        self.preview_label = QLabel("")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setVisible(False)
        self.preview_label.setStyleSheet(
            """
            QLabel {
                background-color: #111111;
                border: 1px solid #333333;
                border-top: none;
                padding: 8px;
            }
            """
        )
        layout.addWidget(self.preview_label)

        self.action_row = QWidget()
        action_layout = QHBoxLayout(self.action_row)
        action_layout.setContentsMargins(10, 0, 10, 8)
        action_layout.addStretch()
        self.action_btn = QPushButton("打开证据文件/图片")
        self.action_btn.setVisible(False)
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2b579a;
                color: white;
                border: 1px solid #3d6db5;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #3568b2;
            }
            """
        )
        self.action_btn.clicked.connect(self._on_action_clicked)
        action_layout.addWidget(self.action_btn)
        layout.addWidget(self.action_row)
        self.action_row.setVisible(False)

        self.children_host = QWidget()
        self.children_layout = QVBoxLayout(self.children_host)
        self.children_layout.setContentsMargins(18, 2, 0, 0)
        self.children_layout.setSpacing(2)
        self.children_host.setVisible(False)
        layout.addWidget(self.children_host)

    def _toggle_expanded(self):
        expanded = self.header_btn.isChecked()
        self.header_btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.detail_label.setVisible(expanded)
        if expanded:
            self._adjust_detail_height()
        preview_pixmap = self.preview_label.pixmap()
        self.preview_label.setVisible(expanded and preview_pixmap is not None and not preview_pixmap.isNull())
        self.action_row.setVisible(expanded and self.action_btn.isVisible())
        self.children_host.setVisible(expanded and self.children_layout.count() > 0)

    def set_content(
        self,
        title: str,
        detail: str,
        *,
        status: str = "pending",
        action_label: str = "",
        action_callback=None,
        preview_path: str = "",
    ):
        self.title_text = title
        self.detail_text = detail or "无额外说明"
        self.status = status
        self._action_callback = action_callback
        self._preview_path = preview_path or ""
        dot, color, bg = self.STATUS_STYLES.get(status, self.STATUS_STYLES["pending"])
        self.header_btn.setText(f"{dot} {title}")
        self.header_btn.setStyleSheet(
            f"""
            QToolButton {{
                text-align: left;
                padding: 5px 8px;
                border: 1px solid #333333;
                border-bottom: none;
                font-weight: bold;
                color: {color};
                background-color: {bg};
            }}
            """
        )
        self.detail_label.setPlainText(self.detail_text)
        self._adjust_detail_height()
        QTimer.singleShot(0, self._adjust_detail_height)
        self.action_btn.setText(action_label or "打开证据文件/图片")
        self.action_btn.setVisible(callable(action_callback))
        self.action_row.setVisible(self.header_btn.isChecked() and self.action_btn.isVisible())
        self._update_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if self.detail_label.isVisible():
            QTimer.singleShot(0, self._adjust_detail_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._adjust_detail_height)

    def _adjust_detail_height(self):
        if not hasattr(self, "detail_label"):
            return
        if not self.detail_label.isVisible():
            return
        viewport = self.detail_label.viewport()
        if viewport is None:
            return
        vp_width = viewport.width()
        if vp_width < 50:
            return
        content_text = self.detail_label.toPlainText() or ""
        width = vp_width - 8
        metrics = QFontMetrics(self.detail_label.font())
        rect = metrics.boundingRect(
            0,
            0,
            width,
            10000,
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs,
            content_text,
        )
        padding = self.detail_label.frameWidth() * 2 + 10
        target_height = rect.height() + padding
        target_height = max(22, min(320, target_height))
        self.detail_label.setFixedHeight(target_height)

    def _update_preview(self):
        if not self._preview_path or not os.path.exists(self._preview_path):
            self.preview_label.clear()
            self.preview_label.setVisible(False)
            return
        pixmap = QPixmap(self._preview_path)
        if pixmap.isNull():
            self.preview_label.clear()
            self.preview_label.setVisible(False)
            return
        scaled = pixmap.scaled(
            760,
            420,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        self.preview_label.setVisible(self.header_btn.isChecked())

    def expand(self):
        if not self.header_btn.isChecked():
            self.header_btn.click()
        QTimer.singleShot(0, self._adjust_detail_height)

    def clear_children(self):
        while self.children_layout.count():
            item = self.children_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.children_host.setVisible(False)

    def add_child_card(self, child_card: "CollapsibleStepCard"):
        self.children_layout.addWidget(child_card)
        if self.header_btn.isChecked():
            self.children_host.setVisible(True)

    def _on_action_clicked(self):
        if callable(self._action_callback):
            self._action_callback()

