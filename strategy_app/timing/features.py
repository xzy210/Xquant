from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, List

import numpy as np
import pandas as pd

REQUIRED_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class TimingFeatureConfig:
    """时序策略特征配置。"""

    momentum_windows: tuple[int, ...] = (3, 5, 15)
    ma_windows: tuple[int, ...] = (20,)
    rsi_window: int = 14
    volatility_window: int = 20
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    include_amount: bool = True
    extra_feature_columns: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return asdict(self)


def build_timing_features(
    df: pd.DataFrame,
    config: TimingFeatureConfig | None = None,
) -> tuple[pd.DataFrame, List[str]]:
    """生成 TCN 时序模型使用的特征表。

    返回值包含完整 DataFrame 和严格排序的特征列名。调用方应保存该列名，
    推理时按同一顺序取值，避免训练/推理列顺序漂移。
    """

    cfg = config or TimingFeatureConfig()
    data = _normalize_bars(df)
    feature_names: list[str] = []

    close = data["close"]
    open_ = data["open"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]

    data["return_1"] = close.pct_change()
    data["log_return_1"] = np.log(close / close.shift(1))
    data["amplitude"] = (high - low) / close.replace(0, np.nan)
    data["body_ratio"] = (close - open_) / open_.replace(0, np.nan)
    data["upper_shadow_ratio"] = (high - np.maximum(open_, close)) / close.replace(0, np.nan)
    data["lower_shadow_ratio"] = (np.minimum(open_, close) - low) / close.replace(0, np.nan)
    data["volume_change"] = volume.pct_change()
    feature_names.extend(
        [
            "return_1",
            "log_return_1",
            "amplitude",
            "body_ratio",
            "upper_shadow_ratio",
            "lower_shadow_ratio",
            "volume_change",
        ]
    )

    if cfg.include_amount and "amount" in data.columns:
        data["amount"] = pd.to_numeric(data["amount"], errors="coerce")
        data["amount_change"] = data["amount"].pct_change()
        feature_names.append("amount_change")

    for window in _positive_windows(cfg.momentum_windows):
        name = f"mom{window}"
        data[name] = close.pct_change(window)
        feature_names.append(name)

    for window in _positive_windows(cfg.ma_windows):
        ma_name = f"ma{window}"
        distance_name = f"ma{window}_distance"
        slope_name = f"ma{window}_slope"
        data[ma_name] = close.rolling(window, min_periods=window).mean()
        data[distance_name] = close / data[ma_name].replace(0, np.nan) - 1
        data[slope_name] = data[ma_name].pct_change()
        feature_names.extend([distance_name, slope_name])

    dif, dea, hist = _compute_macd(
        close,
        fast=cfg.macd_fast,
        slow=cfg.macd_slow,
        signal=cfg.macd_signal,
    )
    data["macd_dif"] = dif
    data["macd_dea"] = dea
    data["macd_hist"] = hist
    feature_names.extend(["macd_dif", "macd_dea", "macd_hist"])

    data[f"rsi{cfg.rsi_window}"] = _compute_rsi(close, cfg.rsi_window)
    data[f"volatility{cfg.volatility_window}"] = close.pct_change().rolling(
        cfg.volatility_window,
        min_periods=cfg.volatility_window,
    ).std()
    feature_names.extend([f"rsi{cfg.rsi_window}", f"volatility{cfg.volatility_window}"])

    for column in cfg.extra_feature_columns:
        if column in data.columns and column not in feature_names:
            data[column] = pd.to_numeric(data[column], errors="coerce")
            feature_names.append(column)

    data[feature_names] = data[feature_names].replace([np.inf, -np.inf], np.nan)
    return data, feature_names


def _normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("K 线数据为空，无法生成时序特征")

    missing = [column for column in REQUIRED_OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"K 线数据缺少必要字段: {missing}")

    data = df.copy()
    if "date" in data.columns:
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        data = data.dropna(subset=["date"]).sort_values("date")
    data = data.drop_duplicates(subset=["date"] if "date" in data.columns else None, keep="last")
    data = data.reset_index(drop=True)

    for column in REQUIRED_OHLCV_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=list(REQUIRED_OHLCV_COLUMNS))
    data = data[data["close"] > 0].reset_index(drop=True)
    if data.empty:
        raise ValueError("K 线清洗后为空，无法生成时序特征")
    return data


def _positive_windows(windows: Iterable[int]) -> list[int]:
    return sorted({int(window) for window in windows if int(window) > 0})


def _compute_macd(close: pd.Series, *, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea
    return dif, dea, hist


def _compute_rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window, min_periods=window).mean()
    avg_loss = loss.rolling(window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))
