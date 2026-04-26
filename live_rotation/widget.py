"""
ETF轮动实盘 - UI面板

可作为独立Tab嵌入 trading_app 的 MainWindow。
显示持仓状态、ETF得分、交易历史、参数配置，并提供手动交易和自动信号入口。
"""
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

import json
import random

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QTextEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QComboBox, QLineEdit, QMessageBox, QTabWidget,
    QSizePolicy, QScrollArea, QListWidget, QListWidgetItem, QFrame, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QColor, QFont

from common.broker_connection_panel import BrokerConnectionPanel
from common.broker_session_service import get_broker_session_service
from common.live_strategy_shell import LiveStrategyShell
from common.strategy_panel_context import StrategyPanelContext
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from trading_app.services.strategy_budget_service import get_strategy_budget_service
from trading_app.services.live_strategy_end_of_day_service import StrategyEndOfDayResult
from trading_app.services.strategy_constants import normalize_symbol_code
from trading_app.services.strategy_registry_service import get_strategy_registry_service
from trading_app.services.strategy_spec_service import get_strategy_spec_service
from trading_app.services.qmt_startup_orchestrator import QmtStartupOrchestrator
from trading_app.services.market_data_status_service import get_market_data_status_service
from trading_app.services.trade_record_service import get_trade_record_service
from trading_app.widgets.strategy_risk_settings_panel import StrategyRiskSettingsPanel

from .config import RotationConfig, ConfigManager
from .config_dialog import ETFStrategyConfigDialog
from .manual_order_dialog import ETFManualOrderDialog
from .notifier import RotationNotifier
from .rotation_engine import RotationEngine
from .scheduler_settings_dialog import ETFSchedulerSettingsDialog
from .trade_executor import TradeExecutor, SimulatedExecutor, XtQuantExecutor

_strategy_app = str(Path(__file__).resolve().parent.parent / "strategy_app")
if _strategy_app not in sys.path:
    sys.path.insert(0, _strategy_app)
from factors.registry import factor_registry
import factors.etf_momentum_factors_optimized  # noqa: F401

logger = logging.getLogger(__name__)
_ETF_BUDGET_MIGRATION_FLAG = Path(__file__).parent / "config" / "etf_budget_migration_done.json"


