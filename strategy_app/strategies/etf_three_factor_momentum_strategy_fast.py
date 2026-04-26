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
import pandas as pd
import numpy as np
from typing import Any, ClassVar, Dict, Optional, List
from common.data_portal import MarketDataBundle
from common.execution_contract import StrategySignal
from common.strategy_spec import StrategySpec

from .base_strategy import BaseStrategy
from ..factors.registry import factor_registry
from ..factors.etf_momentum_factors_optimized import calculate_zscore_fast
from ..factors import etf_momentum_factors_optimized  # noqa: F401 - trigger registration


class ETFThreeFactorMomentumStrategyFast(BaseStrategy):
    """
    ETF三因子动量轮动策略（优化版）
    
    性能提升5-10倍，适合大规模回测
    """

    spec: ClassVar[StrategySpec] = StrategySpec(
        strategy_id="etf_rotation",
        strategy_name="ETF轮动实盘",
        owner_type="etf_rotation",
        asset_class="etf",
        frequency="daily",
        universe=["510880", "159949", "513100", "518880"],
        plugin_id="etf_rotation",
        plugin_name="ETF轮动实盘",
        plugin_tab_key="etf",
        plugin_tab_title="ETF轮动实盘",
        metadata={
            "source": "strategy_app",
            "strategy_family": "etf_rotation",
            "research_alias": "etf_three_factor_momentum",
        },
    )
    
    # 默认ETF标的池
    DEFAULT_ETF_POOL = ['510880', '159949', '513100', '518880']

    # 默认因子配置: [(因子注册名, 权重)]
    DEFAULT_FACTOR_CONFIG = [
        ('bias_momentum_fast', 0.3),
        ('slope_momentum_fast', 0.3),
        ('efficiency_momentum_fast', 0.4),
    ]
    
    def __init__(self):
        super().__init__()
        self.name = "ETF三因子动量轮动策略（优化版）"
        self.description = "基于快速因子计算的ETF轮动策略"
        
        self.params = {
            'etf_pool': self.DEFAULT_ETF_POOL,
            'factor_config': list(self.DEFAULT_FACTOR_CONFIG),
            'rebalance_threshold': 1.5,
            'momentum_window': 25,
            'zscore_window': 60,
            'empty_threshold': -0.5,
            'enable_empty_position': True,
            'rebalance_period': 1,
            'enable_trailing_stop': True,
            'trailing_stop_pct': 0.08,
            'enable_drawdown_protection': True,
            'max_drawdown_pct': 0.15,
            'drawdown_cooldown_days': 10,
        }
        
        # 回测状态
        self.current_holding: Optional[str] = None
        self.current_score: float = 0.0
        self._bar_count: int = 0
        
        # 风控状态
        self._holding_high_price: float = 0.0
        self._account_peak: float = 0.0
        self._cooldown_remaining: int = 0
        
        # 预计算的因子数据 {code: DataFrame}
        self._precomputed_scores: Dict[str, pd.DataFrame] = {}
        self._pending_signals: List[StrategySignal] = []

    def _get_factor_config(self) -> List[tuple]:
        """获取当前因子配置列表 [(name, weight), ...]"""
        return self.params.get('factor_config', self.DEFAULT_FACTOR_CONFIG)
        
    def set_params(self, params: Dict[str, Any]):
        """设置策略参数"""
        super().set_params(params)
        
    def precompute_scores(self, all_data: Dict[str, pd.DataFrame]):
        """
        预计算所有ETF的因子得分（动态因子版）
        
        根据 params['factor_config'] 中配置的因子名称和权重，
        从全局 factor_registry 获取因子实例并计算。
        """
        if isinstance(all_data, MarketDataBundle):
            return self.precompute_scores_from_bundle(all_data)

        factor_config = self._get_factor_config()
        factor_names = [name for name, _ in factor_config]
        print(f"[{self.name}] 预计算因子得分 (因子: {factor_names})...")
        
        for code, data in all_data.items():
            if len(data) < self.params['zscore_window']:
                print(f"  ⚠ {code}: 数据不足")
                continue
            
            score_data = {'date': data['date']}
            composite = pd.Series(0.0, index=data.index)
            
            for fname, weight in factor_config:
                factor = factor_registry.get(fname)
                if factor is None:
                    print(f"  ⚠ 因子 '{fname}' 未注册，跳过")
                    continue
                raw = factor.compute(data, window=self.params['momentum_window'])
                zscore = calculate_zscore_fast(raw, window=self.params['zscore_window'])
                score_data[f'{fname}_zscore'] = zscore
                composite = composite + weight * zscore.fillna(0)
            
            # 如果某日期所有因子的 zscore 都是 NaN，composite 应为 NaN
            zscore_columns = {k: v for k, v in score_data.items() if k.endswith('_zscore')}
            if not zscore_columns:
                score_data['composite_score'] = pd.Series(np.nan, index=data.index)
                self._precomputed_scores[code] = pd.DataFrame(score_data)
                continue
            all_nan_mask = pd.DataFrame(zscore_columns, index=data.index).isna().all(axis=1)
            composite[all_nan_mask] = np.nan
            
            score_data['composite_score'] = composite
            self._precomputed_scores[code] = pd.DataFrame(score_data)
        
        print(f"[{self.name}] 预计算完成: {len(self._precomputed_scores)} 只ETF")

    def precompute_scores_from_bundle(self, bundle: MarketDataBundle):
        """Precompute factor scores from the unified market data contract."""
        self.on_data_bundle(bundle)
        return self.precompute_scores(bundle.to_data_dict())
    
    def get_score_for_date(self, code: str, date) -> Optional[float]:
        """获取指定日期某ETF的得分（从预计算数据）"""
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
        """计算所有ETF的动量得分（实时计算版，动态因子）"""
        scores = {}
        for code, data in history.items():
            score = self.calculate_momentum_score(data)
            if score is not None and not pd.isna(score):
                scores[code] = score
        return scores
    
    def calculate_momentum_score(self, data: pd.DataFrame) -> Optional[float]:
        """计算单只ETF的综合动量得分（实时计算版，动态因子）"""
        if len(data) < self.params['zscore_window']:
            return None

        factor_config = self._get_factor_config()
        composite = 0.0
        any_valid = False

        for fname, weight in factor_config:
            factor = factor_registry.get(fname)
            if factor is None:
                continue
            raw = factor.compute(data, window=self.params['momentum_window'])
            if pd.isna(raw.iloc[-1]):
                continue
            zscore_val = calculate_zscore_fast(raw, window=self.params['zscore_window']).iloc[-1]
            if pd.isna(zscore_val):
                continue
            composite += weight * zscore_val
            any_valid = True
        
        return composite if any_valid else None
    
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
        self._pending_signals = []
        
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
                self._pending_signals.append(
                    StrategySignal(
                        symbol=self.current_holding,
                        action="sell",
                            strategy_id=self.strategy_id,
                            strategy_name=self.strategy_name,
                        target_quantity=0,
                        reason=signal_type or reason,
                        timestamp=context.current_dt,
                    )
                )
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
                    self._pending_signals.append(
                        StrategySignal(
                            symbol=self.current_holding,
                            action="sell",
                            strategy_id=self.strategy_id,
                            strategy_name=self.strategy_name,
                            target_quantity=0,
                            reason=signal,
                            timestamp=context.current_dt,
                        )
                    )
                    print(f"[{context.current_dt}] 卖出 {self.current_holding}", flush=True)
            
            # 买入新的排名第一的ETF
            if top_code in bars:
                current_price = bars[top_code]['close']
                if current_price > 0:
                    self._pending_signals.append(
                        StrategySignal(
                            symbol=top_code,
                            action="buy",
                            strategy_id=self.strategy_id,
                            strategy_name=self.strategy_name,
                            target_percent=0.99,
                            price=float(current_price),
                            reason=signal,
                            timestamp=context.current_dt,
                            metadata={"rotation_reason": reason},
                        )
                    )
                    estimated_quantity = int((context.cash * 0.99) / current_price)
                    self.current_holding = top_code
                    self.current_score = top_score
                    self._holding_high_price = current_price
                    
                    print(f"[{context.current_dt}] 买入 {top_code} {estimated_quantity}股 @ {current_price:.3f}, 原因: {reason}", flush=True)

    def generate_signals(self, data: Any, context: Any = None) -> list[StrategySignal]:
        """Return signals generated during the current unified backtest event."""
        signals = list(self._pending_signals)
        self._pending_signals = []
        return signals


