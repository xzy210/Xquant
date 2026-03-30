"""AI 交易决策中心 — 独立窗口

将 AI 决策分析、交易下单、账户信息三大功能聚合在同一面板中，
使用户无需在多个窗口间切换即可完成「分析 → 决策 → 执行 → 追踪」的完整流程。
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from services.agent_context_service import (
        AgentContextService,
        AgentRuntimeContext,
        BrokerContext,
        SymbolContext,
        TASK_MODE_TRADE_DECISION,
    )
    from services.agent_prompt_builder import AgentPromptBuilder
    from services.agent_runtime import StockAgentRuntime
    from services.trade_decision_extractor import TradeDecisionExtractor
    from services.trade_decision_models import (
        DecisionOutcome,
        TRADE_ACTION_LABELS,
        TradeAction,
        TradeDecision,
    )
    from services.risk_guard_service import RiskGuardService
    from services.decision_tracker_service import DecisionTrackerService
    from common.broker_session_service import get_broker_session_service
except ImportError:
    from trading_app.services.agent_context_service import (
        AgentContextService,
        AgentRuntimeContext,
        BrokerContext,
        SymbolContext,
        TASK_MODE_TRADE_DECISION,
    )
    from trading_app.services.agent_prompt_builder import AgentPromptBuilder
    from trading_app.services.agent_runtime import StockAgentRuntime
    from trading_app.services.trade_decision_extractor import TradeDecisionExtractor
    from trading_app.services.trade_decision_models import (
        DecisionOutcome,
        TRADE_ACTION_LABELS,
        TradeAction,
        TradeDecision,
    )
    from trading_app.services.risk_guard_service import RiskGuardService
    from trading_app.services.decision_tracker_service import DecisionTrackerService
    from trading_app.common.broker_session_service import get_broker_session_service

logger = logging.getLogger(__name__)

DECISION_MODE_SINGLE = "single"
DECISION_MODE_POSITION_SCAN = "position_scan"
SCAN_SUBAGENT_CONCURRENCY = 3


# ---------------------------------------------------------------------------
#  Helper: reuse ChatThread from ai_agent_widget to avoid duplication
# ---------------------------------------------------------------------------
def _get_chat_thread_class():
    try:
        from widgets.ai_agent_widget import ChatThread
    except ImportError:
        from trading_app.widgets.ai_agent_widget import ChatThread
    return ChatThread


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
    ):
        super().__init__(parent)
        self._action_callback = None
        self._setup_ui()
        self.set_content(
            title,
            detail,
            status=status,
            action_label=action_label,
            action_callback=action_callback,
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
                padding: 8px 10px;
                border: 1px solid #333333;
                border-bottom: none;
                font-weight: bold;
                color: #f0f0f0;
            }
            """
        )
        layout.addWidget(self.header_btn)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setVisible(False)
        self.detail_label.setStyleSheet(
            """
            QLabel {
                color: #d0d0d0;
                padding: 10px 12px;
                border: 1px solid #333333;
                border-top: none;
                background-color: #171717;
            }
            """
        )
        layout.addWidget(self.detail_label)

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
        self.children_layout.setContentsMargins(18, 8, 0, 0)
        self.children_layout.setSpacing(6)
        self.children_host.setVisible(False)
        layout.addWidget(self.children_host)

    def _toggle_expanded(self):
        expanded = self.header_btn.isChecked()
        self.header_btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.detail_label.setVisible(expanded)
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
    ):
        self.title_text = title
        self.detail_text = detail or "无额外说明"
        self.status = status
        self._action_callback = action_callback
        dot, color, bg = self.STATUS_STYLES.get(status, self.STATUS_STYLES["pending"])
        self.header_btn.setText(f"{dot} {title}")
        self.header_btn.setStyleSheet(
            f"""
            QToolButton {{
                text-align: left;
                padding: 8px 10px;
                border: 1px solid #333333;
                border-bottom: none;
                font-weight: bold;
                color: {color};
                background-color: {bg};
            }}
            """
        )
        self.detail_label.setText(self.detail_text)
        self.action_btn.setText(action_label or "打开证据文件/图片")
        self.action_btn.setVisible(callable(action_callback))
        self.action_row.setVisible(self.header_btn.isChecked() and self.action_btn.isVisible())

    def expand(self):
        if not self.header_btn.isChecked():
            self.header_btn.click()

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


