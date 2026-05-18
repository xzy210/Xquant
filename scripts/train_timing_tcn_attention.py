# -*- coding: utf-8 -*-
"""Train the first offline TCN + Attention timing model."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from strategy_app.timing import (  # noqa: E402
    TimingDatasetConfig,
    TimingFeatureConfig,
    TripleBarrierConfig,
    build_timing_dataset,
    build_timing_features,
    build_triple_barrier_labels,
)
from strategy_app.timing.data_loader import load_timing_bars, normalize_frequency  # noqa: E402
from strategy_app.timing.dataset import describe_labels  # noqa: E402
from strategy_app.timing.model import TCNAttentionConfig  # noqa: E402
from strategy_app.timing.model_store import save_timing_model  # noqa: E402
from strategy_app.timing.trainer import TimingTrainConfig, train_timing_model  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="训练 TCN + Attention 时序三障碍方向模型")
    parser.add_argument("--symbols", nargs="+", required=True, help="标的代码，例如 000001 或 000001.SZ")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"), help="parquet K 线数据目录")
    parser.add_argument("--frequency", default="1d", help="K线周期: 1d/1m/5m/15m/30m/60m/1h")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "models" / "timing" / "tcn_attention"))
    parser.add_argument("--start-date", default="", help="训练开始日期，例如 2024-01-01")
    parser.add_argument("--end-date", default="", help="训练结束日期，例如 2026-05-15")
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--horizon", type=int, default=12)
    parser.add_argument("--momentum-windows", default="3,5,15")
    parser.add_argument("--ma-windows", default="20")
    parser.add_argument("--volatility-window", type=int, default=20)
    parser.add_argument("--up-mult", type=float, default=1.5)
    parser.add_argument("--down-mult", type=float, default=1.0)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--channels", default="64,64,64")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--device", default="auto")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = [_normalize_symbol(symbol) for symbol in args.symbols]
    frequency = normalize_frequency(args.frequency)

    feature_config = TimingFeatureConfig(
        momentum_windows=_parse_int_tuple(args.momentum_windows),
        ma_windows=_parse_int_tuple(args.ma_windows),
        volatility_window=args.volatility_window,
    )
    label_config = TripleBarrierConfig(
        horizon=args.horizon,
        up_mult=args.up_mult,
        down_mult=args.down_mult,
        volatility_window=args.volatility_window,
    )
    dataset_config = TimingDatasetConfig(
        lookback=args.lookback,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
    )
    train_config = TimingTrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        patience=args.patience,
        device=args.device,
    )

    frames: list[pd.DataFrame] = []
    feature_names: list[str] = []
    for symbol in symbols:
        raw = load_timing_bars(
            Path(args.data_dir),
            symbol,
            frequency=frequency,
            start_date=args.start_date,
            end_date=args.end_date,
            auto_fetch=True,
            log_callback=print,
        )
        features, current_feature_names = build_timing_features(raw, feature_config)
        labeled = build_triple_barrier_labels(features, label_config)
        labeled["symbol"] = symbol
        frames.append(labeled)
        if not feature_names:
            feature_names = current_feature_names
        elif feature_names != current_feature_names:
            raise RuntimeError(f"{symbol} 的特征列与前序标的不一致")

    all_data = pd.concat(frames, ignore_index=True)
    dataset = build_timing_dataset(all_data, feature_names, dataset_config)
    model_config = TCNAttentionConfig(
        input_dim=dataset.num_features,
        channels=_parse_int_tuple(args.channels),
    )
    train_result = train_timing_model(dataset, model_config, train_config)
    labels = pd.concat(
        [
            pd.Series(dataset.y_train, name="train"),
            pd.Series(dataset.y_val, name="val"),
            pd.Series(dataset.y_test, name="test"),
        ],
        ignore_index=True,
    ).to_numpy()
    label_distribution = describe_labels(labels)
    data_start, data_end = _data_range(all_data)

    model_dir = save_timing_model(
        output_dir=args.output_dir,
        train_result=train_result,
        scaler=dataset.scaler,
        feature_names=feature_names,
        feature_config=feature_config,
        label_config=label_config,
        dataset_config=dataset_config,
        model_config=model_config,
        train_config=train_config,
        symbols=symbols,
        frequency=frequency,
        data_start=data_start,
        data_end=data_end,
        label_distribution=label_distribution,
    )

    print(f"训练完成，模型已保存: {model_dir}")
    print(f"样本数: train={len(dataset.y_train)}, val={len(dataset.y_val)}, test={len(dataset.y_test)}")
    print(f"测试集准确率: {train_result.metrics['test_accuracy']:.4f}")
    return 0


def _parse_int_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in str(value).split(",") if item.strip())


def _normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    return value.split(".", 1)[0] if "." in value else value


def _data_range(df: pd.DataFrame) -> tuple[str, str]:
    if "date" not in df.columns or df.empty:
        return "", ""
    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    if dates.empty:
        return "", ""
    return dates.min().strftime("%Y-%m-%d"), dates.max().strftime("%Y-%m-%d")


if __name__ == "__main__":
    raise SystemExit(main())
