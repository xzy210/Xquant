from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

from .dataset import StandardScaler
from .features import TimingFeatureConfig, build_timing_features
from .labels import TripleBarrierConfig
from .model import TCNAttentionClassifier, TCNAttentionConfig
from .model_store import load_scaler


@dataclass(frozen=True)
class TimingPrediction:
    """单次时序模型推理结果。"""

    label: int
    p_down: float
    p_flat: float
    p_up: float
    upper_price: float
    lower_price: float
    model_version: str
    feature_names: list[str]

    @property
    def confidence(self) -> float:
        return max(self.p_down, self.p_flat, self.p_up)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "p_down": self.p_down,
            "p_flat": self.p_flat,
            "p_up": self.p_up,
            "upper_price": self.upper_price,
            "lower_price": self.lower_price,
            "confidence": self.confidence,
            "model_version": self.model_version,
        }


class TimingModelPredictor:
    """加载训练产物并执行最新窗口推理。"""

    def __init__(self, model_dir: str | Path, *, device: str = "auto"):
        self.model_dir = Path(model_dir)
        self.device = torch.device("cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device))
        self.manifest = _read_json(self.model_dir / "manifest.json")
        self.feature_names = list(self.manifest.get("feature_names") or [])
        if not self.feature_names:
            raise ValueError(f"模型清单缺少 feature_names: {self.model_dir}")

        self.feature_config = TimingFeatureConfig(**_tuple_fields(self.manifest.get("feature_config") or {}, ("momentum_windows", "ma_windows", "extra_feature_columns")))
        self.label_config = TripleBarrierConfig(**(self.manifest.get("label_config") or {}))
        self.lookback = int((self.manifest.get("dataset_config") or {}).get("lookback") or 60)
        self.scaler: StandardScaler = load_scaler(self.model_dir / "scaler.pkl")
        self.model = self._load_model()

    @property
    def model_version(self) -> str:
        return str(self.manifest.get("model_version") or self.model_dir.name)

    def predict_latest(self, bars: pd.DataFrame) -> TimingPrediction | None:
        if bars is None or len(bars) < self.lookback:
            return None

        features, _ = build_timing_features(bars, self.feature_config)
        missing = [column for column in self.feature_names if column not in features.columns]
        if missing:
            raise ValueError(f"推理数据缺少模型特征: {missing}")

        window = features[self.feature_names].tail(self.lookback)
        if len(window) < self.lookback or window.isna().any().any():
            return None

        values = self.scaler.transform(window.to_numpy(dtype=np.float32))
        tensor = torch.as_tensor(values[None, :, :], dtype=torch.float32, device=self.device)
        with torch.no_grad():
            proba = self.model.predict_proba(tensor).detach().cpu().numpy()[0]

        close = float(features["close"].iloc[-1])
        volatility_col = f"volatility{self.label_config.volatility_window}"
        volatility = float(features[volatility_col].iloc[-1]) if volatility_col in features.columns else self.label_config.min_volatility
        if not np.isfinite(volatility):
            volatility = self.label_config.min_volatility
        volatility = max(volatility, self.label_config.min_volatility)

        label = {-1: -1, 0: 0, 1: 1}[int(np.argmax(proba)) - 1]
        return TimingPrediction(
            label=label,
            p_down=float(proba[0]),
            p_flat=float(proba[1]),
            p_up=float(proba[2]),
            upper_price=close * (1 + self.label_config.up_mult * volatility),
            lower_price=close * (1 - self.label_config.down_mult * volatility),
            model_version=self.model_version,
            feature_names=list(self.feature_names),
        )

    def _load_model(self) -> TCNAttentionClassifier:
        model_payload = _tuple_fields(self.manifest.get("model_config") or {}, ("channels",))
        config = TCNAttentionConfig(**model_payload)
        model = TCNAttentionClassifier(config).to(self.device)
        state = torch.load(self.model_dir / "model.pt", map_location=self.device)
        model.load_state_dict(state)
        model.eval()
        return model


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _tuple_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = dict(payload or {})
    for field in fields:
        if field in result:
            result[field] = tuple(result[field] or ())
    return result
