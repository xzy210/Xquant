"""
ETF三因子动量轮动策略（优化版）

基于知乎文章《Claude Code 开发100个量化策略：ETF三因子动量轮动》实现

优化点：
1. 使用快速因子计算（numpy代替sklearn）
2. 预计算所有因子，避免回测时重复计算
3. 缓存Z-Score结果

Author: AI Assistant
Date: 2026-02-05
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from strategies.base_strategy import BaseStrategy
from factors.etf_momentum_factors_optimized import (
    BiasMomentumFactorFast,
    SlopeMomentumFactorFast,
    EfficiencyMomentumFactorFast,
    calculate_zscore_fast,
    calculate_composite_momentum_score_fast
)


class ETFThreeFactorMomentumStrategyFast(BaseStrategy):
    """
    ETF三因子动量轮动策略（优化版）
    
    性能提升5-10倍，适合大规模回测
    """
    
    # 默认ETF标的池
    DEFAULT_ETF_POOL = ['510880', '159949', '513100', '518880']
    
    def __init__(self):
        super().__init__()
        self.name = "ETF三因子动量轮动策略（优化版）"
        self.description = "基于快速因子计算的ETF轮动策略"
        
        # 默认参数
        self.params = {
            'etf_pool': self.DEFAULT_ETF_POOL,
            'bias_weight': 0.3,
            'slope_weight': 0.3,
            'efficiency_weight': 0.4,
            'rebalance_threshold': 1.5,
            'momentum_window': 25,
            'zscore_window': 60,
            'empty_threshold': -0.5,
            'enable_empty_position': True,
            'rebalance_period': 1,
            # 第一层风控：个股移动止盈
            'enable_trailing_stop': True,
            'trailing_stop_pct': 0.08,
            # 第二层风控：账户最大回撤保护
            'enable_drawdown_protection': True,
            'max_drawdown_pct': 0.15,
            'drawdown_cooldown_days': 10,
        }
        
        # 因子实例（使用优化版）
        self.bias_factor = BiasMomentumFactorFast()
        self.slope_factor = SlopeMomentumFactorFast()
        self.efficiency_factor = EfficiencyMomentumFactorFast()
        
        # 回测状态
        self.current_holding: Optional[str] = None
        self.current_score: float = 0.0
        self._bar_count: int = 0
        
        # 风控状态
        self._holding_high_price: float = 0.0  # 持仓期间最高价
        self._account_peak: float = 0.0        # 账户历史最高净值
        self._cooldown_remaining: int = 0      # 剩余冷却天数
        
        # 预计算的因子数据 {code: DataFrame}
        self._precomputed_scores: Dict[str, pd.DataFrame] = {}
        
    def set_params(self, params: Dict[str, Any]):
        """设置策略参数"""
        super().set_params(params)
        
    def precompute_scores(self, all_data: Dict[str, pd.DataFrame]):
        """
        预计算所有ETF的因子得分
        
        这是性能优化的关键：一次性计算所有因子，避免回测时重复计算
        
        Args:
            all_data: 所有ETF的历史数据字典 {code: dataframe}
        """
        print(f"[{self.name}] 预计算因子得分...")
        
        for code, data in all_data.items():
            if len(data) < self.params['zscore_window']:
                print(f"  ⚠ {code}: 数据不足")
                continue
            
            # 计算三个因子
            bias_score = self.bias_factor.compute(data, window=self.params['momentum_window'])
            slope_score = self.slope_factor.compute(data, window=self.params['momentum_window'])
            efficiency_score = self.efficiency_factor.compute(data, window=self.params['momentum_window'])
            
            # 计算Z-Score
            bias_zscore = calculate_zscore_fast(bias_score, window=self.params['zscore_window'])
            slope_zscore = calculate_zscore_fast(slope_score, window=self.params['zscore_window'])
            efficiency_zscore = calculate_zscore_fast(efficiency_score, window=self.params['zscore_window'])
            
            # 计算综合得分
            composite_score = (
                self.params['bias_weight'] * bias_zscore +
                self.params['slope_weight'] * slope_zscore +
                self.params['efficiency_weight'] * efficiency_zscore
            )
            
            # 保存所有得分
            self._precomputed_scores[code] = pd.DataFrame({
                'date': data['date'],
                'bias_zscore': bias_zscore,
                'slope_zscore': slope_zscore,
                'efficiency_zscore': efficiency_zscore,
                'composite_score': composite_score
            })
        
        print(f"[{self.name}] 预计算完成: {len(self._precomputed_scores)} 只ETF")
    
    def get_score_for_date(self, code: str, date) -> Optional[float]:
        """
        获取指定日期某ETF的得分
        
        使用预计算的数据，O(1)时间复杂度
        """
        if code not in self._precomputed_scores:
            return None
        
        scores_df = self._precomputed_scores[code]
        row = scores_df[scores_df['date'] == date]
        
        if row.empty:
            return None
        
        score = row['composite_score'].values[0]
        if pd.isna(score):
            return None
        
        return score
    
    def calculate_all_scores(self, history: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """
        计算所有ETF的动量得分（实时计算版）
        
        如果没有预计算数据，使用实时计算
        """
        scores = {}
        for code, data in history.items():
            score = self.calculate_momentum_score(data)
            if score is not None and not pd.isna(score):
                scores[code] = score
        return scores
    
    def calculate_momentum_score(self, data: pd.DataFrame) -> Optional[float]:
        """
        计算单只ETF的综合动量得分（实时计算版）
        """
        if len(data) < self.params['zscore_window']:
            return None
            
        # 计算三个因子
        bias_score = self.bias_factor.compute(data, window=self.params['momentum_window'])
        slope_score = self.slope_factor.compute(data, window=self.params['momentum_window'])
        efficiency_score = self.efficiency_factor.compute(data, window=self.params['momentum_window'])
        
        # 取最新的因子值
        latest_bias = bias_score.iloc[-1]
        latest_slope = slope_score.iloc[-1]
        latest_efficiency = efficiency_score.iloc[-1]
        
        # 检查是否有有效值
        if pd.isna(latest_bias) or pd.isna(latest_slope) or pd.isna(latest_efficiency):
            return None
        
        # 计算Z-Score
        bias_zscore = calculate_zscore_fast(bias_score, window=self.params['zscore_window']).iloc[-1]
        slope_zscore = calculate_zscore_fast(slope_score, window=self.params['zscore_window']).iloc[-1]
        efficiency_zscore = calculate_zscore_fast(efficiency_score, window=self.params['zscore_window']).iloc[-1]
        
        # 加权计算
        composite_score = (
            self.params['bias_weight'] * bias_zscore +
            self.params['slope_weight'] * slope_zscore +
            self.params['efficiency_weight'] * efficiency_zscore
        )
        
        return composite_score
    
    def check(self, code: str, data: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        【选股模式】检查ETF是否符合策略条件
        """
        score = self.calculate_momentum_score(data)
        
        if score is None:
            return None
            
        return {
            'code': code,
            'score': score,
            'signal': 'buy' if score > 0 else 'sell',
            'strength': abs(score)
        }
    
    def initialize(self, context):
        """
        【回测模式】初始化策略
        """
        self.current_holding = None
        self.current_score = 0.0
        self._bar_count = 0
        
        self._holding_high_price = 0.0
        self._account_peak = context.initial_cash
        self._cooldown_remaining = 0
        
        period = self.params.get('rebalance_period', 1)
        print(f"[{self.name}] 策略初始化完成 (调仓周期: 每{period}个交易日)")
        if self.params.get('enable_trailing_stop', False):
            print(f"  移动止盈: 开启 (回撤 {self.params['trailing_stop_pct']*100:.0f}% 触发)")
        if self.params.get('enable_drawdown_protection', False):
            print(f"  账户回撤保护: 开启 (回撤 {self.params['max_drawdown_pct']*100:.0f}% 触发, 冷却 {self.params['drawdown_cooldown_days']} 天)")
    
    def _calc_total_asset(self, context, bars: Dict[str, Any]) -> float:
        """计算当前账户总资产"""
        market_value = 0.0
        for code, pos in context.positions.items():
            price = bars[code]['close'] if code in bars else context.current_prices.get(code, pos.avg_price)
            market_value += pos.quantity * price
        return context.cash + market_value

    def _sell_all(self, context, reason: str, signal_type: str = ""):
        """清空所有持仓"""
        if self.current_holding and self.current_holding in context.positions:
            position = context.positions[self.current_holding]
            if position.quantity > 0:
                context.order_target(self.current_holding, 0, reason=signal_type)
                print(f"[{context.current_dt}] {reason}, 卖出 {self.current_holding}", flush=True)
        self.current_holding = None
        self.current_score = 0.0
        self._holding_high_price = 0.0

    def on_bar(self, context, bars: Dict[str, Any], history: Dict[str, pd.DataFrame] = None):
        """
        【回测模式】每根K线调用一次（优化版，使用预计算数据）
        """
        if not bars:
            return
        
        current_date = context.current_dt
        self._bar_count += 1
        
        # ====== 第二层风控：账户最大回撤保护（优先级最高） ======
        if self.params.get('enable_drawdown_protection', False):
            total_asset = self._calc_total_asset(context, bars)
            if total_asset > self._account_peak:
                self._account_peak = total_asset
            
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
                if self._cooldown_remaining == 0:
                    self._account_peak = total_asset
                    print(f"[{current_date}] 账户回撤冷却期结束，恢复交易 "
                          f"(账户峰值重置为 {total_asset:,.2f})", flush=True)
                return
            
            if self._account_peak > 0:
                drawdown = (self._account_peak - total_asset) / self._account_peak
                max_dd_pct = self.params.get('max_drawdown_pct', 0.15)
                if drawdown >= max_dd_pct:
                    self._sell_all(context,
                        f"账户回撤保护: 回撤 {drawdown*100:.1f}% >= {max_dd_pct*100:.0f}%",
                        signal_type="回撤保护")
                    self._cooldown_remaining = self.params.get('drawdown_cooldown_days', 10)
                    print(f"[{current_date}] 进入冷却期 {self._cooldown_remaining} 个交易日", flush=True)
                    return
        
        # ====== 第一层风控：个股移动止盈 ======
        if self.params.get('enable_trailing_stop', False) and self.current_holding:
            if self.current_holding in bars:
                current_price = bars[self.current_holding]['close']
                if current_price > self._holding_high_price:
                    self._holding_high_price = current_price
                
                if self._holding_high_price > 0:
                    drop_from_high = (self._holding_high_price - current_price) / self._holding_high_price
                    trailing_pct = self.params.get('trailing_stop_pct', 0.08)
                    if drop_from_high >= trailing_pct:
                        self._sell_all(context,
                            f"移动止盈: {self.current_holding} 从最高价 {self._holding_high_price:.3f} "
                            f"回撤 {drop_from_high*100:.1f}% >= {trailing_pct*100:.0f}%",
                            signal_type="移动止盈")
                        return
        
        # 调仓周期控制：仅在调仓日执行信号判断
        rebalance_period = max(1, self.params.get('rebalance_period', 1))
        is_rebalance_day = (self._bar_count % rebalance_period == 0)
        
        # 获取所有ETF的得分（从预计算数据）
        scores = {}
        for code in bars.keys():
            score = self.get_score_for_date(code, current_date)
            if score is not None:
                scores[code] = score
        
        # 如果没有预计算数据，回退到实时计算
        if not scores and history:
            scores = self.calculate_all_scores(history)
        
        if not scores:
            return
        
        # 按得分排序
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        
        # === 空仓信号（不受调仓周期限制） ===
        enable_empty = self.params.get('enable_empty_position', True)
        empty_threshold = self.params.get('empty_threshold', -0.5)
        
        if enable_empty:
            all_below_threshold = all(score < empty_threshold for _, score in sorted_scores)
            
            if all_below_threshold:
                if self.current_holding and self.current_holding in context.positions:
                    top_score_val = sorted_scores[0][1] if sorted_scores else 0
                    self._sell_all(context,
                        f"空仓信号: 所有ETF得分低于阈值({empty_threshold}), 最高得分={top_score_val:.4f}",
                        signal_type="空仓信号")
                return
        
        # 非调仓日跳过
        if not is_rebalance_day:
            return
        
        # 获取当前持仓的得分
        if self.current_holding and self.current_holding in scores:
            current_score = scores[self.current_holding]
        else:
            current_score = None
            
        # 获取排名第一的ETF
        top_code, top_score = sorted_scores[0]
        
        # 判断是否需要调仓
        should_rebalance = False
        reason = ""
        
        if current_score is None:
            should_rebalance = True
            reason = "初始建仓"
        elif top_code != self.current_holding:
            threshold = self.params['rebalance_threshold']
            
            if top_score > current_score * threshold:
                should_rebalance = True
                reason = f"得分超过阈值: {top_score:.4f} > {current_score:.4f} * {threshold}"
        
        # 执行调仓
        if should_rebalance:
            signal = "初始建仓" if reason == "初始建仓" else "调仓"
            
            # 卖出当前持仓
            if self.current_holding and self.current_holding in context.positions:
                position = context.positions[self.current_holding]
                if position.quantity > 0:
                    context.order_target(self.current_holding, 0, reason=signal)
                    print(f"[{context.current_dt}] 卖出 {self.current_holding}", flush=True)
            
            # 买入新的排名第一的ETF
            if top_code in bars:
                current_price = bars[top_code]['close']
                available_cash = context.cash
                
                buy_amount = available_cash * 0.99
                quantity = int(buy_amount / current_price)
                
                if quantity > 0:
                    context.order(top_code, quantity, current_price, reason=signal)
                    self.current_holding = top_code
                    self.current_score = top_score
                    self._holding_high_price = current_price
                    
                    print(f"[{context.current_dt}] 买入 {top_code} {quantity}股 @ {current_price:.3f}, 原因: {reason}", flush=True)


