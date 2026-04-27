# -*- coding: utf-8 -*-
"""Native ETF grid strategy tab for the new application shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
from pydantic import BaseModel, Field, validator
from PyQt6.QtCore import QDate, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
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


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STRATEGY_APP_DIR = _PROJECT_ROOT / "strategy_app"
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
}
_SHELL_EVENT_TYPES = _PERSISTED_EVENT_TYPES | {"progress"}


try:
    from xtquant import xtdata as _xtdata  # noqa: F401

    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False

try:
    from scripts.fetch_kline_xtquant import check_connection, fetch_etf_kline
except ImportError:
    check_connection = None
    fetch_etf_kline = None


def _ensure_strategy_import_path() -> None:
    root_path = str(_PROJECT_ROOT)
    strategy_path = str(_STRATEGY_APP_DIR)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    if strategy_path not in sys.path:
        sys.path.insert(0, strategy_path)


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
        payload=payload or {},
    )


def _model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class ETFGridParams(BaseModel):
    """Validated ETF grid strategy parameters used by the native shell."""

    initial_capital: float = Field(100000.0, ge=10000.0, le=10000000.0, title="Initial Capital")
    grid_count: int = Field(10, ge=3, le=30, title="Grid Count Per Side")
    grid_spacing_pct: float = Field(2.0, ge=0.5, le=10.0, title="Grid Spacing %")
    grid_type: Literal["geometric", "arithmetic"] = Field("geometric", title="Grid Type")
    position_per_grid_pct: float = Field(10.0, ge=1.0, le=30.0, title="Position Per Grid %")
    max_position_ratio_pct: float = Field(80.0, ge=10.0, le=100.0, title="Max Position %")
    min_trade_amount: int = Field(100, ge=1, le=1000000, title="Min Trade Amount")
    use_atr_adaptive: bool = Field(True, title="Use ATR Adaptive Grid")
    atr_period: int = Field(14, ge=5, le=50, title="ATR Period")
    atr_multiplier: float = Field(1.5, ge=0.5, le=5.0, title="ATR Multiplier")
    stop_loss_ratio_pct: float = Field(15.0, ge=1.0, le=50.0, title="Stop Loss %")
    take_profit_ratio_pct: float = Field(30.0, ge=5.0, le=100.0, title="Take Profit %")
    rebalance_threshold_pct: float = Field(10.0, ge=5.0, le=50.0, title="Rebalance Threshold %")
    commission_rate_pct: float = Field(0.03, ge=0.0, le=1.0, title="Commission Rate %")
    min_commission: float = Field(5.0, ge=0.0, le=1000.0, title="Min Commission")
    slippage_pct: float = Field(0.1, ge=0.0, le=5.0, title="Slippage %")

    @validator("min_trade_amount")
    def _validate_trade_lot(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("min trade amount must be positive")
        return value

    def to_grid_config(self):
        _ensure_strategy_import_path()
        from strategy_app.strategies.etf_grid_strategy import GridConfig, GridType

        return GridConfig(
            initial_capital=self.initial_capital,
            grid_count=self.grid_count,
            grid_spacing=self.grid_spacing_pct / 100.0,
            grid_type=GridType.GEOMETRIC if self.grid_type == "geometric" else GridType.ARITHMETIC,
            position_per_grid=self.position_per_grid_pct / 100.0,
            max_position_ratio=self.max_position_ratio_pct / 100.0,
            min_trade_amount=self.min_trade_amount,
            use_atr_adaptive=self.use_atr_adaptive,
            atr_period=self.atr_period,
            atr_multiplier=self.atr_multiplier,
            stop_loss_ratio=self.stop_loss_ratio_pct / 100.0,
            take_profit_ratio=self.take_profit_ratio_pct / 100.0,
            rebalance_threshold=self.rebalance_threshold_pct / 100.0,
            commission_rate=self.commission_rate_pct / 100.0,
            min_commission=self.min_commission,
            slippage=self.slippage_pct / 100.0,
        )

    @classmethod
    def from_grid_config(cls, config: Any) -> "ETFGridParams":
        grid_type = getattr(getattr(config, "grid_type", None), "value", "geometric")
        return cls(
            initial_capital=float(getattr(config, "initial_capital", 100000.0)),
            grid_count=int(getattr(config, "grid_count", 10)),
            grid_spacing_pct=float(getattr(config, "grid_spacing", 0.02)) * 100.0,
            grid_type="arithmetic" if grid_type == "arithmetic" else "geometric",
            position_per_grid_pct=float(getattr(config, "position_per_grid", 0.1)) * 100.0,
            max_position_ratio_pct=float(getattr(config, "max_position_ratio", 0.8)) * 100.0,
            min_trade_amount=int(getattr(config, "min_trade_amount", 100)),
            use_atr_adaptive=bool(getattr(config, "use_atr_adaptive", True)),
            atr_period=int(getattr(config, "atr_period", 14)),
            atr_multiplier=float(getattr(config, "atr_multiplier", 1.5)),
            stop_loss_ratio_pct=float(getattr(config, "stop_loss_ratio", 0.15)) * 100.0,
            take_profit_ratio_pct=float(getattr(config, "take_profit_ratio", 0.3)) * 100.0,
            rebalance_threshold_pct=float(getattr(config, "rebalance_threshold", 0.1)) * 100.0,
            commission_rate_pct=float(getattr(config, "commission_rate", 0.0003)) * 100.0,
            min_commission=float(getattr(config, "min_commission", 5.0)),
            slippage_pct=float(getattr(config, "slippage", 0.001)) * 100.0,
        )


def run_etf_grid_backtest(
    params: ETFGridParams,
    data: pd.DataFrame,
    *,
    code: str = "ETF_GRID",
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Run ETF grid strategy through UnifiedBacktestEngine with EventBus events."""
    _ensure_strategy_import_path()

    from strategy_app.backtest import (
        BacktestConfig,
        FeeModelConfig,
        OrderMatcher,
        OrderMatcherConfig,
        SimulationBroker,
        UnifiedBacktestEngine,
    )
    from strategy_app.strategies.etf_grid_strategy import ETFGridStrategy

    if data is None or data.empty or len(data) < 2:
        raise ValueError("Insufficient data for ETF grid backtest")

    config = params.to_grid_config()
    strategy = ETFGridStrategy(config)
    broker = SimulationBroker(
        matcher=OrderMatcher(
            OrderMatcherConfig(
                slippage_pct=config.slippage,
                min_lot=config.min_trade_amount,
                enforce_volume_limit=False,
                enforce_bar_price_range=False,
                enforce_price_limit=False,
            )
        ),
        fee_config=FeeModelConfig(
            buy_commission_rate=config.commission_rate,
            sell_commission_rate=config.commission_rate,
            min_commission=config.min_commission,
        ),
    )
    engine = UnifiedBacktestEngine(
        BacktestConfig(initial_cash=config.initial_capital, mode="bar"),
        broker=broker,
        bus=event_bus or EventBus(),
    )
    return engine.run(strategy, data.copy(), code=code or "ETF_GRID", mode="bar")


