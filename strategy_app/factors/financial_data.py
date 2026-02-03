"""
Financial Data Module - Download and cache financial data from Tushare

This module provides functions to download financial indicator data from Tushare,
including PE, PB, ROE, profit growth rate, etc.

Usage:
from trading_app.factors.financial_data import FinancialDataLoader
    
    loader = FinancialDataLoader(data_dir='data/financial', tushare_token='your_token')
    
    # Download daily basic data (PE, PB, etc.)
    loader.download_daily_basic('000001', start_date='20230101', end_date='20231231')
    
    # Download financial indicators (ROE, profit growth, etc.)
    loader.download_fina_indicator('000001')
    
    # Load cached data
    df = loader.load_daily_basic('000001')
"""

import os
import pandas as pd
import numpy as np
from typing import Optional, List
from datetime import datetime, timedelta
from pathlib import Path

# Tushare optional import
try:
    import tushare as ts
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False
    ts = None


class FinancialDataLoader:
    """
    Financial data loader using Tushare API
    
    Downloads and caches financial data including:
    - daily_basic: PE, PB, total_mv, circ_mv, turnover_rate, etc.
    - fina_indicator: ROE, ROA, profit growth rate, etc.
    """
    
    def __init__(self, data_dir: str = "data/financial", tushare_token: str = None):
        """
        Initialize FinancialDataLoader
        
        Args:
            data_dir: Directory to store cached financial data
            tushare_token: Tushare API token (optional, can be set later)
        """
        self.data_dir = Path(data_dir)
        self.daily_basic_dir = self.data_dir / "daily_basic"
        self.fina_indicator_dir = self.data_dir / "fina_indicator"
        
        # Create directories
        self.daily_basic_dir.mkdir(parents=True, exist_ok=True)
        self.fina_indicator_dir.mkdir(parents=True, exist_ok=True)
        
        self._pro = None
        self._token = tushare_token
        
    def _init_api(self):
        """Initialize Tushare API if not already done"""
        if self._pro is not None:
            return True
            
        if not HAS_TUSHARE:
            print("Warning: Tushare not installed. Run: pip install tushare")
            return False
            
        if not self._token:
            print("Warning: Tushare token not set")
            return False
            
        try:
            ts.set_token(self._token)
            self._pro = ts.pro_api()
            return True
        except Exception as e:
            print(f"Error initializing Tushare API: {e}")
            return False
    
    def set_token(self, token: str):
        """Set Tushare API token"""
        self._token = token
        self._pro = None  # Reset API to use new token
        
    def _code_to_ts_code(self, code: str) -> str:
        """Convert stock code to Tushare format (e.g., 000001 -> 000001.SZ)"""
        if '.' in code:
            return code
        if code.startswith(('6', '9')):
            return f"{code}.SH"
        else:
            return f"{code}.SZ"
    
    def _ts_code_to_code(self, ts_code: str) -> str:
        """Convert Tushare code to standard code (e.g., 000001.SZ -> 000001)"""
        return ts_code.split('.')[0] if '.' in ts_code else ts_code
    
    def download_daily_basic(self, code: str, start_date: str = None, 
                             end_date: str = None, force: bool = False) -> Optional[pd.DataFrame]:
        """
        Download daily basic indicators from Tushare
        
        Includes: PE, PB, total_mv, circ_mv, turnover_rate, volume_ratio, etc.
        
        Args:
            code: Stock code (6-digit)
            start_date: Start date (YYYYMMDD format)
            end_date: End date (YYYYMMDD format)
            force: Force re-download even if cache exists
            
        Returns:
            DataFrame with daily basic data, or None if failed
        """
        if not self._init_api():
            return None
            
        ts_code = self._code_to_ts_code(code)
        cache_file = self.daily_basic_dir / f"{code}.csv"
        
        # Check cache
        if not force and cache_file.exists():
            try:
                existing_df = pd.read_csv(cache_file)
                if not existing_df.empty:
                    # Only download new data
                    last_date = existing_df['trade_date'].max()
                    if end_date and str(last_date) >= end_date:
                        return existing_df
                    start_date = str(int(last_date) + 1)
            except:
                pass
        
        # Set default dates
        if not end_date:
            end_date = datetime.now().strftime('%Y%m%d')
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365*3)).strftime('%Y%m%d')
        
        try:
            # Download daily basic data
            df = self._pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields='ts_code,trade_date,close,turnover_rate,turnover_rate_f,'
                       'volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,'
                       'total_share,float_share,free_share,total_mv,circ_mv'
            )
            
            if df is None or df.empty:
                return None
                
            # Sort by date
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            # Merge with existing data
            if cache_file.exists() and not force:
                try:
                    existing_df = pd.read_csv(cache_file)
                    df = pd.concat([existing_df, df]).drop_duplicates(
                        subset=['trade_date'], keep='last'
                    ).sort_values('trade_date').reset_index(drop=True)
                except:
                    pass
            
            # Save to cache
            df.to_csv(cache_file, index=False, encoding='utf-8-sig')
            
            return df
            
        except Exception as e:
            print(f"Error downloading daily_basic for {code}: {e}")
            return None
    
    def download_fina_indicator(self, code: str, force: bool = False) -> Optional[pd.DataFrame]:
        """
        Download financial indicators from Tushare
        
        Includes: ROE, ROA, profit growth rate, revenue growth rate, etc.
        
        Args:
            code: Stock code (6-digit)
            force: Force re-download even if cache exists
            
        Returns:
            DataFrame with financial indicators, or None if failed
        """
        if not self._init_api():
            return None
            
        ts_code = self._code_to_ts_code(code)
        cache_file = self.fina_indicator_dir / f"{code}.csv"
        
        # Check cache (financial indicators update quarterly, check if recent)
        if not force and cache_file.exists():
            try:
                existing_df = pd.read_csv(cache_file)
                if not existing_df.empty:
                    # Check if data is recent (within 3 months)
                    last_date = str(existing_df['end_date'].max())
                    current_quarter_end = self._get_current_quarter_end()
                    if last_date >= current_quarter_end:
                        return existing_df
            except:
                pass
        
        try:
            # Download financial indicators
            df = self._pro.fina_indicator(
                ts_code=ts_code,
                fields='ts_code,ann_date,end_date,eps,dt_eps,total_revenue_ps,'
                       'revenue_ps,capital_rese_ps,surplus_rese_ps,undist_profit_ps,'
                       'extra_item,profit_dedt,gross_margin,current_ratio,quick_ratio,'
                       'cash_ratio,ar_turn,ca_turn,fa_turn,assets_turn,op_income,'
                       'ebit_of_gr,roe,roe_waa,roe_dt,roa,npta,roic,roe_yearly,'
                       'roa2_yearly,debt_to_assets,op_yoy,ebt_yoy,tr_yoy,or_yoy,'
                       'q_sales_yoy,q_op_yoy,q_profit_yoy,eq_yoy,netprofit_yoy,'
                       'dt_netprofit_yoy,ocf_yoy,roe_yoy,basic_eps_yoy,dt_eps_yoy'
            )
            
            if df is None or df.empty:
                return None
                
            # Sort by date
            df = df.sort_values('end_date', ascending=False).reset_index(drop=True)
            
            # Save to cache
            df.to_csv(cache_file, index=False, encoding='utf-8-sig')
            
            return df
            
        except Exception as e:
            print(f"Error downloading fina_indicator for {code}: {e}")
            return None
    
    def _get_current_quarter_end(self) -> str:
        """Get current quarter end date in YYYYMMDD format"""
        now = datetime.now()
        quarter = (now.month - 1) // 3
        if quarter == 0:
            return f"{now.year - 1}1231"
        elif quarter == 1:
            return f"{now.year}0331"
        elif quarter == 2:
            return f"{now.year}0630"
        else:
            return f"{now.year}0930"
    
    def load_daily_basic(self, code: str) -> Optional[pd.DataFrame]:
        """
        Load cached daily basic data
        
        Args:
            code: Stock code (6-digit)
            
        Returns:
            DataFrame with daily basic data, or None if not found
        """
        cache_file = self.daily_basic_dir / f"{code}.csv"
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file)
                df['trade_date'] = df['trade_date'].astype(str)
                return df
            except Exception as e:
                print(f"Error loading daily_basic for {code}: {e}")
        return None
    
    def load_fina_indicator(self, code: str) -> Optional[pd.DataFrame]:
        """
        Load cached financial indicator data
        
        Args:
            code: Stock code (6-digit)
            
        Returns:
            DataFrame with financial indicators, or None if not found
        """
        cache_file = self.fina_indicator_dir / f"{code}.csv"
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file)
                df['end_date'] = df['end_date'].astype(str)
                if 'ann_date' in df.columns:
                    df['ann_date'] = df['ann_date'].astype(str)
                return df
            except Exception as e:
                print(f"Error loading fina_indicator for {code}: {e}")
        return None
    
    def download_batch(self, codes: List[str], data_type: str = 'daily_basic',
                       start_date: str = None, end_date: str = None,
                       progress_callback=None) -> dict:
        """
        Download financial data for multiple stocks
        
        Args:
            codes: List of stock codes
            data_type: 'daily_basic' or 'fina_indicator'
            start_date: Start date for daily_basic
            end_date: End date for daily_basic
            progress_callback: Optional callback function(current, total, code)
            
        Returns:
            Dict with success/fail counts
        """
        success_count = 0
        fail_count = 0
        total = len(codes)
        
        for i, code in enumerate(codes):
            if progress_callback:
                progress_callback(i + 1, total, code)
            
            try:
                if data_type == 'daily_basic':
                    result = self.download_daily_basic(code, start_date, end_date)
                else:
                    result = self.download_fina_indicator(code)
                    
                if result is not None and not result.empty:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                print(f"Error downloading {data_type} for {code}: {e}")
                fail_count += 1
                
            # Rate limiting for Tushare API
            import time
            time.sleep(0.1)
        
        return {'success': success_count, 'fail': fail_count}
    
    def get_factor_value(self, code: str, factor_name: str, 
                         date: str = None) -> Optional[float]:
        """
        Get a specific factor value for a stock
        
        Args:
            code: Stock code
            factor_name: Factor name (pe, pb, roe, netprofit_yoy, etc.)
            date: Trade date (YYYYMMDD format), or None for latest
            
        Returns:
            Factor value, or None if not found
        """
        # Daily basic factors
        daily_basic_factors = ['pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 
                              'turnover_rate', 'turnover_rate_f', 'volume_ratio',
                              'dv_ratio', 'dv_ttm', 'total_mv', 'circ_mv',
                              'total_share', 'float_share', 'free_share']
        
        # Financial indicator factors
        fina_factors = ['roe', 'roe_waa', 'roe_dt', 'roa', 'roic',
                       'netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy', 'or_yoy',
                       'op_yoy', 'eps', 'dt_eps', 'gross_margin',
                       'current_ratio', 'quick_ratio', 'debt_to_assets']
        
        if factor_name in daily_basic_factors:
            df = self.load_daily_basic(code)
            if df is None or df.empty:
                return None
            if date:
                df = df[df['trade_date'] == date]
            if df.empty:
                return None
            return df.iloc[-1].get(factor_name)
            
        elif factor_name in fina_factors:
            df = self.load_fina_indicator(code)
            if df is None or df.empty:
                return None
            # Get most recent report before the date
            if date:
                df = df[df['ann_date'] <= date]
            if df.empty:
                return None
            return df.iloc[0].get(factor_name)  # Already sorted descending
            
        return None
    
    def get_factor_series(self, code: str, factor_name: str,
                          start_date: str = None, end_date: str = None) -> Optional[pd.Series]:
        """
        Get a factor time series for a stock
        
        Args:
            code: Stock code
            factor_name: Factor name
            start_date: Start date
            end_date: End date
            
        Returns:
            pd.Series with factor values indexed by date
        """
        daily_basic_factors = ['pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 
                              'turnover_rate', 'turnover_rate_f', 'volume_ratio',
                              'dv_ratio', 'dv_ttm', 'total_mv', 'circ_mv']
        
        if factor_name in daily_basic_factors:
            df = self.load_daily_basic(code)
            if df is None or df.empty:
                return None
            
            # Convert date and filter
            df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
            if start_date:
                df = df[df['trade_date'] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df['trade_date'] <= pd.to_datetime(end_date)]
            
            if df.empty or factor_name not in df.columns:
                return None
                
            series = df.set_index('trade_date')[factor_name]
            series.index = series.index.strftime('%Y-%m-%d')
            return series
        
        return None


# Global instance (lazy initialization)
_financial_data_loader = None

def get_financial_data_loader(data_dir: str = None, tushare_token: str = None) -> FinancialDataLoader:
    """Get or create global FinancialDataLoader instance"""
    global _financial_data_loader
    
    if _financial_data_loader is None:
        if data_dir is None:
            # Default to project data directory
            project_root = Path(__file__).parent.parent.parent
            data_dir = project_root / "data" / "financial"
        _financial_data_loader = FinancialDataLoader(data_dir=str(data_dir))
        
    if tushare_token:
        _financial_data_loader.set_token(tushare_token)
        
    return _financial_data_loader