# ───────────────────────────────────────────────────────────────────────────
#  Left panel: Account & Position overview
# ───────────────────────────────────────────────────────────────────────────
class AccountPanel(QWidget):
    """Compact account + position summary panel."""

    order_requested = pyqtSignal(str, str, float)  # code, direction("buy"/"sell"), price

    def __init__(self, parent=None):
        super().__init__(parent)
        self.broker = get_broker_session_service()
        self._setup_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(30_000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # -- Connection status bar --
        conn_row = QHBoxLayout()
        self.status_icon = QLabel("🔴")
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("font-weight: bold;")
        conn_row.addWidget(self.status_icon)
        conn_row.addWidget(self.status_label)
        conn_row.addStretch()
        self.connect_btn = QPushButton("连接券商")
        self.connect_btn.setFixedWidth(90)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        conn_row.addWidget(self.connect_btn)
        layout.addLayout(conn_row)

        # -- Asset summary --
        asset_group = QGroupBox("账户概览")
        asset_form = QFormLayout(asset_group)
        asset_form.setSpacing(4)
        self.lbl_total_asset = QLabel("-")
        self.lbl_available = QLabel("-")
        self.lbl_market_value = QLabel("-")
        self.lbl_profit = QLabel("-")
        for label in (self.lbl_total_asset, self.lbl_available, self.lbl_market_value, self.lbl_profit):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet("font-weight: bold;")
        asset_form.addRow("总资产:", self.lbl_total_asset)
        asset_form.addRow("可用资金:", self.lbl_available)
        asset_form.addRow("持仓市值:", self.lbl_market_value)
        asset_form.addRow("总盈亏:", self.lbl_profit)
        layout.addWidget(asset_group)

        # -- Position table --
        pos_group = QGroupBox("当前持仓")
        pos_layout = QVBoxLayout(pos_group)
        pos_layout.setContentsMargins(4, 4, 4, 4)
        self.position_table = QTableWidget(0, 6)
        self.position_table.setHorizontalHeaderLabels(
            ["代码", "名称", "数量", "可用", "成本", "盈亏%"]
        )
        self.position_table.setStyleSheet(
            """
            QTableWidget {
                background-color: #1e1e1e;
                alternate-background-color: #2a2a2a;
                color: #e6e6e6;
                gridline-color: #444444;
                border: 1px solid #444444;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #333333;
                color: #f0f0f0;
                padding: 6px 4px;
                border: 1px solid #444444;
                font-weight: bold;
            }
            """
        )
        self.position_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.position_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.position_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.position_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.position_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.position_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.position_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.position_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.position_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.position_table.verticalHeader().setVisible(False)
        self.position_table.setAlternatingRowColors(True)
        self.position_table.setShowGrid(True)
        self.position_table.setWordWrap(False)
        self.position_table.doubleClicked.connect(self._on_position_double_clicked)
        pos_layout.addWidget(self.position_table)
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addStretch()
        btn_row.addWidget(refresh_btn)
        pos_layout.addLayout(btn_row)
        layout.addWidget(pos_group, stretch=1)

        self.broker.connection_changed.connect(self._on_connection_changed)
        if self.broker.is_connected:
            self._on_connection_changed(True, "已连接")

    def _on_connect_clicked(self):
        if self.broker.is_connected:
            self.broker.disconnect()
            return
        config = self.broker.get_config()
        qmt_path = config.get("qmt_path", "")
        account = config.get("account", "")
        if not qmt_path or not account:
            QMessageBox.warning(self, "提示", "请先在交易窗口中配置券商路径和账号")
            return
        self.broker.connect_async(qmt_path, account)
        self.connect_btn.setEnabled(False)
        self.status_label.setText("连接中...")

    def _on_connection_changed(self, connected: bool, message: str):
        self.connect_btn.setEnabled(True)
        if connected:
            self.status_icon.setText("🟢")
            self.status_label.setText("已连接")
            self.connect_btn.setText("断开")
            self.refresh()
        else:
            self.status_icon.setText("🔴")
            self.status_label.setText("未连接")
            self.connect_btn.setText("连接券商")
            self._clear_display()

    def refresh(self):
        if not self.broker.is_connected:
            return
        try:
            self._update_assets()
            self._update_positions()
        except Exception as exc:
            logger.warning("AccountPanel refresh failed: %s", exc)

    def _update_assets(self):
        try:
            asset = self.broker.query_stock_asset()
            if asset is None:
                return
            total = float(getattr(asset, "total_asset", 0) or 0)
            cash = float(getattr(asset, "cash", 0) or 0)
            market = float(getattr(asset, "market_value", 0) or 0)
            profit = float(getattr(asset, "total_profit", 0) or 0)
            self.lbl_total_asset.setText(f"¥{total:,.2f}")
            self.lbl_available.setText(f"¥{cash:,.2f}")
            self.lbl_market_value.setText(f"¥{market:,.2f}")
            color = "green" if profit >= 0 else "red"
            self.lbl_profit.setText(f"<span style='color:{color}'>¥{profit:,.2f}</span>")
        except Exception:
            pass

    def _update_positions(self):
        try:
            positions = self.broker.query_stock_positions()
            if positions is None:
                positions = []
            # Filter out zero-volume rows
            positions = [p for p in positions if int(getattr(p, "volume", 0) or 0) > 0]
            self.position_table.setRowCount(len(positions))
            for row, pos in enumerate(positions):
                code = getattr(pos, "stock_code", "") or ""
                name = self._resolve_symbol_name(code, getattr(pos, "stock_name", "") or "")
                volume = int(getattr(pos, "volume", 0) or 0)
                can_use = int(getattr(pos, "can_use_volume", 0) or 0)
                cost = float(getattr(pos, "open_price", 0) or 0)
                market_value = float(getattr(pos, "market_value", 0) or 0)
                position_cost = cost * volume
                profit = market_value - position_cost if volume > 0 else 0.0
                profit_rate = (profit / position_cost * 100) if position_cost > 0 else 0.0

                code_item = QTableWidgetItem(self._display_code(code))
                code_item.setData(Qt.ItemDataRole.UserRole, code)
                code_item.setToolTip(code)
                name_item = QTableWidgetItem(name)
                name_item.setToolTip(f"{name} ({code})")
                volume_item = QTableWidgetItem(f"{volume:,}")
                can_use_item = QTableWidgetItem(f"{can_use:,}")
                cost_item = QTableWidgetItem(f"{cost:.3f}")
                pnl_item = QTableWidgetItem(f"{profit_rate:+.2f}%")

                for numeric_item in (volume_item, can_use_item, cost_item, pnl_item):
                    numeric_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )

                pnl_color = QColor("#ec0000") if profit_rate >= 0 else QColor("#00da3c")
                pnl_item.setForeground(QBrush(pnl_color))
                self.position_table.setItem(row, 0, code_item)
                self.position_table.setItem(row, 1, name_item)
                self.position_table.setItem(row, 2, volume_item)
                self.position_table.setItem(row, 3, can_use_item)
                self.position_table.setItem(row, 4, cost_item)
                self.position_table.setItem(row, 5, pnl_item)
                self.position_table.setRowHeight(row, 30)
        except Exception:
            pass

    def _clear_display(self):
        self.lbl_total_asset.setText("-")
        self.lbl_available.setText("-")
        self.lbl_market_value.setText("-")
        self.lbl_profit.setText("-")
        self.position_table.setRowCount(0)

    def _on_position_double_clicked(self, index):
        row = index.row()
        code_item = self.position_table.item(row, 0)
        if code_item:
            full_code = str(code_item.data(Qt.ItemDataRole.UserRole) or code_item.text())
            self.order_requested.emit(full_code, "sell", 0.0)

    def get_broker_context(self) -> BrokerContext:
        if not self.broker.is_connected:
            return BrokerContext()
        try:
            asset = self.broker.query_stock_asset()
            positions = self.broker.query_stock_positions() or []
            positions = [p for p in positions if int(getattr(p, "volume", 0) or 0) > 0]
            top = []
            for p in positions[:10]:
                top.append({
                    "code": getattr(p, "stock_code", ""),
                    "volume": int(getattr(p, "volume", 0) or 0),
                    "cost_price": float(getattr(p, "open_price", 0) or 0),
                    "market_value": float(getattr(p, "market_value", 0) or 0),
                })
            return BrokerContext(
                connected=True,
                account_id=getattr(self.broker, "_last_config", {}).get("account", ""),
                total_asset=float(getattr(asset, "total_asset", 0) or 0),
                available_cash=float(getattr(asset, "cash", 0) or 0),
                position_count=len(positions),
                top_positions=top,
            )
        except Exception:
            return BrokerContext(connected=True)

    def get_live_positions(self) -> List[Dict[str, Any]]:
        if not self.broker.is_connected:
            return []
        try:
            positions = self.broker.query_stock_positions() or []
        except Exception:
            return []

        results: List[Dict[str, Any]] = []
        for pos in positions:
            volume = int(getattr(pos, "volume", 0) or 0)
            if volume <= 0:
                continue
            code = getattr(pos, "stock_code", "") or ""
            results.append({
                "code": code,
                "name": self._resolve_symbol_name(code, getattr(pos, "stock_name", "") or ""),
                "volume": volume,
                "can_use_volume": int(getattr(pos, "can_use_volume", 0) or 0),
                "cost_price": float(getattr(pos, "open_price", 0) or 0),
                "market_value": float(getattr(pos, "market_value", 0) or 0),
                "profit_rate": float(getattr(pos, "profit_rate", 0) or 0),
            })
        return results

    def _resolve_symbol_name(self, code: str, fallback_name: str = "") -> str:
        if fallback_name:
            return fallback_name
        trade_window = self._find_trade_window()
        if trade_window:
            looked_up = trade_window.lookup_symbol_name(code)
            if looked_up:
                return looked_up
        return code

    def _display_code(self, code: str) -> str:
        if "." in code:
            return code.split(".")[0]
        return code

    def _find_trade_window(self):
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, AITradeDecisionWindow):
                return parent
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None
        return None