class ETFGridBacktestWorker(QThread):
    """Run ETF grid backtest outside the UI thread."""

    finished_signal = pyqtSignal()

    def __init__(self, params: ETFGridParams, data: pd.DataFrame, code: str, event_bus: EventBus) -> None:
        super().__init__()
        self.params = params
        self.data = data.copy()
        self.code = code or "ETF_GRID"
        self.event_bus = event_bus

    def run(self) -> None:
        try:
            result = run_etf_grid_backtest(self.params, self.data, code=self.code, event_bus=self.event_bus)
            run_id = str(result.get("run_id", "") or "")
            self.event_bus.publish(_make_event(
                "result_ready",
                "ETF grid result ready",
                run_id=run_id,
                payload={"strategy_id": "etf_grid", "code": self.code, "result": result},
            ))
        except Exception as exc:
            self.event_bus.publish(_make_event(
                "run_failed",
                f"ETF grid backtest failed: {exc}",
                payload={"strategy_id": "etf_grid", "code": self.code, "error": str(exc)},
            ))
        finally:
            self.finished_signal.emit()


class ETFGridResearchTab(QWidget):
    """Native shell ETF grid research workspace."""

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
        self.setObjectName("native_etf_grid_tab")
        self.shell_event_bus = event_bus or EventBus()
        self.run_event_bus = EventBus()
        self.experiment_store = experiment_store
        self.on_experiment_saved = on_experiment_saved
        self.data_dir = str(_PROJECT_ROOT / "data")
        self.current_data: pd.DataFrame | None = None
        self.current_result: dict[str, Any] | None = None
        self.current_code = ""
        self.etf_name_map: dict[str, str] = {}
        self.full_etf_list: list[tuple[str, str, str]] = []
        self._run_events: list[BacktestEvent] = []
        self._worker: ETFGridBacktestWorker | None = None
        self._unsubscribe_run_bus = self.run_event_bus.subscribe(lambda event: self.eventReceived.emit(event))

        self.eventReceived.connect(self._handle_run_event)
        self._build_ui()
        self._load_etf_list()

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
        self.tabs.add_workspace_tab(self.playback_tab, "回放", closable=False)
        self.tabs.add_workspace_tab(self.logs_tab, "日志", closable=False)
        self.tabs.setCurrentWidget(self.overview_tab)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.tabs)

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        title = QLabel("ETF Grid Research", tab)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.run_status_label = QLabel("请选择 ETF、加载数据，然后运行回测。", tab)
        self.run_status_label.setProperty("class", "description")

        self.progress_bar = QProgressBar(tab)
        self.progress_bar.setVisible(False)

        self.summary_table = QTableWidget(tab)
        self.summary_table.setColumnCount(2)
        self.summary_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.summary_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.summary_table.verticalHeader().setVisible(False)

        self.result_text = QTextEdit(tab)
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Backtest summary will appear here.")

        layout.addWidget(title)
        layout.addWidget(self.run_status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.summary_table, 1)
        layout.addWidget(self.result_text, 1)
        return tab

    def _build_parameters_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        self.params_form = FormBuilder(ETFGridParams, ETFGridParams(), tab, run_button_text="Apply Parameters")
        self.params_form.run_button.clicked.connect(self._apply_parameters)

        defaults_group = QGroupBox("Preset", tab)
        defaults_layout = QHBoxLayout(defaults_group)
        self.preset_combo = QComboBox(defaults_group)
        self.preset_combo.addItem("Broad Market", "broad_market")
        self.preset_combo.addItem("Sector", "sector")
        self.preset_combo.addItem("Commodity", "commodity")
        self.preset_combo.addItem("Bond", "bond")
        apply_preset_btn = QPushButton("Load Preset", defaults_group)
        apply_preset_btn.clicked.connect(self._load_selected_preset)
        defaults_layout.addWidget(QLabel("ETF Type:", defaults_group))
        defaults_layout.addWidget(self.preset_combo, 1)
        defaults_layout.addWidget(apply_preset_btn)

        layout.addWidget(defaults_group)
        layout.addWidget(self.params_form, 1)
        return tab

    def _build_data_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal, tab)

        control_panel = QWidget(splitter)
        control_layout = QVBoxLayout(control_panel)

        etf_group = QGroupBox("ETF Selection", control_panel)
        etf_layout = QFormLayout(etf_group)
        self.etf_search_input = QLineEdit(etf_group)
        self.etf_search_input.setPlaceholderText("Search by code or name...")
        self.etf_search_input.textChanged.connect(self._filter_etf_list)
        self.etf_combo = QComboBox(etf_group)
        self.etf_combo.currentIndexChanged.connect(self._on_etf_changed)
        etf_layout.addRow("Search:", self.etf_search_input)
        etf_layout.addRow("ETF:", self.etf_combo)

        data_group = QGroupBox("Data Window", control_panel)
        data_layout = QFormLayout(data_group)
        self.period_combo = QComboBox(data_group)
        self.period_combo.addItem("1 minute", "1m")
        self.period_combo.addItem("5 minutes", "5m")
        self.start_date_edit = QDateEdit(data_group)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.currentDate().addDays(-300))
        self.end_date_edit = QDateEdit(data_group)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate())
        self.data_status_label = QLabel("Data not loaded", data_group)
        self.data_status_label.setWordWrap(True)
        self.load_data_btn = QPushButton("Load Minute Data", data_group)
        self.load_data_btn.clicked.connect(self._load_minute_data)
        data_layout.addRow("Period:", self.period_combo)
        data_layout.addRow("Start:", self.start_date_edit)
        data_layout.addRow("End:", self.end_date_edit)
        data_layout.addRow(self.load_data_btn)
        data_layout.addRow("Status:", self.data_status_label)

        run_group = QGroupBox("Run", control_panel)
        run_layout = QVBoxLayout(run_group)
        self.run_btn = QPushButton("Run ETF Grid Backtest", run_group)
        self.run_btn.setProperty("class", "primary")
        self.run_btn.clicked.connect(self._run_backtest)
        self.reset_btn = QPushButton("Reset Parameters", run_group)
        self.reset_btn.clicked.connect(self._load_selected_preset)
        run_layout.addWidget(self.run_btn)
        run_layout.addWidget(self.reset_btn)

        control_layout.addWidget(etf_group)
        control_layout.addWidget(data_group)
        control_layout.addWidget(run_group)
        control_layout.addStretch(1)

        preview_panel = QWidget(splitter)
        preview_layout = QVBoxLayout(preview_panel)
        self.data_preview_table = QTableWidget(preview_panel)
        self.data_preview_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.data_preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        preview_layout.addWidget(QLabel("Data Preview", preview_panel))
        preview_layout.addWidget(self.data_preview_table)

        splitter.addWidget(control_panel)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        return tab

    def _build_playback_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Vertical, tab)

        self.trade_table = QTableWidget(splitter)
        self.trade_table.setColumnCount(8)
        self.trade_table.setHorizontalHeaderLabels(["Date", "Type", "Price", "Quantity", "Amount", "Commission", "Grid", "Reason"])
        self.trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        lower_panel = QWidget(splitter)
        lower_layout = QHBoxLayout(lower_panel)
        self.daily_stats_table = QTableWidget(lower_panel)
        self.daily_stats_table.setColumnCount(7)
        self.daily_stats_table.setHorizontalHeaderLabels(["Time", "Price", "Position", "Position Value", "Total Value", "Return %", "Position %"])
        self.daily_stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.grid_info_text = QTextEdit(lower_panel)
        self.grid_info_text.setReadOnly(True)
        self.grid_info_text.setPlaceholderText("Grid levels and replay information will appear here.")
        lower_layout.addWidget(self.daily_stats_table, 2)
        lower_layout.addWidget(self.grid_info_text, 1)

        splitter.addWidget(self.trade_table)
        splitter.addWidget(lower_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        return tab

    def _build_logs_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.event_log = QTextEdit(tab)
        self.event_log.setReadOnly(True)
        self.event_log.setPlaceholderText("EventBus messages will appear here.")
        layout.addWidget(self.event_log)
        return tab

    def _load_etf_list(self) -> None:
        try:
            portal = get_data_portal()
            self.etf_name_map = portal.get_name_map(asset_type="etf")
            etf_codes = portal.list_symbols(asset_type="etf", data_dir=self.data_dir)
        except Exception as exc:
            self._append_log(f"Failed to load ETF list from DataPortal: {exc}")
            self.etf_name_map = {}
            etf_codes = []

        self.full_etf_list = []
        for code in etf_codes:
            name = self.etf_name_map.get(code, code)
            self.full_etf_list.append((code, name, f"{code} {name}"))

        if not self.full_etf_list:
            self.full_etf_list = [
                ("510300", "CSI 300 ETF", "510300 CSI 300 ETF"),
                ("510500", "CSI 500 ETF", "510500 CSI 500 ETF"),
                ("159915", "ChiNext ETF", "159915 ChiNext ETF"),
            ]
        self._refresh_etf_combo()

    def _refresh_etf_combo(self, search_text: str = "") -> None:
        current_code = self.etf_combo.currentData()
        term = search_text.strip().lower()
        self.etf_combo.blockSignals(True)
        self.etf_combo.clear()
        for code, name, display in self.full_etf_list:
            if term and term not in code.lower() and term not in name.lower() and term not in display.lower():
                continue
            self.etf_combo.addItem(display, code)
        if current_code:
            for index in range(self.etf_combo.count()):
                if self.etf_combo.itemData(index) == current_code:
                    self.etf_combo.setCurrentIndex(index)
                    break
        self.etf_combo.blockSignals(False)
        self._on_etf_changed(self.etf_combo.currentIndex())

    def _filter_etf_list(self, search_text: str) -> None:
        self._refresh_etf_combo(search_text)

    def _on_etf_changed(self, _index: int) -> None:
        code = self.selected_code()
        if not code:
            return
        if code != self.current_code:
            self.current_data = None
            self.current_result = None
            self.current_code = code
            self.data_status_label.setText(f"Selected {code}; load minute data before running backtest.")
            self._clear_data_preview()

    def _load_selected_preset(self) -> None:
        _ensure_strategy_import_path()
        from strategy_app.strategies.etf_grid_strategy import create_default_etf_config

        preset = str(self.preset_combo.currentData() or "broad_market")
        self.params_form.set_values(ETFGridParams.from_grid_config(create_default_etf_config(preset)))
        self._append_log(f"Loaded ETF grid preset: {preset}")

    def _apply_parameters(self) -> None:
        params = self.current_params()
        self._append_log(f"Applied parameters: {_model_to_dict(params)}")

    def _load_minute_data(self) -> None:
        code = self.selected_code()
        if not code:
            QMessageBox.warning(self, "Error", "Please select an ETF first.")
            return
        if not HAS_XTQUANT:
            QMessageBox.warning(self, "Error", "xtquant is not installed; miniQMT minute data cannot be loaded.")
            return
        if fetch_etf_kline is None:
            QMessageBox.warning(self, "Error", "Cannot import fetch_etf_kline from scripts.fetch_kline_xtquant.")
            return
        if check_connection is not None:
            connected, message = check_connection()
            if not connected:
                QMessageBox.warning(self, "Connection Error", f"miniQMT connection failed: {message}")
                return

        start = self.start_date_edit.date().toString("yyyyMMdd")
        end = self.end_date_edit.date().toString("yyyyMMdd")
        period = str(self.period_combo.currentData() or "1m")
        period_text = self.period_combo.currentText()

        self.load_data_btn.setEnabled(False)
        self.data_status_label.setText(f"Loading {period_text} data for {code}...")
        QApplication.processEvents()

        try:
            data = fetch_etf_kline(code, start, end, period)
            if data is None or data.empty:
                raise ValueError(f"No {period_text} data returned for {code}")
            self.current_data = data
            self.current_result = None
            time_range = self._format_data_time_range(data)
            self.data_status_label.setText(f"Loaded {len(data)} rows. {time_range}")
            self._render_data_preview(data)
            event = _make_event(
                "data_loaded",
                f"ETF grid data loaded: {code} {period_text} {len(data)} rows",
                payload={"strategy_id": "etf_grid", "code": code, "rows": len(data), "period": period},
            )
            self._handle_run_event(event)
        except Exception as exc:
            self.data_status_label.setText(f"Load failed: {exc}")
            event = _make_event(
                "data_load_failed",
                f"ETF grid data load failed: {exc}",
                payload={"strategy_id": "etf_grid", "code": code, "error": str(exc)},
            )
            self._handle_run_event(event)
            QMessageBox.warning(self, "Load Error", f"Failed to load data: {exc}")
        finally:
            self.load_data_btn.setEnabled(True)

    def _run_backtest(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Running", "ETF grid backtest is already running.")
            return
        code = self.selected_code()
        if not code:
            QMessageBox.warning(self, "Error", "Please select an ETF first.")
            return
        if self.current_data is None or self.current_data.empty:
            QMessageBox.warning(self, "Error", f"Please load minute data for {code} before running ETF grid backtest.")
            return

        params = self.current_params()
        self._run_events = []
        self.current_result = None
        self.run_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.run_status_label.setText(f"Running ETF grid backtest for {code}...")
        self._clear_result_views()
        request_event = _make_event(
            "run_requested",
            "ETF grid backtest requested",
            payload={"strategy_id": "etf_grid", "code": code, "params": _model_to_dict(params)},
        )
        self._handle_run_event(request_event)

        self._worker = ETFGridBacktestWorker(params, self.current_data, code, self.run_event_bus)
        self._worker.finished_signal.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _handle_run_event(self, event: BacktestEvent) -> None:
        if event.event_type in _PERSISTED_EVENT_TYPES or self._is_final_progress(event):
            self._run_events.append(event)
        if event.event_type in _SHELL_EVENT_TYPES and self._should_publish_shell_event(event):
            self.shell_event_bus.publish(event)
        self._append_event_to_log(event)
        self._update_from_event(event)

    def _update_from_event(self, event: BacktestEvent) -> None:
        if event.event_type == "progress" and event.progress_current is not None and event.progress_total:
            progress = int(event.progress_current / event.progress_total * 100)
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(progress)
            self.run_status_label.setText(f"Backtest progress: {event.progress_current}/{event.progress_total}")
            return
        if event.event_type == "run_started":
            self.run_status_label.setText("ETF grid backtest started.")
            return
        if event.event_type == "run_completed":
            self.run_status_label.setText("ETF grid backtest completed; preparing result view.")
            return
        if event.event_type == "run_failed":
            self.run_status_label.setText(event.message)
            self.progress_bar.setVisible(False)
            return
        if event.event_type == "result_ready":
            result = event.payload.get("result", {})
            if isinstance(result, dict):
                self.current_result = result
                self._render_result(result)
                self._save_experiment_record(result)
            return
        if event.event_type == "experiment_saved":
            self.run_status_label.setText(event.message)

    def _save_experiment_record(self, result: dict[str, Any]) -> None:
        if self.experiment_store is None:
            return
        try:
            params_payload = {
                "params": _model_to_dict(self.current_params()),
                "code": self.selected_code(),
                "period": self.period_combo.currentData(),
                "start": self.start_date_edit.date().toString("yyyy-MM-dd"),
                "end": self.end_date_edit.date().toString("yyyy-MM-dd"),
            }
            record = self.experiment_store.save(result, events=self._run_events, params=params_payload)
        except Exception as exc:
            self._handle_run_event(_make_event(
                "experiment_save_failed",
                f"ETF grid experiment save failed: {exc}",
                payload={"strategy_id": "etf_grid", "error": str(exc)},
            ))
            return

        self._handle_run_event(_make_event(
            "experiment_saved",
            f"ETF grid experiment saved: {record.run_id}",
            run_id=record.run_id,
            payload={"strategy_id": "etf_grid", "path": record.path},
        ))
        if self.on_experiment_saved is not None:
            self.on_experiment_saved()

    def selected_code(self) -> str:
        return str(self.etf_combo.currentData() or "")

    def current_params(self) -> ETFGridParams:
        model = self.params_form.model()
        if isinstance(model, ETFGridParams):
            return model
        return ETFGridParams(**_model_to_dict(model))

    def _render_result(self, result: dict[str, Any]) -> None:
        summary = result.get("summary", {}) if isinstance(result.get("summary", {}), dict) else {}
        self._render_summary(summary)
        self._render_result_text(result)
        self._render_trade_history(result.get("trade_history", []))
        self._render_daily_stats(result.get("daily_stats", []))
        self._render_grid_info(result)
        self.run_status_label.setText("ETF grid backtest completed.")
        self.tabs.setCurrentWidget(self.overview_tab)

    def _render_summary(self, summary: dict[str, Any]) -> None:
        preferred_keys = [
            "total_return",
            "total_profit",
            "realized_profit",
            "unrealized_profit",
            "max_drawdown",
            "win_rate",
            "total_trades",
            "position_ratio",
            "total_value",
            "current_position",
            "base_price",
            "grid_spacing",
        ]
        rows = [(key, summary.get(key, "")) for key in preferred_keys if key in summary]
        self.summary_table.setRowCount(len(rows))
        for row, (key, value) in enumerate(rows):
            self.summary_table.setItem(row, 0, QTableWidgetItem(key))
            self.summary_table.setItem(row, 1, QTableWidgetItem(self._format_value(value)))

    def _render_result_text(self, result: dict[str, Any]) -> None:
        summary = result.get("summary", {})
        lines = ["ETF Grid Backtest Result", ""]
        for key, value in sorted(summary.items() if isinstance(summary, dict) else []):
            lines.append(f"{key}: {self._format_value(value)}")
        lines.extend([
            "",
            f"run_id: {result.get('run_id', '')}",
            f"strategy_id: {result.get('strategy_id', '')}",
            f"params_hash: {result.get('params_hash', '')}",
            f"final_value: {self._format_value(result.get('final_value', ''))}",
        ])
        self.result_text.setPlainText("\n".join(lines))

    def _render_trade_history(self, trades: Any) -> None:
        rows = trades if isinstance(trades, list) else []
        self.trade_table.setRowCount(0)
        for trade in rows:
            if not isinstance(trade, dict) or trade.get("type") not in {"buy", "sell", "rebalance"}:
                continue
            row = self.trade_table.rowCount()
            self.trade_table.insertRow(row)
            values = [
                trade.get("date", ""),
                trade.get("type", ""),
                trade.get("price", ""),
                trade.get("quantity", ""),
                trade.get("amount", ""),
                trade.get("commission", ""),
                trade.get("grid_level", ""),
                trade.get("reason", ""),
            ]
            for col, value in enumerate(values):
                self.trade_table.setItem(row, col, QTableWidgetItem(self._format_value(value)))

    def _render_daily_stats(self, stats: Any) -> None:
        rows = stats[-200:] if isinstance(stats, list) else []
        self.daily_stats_table.setRowCount(len(rows))
        for row, stat in enumerate(rows):
            values = [
                stat.get("date", ""),
                stat.get("price", ""),
                stat.get("current_position", ""),
                stat.get("position_value", ""),
                stat.get("total_value", ""),
                stat.get("total_return", ""),
                stat.get("position_ratio", ""),
            ] if isinstance(stat, dict) else []
            for col, value in enumerate(values):
                self.daily_stats_table.setItem(row, col, QTableWidgetItem(self._format_value(value)))

    def _render_grid_info(self, result: dict[str, Any]) -> None:
        trades = result.get("trade_history", [])
        last_snapshot = []
        if isinstance(trades, list):
            for trade in reversed(trades):
                if isinstance(trade, dict) and trade.get("grids_snapshot"):
                    last_snapshot = trade.get("grids_snapshot") or []
                    break
        lines = ["Grid Snapshot", ""]
        for grid in last_snapshot:
            if isinstance(grid, dict):
                lines.append(
                    f"Level {grid.get('level', ''):>3}: "
                    f"price={self._format_value(grid.get('price', ''))}, "
                    f"quantity={self._format_value(grid.get('quantity', ''))}"
                )
        if not last_snapshot:
            config = result.get("config", {}) if isinstance(result.get("config", {}), dict) else {}
            for key, value in sorted(config.items()):
                lines.append(f"{key}: {self._format_value(value)}")
        self.grid_info_text.setPlainText("\n".join(lines))

    def _render_data_preview(self, data: pd.DataFrame) -> None:
        preview = data.tail(200).copy()
        self.data_preview_table.setColumnCount(len(preview.columns))
        self.data_preview_table.setHorizontalHeaderLabels([str(col) for col in preview.columns])
        self.data_preview_table.setRowCount(len(preview))
        for row_idx, (_, row) in enumerate(preview.iterrows()):
            for col_idx, value in enumerate(row.tolist()):
                self.data_preview_table.setItem(row_idx, col_idx, QTableWidgetItem(self._format_value(value)))

    def _clear_data_preview(self) -> None:
        self.data_preview_table.setRowCount(0)
        self.data_preview_table.setColumnCount(0)

    def _clear_result_views(self) -> None:
        self.summary_table.setRowCount(0)
        self.result_text.clear()
        self.trade_table.setRowCount(0)
        self.daily_stats_table.setRowCount(0)
        self.grid_info_text.clear()

    def _append_event_to_log(self, event: BacktestEvent) -> None:
        progress = ""
        if event.progress_current is not None and event.progress_total is not None:
            progress = f" [{event.progress_current}/{event.progress_total}]"
        text = event.message or str(event.payload or "")
        self._append_log(f"{event.event_type}{progress}: {text}")

    def _append_log(self, message: str) -> None:
        self.event_log.append(message)

    @staticmethod
    def _format_data_time_range(data: pd.DataFrame) -> str:
        time_col = "time" if "time" in data.columns else "date" if "date" in data.columns else ""
        if not time_col:
            return "Time range unavailable"
        min_time = data[time_col].min()
        max_time = data[time_col].max()
        if hasattr(min_time, "strftime"):
            return f"{min_time.strftime('%Y-%m-%d %H:%M')} ~ {max_time.strftime('%Y-%m-%d %H:%M')}"
        return f"{min_time} ~ {max_time}"

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:,.4f}" if abs(value) < 1000 else f"{value:,.2f}"
        if pd.isna(value) if not isinstance(value, (list, tuple, dict, str)) else False:
            return ""
        return str(value)

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


def create_etf_grid_tab(
    parent: QWidget | None = None,
    *,
    event_bus: EventBus | None = None,
    experiment_store: ExperimentStore | None = None,
    on_experiment_saved: Callable[[], None] | None = None,
) -> QWidget:
    """Return the native ETF grid research UI for the shell."""
    _ensure_strategy_import_path()
    return ETFGridResearchTab(
        parent,
        event_bus=event_bus,
        experiment_store=experiment_store,
        on_experiment_saved=on_experiment_saved,
    )


__all__ = ["ETFGridParams", "ETFGridResearchTab", "create_etf_grid_tab", "run_etf_grid_backtest"]
