from __future__ import annotations

from datetime import datetime, timedelta

try:
    from services.strategy_budget_service import get_strategy_budget_service
    from services.trade_record_service import get_trade_record_service
    from services.strategy_constants import AI_STOCK_STRATEGY_ID, UNMANAGED_STRATEGY_ID
    from common.broker_session_service import get_broker_session_service
except ImportError:
    from trading_app.services.strategy_budget_service import get_strategy_budget_service
    from trading_app.services.trade_record_service import get_trade_record_service
    from trading_app.services.strategy_constants import AI_STOCK_STRATEGY_ID, UNMANAGED_STRATEGY_ID
    from trading_app.common.broker_session_service import get_broker_session_service

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


class LiveStrategyPerformanceWidget(QWidget):
    def __init__(self, ai_panel=None, etf_panel=None, parent=None) -> None:
        super().__init__(parent)
        self.trade_service = get_trade_record_service()
        self.strategy_budget = get_strategy_budget_service()
        self.ai_panel = ai_panel
        self.etf_panel = etf_panel
        self._broker_service = get_broker_session_service()
        self._setup_ui()

        # 券商连上后持仓数据还要拉取/回报一小会，这里延迟几秒再刷一次，保证浮盈能恢复到真实值。
        try:
            self._broker_service.connection_changed.connect(self._on_broker_connection_changed)
        except Exception:
            pass
        try:
            self.trade_service.records_changed.connect(self.refresh_view)
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

    def _active_strategy_ids(self) -> set[str]:
        ids = {AI_STOCK_STRATEGY_ID}
        if self.etf_panel is not None:
            try:
                strategy_id, _strategy_name, _virtual_account_id = self.etf_panel._etf_strategy_identity()  # noqa: SLF001
            except Exception:
                strategy_id = ""
            if str(strategy_id or "").strip():
                ids.add(str(strategy_id).strip())
        return ids

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        refresh_btn = QPushButton("刷新收益")
        refresh_btn.clicked.connect(self.refresh_view)
        toolbar.addWidget(refresh_btn)

        capital_btn = QPushButton("策略资金管理")
        capital_btn.clicked.connect(self._open_capital_management_dialog)
        toolbar.addWidget(capital_btn)

        fee_btn = QPushButton("交易费用设置")
        fee_btn.clicked.connect(self._open_fee_settings_dialog)
        toolbar.addWidget(fee_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 账户总览（主账本聚合：Σ 所有策略 + unmanaged）
        portfolio_group = QGroupBox("账户总览（主账本）")
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

        summary_group = QGroupBox("交易统计摘要")
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

    def _build_ai_row(self) -> dict:
        if self.ai_panel is None:
            return {}
        account_panel = getattr(self.ai_panel, "account_panel", None)
        if account_panel is None:
            return {}
        broker_ctx = account_panel.get_broker_context()
        total_asset_ref = float(getattr(broker_ctx, "total_asset", 0.0) or 0.0)
        # 注意：必须带上 code/volume，否则 get_positions_view 里 live_map 为空、浮盈永远为 0。
        live_positions = []
        for item in (account_panel.get_live_positions() or []):
            code = str(item.get("code", "") or "")
            if not code:
                continue
            live_positions.append({
                "code": code,
                "stock_code": code,
                "volume": int(item.get("volume", 0) or 0),
                "market_value": float(item.get("market_value", 0.0) or 0.0),
                "name": str(item.get("name", "") or ""),
            })
        account = self.strategy_budget.build_account_snapshot(
            AI_STOCK_STRATEGY_ID,
            strategy_name="AI交易中心",
            virtual_account_id="va_ai_trade_decision_center",
            real_total_asset=total_asset_ref,
            live_positions=live_positions,
        )
        account["strategy_name"] = account.get("strategy_name") or "AI交易中心"
        return account

    def _build_etf_row(self) -> dict:
        if self.etf_panel is None:
            return {}
        strategy_id, strategy_name, virtual_account_id = self.etf_panel._etf_strategy_identity()  # noqa: SLF001
        summary = dict(self.etf_panel.engine.get_status_summary() or {})
        account_view = dict(self.etf_panel._get_etf_strategy_account_view(summary) or {})  # noqa: SLF001
        market_value = float(account_view.get("market_value", 0.0) or 0.0)
        # 主账本为唯一真源：capital_limit / cash_balance 不再 override，只透传实时市值
        snapshot_kwargs = dict(
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
        )
        # 只有真实观测到市值时才传 override；否则让 build_account_snapshot 自己退化到成本口径，避免 0 把浮盈打成 -invested_cost。
        if market_value > 0:
            snapshot_kwargs["market_value_override"] = market_value
        account = self.strategy_budget.build_account_snapshot(strategy_id, **snapshot_kwargs)
        return account

    def _fetch_broker_live_positions(self) -> list[dict]:
        """拉一次券商实盘持仓，返回 [{stock_code, volume, market_value, name, open_price}]。

        供未管理账户 snapshot / 持仓表读取当前市值使用。查询失败/未连接时返回 []，
        调用方会退化为"现价=成本价"。
        """
        if not getattr(self._broker_service, "is_connected", False):
            return []
        try:
            raw = self._broker_service.query_stock_positions() or []
        except Exception:
            return []
        result: list[dict] = []
        for pos in raw:
            try:
                code = str(getattr(pos, "stock_code", "") or "")
                volume = int(getattr(pos, "volume", 0) or 0)
                if not code or volume <= 0:
                    continue
                market_value = float(getattr(pos, "market_value", 0.0) or 0.0)
                name = str(getattr(pos, "stock_name", "") or "")
                open_price = float(getattr(pos, "open_price", 0.0) or 0.0)
                result.append({
                    "stock_code": code,
                    "volume": volume,
                    "market_value": market_value,
                    "name": name,
                    "open_price": open_price,
                })
            except Exception:
                continue
        return result

    def _reconcile_unmanaged_from_broker(self, broker_live_positions: list[dict]) -> dict | None:
        """每次手动/自动刷新都同步一次 unmanaged 账户，并返回 reconcile 摘要。

        这里主动跑一次 reconcile，保证"收益中心"看到的 unmanaged 现金和持仓与
        券商实时数据一致；避免要等 HubStateService 的定时/事件触发（可能因为
        throttle 或启动时序慢）。

        返回值即 `reconcile_unmanaged_with_broker` 的 summary，供 UI 显示
        "主账本 vs 券商" 真实漂移信号（cash_shortfall / position_shortfalls）。
        """
        if not getattr(self._broker_service, "is_connected", False):
            return None
        try:
            asset = self._broker_service.query_stock_asset()
            broker_cash = float(getattr(asset, "cash", 0.0) or 0.0)
        except Exception:
            return None
        broker_positions = [
            {
                "stock_code": p.get("stock_code", ""),
                "volume": p.get("volume", 0),
                "open_price": p.get("open_price", 0.0),
            }
            for p in (broker_live_positions or [])
        ]
        try:
            return self.strategy_budget.reconcile_unmanaged_with_broker(
                broker_cash=broker_cash,
                broker_positions=broker_positions,
            )
        except Exception:
            return None

    def _build_unmanaged_row(self, broker_live_positions: list[dict] | None = None) -> dict:
        """未管理账户：承载券商里未被任何策略认领的现金/持仓。"""
        try:
            account = self.strategy_budget.build_account_snapshot(
                UNMANAGED_STRATEGY_ID,
                live_positions=broker_live_positions or None,
            )
        except Exception:
            return {}
        if not account:
            return {}
        # 未管理账户里只要有现金或持仓都展示（有零资产时也便于用户确认状态）
        account["strategy_name"] = account.get("strategy_name") or "未管理账户"
        account["is_unmanaged"] = True
        return account

    def _build_strategy_rows(self, broker_live_positions: list[dict] | None = None) -> list[dict]:
        rows: list[dict] = []
        builders = [
            self._build_ai_row,
            self._build_etf_row,
            lambda: self._build_unmanaged_row(broker_live_positions),
        ]
        for builder in builders:
            try:
                row = dict(builder() or {})
            except Exception:
                row = {}
            if row:
                rows.append(row)
        return rows

    def _build_summary_metrics(self, rows: list[dict]) -> dict:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        active_ids = sorted(
            {str(item.get("strategy_id", "") or "").strip() for item in rows if item.get("strategy_id")}
        )
        period = self.trade_service.get_period_stats(
            strategy_ids=active_ids or None,
            start_date=start_date,
        )
        win_stats = self.trade_service.get_win_rate_stats(
            strategy_ids=active_ids or None,
            start_date=start_date,
        )
        return {
            "total_trades": int(period.get("total_trades", 0) or 0),
            "buy_count": int(period.get("buy_count", 0) or 0),
            "sell_count": int(period.get("sell_count", 0) or 0),
            "closed_count": int(win_stats.get("closed_count", 0) or 0),
            "win_rate": float(win_stats.get("win_rate", 0.0) or 0.0) * 100.0,
            "total_pnl": round(sum(float(item.get("total_pnl", 0.0) or 0.0) for item in rows), 2),
        }

    def _build_position_rows(
        self,
        rows: list[dict],
        broker_live_positions: list[dict] | None = None,
    ) -> list[dict]:
        """统一走 strategy_budget.get_positions_view，主账本口径，按策略分组后组内按市值倒序。"""
        results: list[dict] = []

        if self.ai_panel is not None:
            try:
                account_panel = getattr(self.ai_panel, "account_panel", None)
                if account_panel is not None:
                    live = [
                        {
                            "stock_code": item.get("code", "") or "",
                            "market_value": float(item.get("market_value", 0.0) or 0.0),
                            "volume": int(item.get("volume", 0) or 0),
                            "name": item.get("name", "") or "",
                        }
                        for item in (account_panel.get_live_positions() or [])
                    ]
                    ai_rows = self.strategy_budget.get_positions_view(
                        AI_STOCK_STRATEGY_ID,
                        strategy_name="AI交易中心",
                        virtual_account_id="va_ai_trade_decision_center",
                        live_positions=live,
                    )
                    for r in ai_rows:
                        r["strategy_name"] = "AI交易中心"
                    results.extend(ai_rows)
            except Exception:
                pass

        if self.etf_panel is not None:
            try:
                strategy_id, strategy_name, virtual_account_id = self.etf_panel._etf_strategy_identity()  # noqa: SLF001
                summary = dict(self.etf_panel.engine.get_status_summary() or {})
                holding = str(summary.get("holding", "") or "")
                spot_prices: dict[str, float] = {}
                if holding:
                    current_price = float(summary.get("current_price", 0.0) or 0.0)
                    if current_price <= 0:
                        current_price = float(summary.get("buy_price", 0.0) or 0.0)
                    if current_price > 0:
                        spot_prices[holding] = current_price
                etf_rows = self.strategy_budget.get_positions_view(
                    strategy_id,
                    strategy_name=strategy_name,
                    virtual_account_id=virtual_account_id,
                    spot_prices=spot_prices or None,
                )
                for r in etf_rows:
                    r["strategy_name"] = strategy_name
                results.extend(etf_rows)
            except Exception:
                pass

        # 未管理账户持仓：主账本有 avg_cost，行情从 broker 当前持仓的 market_value 取
        try:
            unmanaged_rows = self.strategy_budget.get_positions_view(
                UNMANAGED_STRATEGY_ID,
                live_positions=broker_live_positions or None,
            )
            for r in unmanaged_rows:
                r["strategy_name"] = "未管理账户"
            results.extend(unmanaged_rows)
        except Exception:
            pass

        strategy_order: dict[str, int] = {}
        for idx, row in enumerate(rows):
            strategy_id = str(row.get("strategy_id", "") or "").strip()
            if strategy_id and strategy_id not in strategy_order:
                strategy_order[strategy_id] = idx
        results.sort(
            key=lambda r: (
                strategy_order.get(str(r.get("strategy_id", "") or "").strip(), len(strategy_order)),
                -float(r.get("market_value", 0.0) or 0.0),
                str(r.get("stock_code", "") or ""),
            )
        )
        return results

    def _resolve_position_name(self, item: dict) -> str:
        name = str(item.get("stock_name", "") or "").strip()
        if name:
            return name

        code = str(item.get("stock_code", "") or "").strip()
        if not code:
            return ""

        lookup = getattr(self.ai_panel, "lookup_symbol_name", None)
        if callable(lookup):
            try:
                resolved = lookup(code)
                if resolved:
                    return str(resolved).strip()
            except Exception:
                pass

        etf_panel = getattr(self, "etf_panel", None)
        for attr_name in ("_ui_etf_name_map",):
            name_map = getattr(etf_panel, attr_name, None)
            if isinstance(name_map, dict):
                resolved = name_map.get(code) or name_map.get(code.split(".")[0])
                if resolved:
                    return str(resolved).strip()

        engine = getattr(etf_panel, "engine", None)
        name_map = getattr(engine, "_etf_name_map", None)
        if isinstance(name_map, dict):
            resolved = name_map.get(code) or name_map.get(code.split(".")[0])
            if resolved:
                return str(resolved).strip()
        return ""

    def _build_daily_rows(self, active_ids: set[str]) -> list[dict]:
        start_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        daily = self.trade_service.get_daily_stats(
            strategy_ids=sorted(active_ids) if active_ids else None,
            start_date=start_date,
        )
        return [
            {
                "trade_date": str(item.get("trade_date", "") or ""),
                "trade_count": int(item.get("total_trades", 0) or 0),
                "buy_amount": float(item.get("buy_amount", 0.0) or 0.0),
                "sell_amount": float(item.get("sell_amount", 0.0) or 0.0),
                "net_inflow": float(item.get("net_inflow", 0.0) or 0.0),
            }
            for item in daily
        ]

    def _update_portfolio_labels(
        self,
        rows: list[dict],
        reconcile_summary: dict | None = None,
    ) -> None:
        """账户总览聚合口径（主账本真实现金流口径，自动与券商对齐）。

        关键字段口径约定：
          - portfolio_cash = Σ(cash_balance - reserved_cash)
              主账本按真实资金流累计的"账上现金"，已经扣除全部手续费，和券商
              asset.cash 严格一致（由 reconcile_unmanaged_with_broker 保证）。
          - portfolio_total_asset = portfolio_cash + portfolio_market_value
              与券商 asset.total_asset 口径一致，用于对账展示。

          策略表的"可用现金"列仍然是 available_cash（下单额度语义，不改）。
          两者有微小差即"已付手续费"，属于口径差而不是账本漂移。

        "主账本 vs 券商" 口径：展示 reconcile *之前* 的真实漂移信号（方案A）。
          - cash_shortfall < 0：活跃策略声明现金 > 券商实际现金（虚报）
          - position_shortfalls：活跃策略声明持仓 > 券商实际持仓（虚报）
          - 正常情况下两者均为零/空，unmanaged 会吸收剩余量，展示"一致"。
        """
        portfolio_market_value = sum(float(r.get("market_value", 0.0) or 0.0) for r in rows)
        portfolio_capital_limit = sum(float(r.get("capital_limit", 0.0) or 0.0) for r in rows)
        portfolio_realized_pnl = sum(float(r.get("realized_pnl", 0.0) or 0.0) for r in rows)
        portfolio_unrealized_pnl = sum(float(r.get("unrealized_pnl", 0.0) or 0.0) for r in rows)
        portfolio_total_pnl = sum(float(r.get("total_pnl", 0.0) or 0.0) for r in rows)
        portfolio_cash = sum(
            max(float(r.get("cash_balance", 0.0) or 0.0) - float(r.get("reserved_cash", 0.0) or 0.0), 0.0)
            for r in rows
        )
        portfolio_total_asset = portfolio_cash + portfolio_market_value

        self.lbl_portfolio_total_asset.setText(f"¥{portfolio_total_asset:,.2f}")
        self.lbl_portfolio_cash.setText(f"¥{portfolio_cash:,.2f}")
        self.lbl_portfolio_market_value.setText(f"¥{portfolio_market_value:,.2f}")
        self.lbl_portfolio_capital_limit.setText(f"¥{portfolio_capital_limit:,.2f}")
        self._apply_pnl_color(self.lbl_portfolio_realized_pnl, portfolio_realized_pnl)
        self._apply_pnl_color(self.lbl_portfolio_unrealized_pnl, portfolio_unrealized_pnl)
        self._apply_pnl_color(self.lbl_portfolio_total_pnl, portfolio_total_pnl)

        # 主账本 vs 券商：展示 reconcile 之前的真实账本漂移（方案A）
        diff_text = "券商未连接"
        diff_color = "#888"
        tooltip = ""
        if not getattr(self._broker_service, "is_connected", False):
            pass
        elif not reconcile_summary:
            diff_text = "对账数据缺失"
            diff_color = "#eab308"
        else:
            cash_shortfall = float(reconcile_summary.get("cash_shortfall", 0.0) or 0.0)
            pos_shortfalls = list(reconcile_summary.get("position_shortfalls", []) or [])
            untracked = list(reconcile_summary.get("untracked_broker_codes", []) or [])
            broker_cash = float(reconcile_summary.get("broker_cash", 0.0) or 0.0)
            unmanaged_cash = float(reconcile_summary.get("unmanaged_cash", 0.0) or 0.0)

            if cash_shortfall < -1.0 or pos_shortfalls:
                parts = []
                if cash_shortfall < -1.0:
                    parts.append(f"活跃策略虚报现金 ¥{abs(cash_shortfall):,.2f}")
                if pos_shortfalls:
                    codes_preview = ",".join(
                        f"{s['stock_code']}×{s['shortfall']}" for s in pos_shortfalls[:3]
                    )
                    more = "…" if len(pos_shortfalls) > 3 else ""
                    parts.append(f"虚报持仓 {len(pos_shortfalls)}项({codes_preview}{more})")
                diff_text = "异常: " + "；".join(parts)
                diff_color = "#d9534f"
                tooltip_lines = ["账本漂移（reconcile 之前的真实差）："]
                if cash_shortfall < -1.0:
                    tooltip_lines.append(
                        f"  现金: 活跃策略声明 > 券商实际 {abs(cash_shortfall):,.2f} 元"
                    )
                for s in pos_shortfalls:
                    tooltip_lines.append(
                        f"  持仓 {s['stock_code']}: 声明 {s['claimed']} / 券商 {s['broker']} / 差 {s['shortfall']}"
                    )
                tooltip = "\n".join(tooltip_lines)
            else:
                diff_text = (
                    f"一致 (券商现金 ¥{broker_cash:,.2f}，未管理吸收 ¥{unmanaged_cash:,.2f})"
                )
                diff_color = "#16a34a"
                if untracked:
                    diff_text += f" · 巡检{len(untracked)}只"
                    tooltip = "券商有但无策略声明（理论由未管理吸收）：\n  " + ", ".join(untracked)
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
        # 一次性拉券商实盘持仓（含 market_value），供 unmanaged snapshot / 持仓表使用
        broker_live_positions = self._fetch_broker_live_positions()
        # 顺手刷新一次 unmanaged 账户，保证可用现金=broker_cash-Σ各策略 cash_balance
        reconcile_summary = self._reconcile_unmanaged_from_broker(broker_live_positions)
        rows = self._build_strategy_rows(broker_live_positions)
        self._update_portfolio_labels(rows, reconcile_summary)
        # 交易统计摘要只覆盖活跃策略（unmanaged 不参与成交统计）
        active_rows = [r for r in rows if not bool(r.get("is_unmanaged", False))]
        summary = self._build_summary_metrics(active_rows)
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

        position_rows = self._build_position_rows(rows, broker_live_positions)
        self.positions_table.setRowCount(len(position_rows))
        for row, item in enumerate(position_rows):
            values = [
                str(item.get("strategy_name", "") or item.get("strategy_id", "")),
                str(item.get("stock_code", "") or ""),
                self._resolve_position_name(item),
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

        daily = self._build_daily_rows({str(item.get("strategy_id", "") or "").strip() for item in rows if item.get("strategy_id")})
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