class _FocusSpinBox(QSpinBox):
    """仅在获得焦点后才响应滚轮的 SpinBox，防止滚动页面时误改参数"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _FocusDoubleSpinBox(QDoubleSpinBox):
    """仅在获得焦点后才响应滚轮的 DoubleSpinBox"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _FocusComboBox(QComboBox):
    """仅在获得焦点后才响应滚轮的 ComboBox"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _BrokerConnectWorker(QThread):
    """后台线程：连接 miniQMT，成功后通过 connected 信号返回 (xt_trader, acc)"""

    connected = pyqtSignal(object, object)   # xt_trader, acc
    failed    = pyqtSignal(str)              # error message
    log       = pyqtSignal(str)

    def __init__(self, qmt_path: str, account: str, parent=None):
        super().__init__(parent)
        self.qmt_path = qmt_path
        self.account  = account

    def run(self):
        try:
            from xtquant import xttrader
            from xtquant.xttype import StockAccount

            session_id = random.randint(100000, 999999)
            self.log.emit(f"正在连接 miniQMT（路径: {self.qmt_path}, 账户: {self.account}）…")

            xt_trader = xttrader.XtQuantTrader(self.qmt_path, session_id)
            xt_trader.start()

            result = xt_trader.connect()
            if result != 0:
                self.failed.emit("连接 QMT 交易端失败，请确认 miniQMT 已启动并登录")
                return

            acc = StockAccount(self.account)
            res = xt_trader.subscribe(acc)
            if res != 0:
                self.failed.emit(f"订阅账户失败（返回码 {res}），请检查账户号")
                return

            self.log.emit(f"✅ miniQMT 连接成功，账户: {self.account}")
            self.connected.emit(xt_trader, acc)

        except ImportError:
            self.failed.emit("未找到 xtquant 库，请确认已安装 miniQMT 并激活对应 Python 环境")
        except Exception as e:
            self.failed.emit(f"连接异常: {e}")


class ETFRotationLiveWidget(QWidget):
    """ETF轮动实盘操作面板"""

    def __init__(
        self,
        engine: Optional[RotationEngine] = None,
        parent=None,
        *,
        broker_panel: Optional[BrokerConnectionPanel] = None,
        manage_startup: bool = True,
    ):
        super().__init__(parent)
        self.broker_session_service = get_broker_session_service()
        self.strategy_budget = get_strategy_budget_service()
        self.strategy_registry = get_strategy_registry_service()
        self.market_data_status_service = get_market_data_status_service()
        self.broker_panel = broker_panel
        self._owns_broker_panel = broker_panel is None
        self.manage_startup = bool(manage_startup)
        self._broker_connecting = False
        self._etf_budget_migration_checked = False
        self._center_auto_pause_snapshot: Optional[bool] = None

        # 引擎
        self.engine = engine or RotationEngine()
        self.engine.log_message.connect(self._on_log)
        self.engine.signal_generated.connect(self._on_signal)
        self.engine.trade_executed.connect(self._on_trade)
        self.engine.scores_updated.connect(self._on_scores)
        self.engine.status_updated.connect(self._on_status)

        # 状态刷新定时器
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_status)
        self._refresh_timer.start(5000)

        self._setup_ui()
        self.broker_session_service.log_message.connect(self._on_log)
        self.broker_panel.broker_connected.connect(self._on_shared_broker_connected)
        self.broker_panel.broker_disconnected.connect(self._on_shared_broker_disconnected)
        self.startup_orchestrator = None
        if self.manage_startup:
            self.startup_orchestrator = QmtStartupOrchestrator(self.broker_session_service, self)
            self.startup_orchestrator.status_changed.connect(self._on_startup_status)
            self.startup_orchestrator.finished.connect(self._on_startup_finished)
        self._sync_etf_strategy_profile()
        if self.broker_session_service.is_connected:
            self._on_shared_broker_connected()
        self._refresh_status()
        self._refresh_all_analysis_tabs()
        QTimer.singleShot(800, self._restore_auto_mode_if_needed)
        if self.manage_startup:
            QTimer.singleShot(600, self._start_startup_orchestration)

    # ==================================================================
    #  UI 构建
    # ==================================================================

# 深色主题色板，与 AI 实盘决策页保持一致
    _THEME = {
        'bg':           '#1e1e1e',
        'panel_bg':     '#1e1e1e',
        'border':       '#3c3c3c',
        'text':         '#ffffff',
        'text_secondary': '#888888',
        'accent':       '#0078d4',
        'table_alt':    '#2a2a3e',
        'table_header': '#2d2d2d',
        'table_grid':   '#3c3c3c',
        'selected':     '#3a5fcd',
        'red':          '#DC2626',   # 买入/亏损红
        'green':        '#16A34A',   # 卖出/盈利绿
        'orange':       '#D97706',   # 警告橙
        'holding_bg':   '#1f2a1f',
    }

    def _setup_ui(self):
        t = self._THEME
        # 用 * 通配符确保所有子 widget 都继承统一深色背景，
        # 再用具体选择器覆盖需要特殊处理的控件。
        self.setStyleSheet(
            f"ETFRotationLiveWidget, ETFRotationLiveWidget *{{"
            f"  background-color:{t['bg']}; color:{t['text']};"
            f"}}"
            f"QGroupBox{{"
            f"  background-color:{t['panel_bg']};"
            f"  border:1px solid {t['border']}; border-radius:6px;"
            f"  margin-top:10px; padding:12px 8px 8px 8px;"
            f"  font-weight:bold; color:{t['text']};"
            f"}}"
            f"QGroupBox::title{{"
            f"  subcontrol-origin:margin;"
            f"  left:12px; padding:0 4px;"
            f"  color:{t['accent']};"
            f"}}"
            f"QGroupBox QWidget{{"
            f"  background-color:{t['panel_bg']};"
            f"}}"
            f"QLabel{{ color:{t['text']}; background:transparent; }}"
            f"QCheckBox{{ color:{t['text']}; background:transparent; }}"
            f"QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"  border:1px solid {t['border']}; border-radius:3px;"
            f"  padding:2px 4px;"
            f"}}"
            f"QComboBox QAbstractItemView{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"  selection-background-color:{t['selected']};"
            f"}}"
            f"QListWidget{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"  border:1px solid {t['border']}; border-radius:3px;"
            f"}}"
            f"QListWidget::item{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"}}"
            f"QSplitter::handle{{"
            f"  background:{t['bg']};"
            f"}}"
            f"QFrame{{"
            f"  background:transparent;"
            f"}}"
            f"QPushButton{{"
            f"  background-color:{t['table_header']}; color:{t['text']};"
            f"  border:1px solid {t['border']}; border-radius:4px;"
            f"  padding:4px 10px;"
            f"}}"
            f"QPushButton:hover{{"
            f"  background-color:{t['border']};"
            f"}}"
            f"QPushButton:pressed{{"
            f"  background-color:{t['table_grid']};"
            f"}}"
            f"QPushButton:disabled{{"
            f"  background-color:{t['table_alt']}; color:{t['text_secondary']};"
            f"  border:1px solid {t['table_grid']};"
            f"}}"
            f"QSpinBox::up-button, QDoubleSpinBox::up-button,"
            f"QSpinBox::down-button, QDoubleSpinBox::down-button{{"
            f"  background:{t['table_header']}; border:1px solid {t['border']};"
            f"  width:16px;"
            f"}}"
            f"QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
            f"QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover{{"
            f"  background:{t['border']};"
            f"}}"
            f"QSpinBox::up-arrow, QDoubleSpinBox::up-arrow{{"
            f"  width:8px; height:8px;"
            f"}}"
            f"QSpinBox::down-arrow, QDoubleSpinBox::down-arrow{{"
            f"  width:8px; height:8px;"
            f"}}"
            f"QComboBox::drop-down{{"
            f"  background:{t['table_header']}; border:1px solid {t['border']};"
            f"  border-top-right-radius:3px; border-bottom-right-radius:3px;"
            f"  width:20px;"
            f"}}"
            f"QScrollBar:vertical,QScrollBar:horizontal{{"
            f"  background:{t['bg']}; border:none; width:8px; height:8px;"
            f"}}"
            f"QScrollBar::handle:vertical,QScrollBar::handle:horizontal{{"
            f"  background:{t['border']}; border-radius:4px; min-height:20px;"
            f"}}"
            f"QScrollBar::add-line,QScrollBar::sub-line{{"
            f"  height:0; width:0;"
            f"}}"
            f"QTabWidget::pane{{"
            f"  border:1px solid {t['border']}; border-radius:4px;"
            f"  background:{t['panel_bg']};"
            f"}}"
            f"QTabBar::tab{{"
            f"  background:{t['table_header']}; color:{t['text_secondary']};"
            f"  padding:6px 16px; border:1px solid {t['border']};"
            f"  border-bottom:none; border-top-left-radius:4px;"
            f"  border-top-right-radius:4px; margin-right:2px;"
            f"}}"
            f"QTabBar::tab:selected{{"
            f"  background:{t['panel_bg']}; color:{t['accent']};"
            f"  font-weight:bold;"
            f"}}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── 左侧：状态 & 控制 ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(6)

        if self.broker_panel is None:
            self.broker_panel = BrokerConnectionPanel(self)
        if self._owns_broker_panel:
            left_layout.addWidget(self.broker_panel)
        left_layout.addWidget(self._build_status_panel())
        left_layout.addWidget(self._build_action_panel())

        self._etf_panel = self._build_etf_panel()
        self._config_panel = self._build_config_panel()
        self._manual_order_dialog: Optional[ETFManualOrderDialog] = None
        self._schedule_dialog: Optional[ETFSchedulerSettingsDialog] = None
        self._config_dialog: Optional[ETFStrategyConfigDialog] = None

        left_layout.addStretch()

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left)
        left_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet(
            f"QScrollArea{{border:none; background:{t['bg']};}}"
            f"QScrollArea > QWidget{{background:{t['bg']};}}")

        # ── 右侧：得分表 & 日志 ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self.tabs = QTabWidget()

        _table_style = (
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

        # Tab 1: 得分面板
        self.score_table = QTableWidget()
        self.score_table.setColumnCount(3)
        self.score_table.setHorizontalHeaderLabels(["ETF代码", "名称", "综合得分"])
        self.score_table.horizontalHeader().setStretchLastSection(True)
        self.score_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.score_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.score_table.setAlternatingRowColors(True)
        self.score_table.setStyleSheet(_table_style)
        self.tabs.addTab(self.score_table, "ETF得分")

        # Tab 2: 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            f"QTextEdit{{font-family:Consolas,monospace;font-size:11px;"
            f"background:{t['panel_bg']};color:{t['text']};"
            f"border:none;}}"
        )
        self.tabs.addTab(self.log_text, "运行日志")

        # ── ETF 内部统计数据（不再单独展示 Tab） ──
        self.stat_table = QTableWidget()
        self.stat_table.setColumnCount(2)
        self.stat_table.setHorizontalHeaderLabels(["指标", "数值"])
        self.stat_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.stat_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.stat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stat_table.setAlternatingRowColors(True)
        self.stat_table.verticalHeader().setVisible(False)
        self.stat_table.setStyleSheet(_table_style)

        right_layout.addWidget(self.tabs)
        self.shell = LiveStrategyShell(
            self._build_strategy_context(),
            left_scroll,
            right,
            parent=self,
        )
        self.shell.horizontal_splitter.setSizes([340, 660])
        self.shell.vertical_splitter.setHandleWidth(18)
        self.shell.vertical_splitter.setOpaqueResize(False)
        self.shell.vertical_splitter.setStyleSheet(
            "QSplitter::handle:vertical{background:#3c3c3c;border-radius:4px;margin:2px 0;}"
            "QSplitter::handle:vertical:hover{background:#505050;}"
        )
        self._main_vertical_splitter = self.shell.vertical_splitter
        self.strategy_trade_panel = self.shell.strategy_trade_panel
        layout.addWidget(self.shell)

    # ── 状态面板 ──

    def _build_status_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        asset_group = QGroupBox("账户概览（ETF轮动实盘虚拟账户）")
        asset_form = QFormLayout(asset_group)
        asset_form.setSpacing(4)
        self.lbl_strategy_total_asset = QLabel("-")
        self.lbl_strategy_available_cash = QLabel("-")
        self.lbl_strategy_market_value = QLabel("-")
        self.lbl_strategy_total_pnl = QLabel("-")
        for label in (
            self.lbl_strategy_total_asset,
            self.lbl_strategy_available_cash,
            self.lbl_strategy_market_value,
            self.lbl_strategy_total_pnl,
        ):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet("font-weight:bold;")
        self.lbl_strategy_total_asset.setStyleSheet("color:#0078d4;font-size:13px;font-weight:bold;")
        self.lbl_strategy_total_pnl.setStyleSheet("font-size:13px;font-weight:bold;")
        asset_form.addRow("总资产:", self.lbl_strategy_total_asset)
        asset_form.addRow("可用资金:", self.lbl_strategy_available_cash)
        asset_form.addRow("持仓市值:", self.lbl_strategy_market_value)
        asset_form.addRow("总盈亏:", self.lbl_strategy_total_pnl)
        layout.addWidget(asset_group)

        status_group = QGroupBox("当前状态（ETF轮动实盘）")
        status_form = QFormLayout(status_group)
        status_form.setSpacing(4)

        self.lbl_holding = QLabel("-")
        self.lbl_holding.setStyleSheet("color:#0078d4;font-size:14px;font-weight:bold;")
        self.lbl_buy_price = QLabel("-")
        self.lbl_current_price = QLabel("-")
        self.lbl_pnl = QLabel("-")
        self.lbl_pnl.setStyleSheet("font-size:13px;font-weight:bold;")
        self.lbl_signal = QLabel("-")
        self.lbl_last_check = QLabel("-")
        self.lbl_last_check.setStyleSheet("color:#888888;font-size:11px;")
        self.lbl_data_status = QLabel("-")
        self.lbl_data_status.setStyleSheet("font-size:11px;")
        self.lbl_data_status.setWordWrap(True)
        self.lbl_data_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_data_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_executor = QLabel("-")

        status_form.addRow("持仓标的:", self.lbl_holding)
        status_form.addRow("买入价格:", self.lbl_buy_price)
        status_form.addRow("当前价格:", self.lbl_current_price)
        status_form.addRow("浮动盈亏:", self.lbl_pnl)
        status_form.addRow("最近信号:", self.lbl_signal)
        status_form.addRow("最近检查:", self.lbl_last_check)
        status_form.addRow("数据状态:", self.lbl_data_status)
        status_form.addRow("执行器:", self.lbl_executor)
        layout.addWidget(status_group)

        return panel

    def _etf_strategy_identity(self):
        spec = get_strategy_spec_service().etf_rotation()
        strategy_id = (self.engine.config.strategy_id or spec.strategy_id or "etf_rotation").strip() or "etf_rotation"
        virtual_account_id = spec.virtual_account_id if strategy_id == spec.strategy_id else f"va_{strategy_id}"
        return strategy_id, spec.strategy_name, virtual_account_id

    def _build_strategy_context(self) -> StrategyPanelContext:
        strategy_id, strategy_name, virtual_account_id = self._etf_strategy_identity()
        spec = get_strategy_spec_service().etf_rotation()
        return StrategyPanelContext(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            owner_type=spec.owner_type,
            metadata=spec.to_plugin_metadata(),
        )

    def _sync_etf_strategy_profile(self):
        spec = get_strategy_spec_service().etf_rotation()
        strategy_id, strategy_name, virtual_account_id = self._etf_strategy_identity()
        symbols = [
            normalize_symbol_code(code)
            for code in (self.engine.config.etf_pool or spec.universe or [])
            if normalize_symbol_code(code)
        ]
        self.strategy_budget.upsert_strategy_config(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            capital_limit=float(self.engine.config.dedicated_capital or spec.capital_limit or 0.0),
            enabled=True,
        )
        if symbols:
            ok, message = self.strategy_registry.ensure_strategy_symbols(
                strategy_id=strategy_id,
                symbols=symbols,
                strategy_name=strategy_name,
                virtual_account_id=virtual_account_id,
                owner_type=spec.owner_type,
            )
            if not ok:
                logger.warning("同步 ETF 标的归属失败: %s", message)

    def _ensure_etf_strategy_budget_seeded(self, summary: Optional[dict] = None) -> None:
        if self._etf_budget_migration_checked:
            return
        self._etf_budget_migration_checked = True
        if _ETF_BUDGET_MIGRATION_FLAG.exists():
            return
        legacy_state_path = Path(__file__).parent / "config" / "rotation_state.json"
        if not legacy_state_path.exists():
            self._mark_etf_budget_migration_done(
                status="legacy_state_removed",
                strategy_id=self._etf_strategy_identity()[0],
            )
            return
        strategy_id, strategy_name, virtual_account_id = self._etf_strategy_identity()
        snapshot = self.strategy_budget.get_strategy_snapshot(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=0.0,
        )
        snapshot_cash = float(snapshot.get("cash_balance", 0.0) or 0.0)
        snapshot_position_count = int(snapshot.get("position_count", 0) or 0)
        if snapshot_position_count > 0 or snapshot_cash > 1.0:
            self._mark_etf_budget_migration_done(
                status="already_present",
                strategy_id=strategy_id,
                snapshot_cash=snapshot_cash,
                snapshot_position_count=snapshot_position_count,
            )
            logger.info("ETF 轮动实盘虚拟账户已有状态，标记旧账本迁移完成，后续不再读取旧账本")
            return

        legacy_holding = normalize_symbol_code(getattr(self.engine.state, "current_holding", "") or "")
        legacy_quantity = int(getattr(self.engine.state, "buy_quantity", 0) or 0)
        legacy_buy_price = float(getattr(self.engine.state, "buy_price", 0.0) or 0.0)
        legacy_cash = round(float(getattr(self.engine.state, "dedicated_cash", 0.0) or 0.0), 2)

        need_seed = False
        if legacy_holding and legacy_quantity > 0 and snapshot_position_count <= 0:
            need_seed = True
        if abs(snapshot_cash - legacy_cash) > 1.0:
            need_seed = True
        if not need_seed:
            self._mark_etf_budget_migration_done(
                status="no_legacy_seed_needed",
                strategy_id=strategy_id,
                snapshot_cash=snapshot_cash,
                snapshot_position_count=snapshot_position_count,
            )
            return

        self.strategy_budget.upsert_strategy_config(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            capital_limit=float(self.engine.config.dedicated_capital or 0.0),
            enabled=True,
        )
        self.strategy_budget.reset_strategy_account(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            capital_limit=float(self.engine.config.dedicated_capital or 0.0),
            cash_balance=legacy_cash,
            preserve_positions=False,
        )
        if legacy_holding and legacy_quantity > 0:
            self.strategy_budget.sync_strategy_positions(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                virtual_account_id=virtual_account_id,
                real_total_asset=0.0,
                positions=[
                    {
                        "stock_code": legacy_holding,
                        "volume": legacy_quantity,
                        "open_price": legacy_buy_price,
                    }
                ],
                clear_reservations=True,
            )
        logger.info(
            "已将 ETF 旧账本状态迁移到策略虚拟账户: holding=%s qty=%s cash=%.2f",
            legacy_holding or "-",
            legacy_quantity,
            legacy_cash,
        )
        self._mark_etf_budget_migration_done(
            status="migrated",
            strategy_id=strategy_id,
            legacy_holding=legacy_holding,
            legacy_quantity=legacy_quantity,
            legacy_cash=legacy_cash,
        )

    def _mark_etf_budget_migration_done(self, **payload) -> None:
        data = {
            "done": True,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **payload,
        }
        try:
            with open(_ETF_BUDGET_MIGRATION_FLAG, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("写入 ETF 预算迁移完成标记失败: %s", exc)

    def _get_etf_strategy_account_view(self, summary: Optional[dict] = None) -> dict:
        strategy_id, strategy_name, virtual_account_id = self._etf_strategy_identity()
        self._ensure_etf_strategy_budget_seeded(summary)
        real_total_asset = 0.0
        market_value = 0.0
        owned_codes = {
            normalize_symbol_code(code)
            for code in (self.engine.config.etf_pool or [])
            if normalize_symbol_code(code)
        }
        if self.broker_session_service.is_connected:
            try:
                asset = self.broker_session_service.query_stock_asset()
                real_total_asset = float(getattr(asset, "total_asset", 0.0) or 0.0)
                positions = self.broker_session_service.query_stock_positions() or []
                for pos in positions:
                    volume = int(getattr(pos, "volume", 0) or 0)
                    if volume <= 0:
                        continue
                    code = normalize_symbol_code(getattr(pos, "stock_code", "") or "")
                    if not code:
                        continue
                    owner = self.strategy_registry.get_owner(code)
                    if owner is not None and owner.enabled:
                        if owner.strategy_id != strategy_id:
                            continue
                    elif code not in owned_codes:
                        continue
                    market_value += float(getattr(pos, "market_value", 0.0) or 0.0)
            except Exception as exc:
                logger.warning("读取 ETF 轮动实盘账户视图失败: %s", exc)
        if market_value <= 0 and summary:
            holding = normalize_symbol_code(str(summary.get("holding", "") or ""))
            quantity = int(summary.get("buy_quantity", 0) or 0)
            current_price = float(summary.get("current_price", 0.0) or 0.0)
            if not current_price or current_price <= 0:
                current_price = float(summary.get("buy_price", 0.0) or 0.0)
            if holding and quantity > 0 and current_price > 0:
                market_value = round(current_price * quantity, 2)
        # 主账本为唯一真源：capital_limit / cash_balance / realized_pnl 均由主账本维护，
        # 这里只把实时市值（real-time spot × holdings）覆盖进去，其它字段不再 override
        account = self.strategy_budget.build_account_snapshot(
            strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            real_total_asset=real_total_asset,
            market_value_override=round(market_value, 2),
        )
        return {
            "total_asset": float(account.get("total_asset", 0.0) or 0.0),
            "available_cash": float(account.get("available_cash", 0.0) or 0.0),
            "market_value": float(account.get("market_value", 0.0) or 0.0),
            "capital_limit": float(account.get("capital_limit", 0.0) or 0.0),
            "total_pnl": float(account.get("total_pnl", 0.0) or 0.0),
            "realized_pnl": float(account.get("realized_pnl", 0.0) or 0.0),
            "unrealized_pnl": float(account.get("unrealized_pnl", 0.0) or 0.0),
            "invested_cost": float(account.get("invested_cost", 0.0) or 0.0),
        }

    # ── miniQMT 连接面板 ──

    # 优先读取本模块自己的配置，找不到则回退到 trading_app 的 broker_config
    _BROKER_SETTINGS_FILE = Path(__file__).parent / "config" / "broker_settings.json"
    _BROKER_FALLBACK_FILE = (
        Path(__file__).parent.parent / "trading_app" / "config" / "broker_config.json"
    )

    def _build_broker_panel(self) -> QGroupBox:
        t = self._THEME
        grp = QGroupBox("miniQMT 连接")
        layout = QVBoxLayout(grp)
        layout.setSpacing(5)

        # ── 主操作行：状态 + 按钮 ──
        main_row = QHBoxLayout()

        self.lbl_broker_status = QLabel("⬤ 未连接（模拟模式）")
        self.lbl_broker_status.setStyleSheet(
            f"color:{t['text_secondary']};font-size:11px;")
        main_row.addWidget(self.lbl_broker_status, 1)

        self.btn_connect_broker = QPushButton("连接")
        self.btn_connect_broker.setStyleSheet(
            "QPushButton{background:#16A34A;color:white;padding:4px 12px;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#15803D;}"
            "QPushButton:disabled{background:#4A6A4A;color:#888;}"
        )
        self.btn_connect_broker.clicked.connect(self._on_connect_broker)
        main_row.addWidget(self.btn_connect_broker)

        self.btn_disconnect_broker = QPushButton("断开")
        self.btn_disconnect_broker.setEnabled(False)
        self.btn_disconnect_broker.clicked.connect(self._on_disconnect_broker)
        main_row.addWidget(self.btn_disconnect_broker)

        # ⚙ 展开/折叠设置
        self.btn_broker_settings = QPushButton("⚙")
        self.btn_broker_settings.setMaximumWidth(28)
        self.btn_broker_settings.setToolTip("展开/折叠连接设置")
        self.btn_broker_settings.clicked.connect(self._on_toggle_broker_settings)
        main_row.addWidget(self.btn_broker_settings)
        layout.addLayout(main_row)

        # ── 可折叠的设置区域（默认隐藏）──
        self._broker_settings_widget = QWidget()
        settings_layout = QVBoxLayout(self._broker_settings_widget)
        settings_layout.setContentsMargins(0, 2, 0, 0)
        settings_layout.setSpacing(4)

        # QMT 路径
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("路径:"))
        self.edit_qmt_path = QLineEdit()
        self.edit_qmt_path.setPlaceholderText(
            r"例: D:\中金财富QMT个人版交易端\userdata_mini")
        path_row.addWidget(self.edit_qmt_path)
        btn_browse = QPushButton("…")
        btn_browse.setMaximumWidth(28)
        btn_browse.setToolTip("选择 miniQMT userdata_mini 目录")
        btn_browse.clicked.connect(self._on_browse_qmt_path)
        path_row.addWidget(btn_browse)
        settings_layout.addLayout(path_row)

        # 账户号
        acc_row = QHBoxLayout()
        acc_row.addWidget(QLabel("账户:"))
        self.edit_account = QLineEdit()
        self.edit_account.setPlaceholderText("资金账号")
        acc_row.addWidget(self.edit_account)
        settings_layout.addLayout(acc_row)

        self._broker_settings_widget.setVisible(False)
        layout.addWidget(self._broker_settings_widget)

        # 加载已有配置（优先本模块，其次 trading_app）
        self._load_broker_settings()
        return grp

    def _load_broker_settings(self):
        """从共享 BrokerSessionService 读取配置。"""
        try:
            data = self.broker_session_service.get_config()
            self.edit_qmt_path.setText(data.get('qmt_path', ''))
            self.edit_account.setText(data.get('account', ''))
        except Exception:
            pass

    def _save_broker_settings(self):
        try:
            self.broker_session_service.save_config(
                self.edit_qmt_path.text().strip(),
                self.edit_account.text().strip(),
            )
        except Exception:
            pass

    def _on_broker_config_changed(self, config: dict):
        self.edit_qmt_path.setText(config.get('qmt_path', ''))
        self.edit_account.setText(config.get('account', ''))

    def _sync_broker_ui_from_service(self):
        if self.broker_session_service.is_connected:
            account = self.broker_session_service.get_config().get('account', '')
            self.btn_connect_broker.setText("连接")
            self.btn_connect_broker.setEnabled(False)
            self.btn_disconnect_broker.setEnabled(True)
            self.lbl_broker_status.setText(f"⬤ 已连接  {account}")
            self.lbl_broker_status.setStyleSheet(
                "color:#16A34A;font-size:11px;font-weight:bold;")
            self.inject_broker()
            return

        self.btn_connect_broker.setEnabled(True)
        self.btn_connect_broker.setText("连接")
        self.btn_disconnect_broker.setEnabled(False)
        self.lbl_broker_status.setText("⬤ 已断开（模拟模式）")
        self.lbl_broker_status.setStyleSheet(
            f"color:{self._THEME['text_secondary']};font-size:11px;")

    def _on_broker_session_changed(self, connected: bool, message: str):
        if connected:
            self._broker_connecting = False
            self._sync_broker_ui_from_service()
            return
        if message in ("正在连接券商...", "券商连接正在进行中"):
            self.btn_connect_broker.setEnabled(False)
            self.btn_connect_broker.setText("连接中…")
            self.lbl_broker_status.setText("⬤ 正在连接…")
            self.lbl_broker_status.setStyleSheet("color:#D97706;font-size:11px;")
            return
        if self._broker_connecting:
            self._broker_connecting = False
            self._on_broker_failed(message)
            return
        if message == "券商已断开":
            self.engine.set_executor(SimulatedExecutor())
            self._sync_broker_ui_from_service()
            self._refresh_status()

    def _on_toggle_broker_settings(self):
        visible = self._broker_settings_widget.isVisible()
        self._broker_settings_widget.setVisible(not visible)

    def _on_browse_qmt_path(self):
        d = QFileDialog.getExistingDirectory(
            self, "选择 miniQMT userdata_mini 目录",
            self.edit_qmt_path.text() or "C:\\"
        )
        if d:
            self.edit_qmt_path.setText(d)

    def _on_connect_broker(self):
        qmt_path = self.edit_qmt_path.text().strip()
        account  = self.edit_account.text().strip()
        if not qmt_path:
            # 没有填写时先展开设置区提示用户
            self._broker_settings_widget.setVisible(True)
            QMessageBox.warning(self, "提示", "请先填写 miniQMT 数据路径")
            return
        if not account:
            self._broker_settings_widget.setVisible(True)
            QMessageBox.warning(self, "提示", "请先填写资金账号")
            return

        self._save_broker_settings()
        self.btn_connect_broker.setEnabled(False)
        self.btn_connect_broker.setText("连接中…")
        self.lbl_broker_status.setText("⬤ 正在连接…")
        self.lbl_broker_status.setStyleSheet("color:#D97706;font-size:11px;")
        self._broker_connecting = True
        if not self.broker_session_service.connect_async(qmt_path, account):
            self._broker_connecting = False
            self.btn_connect_broker.setEnabled(True)

    def _on_broker_connected(self, xt_trader=None, acc=None):
        self.inject_broker()

        self.btn_connect_broker.setText("连接")
        self.btn_connect_broker.setEnabled(False)
        self.btn_disconnect_broker.setEnabled(True)
        # 连接成功后自动折叠设置区
        self._broker_settings_widget.setVisible(False)
        account = self.edit_account.text().strip()
        self.lbl_broker_status.setText(f"⬤ 已连接  {account}")
        self.lbl_broker_status.setStyleSheet(
            "color:#16A34A;font-size:11px;font-weight:bold;")

    def _on_broker_failed(self, msg: str):
        self.btn_connect_broker.setText("连接")
        self.btn_connect_broker.setEnabled(True)
        self.lbl_broker_status.setText("⬤ 连接失败")
        self.lbl_broker_status.setStyleSheet("color:#DC2626;font-size:11px;")
        # 展开设置区方便用户修改
        self._broker_settings_widget.setVisible(True)
        QMessageBox.critical(self, "连接失败", msg)

    def _on_disconnect_broker(self):
        self.broker_session_service.disconnect()
        self.engine.set_executor(SimulatedExecutor())
        self.btn_connect_broker.setEnabled(True)
        self.btn_connect_broker.setText("连接")
        self.btn_disconnect_broker.setEnabled(False)
        self.lbl_broker_status.setText("⬤ 已断开（模拟模式）")
        self.lbl_broker_status.setStyleSheet(
            f"color:{self._THEME['text_secondary']};font-size:11px;")
        self._on_log("🔌 已断开券商连接，切回模拟模式")
        self._refresh_status()

    # ── 操作面板 ──

    def _build_action_panel(self) -> QGroupBox:
        grp = QGroupBox("操作")
        grp.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(grp)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        section_title_style = "color:#94A3B8;font-size:11px;font-weight:bold;"
        utility_btn_min_width = 112
        utility_btn_height = 30
        manual_btn_height = 30

        # ── 主操作 ──
        main_label = QLabel("主操作")
        main_label.setStyleSheet(section_title_style)
        layout.addWidget(main_label)

        row1 = QHBoxLayout()
        row1.setSpacing(6)

        self.btn_check = QPushButton("计算信号")
        self.btn_check.setToolTip("仅计算信号，交易由实盘策略中枢统一执行")
        self.btn_check.clicked.connect(self._on_check_signal)
        self.btn_check.setMinimumHeight(36)
        self.btn_check.setStyleSheet(
            "QPushButton{background:#3B82F6;color:white;padding:8px 16px;"
            "border-radius:5px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#2563EB;}"
        )
        row1.addWidget(self.btn_check)

        self.btn_execute = QPushButton("生成执行信号")
        self.btn_execute.setToolTip("生成统一执行信号，交易由实盘策略中枢提交")
        self.btn_execute.clicked.connect(self._on_check_and_execute)
        self.btn_execute.setMinimumHeight(36)
        self.btn_execute.setStyleSheet(
            "QPushButton{background:#DC2626;color:white;padding:8px 16px;"
            "border-radius:5px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#B91C1C;}"
        )
        row1.addWidget(self.btn_execute)

        layout.addLayout(row1)

        sep_main = QFrame()
        sep_main.setFrameShape(QFrame.Shape.HLine)
        sep_main.setStyleSheet("color:#3c3c3c;")
        layout.addWidget(sep_main)

        # ── 设置 ──
        settings_label = QLabel("设置")
        settings_label.setStyleSheet(section_title_style)
        layout.addWidget(settings_label)

        self.lbl_auto_status = QLabel("定时任务: 未启用")
        self.lbl_auto_status.setStyleSheet("color:#6B7B8D;font-size:11px;")
        layout.addWidget(self.lbl_auto_status)

        config_ctl_row = QHBoxLayout()
        config_ctl_row.setSpacing(6)

        self.btn_toggle_schedule = QPushButton("⏰ 定时任务")
        self.btn_toggle_schedule.setToolTip("打开 ETF 自动调度配置")
        self.btn_toggle_schedule.clicked.connect(self._open_schedule_dialog)
        self.btn_toggle_schedule.setMinimumWidth(utility_btn_min_width)
        self.btn_toggle_schedule.setMinimumHeight(utility_btn_height)
        self.btn_toggle_schedule.setStyleSheet(
            "QPushButton{background:#0EA5E9;color:white;padding:5px 10px;"
            "border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#0284C7;}"
        )
        config_ctl_row.addWidget(self.btn_toggle_schedule)

        self.btn_toggle_config = QPushButton("⚙ 查看配置")
        self.btn_toggle_config.setToolTip("打开 ETF 轮动实盘配置弹窗（默认只读）")
        self.btn_toggle_config.clicked.connect(self._on_toggle_config)
        self.btn_toggle_config.setMinimumWidth(utility_btn_min_width)
        self.btn_toggle_config.setMinimumHeight(utility_btn_height)
        self.btn_toggle_config.setStyleSheet(
            "QPushButton{background:#6366F1;color:white;padding:5px 10px;"
            "border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#4F46E5;}"
        )
        config_ctl_row.addWidget(self.btn_toggle_config)

        layout.addLayout(config_ctl_row)
        self._config_locked = True

        sep_settings = QFrame()
        sep_settings.setFrameShape(QFrame.Shape.HLine)
        sep_settings.setStyleSheet("color:#3c3c3c;")
        layout.addWidget(sep_settings)

        # ── 手动干预 ──
        manual_label = QLabel("手动干预")
        manual_label.setStyleSheet(section_title_style)
        layout.addWidget(manual_label)

        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self.btn_manual_sell = QPushButton("手动委托")
        self.btn_manual_sell.clicked.connect(self._open_manual_order_dialog)
        self.btn_manual_sell.setMinimumHeight(manual_btn_height)
        self.btn_manual_sell.setStyleSheet(
            "QPushButton{background:#2d2d2d;color:#ffffff;padding:6px 10px;"
            "border:1px solid #3c3c3c;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#3c3c3c;}"
        )
        row3.addWidget(self.btn_manual_sell)
        layout.addLayout(row3)

        return grp

    # ── ETF 标的池面板 ──

    DEFAULT_ETF_POOL = [
        ('510880', '红利ETF'),
        ('159949', '创业板50ETF'),
        ('513100', '纳指ETF'),
        ('518880', '黄金ETF'),
    ]

    EXTENDED_ETF_POOL = [
        ('510300', '沪深300ETF'),
        ('510500', '中证500ETF'),
        ('159915', '创业板ETF'),
        ('512100', '中证1000ETF'),
        ('159901', '深证100ETF'),
        ('510050', '上证50ETF'),
        ('512010', '医药ETF'),
        ('512880', '证券ETF'),
        ('515180', '红利ETF基金'),
        ('512690', '酒ETF'),
        ('512480', '半导体ETF'),
        ('515790', '光伏ETF'),
        ('512660', '军工ETF'),
        ('159869', '游戏ETF'),
        ('513050', '中概互联ETF'),
        ('159941', '纳指ETF(QDII)'),
        ('513500', '标普500ETF'),
        ('518800', '黄金基金ETF'),
        ('511010', '国债ETF'),
        ('511260', '十年国债ETF'),
    ]

    def _build_etf_panel(self) -> QGroupBox:
        grp = QGroupBox("ETF标的池")
        layout = QVBoxLayout(grp)
        layout.setSpacing(4)

        # 批量操作按钮
        btn_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.setFixedHeight(22)
        btn_all.clicked.connect(self._etf_select_all)
        btn_row.addWidget(btn_all)

        btn_none = QPushButton("全不选")
        btn_none.setFixedHeight(22)
        btn_none.clicked.connect(self._etf_deselect_all)
        btn_row.addWidget(btn_none)

        btn_default = QPushButton("默认")
        btn_default.setFixedHeight(22)
        btn_default.setToolTip("恢复默认的4只ETF")
        btn_default.clicked.connect(self._etf_select_default)
        btn_row.addWidget(btn_default)

        layout.addLayout(btn_row)

        # ETF 列表
        self.etf_list = QListWidget()
        self.etf_list.setMinimumHeight(180)
        self.etf_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)

        # 加载 ETF 名称映射
        try:
            from common.data_loader import load_etf_name_map
            self._ui_etf_name_map = load_etf_name_map()
        except Exception:
            self._ui_etf_name_map = {}

        current_pool = set(self.engine.config.etf_pool)
        added = set()

        for code, name in self.DEFAULT_ETF_POOL:
            self._add_etf_item(code, name, checked=(code in current_pool))
            added.add(code)

        for code, name in self.EXTENDED_ETF_POOL:
            if code not in added:
                self._add_etf_item(code, name, checked=(code in current_pool))
                added.add(code)

        for code in current_pool:
            if code not in added:
                name = self._ui_etf_name_map.get(code, '')
                self._add_etf_item(code, name, checked=True)
                added.add(code)

        layout.addWidget(self.etf_list)

        # 手动添加/删除行
        custom_row = QHBoxLayout()
        self.etf_input = QComboBox()
        self.etf_input.setEditable(True)
        self.etf_input.setPlaceholderText("输入ETF代码")
        self.etf_input.lineEdit().setPlaceholderText("输入ETF代码")
        custom_row.addWidget(self.etf_input, 1)

        btn_add = QPushButton("+")
        btn_add.setFixedSize(26, 26)
        btn_add.setToolTip("添加自定义ETF")
        btn_add.clicked.connect(self._etf_add_custom)
        custom_row.addWidget(btn_add)

        btn_rm = QPushButton("-")
        btn_rm.setFixedSize(26, 26)
        btn_rm.setToolTip("删除选中的ETF")
        btn_rm.clicked.connect(self._etf_remove_selected)
        custom_row.addWidget(btn_rm)

        layout.addLayout(custom_row)

        self.lbl_etf_info = QLabel()
        self.lbl_etf_info.setStyleSheet("color:#888888;font-size:11px;")
        self._etf_update_info()
        layout.addWidget(self.lbl_etf_info)

        return grp

    def _add_etf_item(self, code: str, name: str, checked: bool = False):
        display = f"{code}  {name}" if name else code
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, code)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        self.etf_list.addItem(item)

    def _etf_select_all(self):
        for i in range(self.etf_list.count()):
            self.etf_list.item(i).setCheckState(Qt.CheckState.Checked)
        self._etf_update_info()

    def _etf_deselect_all(self):
        for i in range(self.etf_list.count()):
            self.etf_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._etf_update_info()

    def _etf_select_default(self):
        defaults = {code for code, _ in self.DEFAULT_ETF_POOL}
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            code = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if code in defaults
                else Qt.CheckState.Unchecked
            )
        self._etf_update_info()

    def _etf_add_custom(self):
        code = self.etf_input.currentText().strip()
        if not code:
            return
        for i in range(self.etf_list.count()):
            if self.etf_list.item(i).data(Qt.ItemDataRole.UserRole) == code:
                self.etf_list.item(i).setCheckState(Qt.CheckState.Checked)
                self.etf_list.scrollToItem(self.etf_list.item(i))
                self.etf_input.clearEditText()
                self._etf_update_info()
                return
        name = self._ui_etf_name_map.get(code, '')
        self._add_etf_item(code, name, checked=True)
        self.etf_input.clearEditText()
        self.etf_list.scrollToItem(
            self.etf_list.item(self.etf_list.count() - 1)
        )
        self._etf_update_info()

    def _etf_remove_selected(self):
        selected = self.etf_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先点击选中要删除的ETF条目")
            return
        for item in selected:
            self.etf_list.takeItem(self.etf_list.row(item))
        self._etf_update_info()

    def _etf_update_info(self):
        total = self.etf_list.count()
        checked = sum(
            1 for i in range(total)
            if self.etf_list.item(i).checkState() == Qt.CheckState.Checked
        )
        self.lbl_etf_info.setText(f"共 {total} 只ETF，已选 {checked} 只")

    def _get_selected_etf_codes(self) -> list:
        """返回当前勾选的 ETF 代码列表"""
        codes = []
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                codes.append(item.data(Qt.ItemDataRole.UserRole))
        return codes

    # ── 配置面板 ──

    def _build_config_panel(self) -> QGroupBox:
        grp = QGroupBox("策略参数")
        grid = QGridLayout(grp)
        grid.setSpacing(4)

        cfg = self.engine.config
        row = 0

        # 因子配置（紧凑网格）
        _ETF_FACTORS = {
            'bias_momentum_fast': '乖离动量',
            'slope_momentum_fast': '斜率动量',
            'efficiency_momentum_fast': '效率动量',
            'risk_adjusted_momentum': '风险调整动量',
            'inverse_volatility': '反向波动率',
            'volume_price_correlation': '量价相关性',
        }
        active_factors = {name: w for name, w in cfg.factor_config}
        self._live_factor_rows = []

        factor_label = QLabel("因子:")
        factor_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(factor_label, row, 0, 1, 4)
        row += 1

        for fname, display_name in _ETF_FACTORS.items():
            if factor_registry.get(fname) is None:
                continue
            is_active = fname in active_factors
            chk = QCheckBox(display_name)
            chk.setChecked(is_active)
            chk.setToolTip(fname)
            grid.addWidget(chk, row, 0, 1, 3)

            ws = _FocusDoubleSpinBox()
            ws.setRange(0, 5); ws.setSingleStep(0.05); ws.setDecimals(2)
            ws.setValue(active_factors.get(fname, 0.2))
            ws.setEnabled(is_active)
            ws.setFixedWidth(60)
            chk.stateChanged.connect(lambda st, w=ws: w.setEnabled(st == Qt.CheckState.Checked.value))
            grid.addWidget(ws, row, 3)
            self._live_factor_rows.append((fname, chk, ws))
            row += 1

        grid.addWidget(QLabel("调仓阈值:"), row, 0)
        self.spin_threshold = _FocusDoubleSpinBox()
        self.spin_threshold.setRange(1.0, 5.0); self.spin_threshold.setSingleStep(0.1)
        self.spin_threshold.setDecimals(2); self.spin_threshold.setValue(cfg.rebalance_threshold)
        grid.addWidget(self.spin_threshold, row, 1)
        row += 1

        grid.addWidget(QLabel("动量窗口:"), row, 0)
        self.spin_mom = _FocusSpinBox()
        self.spin_mom.setRange(10, 60); self.spin_mom.setValue(cfg.momentum_window)
        grid.addWidget(self.spin_mom, row, 1)

        grid.addWidget(QLabel("ZScore窗口:"), row, 2)
        self.spin_zscore = _FocusSpinBox()
        self.spin_zscore.setRange(20, 120); self.spin_zscore.setValue(cfg.zscore_window)
        grid.addWidget(self.spin_zscore, row, 3)
        row += 1

        self.chk_empty = QCheckBox("启用空仓信号")
        self.chk_empty.setChecked(cfg.enable_empty_position)
        grid.addWidget(self.chk_empty, row, 0, 1, 2)

        grid.addWidget(QLabel("空仓阈值:"), row, 2)
        self.spin_empty = _FocusDoubleSpinBox()
        self.spin_empty.setRange(-3, 1); self.spin_empty.setSingleStep(0.1)
        self.spin_empty.setDecimals(2); self.spin_empty.setValue(cfg.empty_threshold)
        grid.addWidget(self.spin_empty, row, 3)
        row += 1

        grid.addWidget(QLabel("调仓周期:"), row, 0)
        self.combo_rebalance_period = _FocusComboBox()
        _period_options = [
            ("每日 (1天)", 1), ("每2天", 2), ("每3天", 3),
            ("每周 (5天)", 5), ("每两周 (10天)", 10), ("每月 (20天)", 20),
        ]
        _cur_period = getattr(cfg, 'rebalance_period', 1)
        for label, val in _period_options:
            self.combo_rebalance_period.addItem(label, val)
        for i, (_, val) in enumerate(_period_options):
            if val == _cur_period:
                self.combo_rebalance_period.setCurrentIndex(i)
                break
        grid.addWidget(self.combo_rebalance_period, row, 1, 1, 3)
        row += 1

        # ── 风控设置 ──
        sep_risk = QLabel("── 策略内风控（信号级）──")
        sep_risk.setStyleSheet("color:#94A3B8;font-size:11px;")
        sep_risk.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(sep_risk, row, 0, 1, 4)
        row += 1

        self.chk_trailing_stop = QCheckBox("移动止盈")
        self.chk_trailing_stop.setChecked(cfg.enable_trailing_stop)
        grid.addWidget(self.chk_trailing_stop, row, 0, 1, 2)
        grid.addWidget(QLabel("回撤%:"), row, 2)
        self.spin_trailing_pct = _FocusDoubleSpinBox()
        self.spin_trailing_pct.setRange(1, 30)
        self.spin_trailing_pct.setSingleStep(1)
        self.spin_trailing_pct.setDecimals(1)
        self.spin_trailing_pct.setValue(cfg.trailing_stop_pct * 100)
        grid.addWidget(self.spin_trailing_pct, row, 3)
        row += 1

        self.chk_drawdown = QCheckBox("账户回撤保护")
        self.chk_drawdown.setChecked(cfg.enable_drawdown_protection)
        grid.addWidget(self.chk_drawdown, row, 0, 1, 2)
        grid.addWidget(QLabel("最大回撤%:"), row, 2)
        self.spin_max_dd = _FocusDoubleSpinBox()
        self.spin_max_dd.setRange(5, 50)
        self.spin_max_dd.setSingleStep(1)
        self.spin_max_dd.setDecimals(1)
        self.spin_max_dd.setValue(cfg.max_drawdown_pct * 100)
        grid.addWidget(self.spin_max_dd, row, 3)
        row += 1

        grid.addWidget(QLabel("冷却天数:"), row, 0)
        self.spin_cooldown = _FocusSpinBox()
        self.spin_cooldown.setRange(1, 60)
        self.spin_cooldown.setValue(cfg.drawdown_cooldown_days)
        grid.addWidget(self.spin_cooldown, row, 1)
        row += 1

        # ── 策略风控（网关统一，声明式 schema 自动渲染）──
        # 负责 trading window / 每日交易次数 / 最少持有天数 / 单笔亏损告警
        # 由 ETFRotationRiskPolicy.config_schema() 驱动，与 policy 同步更新
        risk_policy = getattr(self.engine, "_strategy_risk_policy", None)
        if risk_policy is not None:
            self.risk_policy_panel = StrategyRiskSettingsPanel(
                policy=risk_policy,
                title="── 策略风控（网关统一）──",
                parent=grp,
            )
            self.risk_policy_panel.config_saved.connect(self._on_risk_policy_saved)
            grid.addWidget(self.risk_policy_panel, row, 0, 1, 4)
            row += 1
        else:
            self.risk_policy_panel = None

        # ── 专用资金 ──
        sep_cap = QLabel("── 资金管理 ──")
        sep_cap.setStyleSheet("color:#94A3B8;font-size:11px;")
        sep_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(sep_cap, row, 0, 1, 4)
        row += 1

        grid.addWidget(QLabel("当前启动资金:"), row, 0)
        self.lbl_dedicated_capital = QLabel()
        self.lbl_dedicated_capital.setStyleSheet("font-weight:bold;")
        grid.addWidget(self.lbl_dedicated_capital, row, 1, 1, 3)
        row += 1

        # 保存按钮
        self.btn_save_cfg = QPushButton("保存配置")
        self.btn_save_cfg.clicked.connect(self._on_save_config)
        self.btn_save_cfg.setStyleSheet(
            "QPushButton{background:#3B82F6;color:white;padding:5px 12px;"
            "border-radius:4px;}"
            "QPushButton:hover{background:#2563EB;}"
        )
        grid.addWidget(self.btn_save_cfg, row, 2, 1, 2)

        return grp

    def _get_schedule_dialog(self) -> ETFSchedulerSettingsDialog:
        """Lazy-create the ETF scheduler dialog and reuse it across opens."""
        if self._schedule_dialog is None:
            self._schedule_dialog = ETFSchedulerSettingsDialog(
                self.engine,
                log_callback=self._on_log,
                refresh_callback=self._refresh_status,
                parent=self,
            )
        return self._schedule_dialog

    def _get_config_dialog(self) -> ETFStrategyConfigDialog:
        if self._config_dialog is None:
            self._config_dialog = ETFStrategyConfigDialog(self, parent=self.window())
        return self._config_dialog

    def _get_manual_order_dialog(self) -> ETFManualOrderDialog:
        """Lazy-create ETF manual order dialog and reuse it across opens."""
        if self._manual_order_dialog is None:
            self._manual_order_dialog = ETFManualOrderDialog(
                self.engine,
                name_resolver=lambda code: (
                    getattr(self, "_ui_etf_name_map", {}) or getattr(self.engine, "_etf_name_map", {})
                ).get(code, code or ""),
                refresh_callback=self._refresh_status,
                parent=self,
            )
        return self._manual_order_dialog

    # ==================================================================
    #  事件处理
    # ==================================================================

    def _on_shared_broker_connected(self):
        self.inject_broker()
        self._refresh_status()

    def _on_shared_broker_disconnected(self):
        self.engine.set_executor(SimulatedExecutor())
        self._on_log("🔌 已断开券商连接，切回模拟模式")
        self._refresh_status()

    def _start_startup_orchestration(self):
        if self.startup_orchestrator is None:
            return
        if self.startup_orchestrator.is_running:
            return
        started = self.startup_orchestrator.start()
        if started:
            self.broker_panel.show_client_workflow_status("启动自检中...", success=None)
            self._on_log("启动后自动执行 QMT 自检流程")

    def _on_startup_status(self, message: str):
        self.broker_panel.show_client_workflow_status(message, success=None)
        self._on_log(f"QMT启动流程: {message}")

    def _on_startup_finished(self, success: bool, message: str):
        self.broker_panel.show_client_workflow_status(message, success=success)
        self.broker_panel.refresh_client_status()
        if success:
            self._on_log(f"QMT启动流程完成: {message}")
        else:
            self._on_log(f"QMT启动流程失败: {message}")
        self._restore_auto_mode_if_needed()

    def _restore_auto_mode_if_needed(self):
        try:
            if bool(self.engine.config.auto_enabled) and not self.engine._auto_timer.isActive():
                self.engine.start_auto()
            elif not bool(self.engine.config.auto_enabled) and self.engine._auto_timer.isActive():
                self.engine.stop_auto()
        except Exception as exc:
            logger.warning("恢复 ETF 自动调度失败: %s", exc)

    def _refresh_schedule_status(self):
        dialog = self._schedule_dialog
        if dialog is not None:
            dialog.refresh_runtime_status()

    def _check_live_market_data_ready(self) -> tuple[bool, str]:
        codes = list(dict.fromkeys(str(code or "").strip() for code in self.engine.config.etf_pool if str(code or "").strip()))
        status = self.market_data_status_service.check_status(
            stock_codes=[],
            etf_codes=codes,
            index_codes=[],
            realtime_probe_codes=codes[:3] if codes else None,
            require_minute_freshness=False,
        )
        if status.can_run_live_strategy:
            return True, status.summary
        return False, status.summary

    def _on_check_signal(self):
        ok, reason = self._check_live_market_data_ready()
        if not ok:
            message = f"ETF轮动实盘已阻断: {reason}"
            self._on_status(message)
            QMessageBox.warning(self, "行情数据未就绪", message)
            return
        self.btn_check.setEnabled(False)
        self.btn_check.setText("计算中...")
        try:
            self.engine.run_signal_check()
        finally:
            self.btn_check.setEnabled(True)
            self.btn_check.setText("计算信号")

    def _on_check_and_execute(self):
        ok, reason = self._check_live_market_data_ready()
        if not ok:
            message = f"ETF轮动实盘已阻断: {reason}"
            self._on_status(message)
            QMessageBox.warning(self, "行情数据未就绪", message)
            return
        reply = QMessageBox.question(
            self, "确认",
            "将计算 ETF 轮动实盘信号。交易执行请通过实盘策略中枢的统一执行入口完成，确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.btn_execute.setEnabled(False)
        self.btn_execute.setText("计算中...")
        try:
            self.engine.run_signal_check()
            QMessageBox.information(self, "提示", "ETF轮动实盘信号已生成。请在实盘策略中枢执行统一执行。")
        finally:
            self.btn_execute.setEnabled(True)
            self.btn_execute.setText("计算信号")

    # ── 配置弹窗只读/解锁保护 ──

    def _on_toggle_config(self):
        """打开独立的 ETF 轮动实盘配置弹窗。"""
        dialog = self._get_config_dialog()
        dialog.prepare_for_open()
        dialog.exec()

    def _open_schedule_dialog(self):
        dialog = self._get_schedule_dialog()
        dialog.load_from_engine()
        dialog.exec()

    def _open_manual_order_dialog(self):
        dialog = self._get_manual_order_dialog()
        dialog.reload_symbol_options()
        dialog.prefill_from_current_holding()
        dialog.exec()

    def request_unlock_config(self) -> bool:
        """解锁配置面板，允许编辑。"""
        reply = QMessageBox.question(
            self, "解锁编辑",
            "确定要解锁配置面板进行编辑吗？\n修改后请点击「保存配置」按钮。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._unlock_config_panels()
            return True
        return False

    def _reload_config_dialog_data(self):
        cfg = self.engine.config
        current_pool = set(cfg.etf_pool)
        self.etf_list.clear()
        added = set()
        for code, name in self.DEFAULT_ETF_POOL:
            self._add_etf_item(code, name, checked=(code in current_pool))
            added.add(code)
        for code, name in self.EXTENDED_ETF_POOL:
            if code not in added:
                self._add_etf_item(code, name, checked=(code in current_pool))
                added.add(code)
        for code in current_pool:
            if code not in added:
                name = self._ui_etf_name_map.get(code, "")
                self._add_etf_item(code, name, checked=True)
                added.add(code)
        self.etf_input.clearEditText()
        self._etf_update_info()

        active_factors = {name: weight for name, weight in cfg.factor_config}
        for fname, chk, ws in getattr(self, "_live_factor_rows", []):
            enabled = fname in active_factors
            chk.setChecked(enabled)
            ws.setEnabled(enabled)
            if enabled:
                ws.setValue(active_factors.get(fname, ws.value()))

        self.spin_threshold.setValue(cfg.rebalance_threshold)
        self.spin_mom.setValue(cfg.momentum_window)
        self.spin_zscore.setValue(cfg.zscore_window)
        self.chk_empty.setChecked(cfg.enable_empty_position)
        self.spin_empty.setValue(cfg.empty_threshold)
        idx = self.combo_rebalance_period.findData(getattr(cfg, "rebalance_period", 1))
        if idx >= 0:
            self.combo_rebalance_period.setCurrentIndex(idx)
        self.chk_trailing_stop.setChecked(cfg.enable_trailing_stop)
        self.spin_trailing_pct.setValue(cfg.trailing_stop_pct * 100)
        self.chk_drawdown.setChecked(cfg.enable_drawdown_protection)
        self.spin_max_dd.setValue(cfg.max_drawdown_pct * 100)
        self.spin_cooldown.setValue(cfg.drawdown_cooldown_days)
        self.refresh_shared_setting_hint()
        if hasattr(self, "lbl_dedicated_capital"):
            self.lbl_dedicated_capital.setText(f"{float(cfg.dedicated_capital or 0.0):,.0f} 元")
        if self.risk_policy_panel is not None:
            try:
                self.risk_policy_panel.reload()
            except Exception as exc:
                logger.error("reload ETF 轮动实盘风控面板失败: %s", exc, exc_info=True)

    def refresh_shared_setting_hint(self) -> None:
        capital_limit = float(getattr(self.engine.config, "dedicated_capital", 0.0) or 0.0)
        if hasattr(self, "lbl_dedicated_capital"):
            self.lbl_dedicated_capital.setText(f"{capital_limit:,.0f} 元")

    def _lock_config_panels(self):
        """将 ETF 标的池和策略参数面板设为只读"""
        self._config_locked = True
        for w in self._etf_panel.findChildren(QWidget):
            if not isinstance(w, (QLabel, QGroupBox)):
                w.setEnabled(False)
        for w in self._config_panel.findChildren(QWidget):
            if not isinstance(w, (QLabel, QGroupBox)):
                w.setEnabled(False)

    def _unlock_config_panels(self):
        """解锁 ETF 标的池和策略参数面板"""
        self._config_locked = False
        for w in self._etf_panel.findChildren(QWidget):
            w.setEnabled(True)
        for w in self._config_panel.findChildren(QWidget):
            w.setEnabled(True)

    def _on_data_update_done(self, success, total, errors):
        self._refresh_status()

    def _on_risk_policy_saved(self, _values: dict) -> None:
        """策略风控面板保存后刷新配置面板里仍用的信号级控件显示值。

        policy.apply_config 已经通过 RotationEngine.update_config 落盘，
        这里无需再写 RotationConfig；仅同步"当前值"以防 UI 残留。
        """
        cfg = self.engine.config
        try:
            if hasattr(self, "spin_trailing_pct"):
                self.spin_trailing_pct.setValue(cfg.trailing_stop_pct * 100)
            if hasattr(self, "spin_max_dd"):
                self.spin_max_dd.setValue(cfg.max_drawdown_pct * 100)
            if hasattr(self, "spin_cooldown"):
                self.spin_cooldown.setValue(cfg.drawdown_cooldown_days)
        except Exception:
            pass

    def _on_save_config(self):
        cfg = self.engine.config

        selected_etfs = self._get_selected_etf_codes()
        if len(selected_etfs) < 2:
            QMessageBox.warning(self, "提示", "至少需要选中2只ETF")
            return
        cfg.etf_pool = selected_etfs

        fc = []
        for fname, chk, ws in self._live_factor_rows:
            if chk.isChecked():
                fc.append((fname, ws.value()))
        if not fc:
            QMessageBox.warning(self, "提示", "至少需要选中1个因子")
            return
        cfg.factor_config = fc
        cfg.rebalance_threshold = self.spin_threshold.value()
        cfg.momentum_window = self.spin_mom.value()
        cfg.zscore_window = self.spin_zscore.value()
        cfg.enable_empty_position = self.chk_empty.isChecked()
        cfg.empty_threshold = self.spin_empty.value()
        cfg.rebalance_period = self.combo_rebalance_period.currentData()
        cfg.enable_trailing_stop = self.chk_trailing_stop.isChecked()
        cfg.trailing_stop_pct = self.spin_trailing_pct.value() / 100
        cfg.enable_drawdown_protection = self.chk_drawdown.isChecked()
        cfg.max_drawdown_pct = self.spin_max_dd.value() / 100
        cfg.drawdown_cooldown_days = self.spin_cooldown.value()
        self.engine.update_config(cfg)
        self._sync_etf_strategy_profile()
        self.refresh_shared_setting_hint()
        QMessageBox.information(self, "提示",
            f"配置已保存（ETF池: {len(selected_etfs)} 只）")
        self._lock_config_panels()
        if self._config_dialog is not None:
            self._config_dialog.reset_unlock_state()

    # ==================================================================
    #  信号回调
    # ==================================================================

    def _on_log(self, msg: str):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_signal(self, signal: str, detail: dict):
        self._refresh_status()
        self._refresh_statistics()
        self.strategy_trade_panel.refresh_all()

    def _on_trade(self, success: bool, detail: dict):
        self._refresh_status()
        self._refresh_all_analysis_tabs()
        self.strategy_trade_panel.refresh_all()

    def _on_scores(self, scores: dict):
        self._update_score_table(scores)

    def _on_status(self, text: str):
        message = str(text or "").strip()
        if not message:
            return
        self._on_log(message)
        if hasattr(self, "lbl_data_status"):
            is_error = "阻断" in message or "未就绪" in message or "失败" in message or "异常" in message
            self._set_data_status_text(
                message,
                ok=not is_error,
                prefix="⛔" if is_error else "",
                error_color="#DC2626" if is_error else "#94A3B8",
            )

    def _format_compact_status_message(self, message: str, *, max_len: int = 68) -> str:
        compact = " ".join(str(message or "").strip().split())
        if len(compact) <= max_len:
            return compact
        return f"{compact[:max_len].rstrip()}…"

    def _set_data_status_text(
        self,
        message: str,
        *,
        ok: bool,
        prefix: str = "",
        error_color: str = "#DC2626",
    ) -> None:
        full_message = str(message or "").strip() or "-"
        display = self._format_compact_status_message(full_message)
        text = f"{prefix} {display}".strip() if prefix else display
        self.lbl_data_status.setToolTip(full_message)
        self.lbl_data_status.setText(text)
        color = "#16A34A" if ok else error_color
        self.lbl_data_status.setStyleSheet(f"color:{color};font-size:11px;")

    # ==================================================================
    #  数据刷新
    # ==================================================================

    def _refresh_status(self):
        summary = self.engine.get_status_summary()
        self._refresh_schedule_status()
        self.strategy_trade_panel.refresh_all()

        # 持仓信息
        if summary['holding']:
            self.lbl_holding.setText(
                f"{summary['holding']} {summary['holding_name']}"
            )
            self.lbl_buy_price.setText(
                f"{summary['buy_price']:.3f} "
                f"({summary['buy_quantity']}股, {summary['buy_date']})"
            )
        else:
            self.lbl_holding.setText("空仓")
            self.lbl_buy_price.setText("-")

        # 当前价格 & 盈亏
        if summary['current_price'] > 0:
            price_tag = "" if summary.get('price_is_realtime') else " (买入价)"
            self.lbl_current_price.setText(
                f"{summary['current_price']:.3f}{price_tag}")
            pnl = summary['unrealized_pnl']
            pnl_color = "#DC2626" if pnl >= 0 else "#16A34A"
            self.lbl_pnl.setText(f"{pnl:+,.2f}")
            self.lbl_pnl.setStyleSheet(
                f"color:{pnl_color};font-size:13px;font-weight:bold;"
            )
        else:
            self.lbl_current_price.setText("-")
            self.lbl_pnl.setText("-")
            self.lbl_pnl.setStyleSheet("font-size:13px;color:#d0d0d0;")

        # 信号
        signal = summary['last_signal']
        if signal:
            signal_colors = {
                'HOLD': '#3B82F6', 'SWITCH': '#D97706',
                'SELL_ALL': '#DC2626', 'BUY': '#16A34A',
                'NO_ACTION': '#94A3B8',
                'TRAILING_STOP': '#EA580C', 'DRAWDOWN_STOP': '#DC2626',
                'COOLDOWN': '#6B7B8D',
            }
            color = signal_colors.get(signal, '#d0d0d0')
            self.lbl_signal.setText(signal)
            self.lbl_signal.setStyleSheet(f"color:{color};font-weight:bold;")
        else:
            self.lbl_signal.setText("-")

        # 检查时间
        self.lbl_last_check.setText(summary['last_check'] or "-")

        # ETF轮动实盘虚拟账户概览
        account_view = self._get_etf_strategy_account_view(summary)
        total_asset = float(account_view.get('total_asset', 0.0) or 0.0)
        available_cash = float(account_view.get('available_cash', 0.0) or 0.0)
        market_value = float(account_view.get('market_value', 0.0) or 0.0)
        total_pnl = float(account_view.get('total_pnl', 0.0) or 0.0)
        self.lbl_strategy_total_asset.setText(f"{total_asset:,.2f} 元")
        self.lbl_strategy_available_cash.setText(f"{available_cash:,.2f} 元")
        self.lbl_strategy_market_value.setText(f"{market_value:,.2f} 元")
        pnl_color = "#DC2626" if total_pnl >= 0 else "#16A34A"
        self.lbl_strategy_total_pnl.setText(f"{total_pnl:+,.2f} 元")
        self.lbl_strategy_total_pnl.setStyleSheet(
            f"color:{pnl_color};font-size:13px;font-weight:bold;"
        )

        # 数据状态
        try:
            codes = list(dict.fromkeys(str(code or "").strip() for code in self.engine.config.etf_pool if str(code or "").strip()))
            market_status = self.market_data_status_service.check_status(
                stock_codes=[],
                etf_codes=codes,
                index_codes=[],
                realtime_probe_codes=codes[:3] if codes else None,
                require_minute_freshness=False,
            )
            self.lbl_data_status.setToolTip(market_status.summary)
            if market_status.can_run_live_strategy:
                self._set_data_status_text("行情数据可执行", ok=True, prefix="✓")
            else:
                self._set_data_status_text(market_status.summary, ok=False, prefix="⛔")
        except Exception as exc:
            data_fresh = summary.get('data_fresh', False)
            if data_fresh:
                self._set_data_status_text("数据已是最新", ok=True, prefix="✓")
            else:
                self._set_data_status_text(f"数据需要更新: {exc}", ok=False, prefix="✗", error_color="#EA580C")

        # 执行器
        connected = summary['executor_connected']
        exec_type = type(self.engine.executor).__name__
        if connected:
            self.lbl_executor.setText(f"✓ {exec_type}")
            self.lbl_executor.setStyleSheet("color:#16A34A;")
        else:
            self.lbl_executor.setText(f"✗ {exec_type} (未连接)")
            self.lbl_executor.setStyleSheet("color:#DC2626;")

        # 自动模式状态 & 冷却期显示
        cooldown = summary.get('cooldown_remaining', 0)
        if cooldown > 0:
            self.lbl_auto_status.setText(
                f"⚠ 回撤保护冷却期（剩余 {cooldown} 天）"
            )
            self.lbl_auto_status.setStyleSheet("color:#EA580C;font-size:11px;")
        elif self.engine._auto_timer.isActive():
            mode_label = "自动生成信号" if bool(getattr(self.engine.config, "auto_signal_enabled", True)) else "仅手动检查"
            self.lbl_auto_status.setText(
                f"定时任务: 已启用 ({self.engine.config.check_time}，{mode_label})"
            )
            self.lbl_auto_status.setStyleSheet("color:#16A34A;font-size:11px;")
        else:
            self.lbl_auto_status.setText(
                "定时任务: 已启用，等待启动" if bool(self.engine.config.auto_enabled) else "定时任务: 未启用"
            )
            self.lbl_auto_status.setStyleSheet("color:#6B7B8D;font-size:11px;")

        # 得分快照
        if summary['last_scores']:
            self._update_score_table(summary['last_scores'])

    def _update_score_table(self, scores: dict):
        t = self._THEME
        name_map = self.engine._etf_name_map
        holding = self.engine.state.current_holding
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        self.score_table.setRowCount(len(sorted_items))
        for i, (code, score) in enumerate(sorted_items):
            is_holding = (code == holding)
            bg = QColor(t['holding_bg']) if is_holding else None

            # 代码
            code_item = QTableWidgetItem(code)
            code_item.setForeground(QColor(t['text']))
            if bg:
                code_item.setBackground(bg)
            self.score_table.setItem(i, 0, code_item)

            # 名称
            name = name_map.get(code, "")
            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor(t['text_secondary']))
            if bg:
                name_item.setBackground(bg)
            self.score_table.setItem(i, 1, name_item)

            # 得分 — 正值红色、负值绿色、零灰色
            score_item = QTableWidgetItem(f"{score:+.4f}")
            score_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            if score > 0:
                score_item.setForeground(QColor(t['red']))
            elif score < 0:
                score_item.setForeground(QColor(t['green']))
            else:
                score_item.setForeground(QColor(t['text_secondary']))
            if bg:
                score_item.setBackground(bg)
            self.score_table.setItem(i, 2, score_item)

    def _refresh_trade_history(self):
        history = self.engine.state.trade_history
        self.trade_table.setRowCount(len(history))

        for i, rec in enumerate(reversed(history)):
            row = i
            self.trade_table.setItem(row, 0, QTableWidgetItem(rec.get('date', '')))
            self.trade_table.setItem(row, 1, QTableWidgetItem(rec.get('time', '')))

            action = rec.get('action', '')
            action_item = QTableWidgetItem(action)
            if action == 'BUY':
                action_item.setForeground(QColor("#DC2626"))
            elif action in ('SELL', 'SELL_ALL'):
                action_item.setForeground(QColor("#16A34A"))
            self.trade_table.setItem(row, 2, action_item)

            self.trade_table.setItem(row, 3, QTableWidgetItem(rec.get('code', '')))
            self.trade_table.setItem(row, 4, QTableWidgetItem(rec.get('name', '')))

            price = rec.get('price', 0)
            self.trade_table.setItem(row, 5, QTableWidgetItem(
                f"{price:.3f}" if price else "-"
            ))

            qty = rec.get('quantity', 0)
            self.trade_table.setItem(row, 6, QTableWidgetItem(str(qty)))
            self.trade_table.setItem(row, 7, QTableWidgetItem(rec.get('reason', '')))

    def _refresh_statistics(self):
        """刷新统计指标 Tab"""
        stats = self.engine.get_statistics()
        t = self._THEME

        rows = [
            ("总交易次数（完成轮次）", f"{stats['total_trades']} 次"),
            ("盈利次数 / 亏损次数",
             f"{stats['win_trades']} / {stats['loss_trades']}"),
            ("胜率", f"{stats['win_rate']:.1f}%"),
            ("平均单笔盈亏", f"{stats['avg_pnl']:+,.2f} 元"),
            ("最佳单笔",    f"{stats['best_trade']:+,.2f} 元"),
            ("最差单笔",    f"{stats['worst_trade']:+,.2f} 元"),
            ("累计已实现盈亏", f"{stats['total_pnl']:+,.2f} 元"),
            ("平均持仓天数",   f"{stats['avg_hold_days']:.1f} 天"),
            ("当前持仓天数",   f"{stats['current_hold_days']} 天"),
            ("最大回撤",      f"{stats['max_drawdown']:.2f}%"),
            ("初始资金",      f"{stats['initial_capital']:,.0f} 元"),
            ("当前估算净值",  f"{stats['current_equity']:,.2f} 元"),
            ("总收益率",      f"{stats['total_return_pct']:+.2f}%"),
        ]

        self.stat_table.setRowCount(len(rows))
        for i, (label, value) in enumerate(rows):
            self.stat_table.setItem(i, 0, QTableWidgetItem(label))
            val_item = QTableWidgetItem(value)
            val_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            # 盈亏相关行着色
            if any(k in label for k in ("盈亏", "收益", "单笔", "胜率", "回撤")):
                raw = value.replace(",", "").replace("%", "").replace("元", "").strip()
                try:
                    num = float(raw)
                    if num > 0:
                        val_item.setForeground(QColor(t['red']))
                    elif num < 0:
                        val_item.setForeground(QColor(t['green']))
                except ValueError:
                    pass
            self.stat_table.setItem(i, 1, val_item)

    def _refresh_equity_curve(self):
        """刷新净值曲线 Tab（降序展示，最新在上）"""
        t = self._THEME
        equity_dict = self.engine.state.daily_equity
        if not equity_dict:
            self.equity_table.setRowCount(0)
            return

        dates = sorted(equity_dict.keys())
        rows = []
        initial = self.engine.config.dedicated_capital or equity_dict.get(dates[0], 1.0)
        prev_val = None
        for d in dates:
            val = equity_dict[d]
            daily_chg = ((val - prev_val) / prev_val * 100
                         if prev_val and prev_val > 0 else 0.0)
            cum_ret   = (val - initial) / initial * 100 if initial > 0 else 0.0
            rows.append((d, val, daily_chg, cum_ret))
            prev_val = val

        rows_desc = list(reversed(rows))
        self.equity_table.setRowCount(len(rows_desc))
        for i, (d, val, daily_chg, cum_ret) in enumerate(rows_desc):
            self.equity_table.setItem(i, 0, QTableWidgetItem(d))

            val_item = QTableWidgetItem(f"{val:,.2f}")
            val_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.equity_table.setItem(i, 1, val_item)

            for col, pct in [(2, daily_chg), (3, cum_ret)]:
                pct_item = QTableWidgetItem(f"{pct:+.2f}%")
                pct_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                pct_item.setForeground(
                    QColor(t['red']) if pct > 0
                    else (QColor(t['green']) if pct < 0
                          else QColor(t['text_secondary']))
                )
                self.equity_table.setItem(i, col, pct_item)

    def _refresh_order_records(self):
        """刷新委托明细 Tab"""
        t = self._THEME
        records = list(reversed(self.engine.state.order_records))
        self.order_table.setRowCount(len(records))
        for i, r in enumerate(records):
            self.order_table.setItem(i, 0, QTableWidgetItem(r.get('date', '')))
            self.order_table.setItem(i, 1, QTableWidgetItem(r.get('time', '')))

            action = r.get('action', '')
            act_item = QTableWidgetItem(action)
            act_item.setForeground(
                QColor(t['red']) if action == '买入' else QColor(t['green']))
            self.order_table.setItem(i, 2, act_item)

            self.order_table.setItem(i, 3, QTableWidgetItem(r.get('code', '')))
            self.order_table.setItem(i, 4, QTableWidgetItem(r.get('name', '')))

            o_qty = r.get('ordered_qty', 0)
            o_prc = r.get('ordered_price', 0.0)
            self.order_table.setItem(i, 5, QTableWidgetItem(str(o_qty)))
            p_item = QTableWidgetItem(f"{o_prc:.3f}" if o_prc else "-")
            p_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.order_table.setItem(i, 6, p_item)

            f_qty = r.get('filled_qty', 0)
            f_prc = r.get('filled_price', 0.0)
            self.order_table.setItem(i, 7, QTableWidgetItem(str(f_qty) if f_qty else "-"))
            fp_item = QTableWidgetItem(f"{f_prc:.3f}" if f_prc else "-")
            fp_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.order_table.setItem(i, 8, fp_item)

            status = r.get('status', '')
            status_text_map = {
                'pending_submit': '待提交',
                'pending_fill': '待成交',
                'filled': '已成',
                'partially_filled': '部分成交',
                'timeout': '超时',
                'rejected': '失败',
            }
            status_colors = {
                '待提交': t['text_secondary'],
                '待成交': t['text_secondary'],
                '已成': t['green'],
                '部分成交': '#D97706',
                '超时': '#EA580C',
                '失败': t['red'],
                '未成': t['text_secondary'],
            }
            display_status = status_text_map.get(status, status)
            st_item = QTableWidgetItem(display_status)
            st_item.setForeground(
                QColor(status_colors.get(display_status, t['text'])))
            self.order_table.setItem(i, 9, st_item)

    def _refresh_all_analysis_tabs(self):
        """刷新 ETF 专属分析 Tab，并同步底部策略交易面板。"""
        self._refresh_statistics()
        self.strategy_trade_panel.refresh_all()

    # ==================================================================
    #  外部集成接口
    # ==================================================================

    def set_executor(self, executor: TradeExecutor):
        """供 MainWindow 注入真实交易执行器"""
        self.engine.set_executor(executor)
        self._refresh_status()

    def inject_broker(self, xt_trader=None, acc=None):
        """
        供 BrokerAccountWidget 连接成功后调用，注入券商对象

        用法（在 trading_app/main_window.py 中）:
            self.rotation_widget.inject_broker(self.broker_widget.xt_trader,
                                               self.broker_widget.acc)
        """
        executor = XtQuantExecutor()
        executor.set_broker_session_service(self.broker_session_service)
        self._sync_etf_strategy_profile()
        strategy_id, strategy_name, virtual_account_id = self._etf_strategy_identity()
        executor.set_strategy_context(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
        )
        if xt_trader is not None and acc is not None:
            executor.set_broker(xt_trader, acc)
        self.engine.set_executor(executor)
        self._refresh_status()

    def closeEvent(self, event):
        if self.startup_orchestrator is not None:
            try:
                self.startup_orchestrator.cancel()
            except Exception:
                pass
        super().closeEvent(event)

    def get_center_status_summary(self) -> dict:
        summary = self.engine.get_status_summary()
        return {
            "strategy_id": self._etf_strategy_identity()[0],
            "strategy_name": self._etf_strategy_identity()[1],
            "holding": str(summary.get("holding", "") or ""),
            "last_signal": str(summary.get("last_signal", "") or ""),
            "last_check": str(summary.get("last_check", "") or ""),
            "auto_enabled": bool(getattr(self.engine.config, "auto_enabled", False)),
            "auto_running": bool(self.engine._auto_timer.isActive()),
            "auto_status_text": self.lbl_auto_status.text(),
            "executor_connected": bool(summary.get("executor_connected", False)),
            "data_fresh": bool(summary.get("data_fresh", False)),
            "cooldown_remaining": int(summary.get("cooldown_remaining", 0) or 0),
        }

    def get_center_task_summaries(self) -> list[dict]:
        last_date = str(getattr(self.engine.state, "last_check_date", "") or "")
        last_time = str(getattr(self.engine.state, "last_check_time", "") or "")
        if last_date and last_time:
            last_run = f"{last_date} {last_time}"
        else:
            last_run = last_date or last_time
        return [
            {
                "task_key": "etf_rotation_auto_check",
                "task_type": "etf_rotation",
                "title": "ETF 自动轮动检查",
                "status": "enabled" if bool(self.engine.config.auto_enabled) else "disabled",
                "message": self.lbl_auto_status.text(),
                "last_run": last_run,
                "schedule_time": str(getattr(self.engine.config, "check_time", "") or ""),
                "next_mode": "signal_auto" if bool(getattr(self.engine.config, "auto_signal_enabled", True)) else "manual_scan",
            }
        ]

    def generate_live_signals(self, payload: dict | None = None):
        return self.engine.generate_live_signals(payload or {})

    def execute_live_signals(self, signals, *, execution_service=None, stock_name_map=None):
        return self.engine.execute_live_signals(
            list(signals or []),
            execution_service=execution_service,
            stock_name_map=stock_name_map or {},
        )

    def pause_center_automation(self) -> str:
        cfg = self.engine.config
        current_enabled = bool(getattr(cfg, "auto_enabled", False))
        # 幂等：首次暂停记录原始状态；重复调用不能覆盖原状态。
        if self._center_auto_pause_snapshot is None:
            self._center_auto_pause_snapshot = current_enabled
        if not current_enabled:
            return "ETF 自动调度已处于暂停状态"
        cfg.auto_enabled = False
        self.engine.update_config(cfg)
        self.engine.stop_auto()
        self._refresh_status()
        return "已暂停 ETF 自动调度"

    def resume_center_automation(self) -> str:
        cfg = self.engine.config
        # 优先恢复到暂停前的快照状态；若没有快照则维持当前配置。
        if self._center_auto_pause_snapshot is not None:
            target_enabled = bool(self._center_auto_pause_snapshot)
        else:
            target_enabled = bool(getattr(cfg, "auto_enabled", False))
        self._center_auto_pause_snapshot = None
        cfg.auto_enabled = target_enabled
        self.engine.update_config(cfg)
        if target_enabled:
            self.engine.start_auto()
        else:
            self.engine.stop_auto()
        self._refresh_status()
        return "已恢复 ETF 自动调度" if target_enabled else "ETF 自动调度维持停用"

    def run_end_of_day_tasks(self, snapshot_date: str) -> StrategyEndOfDayResult:
        strategy_id, strategy_name, _virtual_account_id = self._etf_strategy_identity()

        reconcile_detail = self._run_eod_reconcile()

        summary = self.engine.get_status_summary()
        account_view = self._get_etf_strategy_account_view(summary)
        self._persist_etf_strategy_daily_snapshot(snapshot_date, account_view, summary)
        holding = normalize_symbol_code(str(summary.get("holding", "") or ""))
        signal = str(summary.get("last_signal", "") or "")
        scores = dict(summary.get("last_scores", {}) or {})
        total_pnl = float(account_view.get("total_pnl", 0.0) or 0.0)
        cfg = self.engine.config
        notify_attempted = bool(getattr(cfg, "notify_daily_report", False))
        notify_message = "日报通知未启用"
        notify_success = False

        if notify_attempted:
            name_map = dict(getattr(self, "_ui_etf_name_map", {}) or getattr(self.engine, "_etf_name_map", {}) or {})
            notifier = RotationNotifier(name_map)
            notify_success, notify_message = notifier.send_daily_report(
                holding=holding or None,
                scores=scores,
                pnl_today=0.0,
                total_pnl=total_pnl,
                signal=signal,
            )

        if notify_attempted and notify_success:
            message = f"ETF 日报已发送，信号 {signal or '无'}，持仓 {holding or '空仓'}"
        elif notify_attempted:
            message = f"ETF 日报未发送（{notify_message}），信号 {signal or '无'}，持仓 {holding or '空仓'}"
        else:
            message = f"ETF 日报未启用，信号 {signal or '无'}，持仓 {holding or '空仓'}"

        return StrategyEndOfDayResult(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            success=True,
            message=message,
            details={
                "snapshot_date": snapshot_date,
                "holding": holding,
                "signal": signal,
                "scores_count": len(scores),
                "total_pnl": total_pnl,
                "notify_attempted": notify_attempted,
                "notify_success": notify_success,
                "notify_message": notify_message,
                "reconcile": reconcile_detail,
            },
        )

    def _run_eod_reconcile(self) -> str:
        """Execute EOD position reconciliation (cash is maintained by main ledger)."""
        try:
            result = self.engine.reconciler.reconcile_end_of_day(self.engine)
            try:
                self.engine.state_mgr.save()
            except Exception as sync_exc:
                logger.warning("ETF 日终对账后同步统一策略账本失败: %s", sync_exc)
            detail = str(result)
            if result.position_adjusted:
                logger.info("ETF 日终对账: %s", detail)
            else:
                logger.debug("ETF 日终对账: %s", detail)
            return detail
        except Exception as exc:
            logger.error("ETF 日终对账失败: %s", exc)
            return f"error: {exc}"

    def _persist_etf_strategy_daily_snapshot(
        self,
        snapshot_date: str,
        account_view: dict,
        summary: Optional[dict] = None,
    ) -> None:
        """ETF 日终快照：统一走 strategy_budget.finalize_day（主账本口径）。"""
        try:
            strategy_id, _strategy_name, _virtual_account_id = self._etf_strategy_identity()
            summary = summary or {}
            holding = normalize_symbol_code(str(summary.get("holding", "") or ""))
            spot_prices: dict[str, float] = {}
            if holding:
                current_price = float(summary.get("current_price", 0.0) or 0.0)
                if current_price <= 0:
                    current_price = float(summary.get("buy_price", 0.0) or 0.0)
                if current_price > 0:
                    spot_prices[holding] = current_price

            provider: dict[str, object] = {
                "remark": "ETF轮动日终对账后校正快照",
            }
            if spot_prices:
                provider["spot_prices"] = spot_prices

            self.strategy_budget.finalize_day(
                snapshot_date,
                providers={strategy_id: provider},
            )
        except Exception as exc:
            logger.warning("保存 ETF 轮动实盘日终快照失败: %s", exc)

    def refresh_end_of_day_ui(self) -> None:
        """Refresh end-of-day related UI on the main thread only."""
        self._refresh_status()
        self._refresh_schedule_status()
