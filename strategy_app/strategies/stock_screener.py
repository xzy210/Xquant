"""
智能选股器 - 专为波动率突破策略优化

功能：
1. 多维度技术指标筛选
2. 智能评分排序
3. 批量数据处理
4. 结果导出和可视化
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

from common.data_portal import DataPortal


@dataclass
class ScreeningCriteria:
    """筛选条件配置"""
    # 波动率条件
    min_atr_pct: float = 0.02          # 最小ATR百分比
    max_atr_pct: float = 0.05          # 最大ATR百分比
    atr_period: int = 20               # ATR计算周期
    
    # 趋势条件
    adx_period: int = 14               # ADX计算周期
    min_adx: float = 25.0              # 最小ADX值
    trend_ma_period: int = 20          # 趋势均线周期
    
    # 成交量条件
    min_avg_amount: float = 100_000_000  # 最小日均成交额（1亿）
    volume_lookback: int = 20          # 成交量计算周期
    
    # 价格条件
    min_price: float = 5.0             # 最小股价（避免低价股）
    max_price: float = 500.0           # 最大股价
    min_market_cap: float = 5e9        # 最小市值（50亿）
    
    # 形态条件
    proximity_to_high: float = 0.95    # 接近近期高点的比例
    high_lookback: int = 20            # 近期高点计算周期
    
    # 风险控制
    exclude_st: bool = True            # 排除ST股
    exclude_new_listing: int = 60      # 排除上市不足N天的股票
    max_stocks: int = 50               # 最大返回股票数


@dataclass
class StockScore:
    """股票评分结果"""
    code: str
    name: str = ""
    total_score: float = 0.0
    
    # 各项指标得分（0-100）
    volatility_score: float = 0.0      # 波动率得分
    trend_score: float = 0.0           # 趋势得分
    volume_score: float = 0.0          # 成交量得分
    pattern_score: float = 0.0         # 形态得分
    momentum_score: float = 0.0        # 动量得分
    
    # 原始指标值
    atr_pct: float = 0.0               # ATR百分比
    adx: float = 0.0                   # ADX值
    avg_amount: float = 0.0            # 平均成交额
    distance_to_high: float = 0.0      # 距离近期高点比例
    return_20d: float = 0.0            # 20日收益率
    
    # 筛选通过情况
    passed_filters: Dict[str, bool] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'code': self.code,
            'name': self.name,
            'total_score': round(self.total_score, 2),
            'volatility_score': round(self.volatility_score, 2),
            'trend_score': round(self.trend_score, 2),
            'volume_score': round(self.volume_score, 2),
            'pattern_score': round(self.pattern_score, 2),
            'momentum_score': round(self.momentum_score, 2),
            'atr_pct': round(self.atr_pct * 100, 2),  # 转为百分比
            'adx': round(self.adx, 2),
            'avg_amount': round(self.avg_amount / 1e8, 2),  # 转为亿
            'distance_to_high': round(self.distance_to_high * 100, 2),
            'return_20d': round(self.return_20d * 100, 2),
            'passed_filters': self.passed_filters
        }


class TechnicalIndicators:
    """技术指标计算类"""
    
    @staticmethod
    def atr(data: pd.DataFrame, period: int = 20) -> pd.Series:
        """计算ATR（平均真实波幅）"""
        high, low, close = data['high'], data['low'], data['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    
    @staticmethod
    def adx(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算ADX（平均趋向指数）"""
        high, low, close = data['high'], data['low'], data['close']
        
        # +DM和-DM
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        # 真实波幅
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # 平滑
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * plus_dm.rolling(window=period).mean() / atr
        minus_di = 100 * minus_dm.rolling(window=period).mean() / atr
        
        # DX和ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        
        return adx
    
    @staticmethod
    def rsi(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算RSI（相对强弱指数）"""
        close = data['close']
        delta = close.diff()
        
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    @staticmethod
    def bollinger_bands(data: pd.DataFrame, period: int = 20, std_dev: float = 2.0):
        """计算布林带"""
        close = data['close']
        middle = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        
        return upper, middle, lower
    
    @staticmethod
    def macd(data: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
        """计算MACD"""
        close = data['close']
        ema_fast = close.ewm(span=fast).mean()
        ema_slow = close.ewm(span=slow).mean()
        
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal).mean()
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram


class StockScreener:
    """
    智能选股器
    
    使用示例：
        screener = StockScreener(data_dir='./data')
        criteria = ScreeningCriteria(min_atr_pct=0.025, max_atr_pct=0.04)
        results = screener.screen(stock_list, criteria)
    """
    
    def __init__(self, data_dir: str, max_workers: int = 4):
        """
        初始化选股器
        
        Args:
            data_dir: 数据目录路径
            max_workers: 并行处理的工作进程数
        """
        self.data_dir = Path(data_dir)
        self.max_workers = max_workers
        self.indicators = TechnicalIndicators()
        self._cache = {}  # 数据缓存
    
    def load_data(self, code: str) -> Optional[pd.DataFrame]:
        """加载股票数据（带缓存）"""
        if code in self._cache:
            return self._cache[code]
        
        df = DataPortal(default_data_dir=self.data_dir).get_daily_bars(
            code,
            asset_type="stock",
            data_dir=self.data_dir,
            use_cache=False,
        )
        if df is not None and not df.empty:
            self._cache[code] = df
            return df
        
        return None
    
    def screen_single(self, code: str, criteria: ScreeningCriteria, 
                      name: str = "") -> Optional[StockScore]:
        """
        对单只股票进行筛选和评分
        
        Returns:
            StockScore对象（通过筛选）或None（未通过）
        """
        data = self.load_data(code)
        if data is None or len(data) < 60:
            return None
        
        # 确保数据足够
        min_periods = max(criteria.atr_period, criteria.adx_period, 
                         criteria.volume_lookback, criteria.high_lookback) + 5
        if len(data) < min_periods:
            return None
        
        # 获取最新数据
        latest = data.iloc[-1]
        current_price = latest['close']
        
        # 基础过滤
        if not (criteria.min_price <= current_price <= criteria.max_price):
            return None
        
        # 计算指标
        atr = self.indicators.atr(data, criteria.atr_period)
        adx = self.indicators.adx(data, criteria.adx_period)
        
        current_atr = atr.iloc[-1]
        current_adx = adx.iloc[-1]
        
        if pd.isna(current_atr) or pd.isna(current_adx):
            return None
        
        atr_pct = current_atr / current_price
        
        # 成交额
        amount = data['volume'] * data['close']
        avg_amount = amount.tail(criteria.volume_lookback).mean()
        
        # 近期高点
        recent_high = data['high'].tail(criteria.high_lookback).max()
        distance_to_high = current_price / recent_high
        
        # 收益率
        return_20d = (current_price - data['close'].iloc[-20]) / data['close'].iloc[-20]
        
        # 执行筛选
        filters = {
            '波动率适中': criteria.min_atr_pct <= atr_pct <= criteria.max_atr_pct,
            '趋势强度': current_adx >= criteria.min_adx,
            '流动性充足': avg_amount >= criteria.min_avg_amount,
            '接近高点': distance_to_high >= criteria.proximity_to_high,
            '价格适中': criteria.min_price <= current_price <= criteria.max_price,
        }
        
        # 计算各项得分
        score = StockScore(code=code, name=name)
        
        # 1. 波动率得分（理想区间2.5%-4%得满分）
        if 0.025 <= atr_pct <= 0.04:
            score.volatility_score = 100
        elif criteria.min_atr_pct <= atr_pct <= criteria.max_atr_pct:
            score.volatility_score = 80
        else:
            score.volatility_score = max(0, 100 - abs(atr_pct - 0.03) * 2000)
        
        # 2. 趋势得分
        if current_adx >= 40:
            score.trend_score = 100
        elif current_adx >= 30:
            score.trend_score = 80
        elif current_adx >= 25:
            score.trend_score = 60
        else:
            score.trend_score = max(0, current_adx * 2)
        
        # 3. 成交量得分
        if avg_amount >= 500_000_000:
            score.volume_score = 100
        elif avg_amount >= 200_000_000:
            score.volume_score = 80
        elif avg_amount >= 100_000_000:
            score.volume_score = 60
        else:
            score.volume_score = max(0, avg_amount / 100_000_000 * 60)
        
        # 4. 形态得分（接近近期高点但不要太高）
        if 0.95 <= distance_to_high <= 0.99:
            score.pattern_score = 100  # 即将突破
        elif 0.90 <= distance_to_high < 0.95:
            score.pattern_score = 80
        elif distance_to_high >= 0.99:
            score.pattern_score = 60  # 已经很高，追高风险
        else:
            score.pattern_score = max(0, distance_to_high * 100)
        
        # 5. 动量得分（近期涨幅适中）
        if 0.05 <= return_20d <= 0.30:  # 5%-30%涨幅最佳
            score.momentum_score = 100
        elif 0 <= return_20d < 0.05:
            score.momentum_score = 70
        elif return_20d > 0.30:
            score.momentum_score = 50  # 涨幅过大，可能回调
        else:
            score.momentum_score = max(0, 50 + return_20d * 100)
        
        # 计算总分（加权平均）
        weights = {
            'volatility': 0.25,
            'trend': 0.25,
            'volume': 0.20,
            'pattern': 0.15,
            'momentum': 0.15
        }
        
        score.total_score = (
            score.volatility_score * weights['volatility'] +
            score.trend_score * weights['trend'] +
            score.volume_score * weights['volume'] +
            score.pattern_score * weights['pattern'] +
            score.momentum_score * weights['momentum']
        )
        
        # 填充原始数据
        score.atr_pct = atr_pct
        score.adx = current_adx
        score.avg_amount = avg_amount
        score.distance_to_high = distance_to_high
        score.return_20d = return_20d
        score.passed_filters = filters
        
        # 检查是否通过所有筛选
        if not all(filters.values()):
            return None
        
        return score
    
    def screen(self, stock_list: List[str], 
               criteria: Optional[ScreeningCriteria] = None,
               name_map: Optional[Dict[str, str]] = None,
               progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[StockScore]:
        """
        批量选股
        
        Args:
            stock_list: 股票代码列表
            criteria: 筛选条件（默认使用默认配置）
            name_map: 股票代码到名称的映射
            progress_callback: 进度回调函数(current, total, current_code)
        
        Returns:
            通过筛选的股票列表（按得分排序）
        """
        if criteria is None:
            criteria = ScreeningCriteria()
        
        results = []
        total = len(stock_list)
        
        print(f"开始筛选 {total} 只股票...")
        
        # 串行处理（避免多进程数据问题）
        for i, code in enumerate(stock_list):
            name = name_map.get(code, "") if name_map else ""
            
            try:
                score = self.screen_single(code, criteria, name)
                if score:
                    results.append(score)
                    print(f"✓ {code} 通过筛选，得分: {score.total_score:.1f}")
            except Exception as e:
                print(f"× {code} 处理失败: {e}")
            
            if progress_callback:
                progress_callback(i + 1, total, code)
        
        # 按总分排序
        results.sort(key=lambda x: x.total_score, reverse=True)
        
        # 限制数量
        if criteria.max_stocks and len(results) > criteria.max_stocks:
            results = results[:criteria.max_stocks]
        
        print(f"\n筛选完成: {len(results)}/{total} 只股票通过")
        return results
    
    def export_results(self, results: List[StockScore], 
                       output_path: str,
                       format: str = 'csv'):
        """
        导出选股结果
        
        Args:
            results: 选股结果列表
            output_path: 输出文件路径
            format: 输出格式（csv, json, excel）
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 转换为DataFrame
        data = [r.to_dict() for r in results]
        df = pd.DataFrame(data)
        
        if format == 'csv':
            df.to_csv(output_path, index=False, encoding='utf-8-sig')
        elif format == 'json':
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        elif format == 'excel':
            df.to_excel(output_path, index=False)
        
        print(f"结果已导出: {output_path}")
    
    def get_statistics(self, results: List[StockScore]) -> Dict:
        """获取选股结果统计信息"""
        if not results:
            return {}
        
        scores = [r.total_score for r in results]
        atrs = [r.atr_pct for r in results]
        adxs = [r.adx for r in results]
        
        return {
            'count': len(results),
            'avg_score': np.mean(scores),
            'score_range': (min(scores), max(scores)),
            'avg_atr_pct': np.mean(atrs) * 100,
            'avg_adx': np.mean(adxs),
            'top_5': [r.code for r in results[:5]]
        }


# ============== 便捷使用函数 ==============

def quick_screen(data_dir: str, 
                 stock_list: List[str],
                 top_n: int = 20,
                 **criteria_kwargs) -> List[StockScore]:
    """
    快速选股函数
    
    Args:
        data_dir: 数据目录
        stock_list: 股票列表
        top_n: 返回前N名
        **criteria_kwargs: 筛选条件参数
    
    Returns:
        选股结果列表
    """
    criteria = ScreeningCriteria(max_stocks=top_n, **criteria_kwargs)
    screener = StockScreener(data_dir)
    return screener.screen(stock_list, criteria)


def screen_for_volatility_breakout(data_dir: str,
                                   stock_list: List[str],
                                   top_n: int = 30) -> List[StockScore]:
    """
    专为波动率突破策略优化的选股函数
    
    预设最佳参数组合
    """
    criteria = ScreeningCriteria(
        min_atr_pct=0.02,
        max_atr_pct=0.05,
        min_adx=25.0,
        min_avg_amount=100_000_000,
        proximity_to_high=0.92,
        max_stocks=top_n
    )
    
    screener = StockScreener(data_dir)
    return screener.screen(stock_list, criteria)


# ============== 命令行运行 ==============

if __name__ == "__main__":
    import sys
    
    # 简单测试
    print("=" * 60)
    print("智能选股器 - 测试模式")
    print("=" * 60)
    
    # 模拟数据路径和股票列表
    data_dir = "./data"
    test_stocks = ["000001.SZ", "000002.SZ", "600000.SH"]
    
    criteria = ScreeningCriteria(
        min_atr_pct=0.02,
        max_atr_pct=0.06,
        min_adx=20.0,
        max_stocks=10
    )
    
    screener = StockScreener(data_dir)
    results = screener.screen(test_stocks, criteria)
    
    print("\n选股结果:")
    for r in results:
        print(f"{r.code}: 总分={r.total_score:.1f}, ATR={r.atr_pct*100:.2f}%, ADX={r.adx:.1f}")
