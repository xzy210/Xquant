from __future__ import annotations

import traceback
from pathlib import Path

import pandas as pd
from PyQt6.QtCore import QDate, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from common.data_portal import get_data_portal
from strategy_app.backtest import BacktestConfig, UnifiedBacktestEngine
from strategy_app.strategies.tcn_attention_timing_strategy import TCNAttentionTimingStrategy
from strategy_app.timing import (
    TimingDatasetConfig,
    TimingFeatureConfig,
    TripleBarrierConfig,
    build_timing_dataset,
    build_timing_features,
    build_triple_barrier_labels,
)
from strategy_app.timing.dataset import describe_labels
from strategy_app.timing.model import TCNAttentionConfig
from strategy_app.timing.model_store import save_timing_model
from strategy_app.timing.trainer import TimingTrainConfig, train_timing_model


class TimingTrainingThread(QThread):
    info_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params

    def run(self) -> None:
        try:
            symbols = self.params["symbols"]
            data_dir = Path(self.params["data_dir"])
            frames = []
            feature_names = []
            feature_config = TimingFeatureConfig(
                momentum_windows=tuple(self.params["momentum_windows"]),
                ma_windows=tuple(self.params["ma_windows"]),
                volatility_window=self.params["volatility_window"],
            )
            label_config = TripleBarrierConfig(
                horizon=self.params["horizon"],
                up_mult=self.params["up_mult"],
                down_mult=self.params["down_mult"],
                volatility_window=self.params["volatility_window"],
            )
            dataset_config = TimingDatasetConfig(
                lookback=self.params["lookback"],
                train_ratio=self.params["train_ratio"],
                val_ratio=self.params["val_ratio"],
            )
            train_config = TimingTrainConfig(
                epochs=self.params["epochs"],
                batch_size=self.params["batch_size"],
                learning_rate=self.params["learning_rate"],
                patience=self.params["patience"],
                device=self.params["device"],
            )

            for index, symbol in enumerate(symbols, start=1):
                self.info_signal.emit(f"加载并处理 {symbol} ({index}/{len(symbols)})")
                raw = _load_symbol_data(data_dir, symbol)
                raw = _filter_dates(raw, self.params["start_date"], self.params["end_date"])
                features, names = build_timing_features(raw, feature_config)
                labeled = build_triple_barrier_labels(features, label_config)
                labeled["symbol"] = symbol
                frames.append(labeled)
                if not feature_names:
                    feature_names = names

            all_data = pd.concat(frames, ignore_index=True)
            self.info_signal.emit("构造滑动窗口样本...")
            dataset = build_timing_dataset(all_data, feature_names, dataset_config)
            self.info_signal.emit(
                f"样本集: train={len(dataset.y_train)}, val={len(dataset.y_val)}, test={len(dataset.y_test)}"
            )

            model_config = TCNAttentionConfig(
                input_dim=dataset.num_features,
                channels=tuple(self.params["channels"]),
            )
            self.info_signal.emit("开始训练 TCN + Attention 模型...")
            result = train_timing_model(dataset, model_config, train_config)
            label_distribution = describe_labels(
                pd.concat(
                    [
                        pd.Series(dataset.y_train),
                        pd.Series(dataset.y_val),
                        pd.Series(dataset.y_test),
                    ],
                    ignore_index=True,
                ).to_numpy()
            )
            model_dir = save_timing_model(
                output_dir=self.params["output_dir"],
                train_result=result,
                scaler=dataset.scaler,
                feature_names=feature_names,
                feature_config=feature_config,
                label_config=label_config,
                dataset_config=dataset_config,
                model_config=model_config,
                train_config=train_config,
                symbols=symbols,
                data_start=self.params["start_date"],
                data_end=self.params["end_date"],
                label_distribution=label_distribution,
            )
            self.finished_signal.emit(str(model_dir))
        except Exception as exc:
            traceback.print_exc()
            self.error_signal.emit(str(exc))


class TimingBacktestThread(QThread):
    info_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, params: dict):
        super().__init__()
        self.params = params

    def run(self) -> None:
        try:
            symbol = self.params["symbol"]
            self.info_signal.emit(f"加载 {symbol} 回测数据...")
            bundle = get_data_portal().get_market_data_bundle(
                [symbol],
                data_dir=self.params["data_dir"],
                start=self.params["start_date"],
                end=self.params["end_date"],
                asset_type="stock",
            )
            if not bundle.data:
                raise ValueError(f"未加载到 {symbol} 的有效数据")

            strategy = TCNAttentionTimingStrategy()
            strategy.set_params(
                {
                    "model_dir": self.params["model_dir"],
                    "device": self.params["device"],
                    "up_threshold": self.params["up_threshold"],
                    "down_threshold": self.params["down_threshold"],
                    "direction_margin": self.params["direction_margin"],
                    "target_percent": self.params["target_percent"],
                }
            )
            engine = UnifiedBacktestEngine(BacktestConfig(initial_cash=self.params["initial_cash"], mode="bar"))
            self.info_signal.emit("运行统一回测引擎...")
            result = engine.run(strategy, bundle, code=symbol, mode="bar")
            self.finished_signal.emit(result)
        except Exception as exc:
            traceback.print_exc()
            self.error_signal.emit(str(exc))


