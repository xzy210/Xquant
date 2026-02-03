"""
Financial factors implementation

Financial factors are different from technical factors - they are loaded from
pre-downloaded Tushare data rather than computed from OHLCV data in real-time.

These factors include:
- Valuation factors: PE, PB, PS
- Profitability factors: ROE, ROA, gross margin
- Growth factors: Net profit growth, revenue growth
- Liquidity factors: Current ratio, quick ratio
"""
from typing import Optional
import pandas as pd
import numpy as np
from pathlib import Path

from .base_factor import BaseFactor
from .registry import factor_registry


class FinancialBaseFactor(BaseFactor):
    """
    Base class for financial factors
    
    Financial factors read from cached Tushare data files.
    The compute method merges financial data with input DataFrame by date.
    """
    
    @property
    def data_source(self) -> str:
        """
        Data source type: 'daily_basic' or 'fina_indicator'
        """
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        """
        Column name in the source data
        """
        raise NotImplementedError
    
    def _get_financial_data_dir(self) -> Path:
        """Get the financial data directory"""
        project_root = Path(__file__).parent.parent.parent
        return project_root / "data" / "financial"
    
    def _load_financial_data(self, code: str) -> Optional[pd.DataFrame]:
        """Load financial data for a stock"""
        data_dir = self._get_financial_data_dir()
        
        if self.data_source == "daily_basic":
            file_path = data_dir / "daily_basic" / f"{code}.csv"
        else:
            file_path = data_dir / "fina_indicator" / f"{code}.csv"
            
        if not file_path.exists():
            return None
            
        try:
            df = pd.read_csv(file_path)
            return df
        except Exception as e:
            print(f"Error loading financial data for {code}: {e}")
            return None
    
    def compute(self, df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
        """
        Compute financial factor by merging with cached financial data
        
        Args:
            df: DataFrame with 'date' column and optionally 'code' column
            window: Not used for financial factors
            
        Returns:
            pd.Series with factor values
        """
        # Get stock code from DataFrame
        code = None
        if 'code' in df.columns:
            code = str(df['code'].iloc[0])
        elif 'ts_code' in df.columns:
            code = str(df['ts_code'].iloc[0]).split('.')[0]
        elif 'stock_code' in df.columns:
            code = str(df['stock_code'].iloc[0])
        
        if code is None:
            # Try to infer from data directory context
            return pd.Series(np.nan, index=df.index)
        
        # Load financial data
        fin_df = self._load_financial_data(code)
        if fin_df is None or fin_df.empty:
            return pd.Series(np.nan, index=df.index)
        
        # Prepare date columns for merging
        if 'date' in df.columns:
            df_dates = pd.to_datetime(df['date'])
        else:
            return pd.Series(np.nan, index=df.index)
        
        if self.data_source == "daily_basic":
            # Daily basic data - direct date match
            if 'trade_date' in fin_df.columns:
                fin_df['trade_date'] = pd.to_datetime(fin_df['trade_date'], format='%Y%m%d')
                fin_df = fin_df.rename(columns={'trade_date': 'date'})
            
            # Merge by date
            merge_df = df[['date']].copy()
            merge_df['date'] = df_dates
            
            if self.source_column not in fin_df.columns:
                return pd.Series(np.nan, index=df.index)
            
            fin_subset = fin_df[['date', self.source_column]].drop_duplicates(subset=['date'])
            merged = merge_df.merge(fin_subset, on='date', how='left')
            
            result = merged[self.source_column].values
            return pd.Series(result, index=df.index)
            
        else:
            # Financial indicator data - use most recent report
            if 'ann_date' in fin_df.columns:
                fin_df['ann_date'] = pd.to_datetime(fin_df['ann_date'], format='%Y%m%d', errors='coerce')
            elif 'end_date' in fin_df.columns:
                fin_df['ann_date'] = pd.to_datetime(fin_df['end_date'], format='%Y%m%d', errors='coerce')
            else:
                return pd.Series(np.nan, index=df.index)
            
            if self.source_column not in fin_df.columns:
                return pd.Series(np.nan, index=df.index)
            
            # Sort by announcement date
            fin_df = fin_df.sort_values('ann_date')
            
            # For each date in df, find the most recent financial report
            result = []
            for date in df_dates:
                mask = fin_df['ann_date'] <= date
                if mask.any():
                    value = fin_df.loc[mask, self.source_column].iloc[-1]
                else:
                    value = np.nan
                result.append(value)
            
            return pd.Series(result, index=df.index)


# ==================== Valuation Factors ====================

@factor_registry.register
class PE_TTM(FinancialBaseFactor):
    """PE (TTM) factor - Price to Earnings ratio (trailing twelve months)"""
    
    @property
    def name(self) -> str:
        return "pe_ttm"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "市盈率(TTM) - 股价/每股收益(滚动12个月)"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "pe_ttm"


@factor_registry.register
class PE(FinancialBaseFactor):
    """PE factor - Price to Earnings ratio"""
    
    @property
    def name(self) -> str:
        return "pe"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "市盈率 - 股价/每股收益"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "pe"


@factor_registry.register
class PB(FinancialBaseFactor):
    """PB factor - Price to Book ratio"""
    
    @property
    def name(self) -> str:
        return "pb"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "市净率 - 股价/每股净资产"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "pb"


@factor_registry.register  
class PS_TTM(FinancialBaseFactor):
    """PS (TTM) factor - Price to Sales ratio"""
    
    @property
    def name(self) -> str:
        return "ps_ttm"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "市销率(TTM) - 股价/每股销售额(滚动12个月)"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "ps_ttm"


@factor_registry.register
class DividendYield(FinancialBaseFactor):
    """Dividend Yield factor"""
    
    @property
    def name(self) -> str:
        return "dv_ttm"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "股息率(TTM) - 每股股息/股价"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "dv_ttm"


@factor_registry.register
class TotalMarketValue(FinancialBaseFactor):
    """Total Market Value factor"""
    
    @property
    def name(self) -> str:
        return "total_mv"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "总市值(万元)"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "total_mv"


@factor_registry.register
class CirculatingMarketValue(FinancialBaseFactor):
    """Circulating Market Value factor"""
    
    @property
    def name(self) -> str:
        return "circ_mv"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "流通市值(万元)"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "circ_mv"


# ==================== Profitability Factors ====================

@factor_registry.register
class ROE(FinancialBaseFactor):
    """ROE factor - Return on Equity"""
    
    @property
    def name(self) -> str:
        return "roe"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "净资产收益率(ROE) - 净利润/净资产"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "roe"


@factor_registry.register
class ROE_Diluted(FinancialBaseFactor):
    """ROE (Diluted) factor"""
    
    @property
    def name(self) -> str:
        return "roe_dt"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "净资产收益率(摊薄)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "roe_dt"


@factor_registry.register
class ROA(FinancialBaseFactor):
    """ROA factor - Return on Assets"""
    
    @property
    def name(self) -> str:
        return "roa"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "总资产收益率(ROA) - 净利润/总资产"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "roa"


@factor_registry.register
class ROIC(FinancialBaseFactor):
    """ROIC factor - Return on Invested Capital"""
    
    @property
    def name(self) -> str:
        return "roic"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "投资资本回报率(ROIC)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "roic"


@factor_registry.register
class GrossMargin(FinancialBaseFactor):
    """Gross Margin factor"""
    
    @property
    def name(self) -> str:
        return "gross_margin"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "销售毛利率 - 毛利/营业收入"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "gross_margin"


@factor_registry.register
class EPS(FinancialBaseFactor):
    """EPS factor - Earnings Per Share"""
    
    @property
    def name(self) -> str:
        return "eps"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "每股收益(EPS)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "eps"


# ==================== Growth Factors ====================

@factor_registry.register
class NetProfitGrowth(FinancialBaseFactor):
    """Net Profit YoY Growth factor"""
    
    @property
    def name(self) -> str:
        return "netprofit_yoy"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "净利润同比增长率(%)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "netprofit_yoy"


@factor_registry.register
class NetProfitGrowthDeducted(FinancialBaseFactor):
    """Deducted Net Profit YoY Growth factor"""
    
    @property
    def name(self) -> str:
        return "dt_netprofit_yoy"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "扣非净利润同比增长率(%)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "dt_netprofit_yoy"


@factor_registry.register
class RevenueGrowth(FinancialBaseFactor):
    """Revenue YoY Growth factor"""
    
    @property
    def name(self) -> str:
        return "tr_yoy"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "营业总收入同比增长率(%)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "tr_yoy"


@factor_registry.register
class OperatingRevenueGrowth(FinancialBaseFactor):
    """Operating Revenue YoY Growth factor"""
    
    @property
    def name(self) -> str:
        return "or_yoy"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "营业收入同比增长率(%)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "or_yoy"


@factor_registry.register
class OperatingProfitGrowth(FinancialBaseFactor):
    """Operating Profit YoY Growth factor"""
    
    @property
    def name(self) -> str:
        return "op_yoy"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "营业利润同比增长率(%)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "op_yoy"


@factor_registry.register
class EPSGrowth(FinancialBaseFactor):
    """EPS YoY Growth factor"""
    
    @property
    def name(self) -> str:
        return "basic_eps_yoy"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "基本每股收益同比增长率(%)"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "basic_eps_yoy"


# ==================== Liquidity/Solvency Factors ====================

@factor_registry.register
class CurrentRatio(FinancialBaseFactor):
    """Current Ratio factor"""
    
    @property
    def name(self) -> str:
        return "current_ratio"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "流动比率 - 流动资产/流动负债"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "current_ratio"


@factor_registry.register
class QuickRatio(FinancialBaseFactor):
    """Quick Ratio factor"""
    
    @property
    def name(self) -> str:
        return "quick_ratio"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "速动比率 - (流动资产-存货)/流动负债"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "quick_ratio"


@factor_registry.register
class DebtToAssets(FinancialBaseFactor):
    """Debt to Assets Ratio factor"""
    
    @property
    def name(self) -> str:
        return "debt_to_assets"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "资产负债率 - 总负债/总资产"
    
    @property
    def data_source(self) -> str:
        return "fina_indicator"
    
    @property
    def source_column(self) -> str:
        return "debt_to_assets"


# ==================== Turnover Factors ====================

@factor_registry.register
class TurnoverRate(FinancialBaseFactor):
    """Turnover Rate factor (from daily basic)"""
    
    @property
    def name(self) -> str:
        return "turnover_rate_daily"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "换手率(%) - 成交量/流通股本"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "turnover_rate"


@factor_registry.register
class TurnoverRateFree(FinancialBaseFactor):
    """Free Float Turnover Rate factor"""
    
    @property
    def name(self) -> str:
        return "turnover_rate_f"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "换手率(自由流通股本)"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "turnover_rate_f"


@factor_registry.register
class VolumeRatioDaily(FinancialBaseFactor):
    """Volume Ratio factor (from daily basic)"""
    
    @property
    def name(self) -> str:
        return "volume_ratio_daily"
    
    @property
    def category(self) -> str:
        return "financial"
    
    @property
    def description(self) -> str:
        return "量比 - 当日成交量/过去5日平均成交量"
    
    @property
    def data_source(self) -> str:
        return "daily_basic"
    
    @property
    def source_column(self) -> str:
        return "volume_ratio"

