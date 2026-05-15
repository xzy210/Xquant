from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn


@dataclass(frozen=True)
class TCNAttentionConfig:
    """TCN + Attention 模型配置。"""

    input_dim: int
    num_classes: int = 3
    channels: tuple[int, ...] = (64, 64, 64)
    kernel_size: int = 3
    dropout: float = 0.2
    attention_dim: int = 64

    def to_dict(self) -> dict:
        return asdict(self)


class TCNAttentionClassifier(nn.Module):
    """用于三障碍方向预测的 TCN + Attention 分类模型。"""

    def __init__(self, config: TCNAttentionConfig):
        super().__init__()
        self.config = config

        layers: list[nn.Module] = []
        in_channels = config.input_dim
        for index, out_channels in enumerate(config.channels):
            dilation = 2**index
            layers.append(
                TemporalBlock(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    kernel_size=config.kernel_size,
                    dilation=dilation,
                    dropout=config.dropout,
                )
            )
            in_channels = out_channels

        self.tcn = nn.Sequential(*layers)
        self.attention = nn.Sequential(
            nn.Linear(in_channels, config.attention_dim),
            nn.Tanh(),
            nn.Linear(config.attention_dim, 1),
        )
        self.classifier = nn.Sequential(
            nn.LayerNorm(in_channels),
            nn.Dropout(config.dropout),
            nn.Linear(in_channels, config.num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播。

        Args:
            x: shape 为 ``[batch, seq_len, feature_dim]`` 的特征窗口。
        """

        encoded = self.tcn(x.transpose(1, 2)).transpose(1, 2)
        weights = torch.softmax(self.attention(encoded), dim=1)
        pooled = torch.sum(encoded * weights, dim=1)
        return self.classifier(pooled)

    @torch.no_grad()
    def predict_proba(self, x: torch.Tensor) -> torch.Tensor:
        self.eval()
        return torch.softmax(self.forward(x), dim=-1)


class TemporalBlock(nn.Module):
    """残差因果卷积块。"""

    def __init__(
        self,
        *,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        dilation: int,
        dropout: float,
    ):
        super().__init__()
        padding = (kernel_size - 1) * dilation
        self.net = nn.Sequential(
            nn.Conv1d(in_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Conv1d(out_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.downsample = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else nn.Identity()
        self.activation = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.activation(self.net(x) + self.downsample(x))


class Chomp1d(nn.Module):
    """裁掉因果卷积右侧 padding，避免看到未来。"""

    def __init__(self, chomp_size: int):
        super().__init__()
        self.chomp_size = int(chomp_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.chomp_size <= 0:
            return x
        return x[:, :, : -self.chomp_size].contiguous()