class TimingStrategyWidget(QWidget):
    """时序策略训练与回测研究页。"""

    def __init__(self, data_dir: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.data_dir = data_dir
        self.project_root = Path(__file__).resolve().parents[2]
        self.models_dir = self.project_root / "models" / "timing" / "tcn_attention"
        self.training_thread: TimingTrainingThread | None = None
        self.backtest_thread: TimingBacktestThread | None = None
        self._setup_ui()
        self.refresh_models()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        tabs.addTab(self._build_train_tab(), "训练")
        tabs.addTab(self._build_backtest_tab(), "回测")
        layout.addWidget(tabs, 2)

        self.log_edit = QTextEdit(self)
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit, 1)

    def _build_train_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        form_group = QGroupBox("训练参数", tab)
        form = QFormLayout(form_group)

        self.train_symbols_edit = QLineEdit("000001", form_group)
        self.train_start_edit = _date_edit(QDate(2024, 1, 1), form_group)
        self.train_end_edit = _date_edit(QDate.currentDate(), form_group)
        self.lookback_spin = _spin(2, 1000, 60, form_group)
        self.horizon_spin = _spin(1, 240, 12, form_group)
        self.epochs_spin = _spin(1, 500, 10, form_group)
        self.batch_spin = _spin(8, 2048, 128, form_group)
        self.train_button = QPushButton("开始训练", form_group)
        self.train_button.clicked.connect(self.start_training)

        form.addRow("标的代码", self.train_symbols_edit)
        form.addRow("开始日期", self.train_start_edit)
        form.addRow("结束日期", self.train_end_edit)
        form.addRow("lookback", self.lookback_spin)
        form.addRow("horizon", self.horizon_spin)
        form.addRow("epochs", self.epochs_spin)
        form.addRow("batch size", self.batch_spin)
        form.addRow(self.train_button)
        layout.addWidget(form_group)
        layout.addStretch(1)
        return tab

    def _build_backtest_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        form_group = QGroupBox("回测参数", tab)
        form = QFormLayout(form_group)

        self.model_combo = QComboBox(form_group)
        refresh_btn = QPushButton("刷新模型", form_group)
        refresh_btn.clicked.connect(self.refresh_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(refresh_btn)

        self.backtest_symbol_edit = QLineEdit("000001", form_group)
        self.backtest_start_edit = _date_edit(QDate(2024, 1, 1), form_group)
        self.backtest_end_edit = _date_edit(QDate.currentDate(), form_group)
        self.initial_cash_spin = _double_spin(10000, 100000000, 100000, form_group)
        self.up_threshold_spin = _prob_spin(0.55, form_group)
        self.down_threshold_spin = _prob_spin(0.55, form_group)
        self.margin_spin = _prob_spin(0.15, form_group)
        self.target_percent_spin = _prob_spin(0.5, form_group)
        self.backtest_button = QPushButton("开始回测", form_group)
        self.backtest_button.clicked.connect(self.start_backtest)

        form.addRow("模型版本", model_row)
        form.addRow("标的代码", self.backtest_symbol_edit)
        form.addRow("开始日期", self.backtest_start_edit)
        form.addRow("结束日期", self.backtest_end_edit)
        form.addRow("初始资金", self.initial_cash_spin)
        form.addRow("看多阈值", self.up_threshold_spin)
        form.addRow("看空阈值", self.down_threshold_spin)
        form.addRow("方向差阈值", self.margin_spin)
        form.addRow("目标仓位", self.target_percent_spin)
        form.addRow(self.backtest_button)
        layout.addWidget(form_group)

        self.result_label = QLabel("暂无回测结果", tab)
        layout.addWidget(self.result_label)
        layout.addStretch(1)
        return tab

    def start_training(self) -> None:
        if self.training_thread and self.training_thread.isRunning():
            QMessageBox.warning(self, "训练中", "已有训练任务正在运行")
            return
        params = {
            "symbols": _parse_symbols(self.train_symbols_edit.text()),
            "data_dir": self.data_dir,
            "output_dir": str(self.models_dir),
            "start_date": self.train_start_edit.date().toString("yyyy-MM-dd"),
            "end_date": self.train_end_edit.date().toString("yyyy-MM-dd"),
            "lookback": self.lookback_spin.value(),
            "horizon": self.horizon_spin.value(),
            "epochs": self.epochs_spin.value(),
            "batch_size": self.batch_spin.value(),
            "momentum_windows": (3, 5, 15),
            "ma_windows": (20,),
            "volatility_window": 20,
            "up_mult": 1.5,
            "down_mult": 1.0,
            "train_ratio": 0.7,
            "val_ratio": 0.15,
            "channels": (64, 64, 64),
            "learning_rate": 1e-3,
            "patience": 5,
            "device": "auto",
        }
        if not params["symbols"]:
            QMessageBox.warning(self, "参数错误", "请至少输入一个标的代码")
            return
        self.train_button.setEnabled(False)
        self._log("启动时序策略训练...")
        self.training_thread = TimingTrainingThread(params)
        self.training_thread.info_signal.connect(self._log)
        self.training_thread.finished_signal.connect(self._on_training_finished)
        self.training_thread.error_signal.connect(self._on_training_error)
        self.training_thread.start()

    def start_backtest(self) -> None:
        if self.backtest_thread and self.backtest_thread.isRunning():
            QMessageBox.warning(self, "回测中", "已有回测任务正在运行")
            return
        model_dir = self.model_combo.currentData()
        if not model_dir:
            QMessageBox.warning(self, "缺少模型", "请先训练或选择模型版本")
            return
        params = {
            "symbol": _parse_symbols(self.backtest_symbol_edit.text())[0],
            "data_dir": self.data_dir,
            "model_dir": model_dir,
            "start_date": self.backtest_start_edit.date().toString("yyyy-MM-dd"),
            "end_date": self.backtest_end_edit.date().toString("yyyy-MM-dd"),
            "initial_cash": self.initial_cash_spin.value(),
            "up_threshold": self.up_threshold_spin.value(),
            "down_threshold": self.down_threshold_spin.value(),
            "direction_margin": self.margin_spin.value(),
            "target_percent": self.target_percent_spin.value(),
            "device": "auto",
        }
        self.backtest_button.setEnabled(False)
        self._log(f"启动回测，模型: {model_dir}")
        self.backtest_thread = TimingBacktestThread(params)
        self.backtest_thread.info_signal.connect(self._log)
        self.backtest_thread.finished_signal.connect(self._on_backtest_finished)
        self.backtest_thread.error_signal.connect(self._on_backtest_error)
        self.backtest_thread.start()

    def refresh_models(self) -> None:
        self.model_combo.clear()
        if not self.models_dir.exists():
            return
        for path in sorted(self.models_dir.iterdir(), reverse=True):
            if path.is_dir() and (path / "manifest.json").exists():
                self.model_combo.addItem(path.name, str(path))

    def _on_training_finished(self, model_dir: str) -> None:
        self.train_button.setEnabled(True)
        self._log(f"训练完成: {model_dir}")
        self.refresh_models()

    def _on_training_error(self, message: str) -> None:
        self.train_button.setEnabled(True)
        self._log(f"训练失败: {message}")
        QMessageBox.critical(self, "训练失败", message)

    def _on_backtest_finished(self, result: dict) -> None:
        self.backtest_button.setEnabled(True)
        metrics = result.get("metrics") or {}
        final_value = float(result.get("final_value") or 0.0)
        trades = len(result.get("trades") or [])
        text = f"最终资产: {final_value:.2f} | 交易数: {trades} | 指标: {metrics}"
        self.result_label.setText(text)
        self._log(text)

    def _on_backtest_error(self, message: str) -> None:
        self.backtest_button.setEnabled(True)
        self._log(f"回测失败: {message}")
        QMessageBox.critical(self, "回测失败", message)

    def _log(self, message: str) -> None:
        self.log_edit.append(message)


def _load_symbol_data(data_dir: Path, symbol: str) -> pd.DataFrame:
    normalized = symbol.split(".", 1)[0].upper()
    path = data_dir / f"{normalized}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"未找到数据文件: {path}")
    return pd.read_parquet(path)


def _filter_dates(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if "date" not in df.columns:
        return df
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    return data[(data["date"] >= pd.to_datetime(start_date)) & (data["date"] <= pd.to_datetime(end_date))].reset_index(drop=True)


def _parse_symbols(text: str) -> list[str]:
    return [item.strip().upper().split(".", 1)[0] for item in text.replace("，", ",").split(",") if item.strip()]


def _date_edit(value: QDate, parent: QWidget) -> QDateEdit:
    widget = QDateEdit(value, parent)
    widget.setCalendarPopup(True)
    return widget


def _spin(minimum: int, maximum: int, value: int, parent: QWidget) -> QSpinBox:
    widget = QSpinBox(parent)
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


def _double_spin(minimum: float, maximum: float, value: float, parent: QWidget) -> QDoubleSpinBox:
    widget = QDoubleSpinBox(parent)
    widget.setRange(minimum, maximum)
    widget.setDecimals(2)
    widget.setValue(value)
    return widget


def _prob_spin(value: float, parent: QWidget) -> QDoubleSpinBox:
    widget = QDoubleSpinBox(parent)
    widget.setRange(0.0, 1.0)
    widget.setSingleStep(0.01)
    widget.setDecimals(2)
    widget.setValue(value)
    return widget