# ───────────────────────────────────────────────────────────────────────────
#  Right panel: Quick Order execution
# ───────────────────────────────────────────────────────────────────────────
class QuickOrderPanel(QWidget):
    """Lightweight order panel for executing AI decisions or manual trades."""

    order_executed = pyqtSignal(bool, str)  # success, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.broker = get_broker_session_service()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        title = QLabel("快捷下单")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(6)

        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("股票代码，如 000001.SZ")
        form.addRow("代码:", self.code_input)

        self.name_label = QLabel("-")
        form.addRow("名称:", self.name_label)

        self.direction_combo = QComboBox()
        self.direction_combo.addItems(["买入", "卖出"])
        form.addRow("方向:", self.direction_combo)

        self.price_input = QLineEdit()
        self.price_input.setPlaceholderText("委托价格")
        form.addRow("价格:", self.price_input)

        self.volume_input = QLineEdit()
        self.volume_input.setPlaceholderText("委托数量(手,1手=100股)")
        form.addRow("数量(手):", self.volume_input)

        self.amount_label = QLabel("-")
        form.addRow("委托金额:", self.amount_label)

        layout.addLayout(form)

        # Quick volume buttons
        vol_row = QHBoxLayout()
        for label, ratio in [("1/4仓", 0.25), ("1/3仓", 0.33), ("半仓", 0.5), ("全仓", 1.0)]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _, r=ratio: self._set_volume_ratio(r))
            vol_row.addWidget(btn)
        layout.addLayout(vol_row)

        # Execute button
        self.exec_btn = QPushButton("确认下单")
        self.exec_btn.setFixedHeight(40)
        self.exec_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: white; font-size: 14px; "
            "font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #106ebe; }"
            "QPushButton:disabled { background-color: #999999; }"
        )
        self.exec_btn.clicked.connect(self._on_execute)
        layout.addWidget(self.exec_btn)

        layout.addStretch()

        # Update amount on input change
        self.price_input.textChanged.connect(self._update_amount)
        self.volume_input.textChanged.connect(self._update_amount)

    def fill_from_decision(self, decision: TradeDecision):
        self.code_input.setText(decision.symbol_code)
        self.name_label.setText(decision.symbol_name)
        if decision.action in (TradeAction.BUY.value, TradeAction.ADD.value):
            self.direction_combo.setCurrentIndex(0)
        else:
            self.direction_combo.setCurrentIndex(1)
        self.price_input.setText(f"{decision.current_price:.2f}" if decision.current_price > 0 else "")

        if self.broker.is_connected and decision.action in (TradeAction.BUY.value, TradeAction.ADD.value):
            try:
                asset = self.broker.query_stock_asset()
                cash = float(getattr(asset, "cash", 0) or 0)
                if decision.current_price > 0:
                    amount = cash * decision.position_pct
                    lots = int(math.floor(amount / (decision.current_price * 100)))
                    self.volume_input.setText(str(max(lots, 1)))
            except Exception:
                pass
        self._update_amount()

    def fill_order(self, code: str, direction: str, price: float):
        self.code_input.setText(code)
        self.direction_combo.setCurrentIndex(0 if direction == "buy" else 1)
        if price > 0:
            self.price_input.setText(f"{price:.2f}")

    def _set_volume_ratio(self, ratio: float):
        if not self.broker.is_connected:
            return
        try:
            price_text = self.price_input.text().strip()
            price = float(price_text) if price_text else 0
            if price <= 0:
                return
            direction = self.direction_combo.currentIndex()
            if direction == 0:  # buy
                asset = self.broker.query_stock_asset()
                cash = float(getattr(asset, "cash", 0) or 0)
                lots = int(math.floor(cash * ratio / (price * 100)))
            else:  # sell
                code = self.code_input.text().strip()
                positions = self.broker.query_stock_positions() or []
                can_use = 0
                for p in positions:
                    if code in (getattr(p, "stock_code", "") or ""):
                        can_use = int(getattr(p, "can_use_volume", 0) or 0)
                        break
                lots = int(math.floor(can_use * ratio / 100))
            self.volume_input.setText(str(max(lots, 0)))
            self._update_amount()
        except Exception:
            pass

    def _update_amount(self):
        try:
            price = float(self.price_input.text())
            lots = int(self.volume_input.text())
            amount = price * lots * 100
            self.amount_label.setText(f"¥{amount:,.2f}")
        except (ValueError, TypeError):
            self.amount_label.setText("-")

    def _on_execute(self):
        code = self.code_input.text().strip()
        if not code:
            QMessageBox.warning(self, "提示", "请输入股票代码")
            return
        if not self.broker.is_connected:
            QMessageBox.warning(self, "提示", "券商未连接")
            return
        try:
            price = float(self.price_input.text())
            lots = int(self.volume_input.text())
        except (ValueError, TypeError):
            QMessageBox.warning(self, "提示", "请输入有效的价格和数量")
            return
        volume = lots * 100
        if volume <= 0:
            QMessageBox.warning(self, "提示", "委托数量必须大于0")
            return
        direction_idx = self.direction_combo.currentIndex()
        order_type = 23 if direction_idx == 0 else 24  # STOCK_BUY / STOCK_SELL
        action_label = "买入" if direction_idx == 0 else "卖出"

        confirm = QMessageBox.question(
            self, "下单确认",
            f"确认{action_label} {code} {lots}手(={volume}股) @ ¥{price:.2f}？\n"
            f"委托金额: ¥{price * volume:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            order_id = self.broker.order_stock(
                stock_code=code,
                order_type=order_type,
                order_volume=volume,
                price_type=5,
                price=price,
                strategy_name="AI_TradeCenter",
                remark="AI交易决策中心下单",
            )
            msg = f"{action_label} {code} {volume}股 已委托 (单号: {order_id})"
            self.order_executed.emit(True, msg)
        except Exception as exc:
            msg = f"下单失败: {exc}"
            self.order_executed.emit(False, msg)


