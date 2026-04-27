# -*- coding: utf-8 -*-
"""New dual-track application entry based on the common UI shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.events import BacktestEvent, EventBus
from common.experiment_store import ExperimentRecord, ExperimentStore
from common.ui import BaseMainWindow, Command, Perspective
from common.ui.themes import DARK_THEME_QSS

from app.perspectives.legacy import create_legacy_rotation_tab, create_legacy_strategy_tab


class ExperimentRecordPanel(QWidget):
    """Small dock panel listing persisted experiment records."""

    def __init__(self, store: ExperimentStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.list_widget = QListWidget(self)
        self.empty_label = QLabel("No experiments found", self)
        self.empty_label.setProperty("class", "description")

        refresh_btn = QPushButton("Refresh", self)
        refresh_btn.clicked.connect(self.refresh)

        header = QHBoxLayout()
        header.addWidget(QLabel("Experiment Records", self))
        header.addStretch(1)
        header.addWidget(refresh_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.empty_label)

        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        records = self.store.query()
        for record in records:
            self.list_widget.addItem(QListWidgetItem(self._format_record(record)))
        self.empty_label.setVisible(not records)

    @staticmethod
    def _format_record(record: ExperimentRecord) -> str:
        title = record.strategy_id or "unknown_strategy"
        suffix = f" / {record.params_hash}" if record.params_hash else ""
        final_value = f" / final={record.final_value:.2f}" if record.final_value is not None else ""
        return f"{title}{suffix}{final_value}\n{record.run_id}  {record.created_at}"


class StrategyTreePanel(QWidget):
    """Navigation dock for current legacy strategy entry points."""

    def __init__(self, open_strategy: Callable[[], None], open_rotation: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._open_strategy = open_strategy
        self._open_rotation = open_rotation

        self.list_widget = QListWidget(self)
        for title, command_id in (
            ("Strategy Research", "legacy.strategy"),
            ("ETF Rotation Live", "legacy.rotation"),
        ):
            item = QListWidgetItem(title, self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, command_id)
        self.list_widget.itemDoubleClicked.connect(self._open_item)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Strategy Tree", self))
        layout.addWidget(self.list_widget, 1)

    def _open_item(self, item: QListWidgetItem) -> None:
        command_id = item.data(Qt.ItemDataRole.UserRole)
        if command_id == "legacy.strategy":
            self._open_strategy()
        elif command_id == "legacy.rotation":
            self._open_rotation()


class EventLogPanel(QWidget):
    """Bottom dock showing backtest events and shell logs."""

    def __init__(self, event_bus: EventBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.event_bus = event_bus
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self._unsubscribe = event_bus.subscribe(self.append_event)

        layout = QVBoxLayout(self)
        layout.addWidget(self.text_edit)

    def append_message(self, message: str) -> None:
        self.text_edit.append(message)

    def append_event(self, event: BacktestEvent) -> None:
        progress = ""
        if event.progress_current is not None and event.progress_total is not None:
            progress = f" [{event.progress_current}/{event.progress_total}]"
        text = event.message or str(event.payload or "")
        self.append_message(f"{event.event_type}{progress}: {text}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._unsubscribe()
        super().closeEvent(event)


class XquantMainWindow(BaseMainWindow):
    """New application shell with legacy tabs and shared docks."""

    STRATEGY_TAB_ID = "legacy.strategy"
    ROTATION_TAB_ID = "legacy.rotation"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Xquant Research Shell", parent, theme_qss=DARK_THEME_QSS)
        self.resize(1500, 950)

        self.event_bus = EventBus()
        self.experiment_store = ExperimentStore(PROJECT_ROOT / "experiments")
        self._tab_indexes: dict[str, int] = {}

        self.experiment_panel = ExperimentRecordPanel(self.experiment_store, self)
        self.strategy_tree_panel = StrategyTreePanel(self.open_strategy_research, self.open_rotation_live, self)
        self.event_log_panel = EventLogPanel(self.event_bus, self)

        self.workspace.tabCloseRequested.connect(lambda _index: self._rebuild_tab_index_cache())

        self._setup_docks()
        self._setup_perspectives()
        self._setup_commands()
        self.event_log_panel.append_message("New shell ready. Open legacy panels from the strategy tree or command palette.")

    def open_strategy_research(self) -> None:
        self._open_or_focus_tab(self.STRATEGY_TAB_ID, "Strategy Research", create_legacy_strategy_tab)

    def open_rotation_live(self) -> None:
        self._open_or_focus_tab(self.ROTATION_TAB_ID, "ETF Rotation", create_legacy_rotation_tab)

    def refresh_experiments(self) -> None:
        self.experiment_panel.refresh()
        self.event_log_panel.append_message("Experiment records refreshed.")

    def reload_legacy_tabs(self) -> None:
        self._close_tab_by_id(self.STRATEGY_TAB_ID)
        self._close_tab_by_id(self.ROTATION_TAB_ID)
        self.open_strategy_research()
        self.open_rotation_live()
        self.event_log_panel.append_message("Legacy tabs reloaded.")

    def _setup_docks(self) -> None:
        left_splitter = QSplitter(Qt.Orientation.Vertical, self)
        left_splitter.addWidget(self.experiment_panel)
        left_splitter.addWidget(self.strategy_tree_panel)
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 1)

        self.register_dock(
            "left.navigator",
            "Experiments / Strategies",
            left_splitter,
            area=Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.register_dock(
            "bottom.events",
            "Logs / Events",
            self.event_log_panel,
            area=Qt.DockWidgetArea.BottomDockWidgetArea,
        )

    def _setup_perspectives(self) -> None:
        self.register_perspective(
            Perspective(
                id="legacy",
                title="Legacy Research",
                activate=lambda _shell: self._activate_legacy_perspective(),
                description="Show legacy strategy research and ETF rotation tabs.",
            )
        )
        self.activate_perspective("legacy")

    def _setup_commands(self) -> None:
        commands = [
            Command(
                id="app.open_strategy_research",
                title="Open Strategy Research",
                callback=self.open_strategy_research,
                description="Open or focus the legacy strategy research tab.",
            ),
            Command(
                id="app.open_rotation_live",
                title="Open ETF Rotation",
                callback=self.open_rotation_live,
                description="Open or focus the legacy ETF rotation tab.",
            ),
            Command(
                id="app.refresh_experiments",
                title="Refresh Experiment Records",
                callback=self.refresh_experiments,
                description="Reload the experiment record dock from ExperimentStore.",
            ),
            Command(
                id="app.reload_legacy_tabs",
                title="Reload Legacy Tabs",
                callback=self.reload_legacy_tabs,
                description="Close and recreate legacy tabs for a lightweight hot reload.",
            ),
        ]
        for command in commands:
            self.register_command(command)

    def _activate_legacy_perspective(self) -> None:
        self.set_dock_visible("left.navigator", True)
        self.set_dock_visible("bottom.events", True)

    def _open_or_focus_tab(self, tab_id: str, title: str, factory: Callable[[QWidget | None], QWidget]) -> int:
        index = self._tab_indexes.get(tab_id)
        if index is not None and self._is_valid_tab_index(index):
            self.workspace.setCurrentIndex(index)
            return index

        widget = factory(self.workspace)
        widget.setProperty("tab_id", tab_id)
        index = self.workspace.add_workspace_tab(widget, title, closable=True)
        self._tab_indexes[tab_id] = index
        return index

    def _close_tab_by_id(self, tab_id: str) -> None:
        index = self._tab_indexes.pop(tab_id, None)
        if index is None or not self._is_valid_tab_index(index):
            return
        self.workspace.close_tab(index)
        self._rebuild_tab_index_cache()

    def _is_valid_tab_index(self, index: int) -> bool:
        return 0 <= index < self.workspace.count() and self.workspace.widget(index) is not None

    def _rebuild_tab_index_cache(self) -> None:
        self._tab_indexes.clear()
        for index in range(self.workspace.count()):
            tab_id = self.workspace.widget(index).property("tab_id")
            if tab_id:
                self._tab_indexes[str(tab_id)] = index


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("Xquant")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("StockTradebyZ")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    return app


def main() -> int:
    app = create_application(sys.argv)
    window = XquantMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EventLogPanel", "ExperimentRecordPanel", "StrategyTreePanel", "XquantMainWindow", "create_application", "main"]
