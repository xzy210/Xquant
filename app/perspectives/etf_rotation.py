# -*- coding: utf-8 -*-
"""Native ETF rotation strategy tab for the new application shell."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pandas as pd
from pydantic import BaseModel
from PyQt6.QtCore import QDate, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from common.data_portal import get_data_portal
from common.events import BacktestEvent, EventBus
from common.experiment_store import ExperimentStore
from common.ui import FormBuilder, TabWorkspace
from live_rotation.config import ConfigManager
from strategy_app.strategies.etf_rotation_params import ETFRotationParams


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PERSISTED_EVENT_TYPES = {
    "run_requested",
    "run_started",
    "run_completed",
    "run_failed",
    "result_ready",
    "experiment_saved",
    "experiment_save_failed",
    "data_loaded",
    "data_load_failed",
    "signal_calculated",
    "decision_made",
    "research_signal_generated",
}
_SHELL_EVENT_TYPES = _PERSISTED_EVENT_TYPES | {"progress"}
_DISPLAY_LABELS = {
    "final_value": "最终净值",
    "total_return": "总收益率",
    "annual_return": "年化收益率",
    "max_drawdown": "最大回撤",
    "sharpe_ratio": "夏普比率",
    "trade_count": "交易次数",
    "run_id": "运行ID",
    "params_hash": "参数哈希",
    "data_version": "数据版本",
    "signal": "信号",
    "target": "目标",
    "reason": "原因",
    "strategy_signals": "策略信号数",
    "action": "动作",
}


def _ensure_project_import_path() -> None:
    root_path = str(_PROJECT_ROOT)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)


def _model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _make_event(
    event_type: str,
    message: str,
    *,
    progress_current: int | None = None,
    progress_total: int | None = None,
    run_id: str = "",
    payload: dict[str, Any] | None = None,
) -> BacktestEvent:
    return BacktestEvent(
        date=None,
        bars={},
        history={},
        prices={},
        valid_symbols=[],
        event_type=event_type,
        message=message,
        progress_current=progress_current,
        progress_total=progress_total,
        mode="bar",
        run_id=run_id,
        payload={"strategy_id": "etf_rotation", **dict(payload or {})},
    )


def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("serializable_result"), dict):
        return dict(result["serializable_result"])
    return dict(result or {})


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.4f}" if abs(value) < 1000 else f"{value:,.2f}"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    try:
        if pd.isna(value) if not isinstance(value, (list, tuple, dict, str)) else False:
            return ""
    except Exception:
        pass
    return str(value)


def _summary_from_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = _result_payload(result)
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    summary = {
        "final_value": payload.get("final_value", ""),
        "total_return": metrics.get("total_return", ""),
        "annual_return": metrics.get("annual_return", ""),
        "max_drawdown": metrics.get("max_drawdown", ""),
        "sharpe_ratio": metrics.get("sharpe_ratio", ""),
        "trade_count": len(payload.get("trades", []) or []),
        "run_id": payload.get("run_id", result.get("run_id", "")),
        "params_hash": payload.get("params_hash", ""),
        "data_version": payload.get("data_version", ""),
    }
    return {key: value for key, value in summary.items() if value != "" and value is not None}


class ETFRotationBacktestWorker(QThread):
    """Run ETF rotation backtest outside the UI thread."""

    finished_signal = pyqtSignal()

    def __init__(
        self,
        params: ETFRotationParams,
        data: dict[str, pd.DataFrame],
        event_bus: EventBus,
    ) -> None:
        super().__init__()
        self.params = params
        self.data = {symbol: frame.copy() for symbol, frame in data.items()}
        self.event_bus = event_bus

    def run(self) -> None:
        try:
            _ensure_project_import_path()
            from strategy_app.backtest import BacktestConfig, UnifiedBacktestEngine
            from strategy_app.strategies import create_strategy

            strategy = create_strategy("etf_rotation", params=self.params)
            initial_cash = float(getattr(strategy.param_model, "initial_cash", 100000.0) or 100000.0)
            engine = UnifiedBacktestEngine(
                BacktestConfig(initial_cash=initial_cash, mode="bar"),
                bus=self.event_bus,
            )
            result = engine.run(strategy, self.data, code=(self.params.etf_pool[0] if self.params.etf_pool else "ETF_ROTATION"), mode="bar")
            payload = _result_payload(result)
            self.event_bus.publish(_make_event(
                "result_ready",
                "ETF轮动回测结果已生成",
                run_id=str(payload.get("run_id", "") or result.get("run_id", "")),
                payload={"result": result},
            ))
        except Exception as exc:
            self.event_bus.publish(_make_event(
                "run_failed",
                f"ETF轮动回测失败：{exc}",
                payload={"error": str(exc)},
            ))
        finally:
            self.finished_signal.emit()


class ETFRotationResearchTab(QWidget):
    """Native shell ETF rotation research workspace."""

    eventReceived = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        event_bus: EventBus | None = None,
        experiment_store: ExperimentStore | None = None,
        on_experiment_saved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("native_etf_rotation_tab")
        self.shell_event_bus = event_bus or EventBus()
        self.run_event_bus = EventBus()
        self.experiment_store = experiment_store
        self.on_experiment_saved = on_experiment_saved
        self.config_mgr = ConfigManager()
        self.config = self.config_mgr.load()
        self.params = self.config.to_params()
        self.data_dir = get_data_portal().default_data_dir
        self.current_data: dict[str, pd.DataFrame] = {}
        self.current_result: dict[str, Any] | None = None
        self.current_research_signal: dict[str, Any] | None = None
        self._run_events: list[BacktestEvent] = []
        self._worker: ETFRotationBacktestWorker | None = None
        self._unsubscribe_run_bus = self.run_event_bus.subscribe(lambda event: self.eventReceived.emit(event))

        self.eventReceived.connect(self._handle_run_event)
        self._build_ui()
        self._refresh_overview()
        self._refresh_data_status()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._unsubscribe_run_bus()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        self.tabs = TabWorkspace(self)
        self.tabs.setTabsClosable(False)

        self.overview_tab = self._build_overview_tab()
        self.parameters_tab = self._build_parameters_tab()
        self.data_tab = self._build_data_tab()
        self.playback_tab = self._build_playback_tab()
        self.logs_tab = self._build_logs_tab()

        self.tabs.add_workspace_tab(self.overview_tab, "概览", closable=False)
        self.tabs.add_workspace_tab(self.parameters_tab, "参数", closable=False)
        self.tabs.add_workspace_tab(self.data_tab, "数据", closable=False)
        self.tabs.add_workspace_tab(self.playback_tab, "回放 / 回测", closable=False)
        self.tabs.add_workspace_tab(self.logs_tab, "日志", closable=False)
        self.tabs.setCurrentWidget(self.overview_tab)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.tabs)

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        title = QLabel("ETF轮动研究", tab)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.status_label = QLabel("ETF轮动研究页已就绪。", tab)
        self.status_label.setProperty("class", "description")
        self.data_version_label = QLabel("数据版本：-", tab)
        self.holding_label = QLabel("当前持仓：-", tab)
        self.decision_label = QLabel("最近决策：-", tab)

        actions = QHBoxLayout()
        research_signal_btn = QPushButton("生成研究信号", tab)
        research_signal_btn.setProperty("class", "primary")
        research_signal_btn.clicked.connect(self._generate_research_signal)
        refresh_btn = QPushButton("刷新概览", tab)
        refresh_btn.clicked.connect(self._refresh_overview)
        actions.addWidget(research_signal_btn)
        actions.addWidget(refresh_btn)
        actions.addStretch(1)

        self.scores_table = QTableWidget(tab)
        self.scores_table.setColumnCount(3)
        self.scores_table.setHorizontalHeaderLabels(["代码", "名称", "评分"])
        self.scores_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.scores_table.verticalHeader().setVisible(False)

        layout.addWidget(title)
        layout.addWidget(self.status_label)
        layout.addWidget(self.data_version_label)
        layout.addWidget(self.holding_label)
        layout.addWidget(self.decision_label)
        layout.addLayout(actions)
        layout.addWidget(self.scores_table, 1)
        return tab

    def _build_parameters_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.params_form = FormBuilder(ETFRotationParams, self.params, tab, run_button_text="保存参数")
        self.params_form.run_button.clicked.connect(self._save_parameters)
        layout.addWidget(self.params_form, 1)

        sync_actions = QHBoxLayout()
        sync_actions.addStretch(1)
        self.sync_live_config_btn = QPushButton("同步到实盘配置", tab)
        self.sync_live_config_btn.setToolTip("将当前研究参数写入 ETF 轮动实盘配置；实盘调度、自动执行、通知等字段不会被研究台修改。")
        self.sync_live_config_btn.clicked.connect(self._sync_parameters_to_live_config)
        sync_actions.addWidget(self.sync_live_config_btn)
        layout.addLayout(sync_actions)
        return tab

    def _build_data_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal, tab)

        controls = QWidget(splitter)
        control_layout = QVBoxLayout(controls)

        pool_group = QGroupBox("ETF池", controls)
        pool_layout = QFormLayout(pool_group)
        self.pool_input = QLineEdit(",".join(self.params.etf_pool), pool_group)
        self.pool_input.setPlaceholderText("510880,159949,513100,518880")
        self.start_date_edit = QDateEdit(pool_group)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.currentDate().addDays(-400))
        self.end_date_edit = QDateEdit(pool_group)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate())
        pool_layout.addRow("ETF池：", self.pool_input)
        pool_layout.addRow("开始日期：", self.start_date_edit)
        pool_layout.addRow("结束日期：", self.end_date_edit)

        data_actions = QHBoxLayout()
        refresh_btn = QPushButton("检查数据", controls)
        refresh_btn.clicked.connect(self._refresh_data_status)
        load_btn = QPushButton("加载数据", controls)
        load_btn.clicked.connect(self._load_data)
        data_actions.addWidget(refresh_btn)
        data_actions.addWidget(load_btn)
        data_actions.addStretch(1)

        run_group = QGroupBox("运行", controls)
        run_layout = QVBoxLayout(run_group)
        self.run_backtest_btn = QPushButton("运行ETF轮动回测", run_group)
        self.run_backtest_btn.setProperty("class", "primary")
        self.run_backtest_btn.clicked.connect(self._run_backtest)
        run_layout.addWidget(self.run_backtest_btn)

        control_layout.addWidget(pool_group)
        control_layout.addLayout(data_actions)
        control_layout.addWidget(run_group)
        control_layout.addStretch(1)

        preview = QWidget(splitter)
        preview_layout = QVBoxLayout(preview)
        self.data_status_table = QTableWidget(preview)
        self.data_status_table.setColumnCount(8)
        self.data_status_table.setHorizontalHeaderLabels(["代码", "名称", "行数", "首日", "最新", "新鲜度", "版本", "路径"])
        self.data_status_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.data_status_table.horizontalHeader().setStretchLastSection(True)
        self.data_preview_table = QTableWidget(preview)
        self.data_preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        preview_layout.addWidget(QLabel("数据覆盖", preview))
        preview_layout.addWidget(self.data_status_table, 1)
        preview_layout.addWidget(QLabel("最新K线预览", preview))
        preview_layout.addWidget(self.data_preview_table, 1)

        splitter.addWidget(controls)
        splitter.addWidget(preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        return tab

    def _build_playback_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Vertical, tab)

        self.summary_table = QTableWidget(splitter)
        self.summary_table.setColumnCount(2)
        self.summary_table.setHorizontalHeaderLabels(["指标", "值"])
        self.summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.summary_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        lower = QWidget(splitter)
        lower_layout = QHBoxLayout(lower)
        self.trade_table = QTableWidget(lower)
        self.trade_table.setColumnCount(7)
        self.trade_table.setHorizontalHeaderLabels(["日期", "代码", "动作", "价格", "数量", "金额", "原因"])
        self.trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.result_text = QTextEdit(lower)
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("回测和研究信号详情会显示在这里。")
        lower_layout.addWidget(self.trade_table, 2)
        lower_layout.addWidget(self.result_text, 1)

        splitter.addWidget(self.summary_table)
        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        return tab

    def _build_logs_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.event_log = QTextEdit(tab)
        self.event_log.setReadOnly(True)
        self.event_log.setPlaceholderText("事件日志会显示在这里。")
        layout.addWidget(self.event_log)
        return tab

    def current_params(self) -> ETFRotationParams:
        model = self.params_form.model()
        if isinstance(model, ETFRotationParams):
            return model
        return ETFRotationParams.from_mapping(_model_to_dict(model))

    def selected_pool(self) -> list[str]:
        text = self.pool_input.text().replace(";", ",")
        return [item.strip() for item in text.split(",") if item.strip()]

    def _save_parameters(self) -> None:
        params = self.current_params()
        self.params = params
        self.pool_input.setText(",".join(params.etf_pool))
        self._refresh_overview()
        self._append_log(f"研究参数已保存到当前页面：{_model_to_dict(params)}")
        self.status_label.setText("研究参数已保存到当前页面，未写入实盘配置。")

    def _sync_parameters_to_live_config(self) -> None:
        params = self.current_params()
        config_path = getattr(self.config_mgr, "config_path", "")
        reply = QMessageBox.question(
            self,
            "确认同步到实盘配置",
            "此操作会把当前 ETF 轮动研究参数写入实盘配置文件。\n\n"
            "影响范围：实盘策略中心 ETF 轮动后续加载/刷新后会使用这些策略参数。\n"
            "不会修改：自动执行、调度时间、通知开关等实盘运行字段。\n\n"
            f"配置文件：{config_path}\n\n"
            "确认同步吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.status_label.setText("已取消同步到实盘配置。")
            return

        live_config = self.config_mgr.load()
        for key, value in params.to_dict().items():
            if hasattr(live_config, key):
                setattr(live_config, key, value)
        strategy_params = dict(getattr(live_config, "strategy_params", {}) or {})
        for key in ETFRotationParams.field_names():
            strategy_params.pop(key, None)
        live_config.strategy_params = strategy_params
        self.config_mgr.save(live_config)
        self.config = live_config
        self.params = params
        self.pool_input.setText(",".join(params.etf_pool))
        self._refresh_overview()
        self._append_log(f"已同步研究参数到实盘配置：{_model_to_dict(params)}")
        self.status_label.setText("研究参数已同步到实盘配置；实盘策略中心刷新或重启后生效。")

    def _refresh_overview(self) -> None:
        self.params = self.current_params()
        self.holding_label.setText("当前持仓：研究模式不读取实盘持仓")
        signal = "-"
        target = ""
        if isinstance(self.current_research_signal, dict):
            signal = str(self.current_research_signal.get("signal", "") or "-")
            target = str(self.current_research_signal.get("target", "") or "")
        self.decision_label.setText(f"最近研究信号：{signal} {target}".rstrip())
        try:
            audit = get_data_portal().get_data_version(
                self.params.etf_pool,
                asset_type="etf",
                data_dir=self.data_dir,
                scope="etf_rotation",
            )
            self.data_version_label.setText(f"数据版本：{audit.data_version}")
        except Exception as exc:
            self.data_version_label.setText(f"数据版本不可用：{exc}")
        scores = dict(self.current_research_signal.get("scores", {}) or {}) if isinstance(self.current_research_signal, dict) else {}
        if scores:
            self._render_scores(scores)

    def _refresh_data_status(self) -> None:
        symbols = self.selected_pool() or self.params.etf_pool
        name_map = get_data_portal().get_name_map(asset_type="etf")
        try:
            status_map = get_data_portal().get_daily_metadata_map(symbols, asset_type="etf", data_dir=self.data_dir)
        except Exception as exc:
            QMessageBox.warning(self, "数据错误", f"检查 ETF 数据失败：{exc}")
            return
        self.data_status_table.setRowCount(len(status_map))
        for row, (symbol, status) in enumerate(status_map.items()):
            values = [
                symbol,
                name_map.get(symbol, ""),
                status.rows,
                status.first_date or "",
                status.latest_date or "",
                "是" if status.is_fresh else status.reason,
                status.data_version,
                status.data_path,
            ]
            for col, value in enumerate(values):
                self.data_status_table.setItem(row, col, QTableWidgetItem(_format_value(value)))
        self._append_log(f"已刷新 {len(status_map)} 个 ETF 的数据覆盖情况。")

    def _load_data(self) -> None:
        symbols = self.selected_pool() or self.params.etf_pool
        start = self.start_date_edit.date().toString("yyyy-MM-dd")
        end = self.end_date_edit.date().toString("yyyy-MM-dd")
        try:
            bundle = get_data_portal().get_market_data_bundle(
                symbols,
                start=start,
                end=end,
                asset_type="etf",
                data_dir=self.data_dir,
                use_cache=False,
            )
            self.current_data = bundle.to_data_dict()
            if not self.current_data:
                raise ValueError("未加载到 ETF 数据")
            self._render_data_preview(self.current_data)
            self._handle_run_event(_make_event(
                "data_loaded",
                f"ETF轮动数据已加载：{len(self.current_data)} 个标的",
                payload={"symbols": list(self.current_data.keys()), "data_audit": bundle.data_audit},
            ))
        except Exception as exc:
            self.current_data = {}
            self._handle_run_event(_make_event(
                "data_load_failed",
                f"ETF轮动数据加载失败：{exc}",
                payload={"symbols": symbols, "error": str(exc)},
            ))
            QMessageBox.warning(self, "加载错误", f"加载 ETF 数据失败：{exc}")

    def _run_backtest(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "运行中", "ETF轮动回测正在运行。")
            return
        if not self.current_data:
            self._load_data()
        if not self.current_data:
            return

        params = self.current_params()
        pool = self.selected_pool() or params.etf_pool
        params = ETFRotationParams.from_mapping(params.to_dict(), etf_pool=pool)
        data = {symbol: frame for symbol, frame in self.current_data.items() if symbol in params.etf_pool}
        if not data:
            QMessageBox.warning(self, "数据错误", "已加载数据与当前 ETF 池不匹配。")
            return

        self._run_events = []
        self.current_result = None
        self.run_backtest_btn.setEnabled(False)
        self.status_label.setText("正在运行 ETF 轮动回测...")
        self._clear_result_views()
        self._handle_run_event(_make_event(
            "run_requested",
            "已请求运行 ETF 轮动回测",
            payload={"params": params.to_dict(), "symbols": list(data.keys())},
        ))
        self._worker = ETFRotationBacktestWorker(params, data, self.run_event_bus)
        self._worker.finished_signal.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_finished(self) -> None:
        self.run_backtest_btn.setEnabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _generate_research_signal(self) -> None:
        try:
            self._run_events = []
            result = self._build_research_signal_result()
            self.current_research_signal = result
            self._render_research_signal_result(result)
            self._save_research_signal_record(result)
            self.status_label.setText(f"研究信号已生成：{result.get('signal', '') or '无信号'}")
            self.decision_label.setText(f"最近研究信号：{result.get('signal', '') or '-'} {result.get('target', '') or ''}".rstrip())
            self.tabs.setCurrentWidget(self.playback_tab)
        except Exception as exc:
            QMessageBox.warning(self, "生成研究信号错误", str(exc))

    def _build_research_signal_result(self) -> dict[str, Any]:
        params = self.current_params()
        pool = self.selected_pool() or params.etf_pool
        params = ETFRotationParams.from_mapping(params.to_dict(), etf_pool=pool)
        if not self.current_data:
            self._load_data()
        data = {symbol: frame for symbol, frame in self.current_data.items() if symbol in params.etf_pool}
        if not data:
            raise ValueError("当前 ETF 池没有可用的已加载数据")

        from strategy_app.strategies import create_strategy

        strategy = create_strategy("etf_rotation", params=params)
        bars: dict[str, Any] = {}
        prices: dict[str, float] = {}
        current_date = None
        for symbol, frame in data.items():
            if frame is None or frame.empty:
                continue
            latest = frame.sort_values("date").iloc[-1] if "date" in frame.columns else frame.iloc[-1]
            bars[symbol] = latest
            if current_date is None and hasattr(latest, "get"):
                current_date = latest.get("date")
            try:
                prices[symbol] = float(latest.get("close", 0.0) or 0.0)
            except Exception:
                prices[symbol] = 0.0
        if not bars:
            raise ValueError("当前 ETF 池没有可生成信号的最新K线")

        context = SimpleNamespace(
            current_dt=current_date,
            initial_cash=100000.0,
            cash=100000.0,
            positions={},
            current_prices=prices,
        )
        payload = {
            "mode": "research_signal",
            "date": current_date,
            "bars": bars,
            "history": data,
            "prices": prices,
            "valid_symbols": list(data.keys()),
        }
        scores = strategy.score_data_view(data) if hasattr(strategy, "score_data_view") else {}
        signals = list(strategy.generate_signals(payload, context=context) or [])
        signal_payloads = [signal.to_dict() if hasattr(signal, "to_dict") else dict(signal) for signal in signals]
        target_symbols = [str(item.get("symbol", "") or "") for item in signal_payloads if item.get("symbol")]
        actions = [str(item.get("action", "") or "") for item in signal_payloads if item.get("action")]
        reasons = [str(item.get("reason", "") or "") for item in signal_payloads if item.get("reason")]
        try:
            audit = get_data_portal().get_data_version(
                params.etf_pool,
                asset_type="etf",
                data_dir=self.data_dir,
                scope="etf_rotation",
            )
            data_version = audit.data_version
            data_audit = audit.to_dict()
        except Exception:
            data_version = ""
            data_audit = {}
        result = {
            "strategy_id": "etf_rotation",
            "mode": "research_signal",
            "date": _format_value(current_date),
            "data_version": data_version,
            "data_audit": data_audit,
            "params": params.to_dict(),
            "scores": scores,
            "strategy_signals": signal_payloads,
            "signal": ",".join(actions) if actions else "NO_SIGNAL",
            "target": ",".join(target_symbols),
            "reason": "；".join(reasons),
        }
        self._handle_run_event(_make_event(
            "research_signal_generated",
            "ETF轮动研究信号已生成",
            payload={"result": result, "scores": scores},
        ))
        return result

    def _handle_run_event(self, event: BacktestEvent) -> None:
        if event.event_type in _PERSISTED_EVENT_TYPES or self._is_final_progress(event):
            self._run_events.append(event)
        if event.event_type in _SHELL_EVENT_TYPES and self._should_publish_shell_event(event):
            self.shell_event_bus.publish(event)
        self._append_event_to_log(event)
        self._update_from_event(event)

    def _update_from_event(self, event: BacktestEvent) -> None:
        if event.event_type == "run_started":
            self.status_label.setText(event.message)
            return
        if event.event_type == "run_completed":
            self.status_label.setText(event.message)
            return
        if event.event_type in {"run_failed", "rebalance_failed"}:
            self.status_label.setText(event.message)
            return
        if event.event_type == "signal_calculated":
            scores = event.payload.get("scores", {})
            if isinstance(scores, dict):
                self._render_scores(scores)
            return
        if event.event_type == "decision_made":
            self.decision_label.setText(
                f"最近决策：{event.payload.get('signal', '')} {event.payload.get('target', '') or ''}"
            )
            return
        if event.event_type == "result_ready":
            result = event.payload.get("result", {})
            if isinstance(result, dict):
                self.current_result = result
                self._render_backtest_result(result)
                self._save_experiment_record(result)
            return
        if event.event_type == "experiment_saved":
            self.status_label.setText(event.message)

    def _save_experiment_record(self, result: dict[str, Any]) -> None:
        if self.experiment_store is None:
            return
        try:
            params_payload = {
                "params": self.current_params().to_dict(),
                "symbols": self.selected_pool() or self.current_params().etf_pool,
                "start": self.start_date_edit.date().toString("yyyy-MM-dd"),
                "end": self.end_date_edit.date().toString("yyyy-MM-dd"),
            }
            record = self.experiment_store.save(result, events=self._run_events, params=params_payload)
        except Exception as exc:
            self._handle_run_event(_make_event(
                "experiment_save_failed",
                f"ETF轮动实验记录保存失败：{exc}",
                payload={"error": str(exc)},
            ))
            return
        self._handle_run_event(_make_event(
            "experiment_saved",
            f"ETF轮动实验记录已保存：{record.run_id}",
            run_id=record.run_id,
            payload={"path": record.path},
        ))
        if self.on_experiment_saved is not None:
            self.on_experiment_saved()

    def _save_research_signal_record(self, result: dict[str, Any]) -> None:
        if self.experiment_store is None:
            return
        try:
            run_id = ""
            for event in reversed(self._run_events):
                if event.run_id:
                    run_id = event.run_id
                    break
            record_payload = {
                "run_id": run_id,
                "strategy_id": "etf_rotation",
                "mode": "research_signal",
                "data_version": result.get("data_version", ""),
                "data_audit": result.get("data_audit", {}),
                "signal": result.get("signal"),
                "target": result.get("target"),
                "reason": result.get("reason"),
                "scores": result.get("scores", {}),
                "strategy_signals": result.get("strategy_signals", []),
            }
            record = self.experiment_store.save(
                record_payload,
                events=self._run_events,
                params={"params": self.current_params().to_dict(), "mode": "research_signal"},
            )
        except Exception as exc:
            self._handle_run_event(_make_event(
                "experiment_save_failed",
                f"ETF轮动研究信号记录保存失败：{exc}",
                payload={"error": str(exc)},
            ))
            return
        self._handle_run_event(_make_event(
            "experiment_saved",
            f"ETF轮动研究信号记录已保存：{record.run_id}",
            run_id=record.run_id,
            payload={"path": record.path},
        ))
        if self.on_experiment_saved is not None:
            self.on_experiment_saved()

    def _render_scores(self, scores: dict[str, Any]) -> None:
        name_map = get_data_portal().get_name_map(asset_type="etf")
        rows = sorted(scores.items(), key=lambda item: float(item[1] or 0.0), reverse=True)
        self.scores_table.setRowCount(len(rows))
        for row, (symbol, score) in enumerate(rows):
            self.scores_table.setItem(row, 0, QTableWidgetItem(str(symbol)))
            self.scores_table.setItem(row, 1, QTableWidgetItem(name_map.get(str(symbol), "")))
            self.scores_table.setItem(row, 2, QTableWidgetItem(_format_value(score)))

    def _render_data_preview(self, data: dict[str, pd.DataFrame]) -> None:
        rows = []
        for symbol, frame in data.items():
            if frame is None or frame.empty:
                continue
            latest = frame.tail(1).iloc[0]
            rows.append({"symbol": symbol, **latest.to_dict()})
        self.data_preview_table.setColumnCount(0)
        self.data_preview_table.setRowCount(0)
        if not rows:
            return
        columns = list(rows[0].keys())
        self.data_preview_table.setColumnCount(len(columns))
        self.data_preview_table.setHorizontalHeaderLabels(columns)
        self.data_preview_table.setRowCount(len(rows))
        for row_idx, row_data in enumerate(rows):
            for col_idx, column in enumerate(columns):
                self.data_preview_table.setItem(row_idx, col_idx, QTableWidgetItem(_format_value(row_data.get(column, ""))))

    def _render_backtest_result(self, result: dict[str, Any]) -> None:
        payload = _result_payload(result)
        self._render_summary(_summary_from_result(result))
        trades = payload.get("trades", []) or []
        self._render_trades(trades)
        lines = ["ETF轮动回测结果", ""]
        for key, value in _summary_from_result(result).items():
            lines.append(f"{_DISPLAY_LABELS.get(key, key)}：{_format_value(value)}")
        self.result_text.setPlainText("\n".join(lines))
        self.status_label.setText("ETF轮动回测已完成。")
        self.tabs.setCurrentWidget(self.playback_tab)

    def _render_research_signal_result(self, result: dict[str, Any]) -> None:
        summary = {
            "signal": result.get("signal", ""),
            "target": result.get("target", ""),
            "reason": result.get("reason", ""),
            "strategy_signals": len(result.get("strategy_signals", []) or []),
            "data_version": result.get("data_version", ""),
        }
        self._render_summary(summary)
        self._render_trades(result.get("strategy_signals", []) or [])
        lines = [
            "ETF轮动研究信号",
            "",
            f"日期：{result.get('date', '')}",
            f"信号：{result.get('signal', '')}",
            f"目标：{result.get('target', '')}",
            f"原因：{result.get('reason', '')}",
            f"数据版本：{result.get('data_version', '')}",
        ]
        self.result_text.setPlainText("\n".join(lines))

    def _render_summary(self, summary: dict[str, Any]) -> None:
        self.summary_table.setRowCount(len(summary))
        for row, (key, value) in enumerate(summary.items()):
            self.summary_table.setItem(row, 0, QTableWidgetItem(_DISPLAY_LABELS.get(str(key), str(key))))
            self.summary_table.setItem(row, 1, QTableWidgetItem(_format_value(value)))

    def _render_trades(self, trades: Any) -> None:
        rows = trades if isinstance(trades, list) else []
        self.trade_table.setRowCount(len(rows))
        for row, trade in enumerate(rows):
            item = trade if isinstance(trade, dict) else getattr(trade, "to_dict", lambda: {})()
            values = [
                item.get("date", item.get("timestamp", "")),
                item.get("symbol", ""),
                item.get("action", item.get("side", item.get("type", ""))),
                item.get("price", ""),
                item.get("quantity", item.get("target_quantity", "")),
                item.get("amount", ""),
                item.get("reason", ""),
            ]
            for col, value in enumerate(values):
                self.trade_table.setItem(row, col, QTableWidgetItem(_format_value(value)))

    def _clear_result_views(self) -> None:
        self.summary_table.setRowCount(0)
        self.trade_table.setRowCount(0)
        self.result_text.clear()

    def _append_event_to_log(self, event: BacktestEvent) -> None:
        progress = ""
        if event.progress_current is not None and event.progress_total is not None:
            progress = f" [{event.progress_current}/{event.progress_total}]"
        text = event.message or str(event.payload or "")
        self._append_log(f"{event.event_type}{progress}: {text}")

    def _append_log(self, message: str) -> None:
        self.event_log.append(str(message))

    @staticmethod
    def _is_final_progress(event: BacktestEvent) -> bool:
        return (
            event.event_type == "progress"
            and event.progress_current is not None
            and event.progress_total is not None
            and event.progress_current >= event.progress_total
        )

    @staticmethod
    def _should_publish_shell_event(event: BacktestEvent) -> bool:
        if event.event_type != "progress":
            return True
        if event.progress_current is None or event.progress_total is None:
            return False
        return event.progress_current == 1 or event.progress_current >= event.progress_total or event.progress_current % 50 == 0


def create_etf_rotation_tab(
    parent: QWidget | None = None,
    *,
    event_bus: EventBus | None = None,
    experiment_store: ExperimentStore | None = None,
    on_experiment_saved: Callable[[], None] | None = None,
) -> QWidget:
    """Return the native ETF rotation research/live dry-run UI for the shell."""
    _ensure_project_import_path()
    return ETFRotationResearchTab(
        parent,
        event_bus=event_bus,
        experiment_store=experiment_store,
        on_experiment_saved=on_experiment_saved,
    )


__all__ = ["ETFRotationResearchTab", "create_etf_rotation_tab"]