# ───────────────────────────────────────────────────────────────────────────
#  Center panel: AI Decision analysis
# ───────────────────────────────────────────────────────────────────────────
class DecisionPanel(QWidget):
    """AI trade decision analysis and display panel."""

    decision_ready = pyqtSignal(object)  # TradeDecision

    def __init__(self, context_provider=None, parent=None):
        super().__init__(parent)
        self.context_provider = context_provider
        self.agent_runtime = StockAgentRuntime()
        self.risk_guard = RiskGuardService()
        self.decision_tracker = DecisionTrackerService()
        self._current_decision: Optional[TradeDecision] = None
        self._current_risk_result = None
        self._chat_thread = None
        self._ai_config = self._load_ai_config()
        self._full_response = ""
        self._context_for_decision = None
        self._current_mode = DECISION_MODE_SINGLE
        self._scan_queue: List[Dict[str, Any]] = []
        self._scan_results: List[Dict[str, Any]] = []
        self._current_scan_item: Optional[Dict[str, Any]] = None
        self._current_scan_index = -1
        self._scan_in_progress = False
        self._scan_total_count = 0
        self._scan_completed_count = 0
        self._scan_active_workers: Dict[str, Any] = {}
        self._scan_worker_states: Dict[str, Dict[str, Any]] = {}
        self._stream_started = False
        self._progress_cards: List[CollapsibleStepCard] = []
        self._setup_ui()

    def _load_ai_config(self) -> dict:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "ai_config.json",
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # -- Top: mode / symbol / model selector --
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("个股决策", DECISION_MODE_SINGLE)
        self.mode_combo.addItem("持仓巡检", DECISION_MODE_POSITION_SCAN)
        self.mode_combo.setFixedWidth(120)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_row.addWidget(self.mode_combo)

        top_row.addWidget(QLabel("标的:"))
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("输入代码，如 000001.SZ（留空则用主窗口当前标的）")
        self.symbol_input.setFixedWidth(240)
        top_row.addWidget(self.symbol_input)

        self.mode_hint_label = QLabel("个股模式: 可手动输入代码，或直接使用主窗口当前选中标的")
        self.mode_hint_label.setStyleSheet("color: #666;")
        top_row.addWidget(self.mode_hint_label)

        top_row.addWidget(QLabel("模型:"))
        self.model_combo = QComboBox()
        model_configs = self._ai_config.get("model_configs", {})
        if model_configs:
            self.model_combo.addItems(list(model_configs.keys()))
        else:
            self.model_combo.addItems(["deepseek-chat", "gpt-4o", "gemini-3-pro-preview"])
        selected = self._ai_config.get("selected_model", "")
        if selected and self.model_combo.findText(selected) >= 0:
            self.model_combo.setCurrentText(selected)
        self.model_combo.setFixedWidth(180)
        top_row.addWidget(self.model_combo)

        top_row.addStretch()
        self.analyze_btn = QPushButton("🔍 生成交易决策")
        self.analyze_btn.setFixedHeight(36)
        self.analyze_btn.setStyleSheet(
            "QPushButton { background-color: #107c10; color: white; font-size: 13px; "
            "font-weight: bold; border-radius: 4px; padding: 0 16px; }"
            "QPushButton:hover { background-color: #0e6b0e; }"
            "QPushButton:disabled { background-color: #888888; }"
        )
        self.analyze_btn.clicked.connect(self._on_analyze_clicked)
        top_row.addWidget(self.analyze_btn)
        layout.addLayout(top_row)

        # -- Stacked: placeholder vs result --
        self.stack = QStackedWidget()

        # Page 0: placeholder
        placeholder = QLabel(
            "点击「生成交易决策」开始分析当前标的\n\n"
            "切换到“持仓巡检”后，可自动遍历当前持仓，逐只生成结构化决策并汇总。"
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #888; font-size: 14px;")
        self.stack.addWidget(placeholder)

        # Page 1: progress
        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)
        progress_layout.setContentsMargins(16, 16, 16, 16)
        progress_layout.setSpacing(10)
        self.progress_label = QLabel("正在分析...")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_label.setStyleSheet("color: #0078d4; font-size: 14px; font-weight: bold;")
        progress_layout.addWidget(self.progress_label)

        self.progress_hint_label = QLabel("以下为本轮交易决策生成的中间步骤概要")
        self.progress_hint_label.setStyleSheet("color: #888;")
        self.progress_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_layout.addWidget(self.progress_hint_label)

        self.progress_scroll = QScrollArea()
        self.progress_scroll.setWidgetResizable(True)
        self.progress_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.progress_scroll.setStyleSheet("background-color: transparent; border: none;")
        self.progress_cards_host = QWidget()
        self.progress_cards_layout = QVBoxLayout(self.progress_cards_host)
        self.progress_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_cards_layout.setSpacing(8)
        self.progress_cards_layout.addStretch()
        self.progress_scroll.setWidget(self.progress_cards_host)
        progress_layout.addWidget(self.progress_scroll, stretch=1)
        self.stack.addWidget(progress_widget)

        # Page 2: result area
        result_widget = QWidget()
        result_layout = QVBoxLayout(result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)

        # Tab: analysis text + decision card + history
        self.result_tabs = QTabWidget()

        # Tab 1: AI analysis text
        self.analysis_display = QTextEdit()
        self.analysis_display.setReadOnly(True)
        self.result_tabs.addTab(self.analysis_display, "AI 分析报告")

        # Tab 2: Batch summary
        self.scan_table = QTableWidget(0, 9)
        self.scan_table.setHorizontalHeaderLabels(
            ["序号", "代码", "名称", "操作", "置信度", "现价", "成本", "风控", "状态"]
        )
        self.scan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.scan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.scan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.scan_table.verticalHeader().setVisible(False)
        self.scan_table.setAlternatingRowColors(True)
        self.scan_table.itemSelectionChanged.connect(self._on_scan_selection_changed)
        self.result_tabs.addTab(self.scan_table, "巡检汇总")

        # Tab 3: Decision card
        self.decision_card_widget = QWidget()
        self.decision_card_layout = QVBoxLayout(self.decision_card_widget)
        self.decision_card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.result_tabs.addTab(self.decision_card_widget, "决策详情")

        # Tab 4: Decision history
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "标的", "操作", "置信度", "目标价", "风控", "结果"]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        self.result_tabs.addTab(self.history_table, "决策记录")

        result_layout.addWidget(self.result_tabs)

        # Action bar under results
        action_row = QHBoxLayout()
        self.approve_btn = QPushButton("✅ 确认执行")
        self.approve_btn.setEnabled(False)
        self.approve_btn.setFixedHeight(34)
        self.approve_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 0 16px; }"
            "QPushButton:hover { background-color: #106ebe; }"
            "QPushButton:disabled { background-color: #aaaaaa; }"
        )
        self.approve_btn.clicked.connect(self._on_approve)
        action_row.addWidget(self.approve_btn)

        self.reject_btn = QPushButton("❌ 驳回")
        self.reject_btn.setEnabled(False)
        self.reject_btn.setFixedHeight(34)
        self.reject_btn.clicked.connect(self._on_reject)
        action_row.addWidget(self.reject_btn)

        self.regenerate_btn = QPushButton("🔄 重新生成")
        self.regenerate_btn.setFixedHeight(34)
        self.regenerate_btn.clicked.connect(self._on_analyze_clicked)
        action_row.addWidget(self.regenerate_btn)

        action_row.addStretch()
        self.decision_status_label = QLabel("")
        action_row.addWidget(self.decision_status_label)
        result_layout.addLayout(action_row)

        self.stack.addWidget(result_widget)
        layout.addWidget(self.stack, stretch=1)
        self._on_mode_changed()

    def set_symbol(self, code: str, name: str = ""):
        self.symbol_input.setText(code)

    def _on_mode_changed(self, _index=None):
        self._current_mode = self.mode_combo.currentData() or DECISION_MODE_SINGLE
        is_single = self._current_mode == DECISION_MODE_SINGLE
        self.symbol_input.setEnabled(is_single)
        self.symbol_input.setPlaceholderText(
            "输入代码，如 000001.SZ（留空则用主窗口当前标的）"
            if is_single
            else "持仓巡检模式下由系统自动读取当前持仓"
        )
        self.mode_hint_label.setText(
            "个股模式: 可手动输入代码，或直接使用主窗口当前选中标的"
            if is_single
            else "持仓巡检: 自动读取当前券商持仓，逐只生成持有/加仓/减仓/卖出决策"
        )
        self.analyze_btn.setText("🔍 生成交易决策" if is_single else "🔎 开始持仓巡检")

    def _build_runtime_context(self) -> AgentRuntimeContext:
        raw_context: Dict[str, Any] = {}
        if self.context_provider:
            try:
                raw_context = self.context_provider()
            except Exception:
                pass

        override_code = self.symbol_input.text().strip()
        if override_code:
            raw_context.setdefault("symbol", {})
            raw_context["symbol"]["code"] = override_code
            raw_context["symbol"]["name"] = raw_context["symbol"].get("name", "")

        context = AgentContextService.from_raw(raw_context)
        return context

    def _build_runtime_context_for_symbol(self, code: str, name: str = "") -> AgentRuntimeContext:
        raw_context: Dict[str, Any] = {}
        if self.context_provider:
            try:
                raw_context = self.context_provider()
            except Exception:
                pass

        raw_context.setdefault("symbol", {})
        raw_context["symbol"]["code"] = code
        raw_context["symbol"]["name"] = name or ""
        context = AgentContextService.from_raw(raw_context)
        return context

    def _resolve_model_config(self) -> Optional[Dict[str, str]]:
        model = self.model_combo.currentText()
        model_configs = self._ai_config.get("model_configs", {})
        config = model_configs.get(model, {})
        api_key = config.get("api_key", "")
        base_url = config.get("base_url", "")
        if not api_key:
            QMessageBox.warning(self, "提示", f"请先在智能体设置中配置模型 {model} 的 API Key")
            return None
        return {"model": model, "api_key": api_key, "base_url": base_url}

    def _clear_progress_cards(self):
        self._progress_cards = []
        while self.progress_cards_layout.count() > 1:
            item = self.progress_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _last_progress_card(self) -> Optional[CollapsibleStepCard]:
        return self._progress_cards[-1] if self._progress_cards else None

    def _find_progress_card(self, contains_text: str) -> Optional[CollapsibleStepCard]:
        for card in self._progress_cards:
            if contains_text in getattr(card, "title_text", ""):
                return card
        return None

    def _finish_last_progress_card(self):
        last = self._last_progress_card()
        if last is not None and getattr(last, "status", "") == "running":
            last.set_content(
                last.title_text,
                last.detail_text,
                status="done",
                action_label=last.action_btn.text() if hasattr(last, "action_btn") else "",
                action_callback=getattr(last, "_action_callback", None),
            )

    def _parse_step_text(self, step: str) -> tuple[str, str]:
        clean = (step or "").strip()
        if "：" in clean:
            title, detail = clean.split("：", 1)
            return title.strip(), detail.strip()
        if ":" in clean:
            title, detail = clean.split(":", 1)
            return title.strip(), detail.strip()
        return clean, ""

    def _tool_display_name(self, tool_name: str) -> str:
        mapping = {
            "context_snapshot": "上下文快照",
            "symbol_technical_snapshot": "技术面摘要",
            "symbol_news_snapshot": "消息面摘要",
            "symbol_fundamental_snapshot": "基本面摘要",
            "symbol_analysis_packet": "深度分析资料",
            "current_kline_image": "K线截图",
            "position_snapshot": "持仓快照",
            "watchlist_snapshot": "自选快照",
            "compare_symbols": "标的对比",
        }
        return mapping.get(tool_name, tool_name)

    def _truncate_text(self, text: str, limit: int = 220) -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + " ..."

    def _open_local_evidence_path(self, path: str):
        if not path:
            return
        normalized = os.path.abspath(path)
        if not os.path.exists(normalized):
            QMessageBox.warning(self, "提示", f"证据文件不存在：\n{normalized}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(normalized))
        if not opened:
            QMessageBox.warning(self, "提示", f"无法打开证据文件：\n{normalized}")

    def _attach_tool_subcards(self, prepared, parent_card: Optional[CollapsibleStepCard] = None):
        if parent_card is None:
            parent_card = self._find_progress_card("执行领域工具链")
        if parent_card is None:
            return
        parent_card.clear_children()
        for idx, item in enumerate(prepared.evidence_items, start=1):
            tool_label = self._tool_display_name(item.tool_name)
            detail_lines = [
                f"工具标识: {item.tool_name}",
                f"证据标题: {item.title}",
                f"摘要: {item.summary}",
            ]
            preview = self._truncate_text(item.content, 260)
            if preview:
                detail_lines.extend(["", "关键内容预览:", preview])
            metadata = item.metadata or {}
            file_path = str(metadata.get("image_path") or metadata.get("file_path") or "").strip()
            action_callback = None
            action_label = ""
            if file_path:
                detail_lines.extend(["", f"原始证据路径: {file_path}"])
                action_label = "打开证据文件/图片"
                action_callback = lambda p=file_path: self._open_local_evidence_path(p)
            child_card = CollapsibleStepCard(
                title=f"子步骤 {idx}: {tool_label}",
                detail="\n".join(detail_lines),
                status="done",
                action_label=action_label,
                action_callback=action_callback,
            )
            parent_card.add_child_card(child_card)
        if prepared.evidence_items:
            parent_card.expand()

    def _set_progress_steps(self, title: str, steps: List[str]):
        self.progress_hint_label.setText(title)
        self._clear_progress_cards()
        for idx, step in enumerate(steps, start=1):
            step_title, detail = self._parse_step_text(step)
            card = CollapsibleStepCard(
                title=f"步骤 {idx}: {step_title}",
                detail=detail or step,
                status="running" if idx == len(steps) else "done",
            )
            if idx == len(steps):
                card.expand()
            self._progress_cards.append(card)
            self.progress_cards_layout.insertWidget(self.progress_cards_layout.count() - 1, card)
        self.progress_scroll.verticalScrollBar().setValue(0)

    def _append_progress_step(self, step: str):
        previous = self._last_progress_card()
        if previous is not None and getattr(previous, "status", "") == "running":
            previous.set_content(
                previous.title_text,
                previous.detail_text,
                status="done",
                action_label=previous.action_btn.text() if hasattr(previous, "action_btn") else "",
                action_callback=getattr(previous, "_action_callback", None),
            )
        existing_count = max(0, self.progress_cards_layout.count() - 1)
        step_title, detail = self._parse_step_text(step)
        card = CollapsibleStepCard(
            title=f"步骤 {existing_count + 1}: {step_title}",
            detail=detail or step,
            status="running",
        )
        card.expand()
        self._progress_cards.append(card)
        self.progress_cards_layout.insertWidget(self.progress_cards_layout.count() - 1, card)
        QTimer.singleShot(
            0,
            lambda: self.progress_scroll.verticalScrollBar().setValue(
                self.progress_scroll.verticalScrollBar().maximum()
            ),
        )

    def _build_prepared_steps_summary(
        self,
        context: AgentRuntimeContext,
        prepared,
        *,
        model_name: str,
        scenario_label: str,
    ) -> List[str]:
        symbol_name = context.symbol.name or "-"
        symbol_code = context.symbol.code or "-"
        summary_lines = [
            f"识别任务场景：{scenario_label}，目标标的为 {symbol_name}({symbol_code})。",
            f"读取运行上下文：账户{'已连接' if context.broker.connected else '未连接'}，当前任务模式为交易决策。",
        ]
        if prepared.executed_tools:
            summary_lines.append(
                "执行领域工具链：" + " -> ".join(prepared.executed_tools)
            )
        if prepared.evidence_items:
            evidence_bits = []
            for item in prepared.evidence_items[:6]:
                evidence_bits.append(f"{item.title}（{item.summary}）")
            summary_lines.append("提取关键证据摘要：" + "；".join(evidence_bits))
        if prepared.evidence_report_path:
            summary_lines.append(f"生成证据存档：{prepared.evidence_report_path}")
        summary_lines.extend([
            "将结构化证据、输出协议和风控要求一起注入最终提示词。",
            f"调用模型 `{model_name}` 进入推理阶段，等待生成多空分析和结构化交易决策。",
        ])
        return summary_lines

    def _reset_current_result(self):
        self._current_decision = None
        self._current_risk_result = None
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)

    def _on_analyze_clicked(self):
        model_cfg = self._resolve_model_config()
        if not model_cfg:
            return

        if self._current_mode == DECISION_MODE_POSITION_SCAN:
            self._start_position_scan(model_cfg)
            return
        context = self._build_runtime_context()
        if not context.symbol.is_available:
            QMessageBox.warning(self, "提示", "请输入标的代码或在主窗口中选择一只股票")
            return
        self._start_single_decision(context, model_cfg)

    def _start_single_decision(
        self,
        context: AgentRuntimeContext,
        model_cfg: Dict[str, str],
        *,
        user_prompt: str | None = None,
        scan_item: Optional[Dict[str, Any]] = None,
    ):
        self.analyze_btn.setEnabled(False)
        self._reset_current_result()
        self.stack.setCurrentIndex(1)
        self._full_response = ""
        self._context_for_decision = context
        self._current_scan_item = scan_item
        self._stream_started = False
        scenario_label = "持仓巡检单票分析" if scan_item else "单股交易决策"
        self.progress_label.setText(
            f"正在收集 {context.symbol.name}({context.symbol.code}) 的多维度数据..."
        )
        self._set_progress_steps(
            "执行步骤概要",
            [
                f"接收请求并识别场景：{scenario_label}。",
                f"解析目标标的：{context.symbol.name or '-'}({context.symbol.code or '-'})。",
                "准备运行上下文，包括账户、图表、行情和可用持仓信息。",
                "开始执行领域工具，采集技术面、消息面、基本面和图表证据。",
            ],
        )

        system_prompt = AgentPromptBuilder.build_system_prompt(
            "你是一个专业的股票交易决策分析师。",
            context,
            task_mode=TASK_MODE_TRADE_DECISION,
        )
        latest_user_content = user_prompt or AgentPromptBuilder.build_quick_task_prompt(
            TASK_MODE_TRADE_DECISION,
            context,
        )
        prepared = self.agent_runtime.prepare_request(
            base_system_prompt=system_prompt,
            context=context,
            task_mode=TASK_MODE_TRADE_DECISION,
            chat_history=[],
            latest_user_content=latest_user_content,
        )
        self._set_progress_steps(
            "执行步骤概要",
            self._build_prepared_steps_summary(
                context,
                prepared,
                model_name=model_cfg["model"],
                scenario_label=scenario_label,
            ),
        )
        self._attach_tool_subcards(prepared)
        self.progress_label.setText("数据收集完成，AI 正在分析决策...")
        self._append_progress_step("模型已开始流式生成分析结果，正在持续接收输出片段。")

        ChatThread = _get_chat_thread_class()
        self._chat_thread = ChatThread(
            model_cfg["api_key"],
            model_cfg["base_url"],
            model_cfg["model"],
            prepared.system_prompt,
            prepared.messages,
        )
        self._chat_thread.message_received.connect(self._on_stream_message)
        self._chat_thread.finished_signal.connect(self._on_analysis_finished)
        self._chat_thread.start()

    def _start_position_scan(self, model_cfg: Dict[str, str]):
        account_panel = self._find_account_panel()
        if account_panel is None:
            QMessageBox.warning(self, "提示", "未找到账户面板")
            return
        positions = account_panel.get_live_positions()
        if not positions:
            QMessageBox.warning(self, "提示", "当前无可巡检持仓，请先连接券商并确认持仓数据")
            return

        self._scan_queue = positions
        self._scan_results = []
        self._current_scan_index = 0
        self._scan_in_progress = True
        self._scan_total_count = len(positions)
        self._scan_completed_count = 0
        self._scan_active_workers = {}
        self._scan_worker_states = {}
        self._active_model_cfg = model_cfg
        self.scan_table.setRowCount(0)
        self.analysis_display.clear()
        self._populate_decision_card(None, None)
        self.stack.setCurrentIndex(1)
        self.progress_label.setText(f"准备开始持仓巡检，共 {len(positions)} 只持仓...")
        self._set_progress_steps(
            "执行步骤概要",
            [
                f"接收持仓巡检请求，本轮共识别到 {len(positions)} 只有效持仓。",
                f"选择并行子代理模式处理，最大并发数设为 {SCAN_SUBAGENT_CONCURRENCY}。",
                "每只持仓都会单独完成：上下文构建 -> 证据采集 -> 模型推理 -> 决策提取 -> 风控评估。",
                "巡检汇总表会在每只股票完成后实时追加结果。",
            ],
        )
        self.result_tabs.setCurrentIndex(1)
        self._launch_scan_subagents()

    def _launch_scan_subagents(self):
        while self._scan_queue and len(self._scan_active_workers) < SCAN_SUBAGENT_CONCURRENCY:
            position = self._scan_queue.pop(0)
            context = self._build_runtime_context_for_symbol(position["code"], position["name"])
            prompt = self._build_position_scan_prompt(context, position)
            worker_id = f"{position['code']}::{self._current_scan_index}"
            self._current_scan_index += 1

            system_prompt = AgentPromptBuilder.build_system_prompt(
                "你是一个专业的股票交易决策分析师。",
                context,
                task_mode=TASK_MODE_TRADE_DECISION,
            )
            prepared = self.agent_runtime.prepare_request(
                base_system_prompt=system_prompt,
                context=context,
                task_mode=TASK_MODE_TRADE_DECISION,
                chat_history=[],
                latest_user_content=prompt,
            )

            ChatThread = _get_chat_thread_class()
            worker = ChatThread(
                self._active_model_cfg["api_key"],
                self._active_model_cfg["base_url"],
                self._active_model_cfg["model"],
                prepared.system_prompt,
                prepared.messages,
            )
            self._scan_active_workers[worker_id] = worker
            self._scan_worker_states[worker_id] = {
                "response": "",
                "context": context,
                "scan_item": position,
                "prepared": prepared,
            }
            self._append_progress_step(
                f"启动子代理：{position['name']}({position['code']})，准备独立生成持仓决策。"
            )
            self._attach_tool_subcards(prepared, parent_card=self._last_progress_card())
            worker.message_received.connect(
                lambda content, is_error, wid=worker_id: self._on_scan_worker_message(wid, content, is_error)
            )
            worker.finished_signal.connect(
                lambda wid=worker_id: self._on_scan_worker_finished(wid)
            )
            worker.start()

        self._update_scan_progress_label()

    def _update_scan_progress_label(self):
        running_names = [
            state["scan_item"].get("name") or state["scan_item"].get("code")
            for state in self._scan_worker_states.values()
        ]
        running_text = "、".join(running_names[:3]) if running_names else "无"
        self.progress_label.setText(
            f"持仓巡检中: 已完成 {self._scan_completed_count}/{self._scan_total_count} | "
            f"运行中 {len(self._scan_active_workers)} 个子代理 | 当前: {running_text}"
        )

    def _build_position_scan_prompt(self, context: AgentRuntimeContext, position: Dict[str, Any]) -> str:
        cost = float(position.get("cost_price", 0) or 0)
        profit_rate = float(position.get("profit_rate", 0) or 0) * 100
        can_use = int(position.get("can_use_volume", 0) or 0)
        volume = int(position.get("volume", 0) or 0)
        market_value = float(position.get("market_value", 0) or 0)
        base_prompt = AgentPromptBuilder.build_quick_task_prompt(TASK_MODE_TRADE_DECISION, context)
        extra_lines = [
            "",
            "这是持仓巡检场景，请结合当前已经持有该股票的事实做判断。",
            f"- 当前持仓数量: {volume} 股，可卖数量: {can_use} 股",
            f"- 持仓成本价: {cost:.3f}" if cost > 0 else "- 持仓成本价: 未知",
            f"- 当前持仓盈亏: {profit_rate:+.2f}%",
            f"- 当前持仓市值: ¥{market_value:,.2f}",
            "",
            "请重点判断：继续持有、加仓、减仓、卖出、还是继续观察。",
            "如果建议卖出或减仓，请明确给出触发依据；如果建议继续持有，也要说明需要继续跟踪的风险信号。",
        ]
        return "\n".join([base_prompt, *extra_lines]).strip()

    def _on_scan_worker_message(self, worker_id: str, content: str, is_error: bool):
        state = self._scan_worker_states.get(worker_id)
        if not state:
            return
        if is_error:
            state["response"] += f"\n\n[错误] {content}"
        else:
            state["response"] += content
        self._update_scan_progress_label()

    def _on_stream_message(self, content: str, is_error: bool):
        if not self._stream_started:
            self._stream_started = True
            self._append_progress_step("模型已返回首段内容，进入结果生成与结构化提取阶段。")
        if is_error:
            self._full_response += f"\n\n[错误] {content}"
        else:
            self._full_response += content
        if self._current_mode == DECISION_MODE_POSITION_SCAN and self._current_scan_item:
            self.progress_label.setText(
                f"巡检 {self._current_scan_item.get('name', '')} 中... ({len(self._full_response)} 字)"
            )
        else:
            self.progress_label.setText(f"AI 分析中... ({len(self._full_response)} 字)")

    def _on_scan_worker_finished(self, worker_id: str):
        worker = self._scan_active_workers.pop(worker_id, None)
        state = self._scan_worker_states.pop(worker_id, None)
        if worker is not None:
            worker.deleteLater()
        if not state:
            self._update_scan_progress_label()
            return

        result = self._build_analysis_result(
            state.get("response", ""),
            state["context"],
            state["scan_item"],
        )
        self._append_scan_result(result)
        if self.scan_table.currentRow() < 0:
            self._display_result(result, switch_to_details=False, emit_decision=False)

        self.decision_tracker.save_decision(
            result["decision"] or TradeDecision(
                action=TradeAction.HOLD.value,
                symbol_code=result["symbol_code"],
                symbol_name=result["symbol_name"],
            ),
            result["risk_result"] or self.risk_guard.evaluate(
                TradeDecision(
                    action=TradeAction.HOLD.value,
                    symbol_code=result["symbol_code"],
                    symbol_name=result["symbol_name"],
                ),
                BrokerContext(),
            ),
            DecisionOutcome.INSPECTED.value,
        )
        if result["decision"] is not None:
            action_label = TRADE_ACTION_LABELS.get(result["decision"].action, result["decision"].action)
            risk_level = (
                result["risk_result"].overall_risk_level.upper()
                if result["risk_result"] is not None else "-"
            )
            self._append_progress_step(
                f"子代理完成：{result['symbol_name']}({result['symbol_code']}) -> {action_label}，"
                f"置信度 {result['decision'].confidence:.0%}，风险 {risk_level}。"
            )
        else:
            self._append_progress_step(
                f"子代理完成：{result['symbol_name']}({result['symbol_code']})，但未能提取有效结构化决策。"
            )

        self._scan_completed_count += 1
        if not self._scan_queue and not self._scan_active_workers:
            self._scan_in_progress = False
            self.analyze_btn.setEnabled(True)
            self.stack.setCurrentIndex(2)
            self.decision_status_label.setText(f"✅ 持仓巡检完成，共 {len(self._scan_results)} 只")
            self.decision_status_label.setStyleSheet("color: green; font-weight: bold;")
            self._append_progress_step("全部子代理已完成，本轮持仓巡检结束，结果已写入巡检汇总和决策记录。")
            self._finish_last_progress_card()
            self._refresh_history()
            self.result_tabs.setCurrentIndex(1)
            if self._scan_results:
                self.scan_table.selectRow(0)
            return

        self._launch_scan_subagents()

    def _on_analysis_finished(self):
        self.stack.setCurrentIndex(2)
        result = self._build_analysis_result(self._full_response, self._context_for_decision, self._current_scan_item)
        self.analyze_btn.setEnabled(True)
        if result["decision"] is not None:
            action_label = TRADE_ACTION_LABELS.get(result["decision"].action, result["decision"].action)
            risk_level = (
                result["risk_result"].overall_risk_level.upper()
                if result["risk_result"] is not None else "-"
            )
            self._append_progress_step(
                f"模型输出已解析完成：建议 {action_label}，置信度 {result['decision'].confidence:.0%}，风险 {risk_level}。"
            )
        else:
            self._append_progress_step("模型输出已返回，但未能解析出有效的结构化交易决策。")
        self._finish_last_progress_card()
        self._display_result(result, switch_to_details=True, emit_decision=True)

    def _build_analysis_result(
        self,
        response_text: str,
        context: AgentRuntimeContext,
        scan_item: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        decision = TradeDecisionExtractor.extract(response_text)
        broker_ctx = BrokerContext()
        account_panel = self._find_account_panel()
        if account_panel:
            broker_ctx = account_panel.get_broker_context()

        if decision is not None:
            if not decision.symbol_code and context.symbol.is_available:
                decision.symbol_code = context.symbol.code
            if not decision.symbol_name and context.symbol.name:
                decision.symbol_name = context.symbol.name
            if decision.current_price <= 0 and context.symbol.latest_close > 0:
                decision.current_price = context.symbol.latest_close
            risk_result = self.risk_guard.evaluate(decision, broker_ctx)
        else:
            risk_result = None

        symbol_code = (
            decision.symbol_code if decision else context.symbol.code or (scan_item or {}).get("code", "")
        )
        symbol_name = (
            decision.symbol_name if decision else context.symbol.name or (scan_item or {}).get("name", "")
        )
        return {
            "response_text": response_text,
            "context": context,
            "decision": decision,
            "risk_result": risk_result,
            "scan_item": scan_item,
            "symbol_code": symbol_code,
            "symbol_name": symbol_name,
        }

    def _display_result(self, result: Dict[str, Any], *, switch_to_details: bool, emit_decision: bool):
        self._render_response_text(result["response_text"])
        decision = result["decision"]
        risk_result = result["risk_result"]
        self._current_decision = decision
        self._current_risk_result = risk_result
        self._populate_decision_card(decision, risk_result)
        self._apply_action_state(decision, risk_result)
        if emit_decision and decision is not None:
            self.decision_ready.emit(decision)
        if switch_to_details:
            self.result_tabs.setCurrentIndex(2)

    def _render_response_text(self, response_text: str):
        try:
            import markdown as md_lib
            html = md_lib.markdown(response_text, extensions=["fenced_code", "tables"])
            self.analysis_display.setHtml(html)
        except Exception:
            self.analysis_display.setPlainText(response_text)

    def _apply_action_state(self, decision: Optional[TradeDecision], risk_result):
        if decision is None:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.decision_status_label.setText("⚠ 未能提取有效决策")
            self.decision_status_label.setStyleSheet("color: orange; font-weight: bold;")
            return
        if self._scan_in_progress:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.decision_status_label.setText(
                f"🔄 巡检进行中: {TRADE_ACTION_LABELS.get(decision.action, decision.action)}"
            )
            self.decision_status_label.setStyleSheet("color: #0078d4; font-weight: bold;")
            return
        if decision.is_actionable and risk_result and risk_result.passed:
            self.approve_btn.setEnabled(True)
            self.reject_btn.setEnabled(True)
            self.decision_status_label.setText("✅ 风控通过，可执行")
            self.decision_status_label.setStyleSheet("color: green; font-weight: bold;")
        elif decision.is_actionable:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(True)
            self.decision_status_label.setText("⛔ 风控未通过")
            self.decision_status_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.approve_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.decision_status_label.setText(f"ℹ 建议: {TRADE_ACTION_LABELS.get(decision.action, decision.action)}")
            self.decision_status_label.setStyleSheet("color: #666; font-weight: bold;")

    def _populate_decision_card(self, decision: Optional[TradeDecision], risk_result):
        while self.decision_card_layout.count():
            child = self.decision_card_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if decision is None:
            lbl = QLabel("未提取到结构化决策。请查看 AI 分析报告内容。")
            lbl.setStyleSheet("color: #888; padding: 20px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.decision_card_layout.addWidget(lbl)
            return

        action_label = TRADE_ACTION_LABELS.get(decision.action, decision.action)

        # Title
        title = QLabel(f"📊 {action_label}  {decision.symbol_name}({decision.symbol_code})")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px 0;")
        self.decision_card_layout.addWidget(title)

        # Details form
        details = QGroupBox("决策参数")
        form = QFormLayout(details)
        form.setSpacing(4)
        form.addRow("操作:", QLabel(action_label))
        form.addRow("当前价:", QLabel(f"{decision.current_price:.2f}" if decision.current_price > 0 else "-"))
        form.addRow("目标价:", QLabel(f"{decision.target_price:.2f}" if decision.target_price > 0 else "-"))
        form.addRow("止损价:", QLabel(f"{decision.stop_loss_price:.2f}" if decision.stop_loss_price > 0 else "-"))

        ret = decision.expected_return_pct
        if ret is not None:
            color = "green" if ret > 0 else "red"
            form.addRow("预期收益:", QLabel(f"<span style='color:{color}'>{ret:+.2f}%</span>"))
        loss = decision.max_loss_pct
        if loss is not None:
            form.addRow("最大亏损:", QLabel(f"<span style='color:red'>{loss:.2f}%</span>"))

        form.addRow("置信度:", QLabel(f"{decision.confidence:.0%}"))
        form.addRow("建议仓位:", QLabel(f"{decision.position_pct:.0%}"))
        form.addRow("风险评分:", QLabel(f"{decision.risk_score:.2f}"))
        form.addRow("持有周期:", QLabel(decision.horizon_label))

        if decision.reasoning:
            r_lbl = QLabel(decision.reasoning)
            r_lbl.setWordWrap(True)
            form.addRow("理由:", r_lbl)
        if decision.bull_case:
            b_lbl = QLabel(decision.bull_case)
            b_lbl.setWordWrap(True)
            form.addRow("看多:", b_lbl)
        if decision.bear_case:
            br_lbl = QLabel(decision.bear_case)
            br_lbl.setWordWrap(True)
            form.addRow("看空:", br_lbl)
        if decision.invalidation:
            inv_lbl = QLabel(decision.invalidation)
            inv_lbl.setWordWrap(True)
            form.addRow("失效条件:", inv_lbl)

        self.decision_card_layout.addWidget(details)

        # Risk checks
        if risk_result:
            risk_group = QGroupBox("风控审核")
            risk_layout = QVBoxLayout(risk_group)
            icon = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(
                risk_result.overall_risk_level, "⚪"
            )
            risk_layout.addWidget(QLabel(f"{icon} 综合风险: {risk_result.overall_risk_level.upper()}"))
            for check in risk_result.checks:
                c_icon = "✅" if check.passed else ("⛔" if check.level == "block" else "⚠️")
                risk_layout.addWidget(QLabel(f"  {c_icon} {check.name}: {check.message}"))
            if risk_result.blocked_reasons:
                blk = QLabel("⛔ " + "; ".join(risk_result.blocked_reasons))
                blk.setStyleSheet("color: red; font-weight: bold;")
                blk.setWordWrap(True)
                risk_layout.addWidget(blk)
            self.decision_card_layout.addWidget(risk_group)

        self.decision_card_layout.addStretch()

    def _append_scan_result(self, result: Dict[str, Any]):
        row = self.scan_table.rowCount()
        self.scan_table.insertRow(row)
        self._scan_results.append(result)

        decision = result["decision"]
        risk_result = result["risk_result"]
        scan_item = result["scan_item"] or {}
        cost_price = float(scan_item.get("cost_price", 0) or 0)
        action_label = "解析失败" if decision is None else TRADE_ACTION_LABELS.get(decision.action, decision.action)
        confidence_text = "-" if decision is None else f"{decision.confidence:.0%}"
        current_price_text = "-" if decision is None or decision.current_price <= 0 else f"{decision.current_price:.2f}"
        cost_text = "-" if cost_price <= 0 else f"{cost_price:.2f}"
        risk_text = "-" if risk_result is None else risk_result.overall_risk_level.upper()
        status_text = "待查看"
        if decision is None:
            status_text = "解析失败"
        elif risk_result and not risk_result.passed and decision.is_actionable:
            status_text = "风控拦截"
        elif decision.is_actionable:
            status_text = "可执行"
        else:
            status_text = "继续跟踪"

        values = [
            str(row + 1),
            result["symbol_code"],
            result["symbol_name"],
            action_label,
            confidence_text,
            current_price_text,
            cost_text,
            risk_text,
            status_text,
        ]
        for col, value in enumerate(values):
            self.scan_table.setItem(row, col, QTableWidgetItem(value))

    def _on_scan_selection_changed(self):
        row = self.scan_table.currentRow()
        if row < 0 or row >= len(self._scan_results):
            return
        result = self._scan_results[row]
        self._display_result(result, switch_to_details=False, emit_decision=True)
        self.result_tabs.setCurrentIndex(2)

    def _on_approve(self):
        if not self._current_decision or not self._current_risk_result:
            return
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)

        record = self.decision_tracker.save_decision(
            self._current_decision,
            self._current_risk_result,
            DecisionOutcome.APPROVED.value,
        )
        self.decision_status_label.setText("✅ 已批准 — 请在右侧下单面板确认执行")
        self.decision_status_label.setStyleSheet("color: green; font-weight: bold;")

        self.decision_ready.emit(self._current_decision)
        self._refresh_history()

    def _on_reject(self):
        if not self._current_decision or not self._current_risk_result:
            return
        self.approve_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)

        self.decision_tracker.save_decision(
            self._current_decision,
            self._current_risk_result,
            DecisionOutcome.REJECTED_BY_USER.value,
        )
        self.decision_status_label.setText("❌ 已驳回")
        self.decision_status_label.setStyleSheet("color: #888; font-weight: bold;")
        self._refresh_history()

    def _refresh_history(self):
        records = self.decision_tracker.query_recent(limit=50)
        self.history_table.setRowCount(len(records))
        for row, rec in enumerate(records):
            d = rec.decision or {}
            risk = rec.risk_result or {}
            self.history_table.setItem(row, 0, QTableWidgetItem(rec.created_at))
            self.history_table.setItem(row, 1, QTableWidgetItem(f"{rec.symbol_name}({rec.symbol_code})"))
            self.history_table.setItem(row, 2, QTableWidgetItem(
                TRADE_ACTION_LABELS.get(d.get("action", ""), d.get("action", ""))
            ))
            self.history_table.setItem(row, 3, QTableWidgetItem(f"{d.get('confidence', 0):.0%}"))
            self.history_table.setItem(row, 4, QTableWidgetItem(
                f"{d.get('target_price', 0):.2f}" if d.get("target_price", 0) > 0 else "-"
            ))
            self.history_table.setItem(row, 5, QTableWidgetItem(
                risk.get("overall_risk_level", "-").upper()
            ))
            self.history_table.setItem(row, 6, QTableWidgetItem(rec.outcome))

    def _find_account_panel(self) -> Optional[AccountPanel]:
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, AITradeDecisionWindow):
                return parent.account_panel
            parent = parent.parent() if hasattr(parent, "parent") and callable(parent.parent) else None
        return None

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_history()


