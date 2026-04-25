from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class LiveLogViewerWidget(QWidget):
    """Simple tail viewer for the live strategy center log."""

    def __init__(self, log_path: str | Path, parent=None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self.log_path = Path(log_path)
        self._compact = compact
        self._last_content = ""
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh_log)
        self._timer.start()
        self.refresh_log()

    def _setup_ui(self) -> None:
        self.setStyleSheet(
            "LiveLogViewerWidget { background:#1e1e1e; color:#d4d4d4; }"
            "QLabel { color:#9aa4b2; background:transparent; border:none; font-size:12px; }"
            "QCheckBox { color:#d4d4d4; background:transparent; border:none; font-size:12px; }"
            "QPushButton { background:#0078d4; color:#ffffff; border:none; border-radius:4px; padding:3px 10px; }"
            "QPushButton:hover { background:#1688dd; }"
            "QPlainTextEdit { background:#1e1e1e; color:#f3f4f6; border:1px solid #3a3a3a; "
            "selection-background-color:#264f78; font-family:Consolas; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4 if self._compact else 6, 3 if self._compact else 6, 4 if self._compact else 6, 4 if self._compact else 6)
        layout.setSpacing(3 if self._compact else 6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.path_label = QLabel(f"日志文件: {self.log_path}")
        self.path_label.setToolTip(str(self.log_path))
        self.path_label.setVisible(not self._compact)
        top_row.addWidget(self.path_label, 1)

        self.status_label = QLabel("")
        top_row.addWidget(self.status_label, 1)

        self.auto_refresh_cb = QCheckBox("自动刷新")
        self.auto_refresh_cb.setChecked(True)
        self.auto_refresh_cb.toggled.connect(self._toggle_auto_refresh)
        top_row.addWidget(self.auto_refresh_cb)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedHeight(24)
        refresh_btn.clicked.connect(self.refresh_log)
        top_row.addWidget(refresh_btn)

        open_file_btn = QPushButton("打开日志")
        open_file_btn.setFixedHeight(24)
        open_file_btn.clicked.connect(self.open_log_file)
        top_row.addWidget(open_file_btn)

        open_dir_btn = QPushButton("打开目录")
        open_dir_btn.setFixedHeight(24)
        open_dir_btn.clicked.connect(self.open_log_dir)
        top_row.addWidget(open_dir_btn)

        layout.addLayout(top_row)

        self.editor = QPlainTextEdit(self)
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setFont(QFont("Consolas", 10))
        layout.addWidget(self.editor, 1)

    def _toggle_auto_refresh(self, enabled: bool) -> None:
        if enabled:
            self._timer.start()
        else:
            self._timer.stop()

    def refresh_log(self) -> None:
        content = self._read_log_tail()
        if content != self._last_content or self.editor.toPlainText() != content:
            self.editor.setPlainText(content)
            self.editor.verticalScrollBar().setValue(self.editor.verticalScrollBar().maximum())
            self._last_content = content
        self._update_status()

    def open_log_file(self) -> None:
        if self.log_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path)))

    def open_log_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path.parent)))

    def _update_status(self) -> None:
        if not self.log_path.exists():
            self.status_label.setText("日志文件尚未生成")
            return
        try:
            stat = self.log_path.stat()
            self.status_label.setText(f"最后更新: {self.log_path.name} | {stat.st_size / 1024:.1f} KB")
        except Exception:
            self.status_label.setText("日志文件已存在")

    def _read_log_tail(self, max_bytes: int = 200_000) -> str:
        if not self.log_path.exists():
            return "日志文件尚未生成，程序运行后会自动写入。"
        try:
            with self.log_path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(size - max_bytes, 0))
                raw = f.read()
            text = raw.decode("utf-8", errors="replace")
            if len(text) >= 1 and not text.startswith("[") and "\n" in text:
                text = text.split("\n", 1)[-1]
            return text or "日志文件为空。"
        except Exception as exc:
            return f"读取日志失败: {exc}"