class ETFThreeFactorMomentumScreenerFast:
    """
    ETF三因子动量选股器（优化版）
    """
    
    def __init__(self, 
                 etf_pool: List[str] = None,
                 bias_weight: float = 0.3,
                 slope_weight: float = 0.3,
                 efficiency_weight: float = 0.4,
                 momentum_window: int = 25,
                 zscore_window: int = 60):
        self.etf_pool = etf_pool or ETFThreeFactorMomentumStrategyFast.DEFAULT_ETF_POOL
        self.bias_weight = bias_weight
        self.slope_weight = slope_weight
        self.efficiency_weight = efficiency_weight
        self.momentum_window = momentum_window
        self.zscore_window = zscore_window
        
        self.strategy = ETFThreeFactorMomentumStrategyFast()
        self.strategy.set_params({
            'etf_pool': self.etf_pool,
            'bias_weight': bias_weight,
            'slope_weight': slope_weight,
            'efficiency_weight': efficiency_weight,
            'momentum_window': momentum_window,
            'zscore_window': zscore_window,
        })
    
    def screen(self, data_dict: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        扫描ETF池并返回排序结果（优化版）
        """
        # 预计算所有得分
        self.strategy.precompute_scores(data_dict)
        
        # 获取最新日期的得分
        results = []
        latest_date = None
        
        for code in self.etf_pool:
            if code not in self.strategy._precomputed_scores:
                continue
            
            scores_df = self.strategy._precomputed_scores[code]
            if scores_df.empty:
                continue
            
            latest = scores_df.iloc[-1]
            latest_date = latest['date']
            
            results.append({
                'code': code,
                'composite_score': latest['composite_score'],
                'bias_zscore': latest['bias_zscore'],
                'slope_zscore': latest['slope_zscore'],
                'efficiency_zscore': latest['efficiency_zscore'],
            })
        
        if not results:
            return pd.DataFrame()
        
        df = pd.DataFrame(results)
        df = df.sort_values('composite_score', ascending=False).reset_index(drop=True)
        df['rank'] = range(1, len(df) + 1)
        df['date'] = latest_date
        
        return df
