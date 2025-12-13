"""
自定义 LSTM/GRU 策略网络

支持：
1. LSTM 特征提取器
2. GRU 特征提取器（更快，效果相近）
3. 与 MaskablePPO 兼容
"""
import torch
import torch.nn as nn
import numpy as np
from gymnasium import spaces
from typing import Dict, List, Tuple, Type, Union, Optional

from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.policies import ActorCriticPolicy


class LSTMFeatureExtractor(BaseFeaturesExtractor):
    """
    LSTM 特征提取器
    
    将时序观察数据 (batch, seq_len, features) 通过 LSTM 处理，
    输出固定维度的特征向量用于后续的策略和价值网络。
    """
    
    def __init__(
        self, 
        observation_space: spaces.Box,
        features_dim: int = 128,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.1,
        bidirectional: bool = False,
    ):
        # 计算最终特征维度
        super().__init__(observation_space, features_dim)
        
        # 从观察空间获取序列长度和输入特征数
        # observation_space.shape = (seq_len, input_features) = (30, 16)
        self.seq_len = observation_space.shape[0]
        self.input_size = observation_space.shape[1]
        
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.bidirectional = bidirectional
        self.num_directions = 2 if bidirectional else 1
        
        # LSTM 层
        self.lstm = nn.LSTM(
            input_size=self.input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
            bidirectional=bidirectional,
        )
        
        # 计算 LSTM 输出维度
        lstm_output_size = hidden_size * self.num_directions
        
        # 注意力机制（可选，增强模型对重要时间点的关注）
        self.attention = nn.Sequential(
            nn.Linear(lstm_output_size, 64),
            nn.Tanh(),
            nn.Linear(64, 1),
        )
        
        # 输出层：将 LSTM 输出映射到 features_dim
        self.fc = nn.Sequential(
            nn.Linear(lstm_output_size, features_dim),
            nn.LayerNorm(features_dim),
            nn.ReLU(),
        )
        
        # 初始化权重
        self._init_weights()
    
    def _init_weights(self):
        """初始化权重"""
        for name, param in self.lstm.named_parameters():
            if 'weight_ih' in name:
                nn.init.xavier_uniform_(param)
            elif 'weight_hh' in name:
                nn.init.orthogonal_(param)
            elif 'bias' in name:
                nn.init.zeros_(param)
        
        for module in [self.attention, self.fc]:
            for layer in module:
                if isinstance(layer, nn.Linear):
                    nn.init.xavier_uniform_(layer.weight)
                    nn.init.zeros_(layer.bias)
    
    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        """
        前向传播
        
        Args:
            observations: shape (batch, seq_len, features) 或 (batch, seq_len * features)
        
        Returns:
            features: shape (batch, features_dim)
        """
        # 确保输入是3D张量
        if observations.dim() == 2:
            # 如果是展平的输入，重新reshape
            batch_size = observations.shape[0]
            observations = observations.view(batch_size, self.seq_len, self.input_size)
        
        # LSTM 前向传播
        # lstm_out: (batch, seq_len, hidden_size * num_directions)
        # h_n: (num_layers * num_directions, batch, hidden_size)
        lstm_out, (h_n, c_n) = self.lstm(observations)
        
        # 使用注意力机制加权平均
        # attention_weights: (batch, seq_len, 1)
        attention_weights = torch.softmax(self.attention(lstm_out), dim=1)
        
        # 加权求和: (batch, hidden_size * num_directions)
        context = torch.sum(lstm_out * attention_weights, dim=1)
        
        # 映射到输出维度
        features = self.fc(context)
        
        return features