# ───────────────────────────────────────────────────────────────────────────
#  Main Window: AI Trade Decision Center
# ───────────────────────────────────────────────────────────────────────────
class AITradeDecisionWindow(QMainWindow):
    """Standalone window combining AI decision, trading, and account panels."""

    def __init__(self, context_provider=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 交易决策中心")
        self.resize(1400, 850)
        self.context_provider = context_provider

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Account panel
        self.account_panel = AccountPanel()
        self.account_panel.setMinimumWidth(260)
        self.account_panel.setMaximumWidth(360)
        splitter.addWidget(self.account_panel)

        # Center: Decision panel
        self.decision_panel = DecisionPanel(context_provider=context_provider)
        self.decision_panel.setMinimumWidth(500)
        splitter.addWidget(self.decision_panel)

        # Right: Quick order panel
        self.order_panel = QuickOrderPanel()
        self.order_panel.setMinimumWidth(240)
        self.order_panel.setMaximumWidth(340)
        splitter.addWidget(self.order_panel)

        splitter.setSizes([300, 700, 300])
        main_layout.addWidget(splitter)

        # Status bar
        self.statusBar().showMessage("就绪")

        # Wiring
        self.decision_panel.decision_ready.connect(self._on_decision_ready)
        self.account_panel.order_requested.connect(self.order_panel.fill_order)
        self.order_panel.order_executed.connect(self._on_order_executed)

    def _on_decision_ready(self, decision: TradeDecision):
        self.order_panel.fill_from_decision(decision)
        self.statusBar().showMessage(
            f"决策: {TRADE_ACTION_LABELS.get(decision.action, decision.action)} "
            f"{decision.symbol_name} | 置信度 {decision.confidence:.0%}"
        )

    def _on_order_executed(self, success: bool, message: str):
        if success:
            self.statusBar().showMessage(f"✅ {message}")
            QTimer.singleShot(2000, self.account_panel.refresh)
        else:
            self.statusBar().showMessage(f"❌ {message}")
        QMessageBox.information(self, "下单结果", message)

    def set_symbol(self, code: str, name: str = ""):
        self.decision_panel.set_symbol(code, name)

    def lookup_symbol_name(self, code: str) -> str:
        parent = self.parent()
        if parent is None:
            return ""
        candidates = [code]
        plain_code = code.split(".")[0] if "." in code else code
        if plain_code not in candidates:
            candidates.append(plain_code)
        if "." not in code and plain_code:
            if plain_code.startswith(("5", "6", "9")):
                candidates.append(f"{plain_code}.SH")
            elif plain_code.startswith(("0", "1", "2", "3")):
                candidates.append(f"{plain_code}.SZ")
        for attr_name in ("name_map", "etf_name_map"):
            name_map = getattr(parent, attr_name, None)
            if not isinstance(name_map, dict):
                continue
            for candidate in candidates:
                if candidate in name_map and name_map.get(candidate):
                    return str(name_map.get(candidate))
        return ""
