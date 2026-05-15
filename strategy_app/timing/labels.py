from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TripleBarrierConfig:
    """Triple-barrier 标签配置。"""

    horizon: int = 12
    up_mult: float = 1.5
    down_mult: float = 1.0
    volatility_window: int = 20
    min_volatility: float = 0.001
    price_col: str = "close"
    high_col: str = "high"
    low_col: str = "low"
    date_col: str = "date"
    tie_breaker: str = "stop_loss"

    def to_dict(self) -> dict:
        return asdict(self)


def build_triple_barrier_labels(
    df: pd.DataFrame,
    config: TripleBarrierConfig | None = None,
) -> pd.DataFrame:
    """基于未来价格路径生成三障碍方向标签。

    标签含义：
    - 1：先触发上障碍
    - -1：先触发下障碍
    - 0：到达时间障碍仍未触发价格障碍
    """

    cfg = config or TripleBarrierConfig()
    _validate_columns(df, cfg)
    if cfg.horizon <= 0:
        raise ValueError("horizon 必须大于 0")

    data = df.copy().reset_index(drop=True)
    if cfg.date_col in data.columns:
        data[cfg.date_col] = pd.to_datetime(data[cfg.date_col], errors="coerce")

    close = pd.to_numeric(data[cfg.price_col], errors="coerce")
    high = pd.to_numeric(data[cfg.high_col], errors="coerce")
    low = pd.to_numeric(data[cfg.low_col], errors="coerce")
    volatility = close.pct_change().rolling(
        cfg.volatility_window,
        min_periods=cfg.volatility_window,
    ).std()
    volatility = volatility.clip(lower=cfg.min_volatility)

    labels = np.full(len(data), np.nan)
    exit_index = np.full(len(data), -1, dtype=int)
    exit_price = np.full(len(data), np.nan)
    upper_price = np.full(len(data), np.nan)
    lower_price = np.full(len(data), np.nan)
    trigger_type = np.full(len(data), "", dtype=object)

    last_start = len(data) - cfg.horizon - 1
    for idx in range(max(last_start + 1, 0)):
        base_price = close.iloc[idx]
        vol = volatility.iloc[idx]
        if not np.isfinite(base_price) or not np.isfinite(vol):
            continue

        upper = base_price * (1 + cfg.up_mult * vol)
        lower = base_price * (1 - cfg.down_mult * vol)
        upper_price[idx] = upper
        lower_price[idx] = lower

        label = 0
        hit_index = idx + cfg.horizon
        hit_price = close.iloc[hit_index]
        hit_type = "time"

        for future_idx in range(idx + 1, idx + cfg.horizon + 1):
            hit_upper = high.iloc[future_idx] >= upper
            hit_lower = low.iloc[future_idx] <= lower
            if hit_upper and hit_lower:
                label, hit_type, hit_price = _resolve_same_bar_hit(cfg, upper, lower)
                hit_index = future_idx
                break
            if hit_upper:
                label = 1
                hit_index = future_idx
                hit_price = upper
                hit_type = "upper"
                break
            if hit_lower:
                label = -1
                hit_index = future_idx
                hit_price = lower
                hit_type = "lower"
                break

        labels[idx] = label
        exit_index[idx] = hit_index
        exit_price[idx] = hit_price
        trigger_type[idx] = hit_type

    result = data.copy()
    result["tb_label"] = labels
    result["tb_exit_index"] = exit_index
    result["tb_exit_price"] = exit_price
    result["tb_upper_price"] = upper_price
    result["tb_lower_price"] = lower_price
    result["tb_trigger_type"] = trigger_type
    result["tb_horizon"] = cfg.horizon
    if cfg.date_col in result.columns:
        result["tb_exit_time"] = pd.NaT
        valid_exit = result["tb_exit_index"] >= 0
        result.loc[valid_exit, "tb_exit_time"] = result.loc[valid_exit, "tb_exit_index"].map(
            lambda item: result.at[int(item), cfg.date_col] if int(item) < len(result) else pd.NaT
        )
    return result


def _validate_columns(df: pd.DataFrame, cfg: TripleBarrierConfig) -> None:
    if df is None or df.empty:
        raise ValueError("K 线数据为空，无法生成 triple-barrier 标签")
    required = {cfg.price_col, cfg.high_col, cfg.low_col}
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"K 线数据缺少标签生成字段: {missing}")


def _resolve_same_bar_hit(cfg: TripleBarrierConfig, upper: float, lower: float) -> tuple[int, str, float]:
    if cfg.tie_breaker == "take_profit":
        return 1, "upper", upper
    if cfg.tie_breaker == "flat":
        return 0, "both", float((upper + lower) / 2)
    return -1, "lower", lower