class GRUFeatureExtractor(BaseFeaturesExtractor):
    """
    GRU 特征提取器
    
    比 LSTM 更快，参数更少，效果相近。
    推荐用于股票交易等序列较短的场景。
    """
    
    def __init__(
        self, 
        observation_space: spaces.Box,
        features_dim: int = 128,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.1,
        bidirectional: bool = False,
    ):
        super().__init__(observation_space, features_dim)
        
        self.seq_len = observation_space.shape[0]
        self.input_size = observation_space.shape[1]
        
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.bidirectional = bidirectional
        self.num_directions = 2 if bidirectional else 1
        
        # GRU 层
        self.gru = nn.GRU(
            input_size=self.input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
            bidirectional=bidirectional,
        )
        
        gru_output_size = hidden_size * self.num_directions
        
        # 注意力机制
        self.attention = nn.Sequential(
            nn.Linear(gru_output_size, 64),
            nn.Tanh(),
            nn.Linear(64, 1),
        )
        
        # 输出层
        self.fc = nn.Sequential(
            nn.Linear(gru_output_size, features_dim),
            nn.LayerNorm(features_dim),
            nn.ReLU(),
        )
        
        self._init_weights()
    
    def _init_weights(self):
        """初始化权重"""
        for name, param in self.gru.named_parameters():
            if 'weight_ih' in name:
                nn.init.xavier_uniform_(param)
            elif 'weight_hh' in name:
                nn.init.orthogonal_(param)
            elif 'bias' in name:
                nn.init.zeros_(param)
        
        for module in [self.attention, self.fc]:
            for layer in module:
                if isinstance(layer, nn.Linear):
                    nn.init.xavier_uniform_(layer.weight)
                    nn.init.zeros_(layer.bias)
    
    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        """前向传播"""
        if observations.dim() == 2:
            batch_size = observations.shape[0]
            observations = observations.view(batch_size, self.seq_len, self.input_size)
        
        # GRU 前向传播
        gru_out, h_n = self.gru(observations)
        
        # 注意力加权
        attention_weights = torch.softmax(self.attention(gru_out), dim=1)
        context = torch.sum(gru_out * attention_weights, dim=1)
        
        # 输出特征
        features = self.fc(context)
        
        return features


class TransformerFeatureExtractor(BaseFeaturesExtractor):
    """
    Transformer 特征提取器（高级选项）
    
    使用自注意力机制，适合捕捉长距离依赖。
    计算量较大，但效果可能更好。
    """
    
    def __init__(
        self, 
        observation_space: spaces.Box,
        features_dim: int = 128,
        d_model: int = 64,
        nhead: int = 4,
        num_layers: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__(observation_space, features_dim)
        
        self.seq_len = observation_space.shape[0]
        self.input_size = observation_space.shape[1]
        self.d_model = d_model
        
        # 输入投影
        self.input_projection = nn.Linear(self.input_size, d_model)
        
        # 位置编码
        self.pos_encoding = nn.Parameter(torch.randn(1, self.seq_len, d_model) * 0.1)
        
        # Transformer 编码器
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        # 输出层
        self.fc = nn.Sequential(
            nn.Linear(d_model, features_dim),
            nn.LayerNorm(features_dim),
            nn.ReLU(),
        )
    
    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        """前向传播"""
        if observations.dim() == 2:
            batch_size = observations.shape[0]
            observations = observations.view(batch_size, self.seq_len, self.input_size)
        
        # 输入投影 + 位置编码
        x = self.input_projection(observations) + self.pos_encoding
        
        # Transformer 编码
        transformer_out = self.transformer(x)
        
        # 取最后一个时间步（或可以用CLS token）
        features = self.fc(transformer_out[:, -1, :])
        
        return features


def get_policy_kwargs(
    rnn_type: str = "lstm",
    features_dim: int = 128,
    hidden_size: int = 128,
    num_layers: int = 2,
    dropout: float = 0.1,
    bidirectional: bool = False,
    net_arch: Optional[List[int]] = None,
) -> Dict:
    """
    获取策略网络配置
    
    Args:
        rnn_type: "lstm", "gru", 或 "transformer"
        features_dim: 特征提取器输出维度
        hidden_size: RNN 隐藏层大小
        num_layers: RNN 层数
        dropout: Dropout 比例
        bidirectional: 是否双向
        net_arch: 策略/价值网络架构，如 [64, 64]
    
    Returns:
        policy_kwargs: 可以传给 PPO/MaskablePPO 的配置字典
    """
    if rnn_type.lower() == "lstm":
        extractor_class = LSTMFeatureExtractor
    elif rnn_type.lower() == "gru":
        extractor_class = GRUFeatureExtractor
    elif rnn_type.lower() == "transformer":
        extractor_class = TransformerFeatureExtractor
    else:
        raise ValueError(f"Unknown rnn_type: {rnn_type}. Use 'lstm', 'gru', or 'transformer'")
    
    if rnn_type.lower() == "transformer":
        extractor_kwargs = dict(
            features_dim=features_dim,
            d_model=hidden_size,  # Map hidden_size to d_model for Transformer
            num_layers=num_layers,
            dropout=dropout,
            nhead=4,  # Default nhead
        )
    else:
        extractor_kwargs = dict(
            features_dim=features_dim,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout,
            bidirectional=bidirectional,
        )

    policy_kwargs = dict(
        features_extractor_class=extractor_class,
        features_extractor_kwargs=extractor_kwargs,
    )
    
    if net_arch is not None:
        policy_kwargs["net_arch"] = net_arch
    
    return policy_kwargs

