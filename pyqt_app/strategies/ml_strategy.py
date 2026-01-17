from .base_strategy import BaseStrategy
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

# 尝试导入 XGBoost，如果未安装则降级处理
try:
    import xgboost as xgb
    from sklearn.model_selection import train_test_split
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

class XGBoostStrategy(BaseStrategy):
    """
    基于 XGBoost 的机器学习策略
    
    核心流程：
    1. 特征工程：计算 RSI, MACD, MA乖离率, 波动率, 动量 等因子。
    2. 滚动训练：每隔 N 天(默认30天)，用过去的历史数据重新训练一次模型。
    3. 预测：根据当前因子预测明日上涨的概率。
    4. 交易：概率 > 0.55 买入，概率 < 0.45 卖出。
    """
    
    def __init__(self):
        super().__init__()
        self.name = "XGBoost 增强选股策略"
        self.description = "利用 XGBoost 机器学习模型，学习历史量价因子，预测股价上涨概率。"
        self.params = {
            "train_window": 200,   # 训练窗口：用过去多少天的数据来训练 (至少200天)
            "retrain_days": 20,    # 重训练频率：每隔多少天更新一次模型
            "prob_threshold_buy": 0.60,  # 买入阈值：预测上涨概率 > 60%
            "prob_threshold_sell": 0.45, # 卖出阈值：预测上涨概率 < 45%
        }
        self.model = None
        self.last_train_date = None
        self.days_since_train = 0

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        [1. 特征工程] 构造因子 (Factors)
        """
        data = df.copy()
        
        # 1. 趋势因子：均线乖离率 (当前价格距离均线多远)
        data['ma5'] = data['close'].rolling(window=5).mean()
        data['ma20'] = data['close'].rolling(window=20).mean()
        data['bias_5'] = (data['close'] - data['ma5']) / data['ma5']
        data['bias_20'] = (data['close'] - data['ma20']) / data['ma20']
        
        # 2. 动量因子：过去N天的涨跌幅
        data['roc_1'] = data['close'].pct_change(1)
        data['roc_3'] = data['close'].pct_change(3)
        data['roc_5'] = data['close'].pct_change(5)
        
        # 3. 波动率因子：振幅
        data['volatility'] = (data['high'] - data['low']) / data['close']
        
        # 4. 量能因子：成交量变化
        data['vol_ma5'] = data['volume'].rolling(window=5).mean()
        data['vol_ratio'] = data['volume'] / data['vol_ma5']
        
        # 5. 相对强弱指标 (简易版 RSI)
        # 这里为了不引入 talib 依赖，手写一个简单逻辑，或者直接用 roc 代替
        # 为了演示简单性，我们直接使用上述因子
        
        # [2. 数据标注] Label: 明日是否上涨 (1=涨, 0=跌)
        # shift(-1) 是因为我们要用今天的因子预测明天的涨跌
        data['target'] = (data['close'].shift(-1) > data['close']).astype(int)
        
        return data

    def train_model(self, data: pd.DataFrame):
        """
        [3. 模型训练]
        """
        if not HAS_XGB:
            return None
            
        # 准备数据
        features = ['bias_5', 'bias_20', 'roc_1', 'roc_3', 'roc_5', 'volatility', 'vol_ratio']
        
        # 去除包含空值的行 (因为计算MA/ROC会有NaN)
        train_data = data.dropna(subset=features + ['target'])
        
        if len(train_data) < 50: # 数据太少不训练
            return None
            
        X = train_data[features]
        y = train_data['target']
        
        # 初始化并训练模型
        # scale_pos_weight 用于处理样本不平衡，这里简单设为1
        model = xgb.XGBClassifier(
            n_estimators=50,     # 树的数量 (不能太少)
            max_depth=3,         # 树的深度 (防止过拟合)
            learning_rate=0.1,   # 学习率
            eval_metric='logloss',
            n_jobs=1             # 单线程运行防止卡顿界面
        )
        model.fit(X, y)
        return model

    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        [选股模式]
        """
        if not HAS_XGB:
            return {"code": code, "info": "请先安装 xgboost: pip install xgboost"}
            
        if len(data) < self.params['train_window']:
            return None
            
        # 1. 计算因子
        df_factors = self.prepare_features(data)
        
        # 2. 训练模型 (使用过去的数据)
        # 注意：选股时，我们用除了最后一天之外的所有数据训练
        model = self.train_model(df_factors.iloc[:-1])
        if not model:
            return None
            
        # 3. 预测今天 (最后一行)
        features = ['bias_5', 'bias_20', 'roc_1', 'roc_3', 'roc_5', 'volatility', 'vol_ratio']
        latest_data = df_factors.iloc[[-1]][features]
        
        # 检查是否有空值 (例如刚上市几天MA算不出来)
        if latest_data.isnull().values.any():
            return None
            
        # prob 是一个 [跌概率, 涨概率] 的数组
        prob = model.predict_proba(latest_data)[0][1] 
        
        if prob > self.params['prob_threshold_buy']:
            day_0 = data.iloc[-1]
            return {
                "code": code,
                "name": "",
                "date": day_0['date'].strftime("%Y-%m-%d"),
                "close": day_0['close'],
                "info": f"AI预测上涨概率: {prob*100:.1f}%"
            }
        return None

    def initialize(self, context):
        self.model = None
        self.days_since_train = 0
        if not HAS_XGB:
            print("警告: 未检测到 xgboost 库，策略无法运行")

    def on_bar(self, context, bars: Dict[str, Any], history: Dict[str, pd.DataFrame] = None):
        """
        [回测模式] 模拟 滚动训练 -> 预测 -> 交易
        """
        if not HAS_XGB:
            return

        for code, bar in bars.items():
            if not history or code not in history:
                continue
            
            hist_data = history[code]
            if len(hist_data) < self.params['train_window']:
                continue
                
            # --- 滚动训练逻辑 ---
            # 为了模拟真实，我们每隔 retrain_days 天重新训练一次模型
            # 这样既保证模型学到最新规律，又不会因为每天训练导致回测太慢
            self.days_since_train += 1
            if self.model is None or self.days_since_train >= self.params['retrain_days']:
                # print(f"[{bar['date']}] 正在重新训练 XGBoost 模型...")
                df_factors = self.prepare_features(hist_data)
                self.model = self.train_model(df_factors)
                self.days_since_train = 0
            
            if self.model is None:
                continue
                
            # --- 预测 ---
            # 构造当前的因子输入
            df_factors = self.prepare_features(hist_data)
            features = ['bias_5', 'bias_20', 'roc_1', 'roc_3', 'roc_5', 'volatility', 'vol_ratio']
            latest_features = df_factors.iloc[[-1]][features]
            
            if latest_features.isnull().values.any():
                continue
                
            prob_up = self.model.predict_proba(latest_features)[0][1]
            
            # --- 交易决策 ---
            current_price = bar['close']
            
            # 持仓处理
            if code in context.positions:
                pos = context.positions[code]
                if pos.quantity > 0:
                    # 如果上涨概率变低 (例如小于 45%)，或者触发止损，则卖出
                    if prob_up < self.params['prob_threshold_sell']:
                        context.order(code, -pos.quantity, reason=f"AI看空 (概率{prob_up:.2f})")
            
            # 买入处理
            else:
                # 如果上涨概率很高 (例如大于 60%)，则买入
                if prob_up > self.params['prob_threshold_buy']:
                    # 简单的资金管理：买入半仓
                    target_amount = context.cash * 0.5
                    qty = int(target_amount / current_price / 100) * 100
                    if qty >= 100:
                        context.order(code, qty, reason=f"AI看多 (概率{prob_up:.2f})")

