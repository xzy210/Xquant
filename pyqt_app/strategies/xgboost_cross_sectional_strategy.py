"""
XGBoost 截面选股策略

与现有的 ml_strategy.py 中的 XGBoostStrategy (单股票时序预测) 不同，
本策略是基于截面比较的多股票选股策略，继承自 CrossSectionalStrategy。
"""

from .cross_sectional_strategy import CrossSectionalStrategy
import pandas as pd
import numpy as np
from typing import Dict, List, Any

# 导入因子库
from pyqt_app.factors import factor_registry

# 尝试导入 XGBoost
try:
    import xgboost as xgb
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False
    print("Warning: xgboost not installed. Please run: pip install xgboost")


class XGBoostCrossSectionalStrategy(CrossSectionalStrategy):
    """
    XGBoost 机器学习截面选股策略
    
    核心思想：
    1. 使用历史因子数据训练 XGBoost 模型预测未来收益
    2. 每次调仓时用滚动窗口重新训练模型（适应市场变化）
    3. 用训练好的模型对当期所有股票打分，选择预测收益最高的股票
    
    与传统多因子策略的区别：
    - 传统多因子：线性加权 score = Σ(factor × weight)
    - XGBoost：非线性模型 score = model.predict(factors)，能捕捉因子间的交互效应
    """
    
    def __init__(self):
        super().__init__()
        self.name = "XGBoost截面选股策略"
        self.description = "使用XGBoost模型学习因子与收益的非线性关系，进行截面选股"
        
        self.params = {
            "top_k": 5,                 # 持仓数量
            "rebalance_period": 20,     # 调仓周期 (交易日)
            "train_window": 252,        # 训练窗口 (约1年的交易日)
            "min_train_samples": 500,   # 最小训练样本数
            "filter_downtrend": True,   # 是否开启趋势过滤
            "trend_ma": 20,             # 趋势判断均线
            
            # XGBoost 超参数
            "xgb_params": {
                "objective": "reg:squarederror",  # 回归任务：预测收益率
                "max_depth": 4,                   # 树深度（防止过拟合）
                "learning_rate": 0.1,             # 学习率
                "n_estimators": 100,              # 树的数量
                "subsample": 0.8,                 # 行采样比例
                "colsample_bytree": 0.8,          # 列采样比例
                "random_state": 42,
                "n_jobs": 1,                      # 单线程避免UI卡顿
            },
            
            # 因子列表
            "factor_cols": [
                "momentum_20d",     # 20日动量
                "momentum_60d",     # 60日动量
                "reversal_5d",      # 5日反转
                "volatility_20d",   # 20日波动率
                "turnover_20d",     # 20日平均换手率
                "volume_ratio",     # 量比
                "bias_20",          # 20日乖离率
            ]
        }
        
        self.rebalance_counter = 0
        self.model = None
        self.all_data = None  # 存储所有历史数据用于训练
        self.last_scores = None  # 存储评分供回放功能使用
        self.feature_importance = {}  # 特征重要性
        self.last_train_info = None  # 存储最近一次训练的信息（供UI显示）
        
    def prepare_factors(self, data_dict: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        预计算因子
        
        注意：这里不训练模型，模型在 on_rebalance 中滚动训练，
        以避免使用未来数据（数据泄露）
        
        使用因子库统一计算因子，确保计算逻辑一致性。
        """
        if not HAS_XGBOOST:
            raise ImportError("xgboost 未安装，请运行: pip install xgboost")
        
        all_factors = []
        factor_cols = self.params['factor_cols']
        
        for code, df in data_dict.items():
            if len(df) < 60:
                continue
                
            df = df.copy()
            df = df.set_index('date').sort_index()
            
            # === 使用因子库计算因子 ===
            for factor_name in factor_cols:
                if factor_name in factor_registry:
                    try:
                        df[factor_name] = factor_registry.compute(factor_name, df)
                    except Exception as e:
                        print(f"Warning: Failed to compute factor {factor_name} for {code}: {e}")
                        df[factor_name] = np.nan
                else:
                    print(f"Warning: Factor {factor_name} not found in registry")
                    df[factor_name] = np.nan
            
            # === 趋势过滤 ===
            if self.params.get('filter_downtrend', False):
                ma_window = self.params.get('trend_ma', 20)
                df['trend_ma'] = df['close'].rolling(ma_window).mean()
                df['is_uptrend'] = df['close'] > df['trend_ma']
            
            # === 目标变量 ===
            # 下期收益 (T+1)，用于训练模型
            df['next_ret'] = df['close'].shift(-1) / df['close'] - 1.0
            
            # 整理输出列
            cols = factor_cols.copy()
            cols.extend(['next_ret', 'close'])
            if 'is_uptrend' in df.columns:
                cols.append('is_uptrend')
            
            # 只保留有效列
            valid_cols = [c for c in cols if c in df.columns]
            factors = df[valid_cols].dropna()
            factors['code'] = code
            all_factors.append(factors)
        
        if not all_factors:
            return pd.DataFrame()
        
        # 合并数据
        combined = pd.concat(all_factors)
        self.all_data = combined.copy()  # 保存用于训练
        
        return combined.reset_index().set_index(['date', 'code']).sort_index()
    
    def _train_model(self, train_data: pd.DataFrame):
        """
        训练 XGBoost 模型
        
        :param train_data: 训练数据，包含因子列和 next_ret 列
        """
        feature_cols = [c for c in self.params['factor_cols'] if c in train_data.columns]
        
        if not feature_cols:
            return
        
        X = train_data[feature_cols].values
        y = train_data['next_ret'].values
        
        # 处理异常值：限制收益率范围，防止极端值影响模型
        y = np.clip(y, -0.2, 0.2)
        
        # 处理 NaN 和 inf 值
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
        
        # 过滤掉仍然存在异常的行
        mask = ~(np.isnan(X).any(axis=1) | np.isnan(y) | np.isinf(X).any(axis=1) | np.isinf(y))
        X = X[mask]
        y = y[mask]
        
        if len(X) < self.params['min_train_samples']:
            return
        
        # 创建并训练模型
        self.model = xgb.XGBRegressor(**self.params['xgb_params'])
        self.model.fit(X, y, verbose=False)
        
        # 记录特征重要性
        self.feature_importance = dict(zip(feature_cols, self.model.feature_importances_))
        sorted_importance = sorted(self.feature_importance.items(), key=lambda x: -x[1])
        
        # 保存训练信息供 UI 显示
        self.last_train_info = {
            'train_samples': len(X),
            'feature_importance': sorted_importance
        }
        
    def on_rebalance(self, context, valid_codes: List[str], daily_factors: pd.DataFrame):
        """
        调仓日逻辑：滚动训练模型 + 预测选股
        """
        self.rebalance_counter += 1
        
        # 检查调仓周期
        if self.rebalance_counter % self.params['rebalance_period'] != 0:
            return
            
        if daily_factors is None or daily_factors.empty:
            return
        
        current_date = context.current_dt
        
        # 1. 准备训练数据 (只能用历史数据，避免未来数据泄露)
        if self.all_data is None or self.all_data.empty:
            return
        
        # 使用布尔索引获取 current_date 之前的数据（索引不唯一时不能用切片）
        historical_data = self.all_data[self.all_data.index < current_date].copy()
        
        if len(historical_data) == 0:
            return
        
        # 滚动窗口：只用最近 train_window 天的数据
        train_window = self.params['train_window']
        unique_dates = sorted(historical_data.index.unique())
        
        if len(unique_dates) > train_window:
            cutoff_date = unique_dates[-train_window]
            train_data = historical_data[historical_data.index >= cutoff_date]
        else:
            train_data = historical_data
        
        if len(train_data) < self.params['min_train_samples']:
            return
        
        # 2. 训练模型
        self._train_model(train_data)
        
        if self.model is None:
            return
        
        # 3. 预测当期股票
        available_factors = daily_factors.loc[daily_factors.index.intersection(valid_codes)].copy()
        
        if len(available_factors) < self.params['top_k']:
            return
        
        # 因子标准化（截面标准化）
        feature_cols = [c for c in self.params['factor_cols'] if c in available_factors.columns]
        
        for col in feature_cols:
            mean = available_factors[col].mean()
            std = available_factors[col].std()
            if std > 0:
                # Winsorize 去极值
                available_factors[col] = available_factors[col].clip(mean - 3*std, mean + 3*std)
                # Z-Score 标准化
                available_factors[col] = (available_factors[col] - mean) / std
            else:
                available_factors[col] = 0
        
        # 模型预测
        X_pred = available_factors[feature_cols].fillna(0).values
        # 处理 inf 值
        X_pred = np.nan_to_num(X_pred, nan=0.0, posinf=0.0, neginf=0.0)
        predictions = self.model.predict(X_pred)
        available_factors['score'] = predictions
        
        # 保存评分供回放功能使用
        self.last_scores = available_factors[['score']].copy()
        self.last_scores['code'] = self.last_scores.index
        
        # 4. 排序选股
        ranked = available_factors.sort_values('score', ascending=False)
        candidates = ranked.head(self.params['top_k'])
        
        # 5. 趋势过滤
        final_targets = []
        if self.params.get('filter_downtrend', False) and 'is_uptrend' in candidates.columns:
            uptrend_stocks = candidates[candidates['is_uptrend'] == True]
            final_targets = uptrend_stocks.index.tolist()
        else:
            final_targets = candidates.index.tolist()
        
        # 6. 执行调仓
        # 6.1 卖出不在目标列表中的持仓
        for code in list(context.positions.keys()):
            if code not in final_targets:
                context.order_target_percent(code, 0.0, reason="调仓卖出")
        
        # 6.2 买入目标股票
        if final_targets:
            single_pos_weight = 0.95 / self.params['top_k']
            for code in final_targets:
                context.order_target_percent(code, single_pos_weight, reason="调仓买入")
        
        # 如果 final_targets 为空或少于 top_k，剩余资金留作现金
