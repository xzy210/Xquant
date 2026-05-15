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

from app.perspectives.ai_decision_research import create_ai_decision_research_tab
from app.perspectives.etf_grid import create_etf_grid_tab
from app.perspectives.etf_rotation import create_etf_rotation_tab
from app.perspectives.research import (
    create_ai_training_tab,
    create_cross_sectional_backtest_tab,
    create_factor_library_tab,
    create_timing_strategy_tab,
)


class ExperimentRecordPanel(QWidget):
    """Small dock panel listing persisted experiment records."""

    def __init__(self, store: ExperimentStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.list_widget = QListWidget(self)
        self.empty_label = QLabel("暂无实验记录", self)
        self.empty_label.setProperty("class", "description")

        refresh_btn = QPushButton("刷新", self)
        refresh_btn.clicked.connect(self.refresh)

        header = QHBoxLayout()
        header.addWidget(QLabel("实验记录", self))
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
        title = record.strategy_id or "未知策略"
        suffix = f" / {record.params_hash}" if record.params_hash else ""
        final_value = f" / 最终净值={record.final_value:.2f}" if record.final_value is not None else ""
        return f"{title}{suffix}{final_value}\n{record.run_id}  {record.created_at}"


class StrategyTreePanel(QWidget):
    """Navigation dock for current strategy entry points."""

    def __init__(
        self,
        open_cross_sectional: Callable[[], None],
        open_factor_library: Callable[[], None],
        open_timing_strategy: Callable[[], None],
        open_ai_training: Callable[[], None],
        open_ai_decision_research: Callable[[], None],
        open_etf_grid: Callable[[], None],
        open_etf_rotation: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._open_cross_sectional = open_cross_sectional
        self._open_factor_library = open_factor_library
        self._open_timing_strategy = open_timing_strategy
        self._open_ai_training = open_ai_training
        self._open_ai_decision_research = open_ai_decision_research
        self._open_etf_grid = open_etf_grid
        self._open_etf_rotation = open_etf_rotation

        self.list_widget = QListWidget(self)
        for title, command_id in (
            ("ETF轮动研究", "native.etf_rotation"),
            ("ETF网格回测", "native.etf_grid"),
            ("截面选股回测", "native.cross_sectional"),
            ("因子研究", "native.factor_library"),
            ("时序策略研究", "native.timing_strategy"),
            ("AI决策研究", "native.ai_decision_research"),
            ("AI策略训练", "native.ai_training"),
        ):
            item = QListWidgetItem(title, self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, command_id)
        self.list_widget.itemDoubleClicked.connect(self._open_item)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("策略目录", self))
        layout.addWidget(self.list_widget, 1)

    def _open_item(self, item: QListWidgetItem) -> None:
        command_id = item.data(Qt.ItemDataRole.UserRole)
        if command_id == "native.cross_sectional":
            self._open_cross_sectional()
        elif command_id == "native.factor_library":
            self._open_factor_library()
        elif command_id == "native.timing_strategy":
            self._open_timing_strategy()
        elif command_id == "native.ai_training":
            self._open_ai_training()
        elif command_id == "native.ai_decision_research":
            self._open_ai_decision_research()
        elif command_id == "native.etf_grid":
            self._open_etf_grid()
        elif command_id == "native.etf_rotation":
            self._open_etf_rotation()


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
    """New application shell with strategy tabs and shared docks."""

    ETF_GRID_TAB_ID = "native.etf_grid"
    ETF_ROTATION_TAB_ID = "native.etf_rotation"
    CROSS_SECTIONAL_TAB_ID = "native.cross_sectional"
    FACTOR_LIBRARY_TAB_ID = "native.factor_library"
    TIMING_STRATEGY_TAB_ID = "native.timing_strategy"
    AI_TRAINING_TAB_ID = "native.ai_training"
    AI_DECISION_RESEARCH_TAB_ID = "native.ai_decision_research"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Xquant 策略研究台", parent, theme_qss=DARK_THEME_QSS)
        self.resize(1500, 950)

        self.event_bus = EventBus()
        self.experiment_store = ExperimentStore(PROJECT_ROOT / "experiments")
        self._tab_indexes: dict[str, int] = {}

        self.experiment_panel = ExperimentRecordPanel(self.experiment_store, self)
        self.strategy_tree_panel = StrategyTreePanel(
            self.open_cross_sectional_backtest,
            self.open_factor_library,
            self.open_timing_strategy,
            self.open_ai_training,
            self.open_ai_decision_research,
            self.open_etf_grid_backtest,
            self.open_etf_rotation,
            self,
        )
        self.event_log_panel = EventLogPanel(self.event_bus, self)

        self.workspace.tabCloseRequested.connect(lambda _index: self._rebuild_tab_index_cache())

        self._setup_docks()
        self._setup_perspectives()
        self._setup_commands()
        self.event_log_panel.append_message("策略研究台已就绪，可从策略目录或命令面板打开功能页。")

    def open_etf_rotation(self) -> None:
        self._open_or_focus_tab(
            self.ETF_ROTATION_TAB_ID,
            "ETF轮动研究",
            lambda parent: create_etf_rotation_tab(
                parent,
                event_bus=self.event_bus,
                experiment_store=self.experiment_store,
                on_experiment_saved=self.refresh_experiments,
            ),
        )

    def open_etf_grid_backtest(self) -> None:
        self._open_or_focus_tab(
            self.ETF_GRID_TAB_ID,
            "ETF网格回测",
            lambda parent: create_etf_grid_tab(
                parent,
                event_bus=self.event_bus,
                experiment_store=self.experiment_store,
                on_experiment_saved=self.refresh_experiments,
            ),
        )

    def open_cross_sectional_backtest(self) -> None:
        self._open_or_focus_tab(
            self.CROSS_SECTIONAL_TAB_ID,
            "截面选股回测",
            create_cross_sectional_backtest_tab,
        )

    def open_factor_library(self) -> None:
        self._open_or_focus_tab(
            self.FACTOR_LIBRARY_TAB_ID,
            "因子研究",
            create_factor_library_tab,
        )

    def open_timing_strategy(self) -> None:
        self._open_or_focus_tab(
            self.TIMING_STRATEGY_TAB_ID,
            "时序策略研究",
            create_timing_strategy_tab,
        )

    def open_ai_training(self) -> None:
        self._open_or_focus_tab(
            self.AI_TRAINING_TAB_ID,
            "AI策略训练",
            create_ai_training_tab,
        )

    def open_ai_decision_research(self) -> None:
        self._open_or_focus_tab(
            self.AI_DECISION_RESEARCH_TAB_ID,
            "AI决策研究",
            lambda parent: create_ai_decision_research_tab(
                parent,
                event_bus=self.event_bus,
                experiment_store=self.experiment_store,
                on_experiment_saved=self.refresh_experiments,
            ),
        )

    def refresh_experiments(self) -> None:
        self.experiment_panel.refresh()
        self.event_log_panel.append_message("实验记录已刷新。")

    def reload_strategy_tabs(self) -> None:
        self._close_tab_by_id(self.ETF_GRID_TAB_ID)
        self._close_tab_by_id(self.ETF_ROTATION_TAB_ID)
        self._close_tab_by_id(self.CROSS_SECTIONAL_TAB_ID)
        self._close_tab_by_id(self.FACTOR_LIBRARY_TAB_ID)
        self._close_tab_by_id(self.TIMING_STRATEGY_TAB_ID)
        self._close_tab_by_id(self.AI_TRAINING_TAB_ID)
        self._close_tab_by_id(self.AI_DECISION_RESEARCH_TAB_ID)
        self.open_etf_grid_backtest()
        self.open_etf_rotation()
        self.open_cross_sectional_backtest()
        self.open_factor_library()
        self.open_timing_strategy()
        self.open_ai_decision_research()
        self.open_ai_training()
        self.event_log_panel.append_message("策略页面已重新加载。")

    def _setup_docks(self) -> None:
        left_splitter = QSplitter(Qt.Orientation.Vertical, self)
        left_splitter.addWidget(self.experiment_panel)
        left_splitter.addWidget(self.strategy_tree_panel)
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 1)

        self.register_dock(
            "left.navigator",
            "实验 / 策略",
            left_splitter,
            area=Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.register_dock(
            "bottom.events",
            "日志 / 事件",
            self.event_log_panel,
            area=Qt.DockWidgetArea.BottomDockWidgetArea,
        )

    def _setup_perspectives(self) -> None:
        self.register_perspective(
            Perspective(
                id="research",
                title="策略研究",
                activate=lambda _shell: self._activate_research_perspective(),
                description="显示 ETF、截面回测、因子和 AI 训练研究功能页。",
            )
        )
        self.activate_perspective("research")

    def _setup_commands(self) -> None:
        commands = [
            Command(
                id="app.open_etf_rotation",
                title="打开ETF轮动研究",
                callback=self.open_etf_rotation,
                description="打开或切换到 ETF 轮动研究页面。",
            ),
            Command(
                id="app.open_etf_grid_backtest",
                title="打开ETF网格回测",
                callback=self.open_etf_grid_backtest,
                description="打开或切换到 ETF 网格回测页面。",
            ),
            Command(
                id="app.open_cross_sectional_backtest",
                title="打开截面选股回测",
                callback=self.open_cross_sectional_backtest,
                description="打开或切换到截面选股回测页面。",
            ),
            Command(
                id="app.open_factor_library",
                title="打开因子研究",
                callback=self.open_factor_library,
                description="打开或切换到因子研究页面。",
            ),
            Command(
                id="app.open_timing_strategy",
                title="打开时序策略研究",
                callback=self.open_timing_strategy,
                description="打开或切换到 TCN Attention 时序策略训练与回测页面。",
            ),
            Command(
                id="app.open_ai_training",
                title="打开AI策略训练",
                callback=self.open_ai_training,
                description="打开或切换到 AI 策略训练页面。",
            ),
            Command(
                id="app.open_ai_decision_research",
                title="打开AI决策研究",
                callback=self.open_ai_decision_research,
                description="打开或切换到 AI 决策研究页面。",
            ),
            Command(
                id="app.refresh_experiments",
                title="刷新实验记录",
                callback=self.refresh_experiments,
                description="从实验记录存储中重新加载记录。",
            ),
            Command(
                id="app.reload_strategy_tabs",
                title="重新加载策略页面",
                callback=self.reload_strategy_tabs,
                description="关闭并重建策略页面，用于轻量刷新。",
            ),
        ]
        for command in commands:
            self.register_command(command)

    def _activate_research_perspective(self) -> None:
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
    app.setApplicationName("Xquant 策略研究台")
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
