from .cross_sectional_strategy import CrossSectionalStrategy
import pandas as pd
import numpy as np
from typing import Dict, List, Any

class MultiFactorStrategy(CrossSectionalStrategy):
    """
    动态IC加权多因子选股策略 (Dynamic IC Weighted Multi-Factor Strategy)
    
    进阶统计模型：
    1. 因子计算：动量、反转、波动率
    2. IC计算：每日计算因子与下期收益率的Rank IC (斯皮尔曼相关系数)
    3. 动态权重：每期调仓时，回看过去 N 天的平均 IC 值，以此作为当期因子权重。
       (表现好的因子自动获得高权重，失效的因子自动降低权重)
    """
    
    def __init__(self):
        super().__init__()
        self.name = "动态IC加权多因子策略"
        self.description = "根据因子近期有效性(IC值)动态调整权重，自适应市场风格变化。"
        self.params = {
            "top_k": 5,                 # 持仓数量
            "rebalance_period": 20,     # 调仓周期 (交易日)
            "ic_window": 60,            # IC回看窗口 (用于计算权重)
            "filter_downtrend": True,   # [新增] 是否开启下跌趋势过滤
            "trend_ma": 20,             # [新增] 趋势判断均线 (如MA20)
            "label_period": None,       # 标签收益率间隔天数, None则使用rebalance_period
            # 初始默认权重 (仅在没有足够历史IC数据时使用)
            "default_weights": {
                "momentum_20d": 0.33,
                "reversal_5d": 0.33,
                "volatility_20d": -0.33
            }
        }
        self.rebalance_counter = 0
        self.ic_history = pd.DataFrame() # 存储历史IC值
        self.last_scores = None  # 存储最近一次调仓的评分数据，供回放功能使用

    def prepare_factors(self, data_dict: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        预计算因子，并同步计算历史IC序列
        """
        all_factors = []
        
        factor_names = list(self.params['default_weights'].keys())
        
        # 1. 计算单股因子和下期收益
        for code, df in data_dict.items():
            if len(df) < 60: continue
            
            df = df.copy()
            df = df.set_index('date').sort_index()
            
            # --- 因子计算 ---
            # 1. 动量 (20日收益率)
            df['momentum_20d'] = df['close'].pct_change(20)
            
            # 2. 反转 (5日收益率)
            df['reversal_5d'] = df['close'].pct_change(5)
            
            # 3. 波动率 (20日日收益率标准差)
            df['volatility_20d'] = df['close'].pct_change().rolling(20).std()
            
            # [新增] 计算均线用于趋势过滤
            if self.params.get('filter_downtrend', False):
                ma_window = self.params.get('trend_ma', 20)
                df['trend_ma'] = df['close'].rolling(ma_window).mean()
                df['is_uptrend'] = df['close'] > df['trend_ma']
            
            # --- 关键：计算下期收益 (用于计算IC) ---
            # Use T+label_k forward return, defaults to rebalance_period if not specified
            label_k = self.params.get('label_period') or self.params['rebalance_period']
            df['next_ret'] = df['close'].shift(-label_k) / df['close'] - 1.0
            
            # 整理数据
            # 确保 trend_ma 相关列被包含
            cols = factor_names + ['next_ret', 'close']
            if 'is_uptrend' in df.columns:
                cols.append('is_uptrend')
                
            factors = df[cols].dropna()
            factors['code'] = code
            all_factors.append(factors)
            
        if not all_factors:
            return pd.DataFrame()
            
        # 2. 合并所有股票数据
        # Index: date, code
        combined = pd.concat(all_factors)
        
        # 3. 计算每日截面 IC (Information Coefficient)
        # 这里的 IC 指的是 Rank IC (斯皮尔曼相关系数)
        ic_list = []
        dates = combined.index.unique()
        
        for date in dates:
            # 获取当日截面
            daily_slice = combined.loc[date]
            if len(daily_slice) < 10: # 股票太少不算IC
                continue
                
            daily_ic = {}
            daily_ic['date'] = date
            
            for factor in factor_names:
                # 计算 因子值 与 下期收益 的相关性
                try:
                    ic = daily_slice[factor].corr(daily_slice['next_ret'], method='spearman')
                    daily_ic[factor] = ic
                except:
                    daily_ic[factor] = 0.0
            
            ic_list.append(daily_ic)
            
        self.ic_history = pd.DataFrame(ic_list).set_index('date').sort_index()
        self.ic_history = self.ic_history.fillna(0)

        # 返回因子数据 (重置索引以符合 Engine 要求: MultiIndex [date, code])
        return combined.reset_index().set_index(['date', 'code']).sort_index()

    def on_rebalance(self, context, valid_codes: List[str], daily_factors: pd.DataFrame):
        """
        调仓日逻辑：使用动态权重
        """
        self.rebalance_counter += 1
        
        # 检查调仓周期
        if self.rebalance_counter % self.params['rebalance_period'] != 0:
            return
            
        if daily_factors is None or daily_factors.empty:
            return

        # 1. 确定当期权重 (Dynamic Weighting)
        current_date = context.current_dt
        current_weights = self.params['default_weights'].copy()
        
        # 从 IC 历史中截取过去 N 天的数据
        # 注意：只能取 current_date 之前的数据，不能用未来数据
        # IMPORTANT: Since next_ret uses T+label_k forward return, a sample at date T
        # has its label depending on close price at T+label_k. To avoid look-ahead bias,
        # we must exclude the last label_k IC values before current_date.
        label_k = self.params.get('label_period') or self.params['rebalance_period']
        k = label_k
        if not self.ic_history.empty:
            # Get IC history strictly before current_date
            valid_ic_history = self.ic_history.loc[:current_date].iloc[:-1] if current_date in self.ic_history.index else self.ic_history[self.ic_history.index < current_date]
            
            # Further exclude last k entries to prevent future data leakage
            # (their next_ret labels depend on prices at or after current_date - k + 1)
            if len(valid_ic_history) > k:
                valid_ic_history = valid_ic_history.iloc[:-k]
            
            if len(valid_ic_history) > 10:
                # 取最近 ic_window 天的平均值作为因子有效性估计
                recent_ic = valid_ic_history.tail(self.params['ic_window']).mean()
                
                # 计算动态权重: 简单归一化
                # 方法：权重 = IC_mean / Sum(Abs(IC_mean))
                # 这样 IC 为负的因子会自动得到负权重
                total_abs_ic = recent_ic.abs().sum()
                
                if total_abs_ic > 0.001:
                    new_weights = {}
                    for factor, ic_val in recent_ic.items():
                        # 放大系数，使总杠杆率为1
                        w = ic_val / total_abs_ic
                        new_weights[factor] = w
                    current_weights = new_weights

        # 2. 数据清洗
        available_factors = daily_factors.loc[daily_factors.index.intersection(valid_codes)].copy()
        
        if len(available_factors) < self.params['top_k']:
            return

        # 3. 因子去极值 & 标准化
        for col in self.params['default_weights'].keys():
            mean = available_factors[col].mean()
            std = available_factors[col].std()
            if std != 0:
                # Winsorize
                available_factors[col] = available_factors[col].clip(mean - 3*std, mean + 3*std)
                # Z-Score
                available_factors[col] = (available_factors[col] - mean) / std
            else:
                available_factors[col] = 0

        # 4. 打分 (使用动态权重)
        available_factors['score'] = 0.0
        for factor, weight in current_weights.items():
            if factor in available_factors.columns:
                available_factors['score'] += available_factors[factor] * weight
        
        # 保存评分数据供回放功能使用
        self.last_scores = available_factors[['score']].copy()
        # 如果有代码列，也保存
        if 'code' in available_factors.columns:
            self.last_scores['code'] = available_factors['code']
        else:
            self.last_scores['code'] = self.last_scores.index
                
        # 5. 排序选股
        ranked = available_factors.sort_values('score', ascending=False)
        candidates = ranked.head(self.params['top_k'])
        
        # [新增] 择时过滤逻辑
        # 如果开启了过滤，且股票处于均线下方，则剔除
        final_targets = []
        
        if self.params.get('filter_downtrend', False) and 'is_uptrend' in candidates.columns:
            # 筛选出处于上升趋势的股票
            uptrend_stocks = candidates[candidates['is_uptrend'] == True]
            final_targets = uptrend_stocks.index.tolist()
        else:
            final_targets = candidates.index.tolist()
        
        # 6. 执行调仓
        # 6.1 卖出 (不在最终目标列表里的全部卖出)
        for code in list(context.positions.keys()):
            if code not in final_targets:
                context.order_target_percent(code, 0.0, reason="调仓卖出")
        
        # 6.2 买入
        if final_targets:
            # 即使过滤后只剩1只，也只买 1/TopK 的仓位吗？
            # 策略A: 剩下的平分资金 -> 激进，可能单票仓位过重
            # 策略B: 维持固定比例 (1/TopK) -> 保守，现金增多 (选择此项，即自动降低总仓位)
            
            single_pos_weight = 0.95 / self.params['top_k']
            
            for code in final_targets:
                context.order_target_percent(code, single_pos_weight, reason="调仓买入")
                
        # 如果 final_targets 为空，或者少于 top_k，剩下的钱自然就留在 cash 里了，实现了“降低仓位/空仓”的效果
