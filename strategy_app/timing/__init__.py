"""时序策略离线研究模块。"""

from .dataset import StandardScaler, TimingDatasetConfig, build_timing_dataset
from .data_loader import load_timing_bars, normalize_frequency
from .features import TimingFeatureConfig, build_timing_features
from .inference import TimingModelPredictor, TimingPrediction
from .labels import TripleBarrierConfig, build_triple_barrier_labels
from .model import TCNAttentionClassifier

__all__ = [
    "StandardScaler",
    "TCNAttentionClassifier",
    "TimingDatasetConfig",
    "TimingFeatureConfig",
    "TimingModelPredictor",
    "TimingPrediction",
    "TripleBarrierConfig",
    "build_timing_dataset",
    "build_timing_features",
    "build_triple_barrier_labels",
    "load_timing_bars",
    "normalize_frequency",
]
