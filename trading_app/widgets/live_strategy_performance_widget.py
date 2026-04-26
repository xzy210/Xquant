from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from trading_app.widgets.live_strategy_capital_management_dialog import (
    LiveStrategyCapitalManagementDialog,
)
from trading_app.widgets.live_strategy_fee_settings_dialog import LiveStrategyFeeSettingsDialog
from trading_app.services.live_strategy_center import LiveStrategyPortfolioService


class LiveStrategyPerformanceWidget(QWidget):
    def __init__(
        self,
        portfolio_service: LiveStrategyPortfolioService,
        *,
        ai_panel=None,
        etf_panel=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.portfolio_service = portfolio_service
        self.ai_panel = ai_panel
        self.etf_panel = etf_panel
        self._setup_ui()

        # 券商连上后持仓数据还要拉取/回报一小会，这里延迟几秒再刷一次，保证浮盈能恢复到真实值。
        try:
            self.portfolio_service.broker_service.connection_changed.connect(self._on_broker_connection_changed)
        except Exception:
            pass
        try:
            self.portfolio_service.trade_service.records_changed.connect(self.refresh_view)
        except Exception:
            pass

        # 兜底定时刷新：行情/持仓是异步拉的，被动信号未必覆盖所有变化。
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(15_000)
        self._auto_refresh_timer.timeout.connect(self.refresh_view)
        self._auto_refresh_timer.start()

        self.refresh_view()

    def _on_broker_connection_changed(self, connected: bool, _message: str = "") -> None:
        if not connected:
            return
        # 立即刷一次；再延迟 3 秒刷一次，给 QMT/持仓回报留出到达时间。
        self.refresh_view()
        QTimer.singleShot(3000, self.refresh_view)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        refresh_btn = QPushButton("刷新实盘收益")
        refresh_btn.clicked.connect(self.refresh_view)
        toolbar.addWidget(refresh_btn)

        capital_btn = QPushButton("实盘资金管理")
        capital_btn.clicked.connect(self._open_capital_management_dialog)
        toolbar.addWidget(capital_btn)

        fee_btn = QPushButton("交易费用设置")
        fee_btn.clicked.connect(self._open_fee_settings_dialog)
        toolbar.addWidget(fee_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 实盘账户总览（主账本聚合：Σ 所有策略 + unmanaged）
        portfolio_group = QGroupBox("实盘账户总览（主账本）")
        portfolio_grid = QGridLayout(portfolio_group)
        self.lbl_portfolio_total_asset = QLabel("-")
        self.lbl_portfolio_cash = QLabel("-")
        self.lbl_portfolio_market_value = QLabel("-")
        self.lbl_portfolio_capital_limit = QLabel("-")
        self.lbl_portfolio_realized_pnl = QLabel("-")
        self.lbl_portfolio_unrealized_pnl = QLabel("-")
        self.lbl_portfolio_total_pnl = QLabel("-")
        self.lbl_broker_diff = QLabel("-")
        self.lbl_broker_diff.setStyleSheet("color:#888;font-size:11px;")
        portfolio_pairs = [
            ("账户总资产", self.lbl_portfolio_total_asset, 0, 0),
            ("可用现金", self.lbl_portfolio_cash, 0, 2),
            ("持仓市值", self.lbl_portfolio_market_value, 0, 4),
            ("启动资金合计", self.lbl_portfolio_capital_limit, 1, 0),
            ("已实现盈亏", self.lbl_portfolio_realized_pnl, 1, 2),
            ("浮动盈亏", self.lbl_portfolio_unrealized_pnl, 1, 4),
            ("总盈亏", self.lbl_portfolio_total_pnl, 2, 0),
            ("主账本 vs 券商", self.lbl_broker_diff, 2, 2),
        ]
        for name, widget, r, c in portfolio_pairs:
            portfolio_grid.addWidget(QLabel(f"{name}:"), r, c)
            portfolio_grid.addWidget(widget, r, c + 1)
        layout.addWidget(portfolio_group)

        summary_group = QGroupBox("实盘交易统计摘要")
        summary_row = QHBoxLayout(summary_group)
        summary_row.setSpacing(16)
        self.lbl_total_trades = QLabel("-")
        self.lbl_buy_count = QLabel("-")
        self.lbl_sell_count = QLabel("-")
        self.lbl_win_rate = QLabel("-")
        self.lbl_total_pnl = QLabel("-")
        for name, widget in [
            ("总成交数", self.lbl_total_trades),
            ("买入数", self.lbl_buy_count),
            ("卖出数", self.lbl_sell_count),
            ("胜率", self.lbl_win_rate),
            ("累计盈亏", self.lbl_total_pnl),
        ]:
            summary_row.addWidget(QLabel(f"{name}:"))
            summary_row.addWidget(widget)
            summary_row.addSpacing(12)
        summary_row.addStretch()
        layout.addWidget(summary_group)

        self.strategy_table = QTableWidget(0, 8)
        self.strategy_table.setHorizontalHeaderLabels(
            ["策略", "启动资金", "可用现金", "持仓成本", "当前市值", "已实现盈亏", "浮动盈亏", "总盈亏"]
        )
        self.strategy_table.horizontalHeader().setStretchLastSection(True)
        self.strategy_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.strategy_table.setSizeAdjustPolicy(QAbstractItemView.SizeAdjustPolicy.AdjustToContents)
        self.strategy_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._install_copy_support(self.strategy_table)
        layout.addWidget(self.strategy_table)

        self.positions_table = QTableWidget(0, 9)
        self.positions_table.setHorizontalHeaderLabels(
            ["策略", "代码", "名称", "数量", "均价", "持仓成本", "现价", "市值", "浮动盈亏"]
        )
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.positions_table.setSizeAdjustPolicy(QAbstractItemView.SizeAdjustPolicy.AdjustToContents)
        self.positions_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._install_copy_support(self.positions_table)
        layout.addWidget(self.positions_table)

        self.daily_table = QTableWidget(0, 5)
        self.daily_table.setHorizontalHeaderLabels(["日期", "成交数", "买入金额", "卖出金额", "净流入"])
        self.daily_table.horizontalHeader().setStretchLastSection(True)
        self.daily_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.daily_table.setSizeAdjustPolicy(QAbstractItemView.SizeAdjustPolicy.AdjustToContents)
        self.daily_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._install_copy_support(self.daily_table)
        layout.addWidget(self.daily_table)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    #  表格复制支持（Ctrl+C / 右键菜单）
    # ------------------------------------------------------------------

    def _install_copy_support(self, table: QTableWidget) -> None:
        """给表格启用多选 + Ctrl+C 复制 + 右键菜单复制。

        - 多选：Ctrl 单选、Shift 连选、鼠标拖选一整块
        - Ctrl+C：把选中区域以 TSV（制表分隔）格式写入剪贴板，行之间换行
        - 右键菜单："复制"（等价 Ctrl+C）、"复制（含表头）"
        """
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)

        shortcut = QShortcut(QKeySequence.StandardKey.Copy, table)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda t=table: self._copy_table_selection(t, with_header=False))

        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, t=table: self._show_table_context_menu(t, pos)
        )

    def _show_table_context_menu(self, table: QTableWidget, pos) -> None:
        menu = QMenu(table)
        act_copy = QAction("复制", menu)
        act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        act_copy.triggered.connect(lambda: self._copy_table_selection(table, with_header=False))
        menu.addAction(act_copy)

        act_copy_header = QAction("复制（含表头）", menu)
        act_copy_header.triggered.connect(lambda: self._copy_table_selection(table, with_header=True))
        menu.addAction(act_copy_header)

        menu.addSeparator()
        act_copy_all = QAction("复制全部（含表头）", menu)
        act_copy_all.triggered.connect(lambda: self._copy_table_all(table))
        menu.addAction(act_copy_all)

        menu.exec(table.viewport().mapToGlobal(pos))

    @staticmethod
    def _copy_table_selection(table: QTableWidget, *, with_header: bool) -> None:
        ranges = table.selectedRanges()
        if not ranges:
            return
        rows: set[int] = set()
        cols: set[int] = set()
        for rng in ranges:
            for r in range(rng.topRow(), rng.bottomRow() + 1):
                rows.add(r)
            for c in range(rng.leftColumn(), rng.rightColumn() + 1):
                cols.add(c)
        sorted_rows = sorted(rows)
        sorted_cols = sorted(cols)

        lines: list[str] = []
        if with_header:
            header = table.horizontalHeader()
            header_cells: list[str] = []
            for c in sorted_cols:
                item = table.horizontalHeaderItem(c)
                text = item.text() if item is not None else header.model().headerData(c, Qt.Orientation.Horizontal) or ""
                header_cells.append(str(text))
            lines.append("\t".join(header_cells))

        for r in sorted_rows:
            cells: list[str] = []
            for c in sorted_cols:
                item = table.item(r, c)
                cells.append(item.text() if item is not None else "")
            lines.append("\t".join(cells))

        QApplication.clipboard().setText("\n".join(lines))

    @staticmethod
    def _copy_table_all(table: QTableWidget) -> None:
        row_count = table.rowCount()
        col_count = table.columnCount()
        if row_count == 0 or col_count == 0:
            return
        lines: list[str] = []
        header_cells = []
        for c in range(col_count):
            item = table.horizontalHeaderItem(c)
            header_cells.append(item.text() if item is not None else "")
        lines.append("\t".join(header_cells))
        for r in range(row_count):
            cells = []
            for c in range(col_count):
                item = table.item(r, c)
                cells.append(item.text() if item is not None else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    @staticmethod
    def _shrink_table_height(table: QTableWidget, *, max_visible_rows: int = 8) -> None:
        header_height = table.horizontalHeader().height()
        frame = table.frameWidth() * 2
        row_count = table.rowCount()
        visible_rows = min(max(row_count, 1), max_visible_rows)
        rows_height = sum(table.rowHeight(row) for row in range(visible_rows))
        scrollbar_height = table.horizontalScrollBar().sizeHint().height() if table.horizontalScrollBar().isVisible() else 0
        height = header_height + rows_height + frame + scrollbar_height + 2
        table.setMinimumHeight(height)
        table.setMaximumHeight(height)

    def _update_portfolio_labels(self, totals: dict) -> None:
        self.lbl_portfolio_total_asset.setText(f"¥{float(totals.get('total_asset', 0.0) or 0.0):,.2f}")
        self.lbl_portfolio_cash.setText(f"¥{float(totals.get('cash', 0.0) or 0.0):,.2f}")
        self.lbl_portfolio_market_value.setText(f"¥{float(totals.get('market_value', 0.0) or 0.0):,.2f}")
        self.lbl_portfolio_capital_limit.setText(f"¥{float(totals.get('capital_limit', 0.0) or 0.0):,.2f}")
        self._apply_pnl_color(self.lbl_portfolio_realized_pnl, float(totals.get("realized_pnl", 0.0) or 0.0))
        self._apply_pnl_color(self.lbl_portfolio_unrealized_pnl, float(totals.get("unrealized_pnl", 0.0) or 0.0))
        self._apply_pnl_color(self.lbl_portfolio_total_pnl, float(totals.get("total_pnl", 0.0) or 0.0))
        diff_text = str(totals.get("broker_diff_text", "") or "-")
        diff_color = str(totals.get("broker_diff_color", "#888") or "#888")
        tooltip = str(totals.get("broker_diff_tooltip", "") or "")
        self.lbl_broker_diff.setText(diff_text)
        self.lbl_broker_diff.setStyleSheet(f"color:{diff_color};font-size:11px;")
        self.lbl_broker_diff.setToolTip(tooltip)

    @staticmethod
    def _apply_pnl_color(label: QLabel, value: float) -> None:
        text = f"¥{value:,.2f}"
        if value > 0.01:
            label.setStyleSheet("color:#d9534f;font-weight:bold;")
        elif value < -0.01:
            label.setStyleSheet("color:#16a34a;font-weight:bold;")
        else:
            label.setStyleSheet("")
        label.setText(text)

    def _open_capital_management_dialog(self) -> None:
        dialog = LiveStrategyCapitalManagementDialog(
            self,
            ai_panel=self.ai_panel,
            etf_panel=self.etf_panel,
        )
        if dialog.exec():
            self._refresh_shared_setting_hints()
            self.refresh_view()

    def _open_fee_settings_dialog(self) -> None:
        dialog = LiveStrategyFeeSettingsDialog(self)
        if dialog.exec():
            self._refresh_shared_setting_hints()
            self.refresh_view()

    def _refresh_shared_setting_hints(self) -> None:
        for panel in (self.ai_panel, self.etf_panel):
            if panel is None:
                continue
            target = getattr(panel, "account_panel", panel)
            refresh = getattr(target, "refresh_shared_setting_hint", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass

    def refresh_view(self) -> None:
        snapshot = self.portfolio_service.refresh_snapshot()
        rows = list(snapshot.get("strategy_rows", []) or [])
        self._update_portfolio_labels(dict(snapshot.get("portfolio_totals", {}) or {}))
        summary = dict(snapshot.get("summary_metrics", {}) or {})
        self.lbl_total_trades.setText(str(int(summary.get("total_trades", 0) or 0)))
        self.lbl_buy_count.setText(str(int(summary.get("buy_count", 0) or 0)))
        self.lbl_sell_count.setText(str(int(summary.get("sell_count", 0) or 0)))
        self.lbl_win_rate.setText(f"{float(summary.get('win_rate', 0.0) or 0.0):.1f}%")
        self.lbl_total_pnl.setText(f"{float(summary.get('total_pnl', 0.0) or 0.0):,.2f}")

        self.strategy_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [
                str(item.get("strategy_name", "") or item.get("strategy_id", "")),
                f"{float(item.get('capital_limit', 0.0) or 0.0):,.2f}",
                f"{float(item.get('available_cash', 0.0) or 0.0):,.2f}",
                f"{float(item.get('invested_market_value', 0.0) or 0.0):,.2f}",
                f"{float(item.get('market_value', 0.0) or 0.0):,.2f}",
                f"{float(item.get('realized_pnl', 0.0) or 0.0):,.2f}",
                f"{float(item.get('unrealized_pnl', 0.0) or 0.0):,.2f}",
                f"{float(item.get('total_pnl', 0.0) or 0.0):,.2f}",
            ]
            for col, value in enumerate(values):
                self.strategy_table.setItem(row, col, QTableWidgetItem(value))
        self._shrink_table_height(self.strategy_table, max_visible_rows=6)

        position_rows = list(snapshot.get("position_rows", []) or [])
        self.positions_table.setRowCount(len(position_rows))
        for row, item in enumerate(position_rows):
            values = [
                str(item.get("strategy_name", "") or item.get("strategy_id", "")),
                str(item.get("stock_code", "") or ""),
                str(item.get("stock_name", "") or ""),
                str(int(item.get("quantity", 0) or 0)),
                f"{float(item.get('avg_cost', 0.0) or 0.0):,.4f}",
                f"{float(item.get('cost_amount', 0.0) or 0.0):,.2f}",
                f"{float(item.get('current_price', 0.0) or 0.0):,.4f}",
                f"{float(item.get('market_value', 0.0) or 0.0):,.2f}",
                f"{float(item.get('unrealized_pnl', 0.0) or 0.0):,.2f}",
            ]
            for col, value in enumerate(values):
                self.positions_table.setItem(row, col, QTableWidgetItem(value))
        self._shrink_table_height(self.positions_table, max_visible_rows=10)

        daily = list(snapshot.get("daily_rows", []) or [])
        self.daily_table.setRowCount(len(daily))
        for row, item in enumerate(daily):
            values = [
                str(item.get("trade_date", "") or ""),
                str(item.get("trade_count", "") or "0"),
                f"{float(item.get('buy_amount', 0.0) or 0.0):,.2f}",
                f"{float(item.get('sell_amount', 0.0) or 0.0):,.2f}",
                f"{float(item.get('net_inflow', 0.0) or 0.0):,.2f}",
            ]
            for col, value in enumerate(values):
                self.daily_table.setItem(row, col, QTableWidgetItem(value))
        self._shrink_table_height(self.daily_table, max_visible_rows=8)
