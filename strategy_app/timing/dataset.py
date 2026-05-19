from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

LABEL_TO_CLASS = {-1: 0, 0: 1, 1: 2}
CLASS_TO_LABEL = {value: key for key, value in LABEL_TO_CLASS.items()}


@dataclass(frozen=True)
class TimingDatasetConfig:
    """滑动窗口样本集配置。"""

    lookback: int = 60
    train_ratio: float = 0.7
    val_ratio: float = 0.15
    label_col: str = "tb_label"
    date_col: str = "date"
    group_col: str = "symbol"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TimingDataset:
    """离线训练数据容器。"""

    x_train: np.ndarray
    y_train: np.ndarray
    x_val: np.ndarray
    y_val: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray
    metadata: pd.DataFrame
    feature_names: List[str]
    scaler: "StandardScaler"
    config: TimingDatasetConfig

    @property
    def num_features(self) -> int:
        return len(self.feature_names)


class StandardScaler:
    """轻量标准化器，避免为训练脚本额外引入 sklearn 依赖。"""

    def __init__(self, mean: np.ndarray | None = None, scale: np.ndarray | None = None):
        self.mean_ = mean
        self.scale_ = scale

    def fit(self, values: np.ndarray) -> "StandardScaler":
        array = np.asarray(values, dtype=np.float32)
        self.mean_ = np.nanmean(array, axis=0)
        self.scale_ = np.nanstd(array, axis=0)
        self.scale_[~np.isfinite(self.scale_) | (self.scale_ == 0)] = 1.0
        self.mean_[~np.isfinite(self.mean_)] = 0.0
        return self

    def transform(self, values: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("StandardScaler 尚未 fit")
        array = np.asarray(values, dtype=np.float32)
        return ((array - self.mean_) / self.scale_).astype(np.float32)

    def fit_transform(self, values: np.ndarray) -> np.ndarray:
        return self.fit(values).transform(values)

    def to_dict(self) -> dict:
        return {
            "mean": self.mean_.tolist() if self.mean_ is not None else [],
            "scale": self.scale_.tolist() if self.scale_ is not None else [],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "StandardScaler":
        mean = np.asarray(payload.get("mean", []), dtype=np.float32)
        scale = np.asarray(payload.get("scale", []), dtype=np.float32)
        return cls(mean=mean, scale=scale)


def build_timing_dataset(
    frame: pd.DataFrame,
    feature_names: Sequence[str],
    config: TimingDatasetConfig | None = None,
) -> TimingDataset:
    """将特征和标签表转换成按时间切分的滑动窗口样本。"""

    cfg = config or TimingDatasetConfig()
    if cfg.lookback <= 1:
        raise ValueError("lookback 必须大于 1")
    if not 0 < cfg.train_ratio < 1:
        raise ValueError("train_ratio 必须位于 (0, 1)")
    if not 0 <= cfg.val_ratio < 1:
        raise ValueError("val_ratio 必须位于 [0, 1)")
    if cfg.train_ratio + cfg.val_ratio >= 1:
        raise ValueError("train_ratio + val_ratio 必须小于 1")

    names = list(feature_names)
    missing = [column for column in [*names, cfg.label_col] if column not in frame.columns]
    if missing:
        raise ValueError(f"样本构造缺少字段: {missing}")

    data = frame.copy().reset_index(drop=True)
    if cfg.date_col in data.columns:
        data[cfg.date_col] = pd.to_datetime(data[cfg.date_col], errors="coerce")
        sort_columns = [cfg.group_col, cfg.date_col] if cfg.group_col in data.columns else [cfg.date_col]
        data = data.sort_values(sort_columns).reset_index(drop=True)
    data[names] = data[names].replace([np.inf, -np.inf], np.nan)
    valid_feature = data[names].notna().all(axis=1)
    valid_label = data[cfg.label_col].notna()
    valid_rows = valid_feature & valid_label

    arrays: list[np.ndarray] = []
    labels: list[int] = []
    meta_rows: list[dict] = []
    feature_values = data[names].to_numpy(dtype=np.float32)
    group_ranges = _group_ranges(data, cfg.group_col)

    for range_start, range_end in group_ranges:
        for end_idx in range(range_start + cfg.lookback - 1, range_end):
            start_idx = end_idx - cfg.lookback + 1
            if not valid_rows.iloc[start_idx : end_idx + 1].all():
                continue
            raw_label = int(data.at[end_idx, cfg.label_col])
            if raw_label not in LABEL_TO_CLASS:
                continue
            arrays.append(feature_values[start_idx : end_idx + 1])
            labels.append(LABEL_TO_CLASS[raw_label])
            meta_rows.append(_build_meta_row(data, end_idx, cfg))

    if not arrays:
        raise ValueError("没有可用训练样本，请检查 lookback、标签和特征缺失值")

    x = np.stack(arrays).astype(np.float32)
    y = np.asarray(labels, dtype=np.int64)
    metadata = pd.DataFrame(meta_rows)

    train_indices, val_indices, test_indices = _split_sample_indices_by_group(metadata, cfg)

    scaler = StandardScaler()
    train_flat = x[train_indices].reshape(-1, x.shape[-1])
    scaler.fit(train_flat)
    x = scaler.transform(x.reshape(-1, x.shape[-1])).reshape(x.shape)

    return TimingDataset(
        x_train=x[train_indices],
        y_train=y[train_indices],
        x_val=x[val_indices],
        y_val=y[val_indices],
        x_test=x[test_indices],
        y_test=y[test_indices],
        metadata=metadata,
        feature_names=names,
        scaler=scaler,
        config=cfg,
    )


def describe_labels(labels: np.ndarray) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for cls, count in zip(*np.unique(labels, return_counts=True)):
        counts[str(CLASS_TO_LABEL[int(cls)])] = int(count)
    return counts


def _split_indices(total: int, train_ratio: float, val_ratio: float) -> tuple[int, int]:
    train_end = int(total * train_ratio)
    val_end = train_end + int(total * val_ratio)
    return train_end, val_end


def _split_sample_indices_by_group(
    metadata: pd.DataFrame,
    cfg: TimingDatasetConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    train_indices: list[int] = []
    val_indices: list[int] = []
    test_indices: list[int] = []

    if cfg.group_col in metadata.columns:
        groups = metadata.groupby(cfg.group_col, sort=False)
    else:
        groups = [("", metadata)]

    for group_value, group in groups:
        indices = group.index.to_numpy(dtype=np.int64)
        train_end, val_end = _split_indices(len(indices), cfg.train_ratio, cfg.val_ratio)
        if train_end <= 0 or val_end <= train_end or val_end >= len(indices):
            label = f"标的 {group_value}" if group_value else "样本"
            raise ValueError(f"{label} 数量不足，无法按当前比例切分训练/验证/测试集")
        train_indices.extend(indices[:train_end].tolist())
        val_indices.extend(indices[train_end:val_end].tolist())
        test_indices.extend(indices[val_end:].tolist())

    return (
        np.asarray(train_indices, dtype=np.int64),
        np.asarray(val_indices, dtype=np.int64),
        np.asarray(test_indices, dtype=np.int64),
    )


def _group_ranges(data: pd.DataFrame, group_col: str) -> list[tuple[int, int]]:
    if group_col not in data.columns:
        return [(0, len(data))]
    ranges: list[tuple[int, int]] = []
    for _, group in data.groupby(group_col, sort=False):
        indices = group.index.to_numpy()
        if len(indices):
            ranges.append((int(indices[0]), int(indices[-1]) + 1))
    return ranges


def _build_meta_row(data: pd.DataFrame, end_idx: int, cfg: TimingDatasetConfig) -> dict:
    row = {
        "row_index": int(end_idx),
        "label": int(data.at[end_idx, cfg.label_col]),
    }
    if cfg.group_col in data.columns:
        row["symbol"] = str(data.at[end_idx, cfg.group_col])
    if cfg.date_col in data.columns:
        value = data.at[end_idx, cfg.date_col]
        row["date"] = value.isoformat() if hasattr(value, "isoformat") else str(value)
    for column in ("tb_exit_time", "tb_exit_price", "tb_upper_price", "tb_lower_price", "tb_trigger_type"):
        if column in data.columns:
            value = data.at[end_idx, column]
            row[column] = value.isoformat() if hasattr(value, "isoformat") else value
    return row