class ETFThreeFactorMomentumScreenerFast:
    """
    ETF三因子动量选股器（优化版）
    """
    
    def __init__(self, 
                 etf_pool: List[str] = None,
                 factor_config: List[tuple] = None,
                 momentum_window: int = 25,
                 zscore_window: int = 60):
        self.etf_pool = etf_pool or ETFThreeFactorMomentumStrategyFast.DEFAULT_ETF_POOL
        self.factor_config = factor_config or list(ETFThreeFactorMomentumStrategyFast.DEFAULT_FACTOR_CONFIG)
        self.momentum_window = momentum_window
        self.zscore_window = zscore_window
        
        self.strategy = ETFThreeFactorMomentumStrategyFast()
        self.strategy.set_params({
            'etf_pool': self.etf_pool,
            'factor_config': self.factor_config,
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
            
            row_data = {
                'code': code,
                'composite_score': latest['composite_score'],
            }
            for col in scores_df.columns:
                if col.endswith('_zscore'):
                    row_data[col] = latest[col]
            results.append(row_data)
        
        if not results:
            return pd.DataFrame()
        
        df = pd.DataFrame(results)
        df = df.sort_values('composite_score', ascending=False).reset_index(drop=True)
        df['rank'] = range(1, len(df) + 1)
        df['date'] = latest_date
        
        return df
