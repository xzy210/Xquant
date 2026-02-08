"""
Factor Data Preprocessor Module

This module provides a complete data preprocessing pipeline for factor data:
1. Missing Value Handling (缺失值处理)
2. Winsorization / Outlier Removal (去极值)
3. Standardization (标准化)
4. Neutralization (中性化)

Usage:
from trading_app.factors.preprocessor import FactorPreprocessor
    
    # Create preprocessor
    preprocessor = FactorPreprocessor()
    
    # Process a single factor (cross-sectional)
    processed = preprocessor.process(
        factor_data,
        missing_method='drop',
        winsorize_method='mad',
        standardize_method='zscore',
        neutralize_method='industry_size'
    )
    
    # Process with pipeline
    pipeline = preprocessor.create_pipeline(
        missing='median',
        winsorize='mad',
        standardize='zscore',
        neutralize='size'
    )
    processed_df = pipeline.fit_transform(factor_df)
"""

import numpy as np
import pandas as pd
from typing import Optional, List, Union, Dict, Literal
from dataclasses import dataclass
from enum import Enum
import warnings

from .registry import factor_registry


class MissingMethod(Enum):
    """缺失值处理方法"""
    DROP = 'drop'                    # 删除含缺失值的行
    FORWARD_FILL = 'ffill'           # 前向填充
    BACKWARD_FILL = 'bfill'          # 后向填充
    MEAN = 'mean'                    # 均值填充
    MEDIAN = 'median'                # 中位数填充
    INDUSTRY_MEAN = 'industry_mean'  # 行业均值填充


class WinsorizeMethod(Enum):
    """去极值方法"""
    MAD = 'mad'                      # MAD法 (Median Absolute Deviation)
    SIGMA = 'sigma'                  # 3σ法
    PERCENTILE = 'percentile'        # 分位数截断法
    NONE = 'none'                    # 不去极值


class StandardizeMethod(Enum):
    """标准化方法"""
    ZSCORE = 'zscore'                # Z-Score标准化
    MINMAX = 'minmax'                # Min-Max标准化
    RANK = 'rank'                    # 排名标准化
    NONE = 'none'                    # 不标准化


class NeutralizeMethod(Enum):
    """中性化方法"""
    SIZE = 'size'                    # 市值中性化
    INDUSTRY = 'industry'            # 行业中性化
    SIZE_INDUSTRY = 'size_industry'  # 市值+行业中性化
    NONE = 'none'                    # 不中性化


@dataclass
class PreprocessConfig:
    """预处理配置"""
    missing_method: str = 'median'
    winsorize_method: str = 'mad'
    winsorize_n: float = 3.0          # MAD/Sigma 倍数或分位数
    standardize_method: str = 'zscore'
    neutralize_method: str = 'none'
    industry_col: str = 'industry'    # 行业列名
    size_col: str = 'total_mv'        # 市值列名（取对数）


class MissingValueHandler:
    """缺失值处理器"""
    
    @staticmethod
    def handle(data: pd.Series, method: str = 'median', 
               industry: pd.Series = None) -> pd.Series:
        """
        处理缺失值
        
        Args:
            data: 因子数据 (pd.Series)
            method: 处理方法 ('drop', 'ffill', 'bfill', 'mean', 'median', 'industry_mean')
            industry: 行业数据，用于行业均值填充
            
        Returns:
            处理后的因子数据
        """
        if data.isna().sum() == 0:
            return data.copy()
        
        result = data.copy()
        
        if method == 'drop':
            # 返回非空值，保持索引
            return result.dropna()
        
        elif method == 'ffill':
            return result.ffill()
        
        elif method == 'bfill':
            return result.bfill()
        
        elif method == 'mean':
            fill_value = result.mean()
            return result.fillna(fill_value)
        
        elif method == 'median':
            fill_value = result.median()
            return result.fillna(fill_value)
        
        elif method == 'industry_mean':
            if industry is None:
                warnings.warn("行业数据未提供，使用整体均值填充")
                return result.fillna(result.mean())
            
            # 按行业计算均值并填充
            industry_mean = result.groupby(industry).transform('mean')
            result = result.fillna(industry_mean)
            # 如果仍有缺失（某行业全为空），使用整体均值
            return result.fillna(result.mean())
        
        else:
            raise ValueError(f"未知的缺失值处理方法: {method}")


class Winsorizer:
    """去极值处理器"""
    
    @staticmethod
    def winsorize(data: pd.Series, method: str = 'mad', 
                  n: float = 3.0) -> pd.Series:
        """
        去极值处理
        
        Args:
            data: 因子数据 (pd.Series)
            method: 去极值方法 ('mad', 'sigma', 'percentile', 'none')
            n: 阈值参数
               - mad: MAD倍数，默认3
               - sigma: 标准差倍数，默认3
               - percentile: 分位数，如0.01表示1%和99%截断
               
        Returns:
            去极值后的因子数据
        """
        if method == 'none':
            return data.copy()
        
        result = data.copy()
        valid_mask = ~result.isna()
        valid_data = result[valid_mask]
        
        if len(valid_data) == 0:
            return result
        
        if method == 'mad':
            # MAD法: 中位数 ± n * MAD * 1.4826
            median = valid_data.median()
            mad = np.median(np.abs(valid_data - median))
            # 1.4826是让MAD与标准差可比的常数
            threshold = n * mad * 1.4826
            lower = median - threshold
            upper = median + threshold
            
        elif method == 'sigma':
            # 3σ法: 均值 ± n * 标准差
            mean = valid_data.mean()
            std = valid_data.std()
            lower = mean - n * std
            upper = mean + n * std
            
        elif method == 'percentile':
            # 分位数截断法
            lower = valid_data.quantile(n)
            upper = valid_data.quantile(1 - n)
            
        else:
            raise ValueError(f"未知的去极值方法: {method}")
        
        # 截断
        result = result.clip(lower=lower, upper=upper)
        
        return result
    
    @staticmethod
    def winsorize_df(df: pd.DataFrame, columns: List[str], 
                     method: str = 'mad', n: float = 3.0) -> pd.DataFrame:
        """
        对DataFrame的多个列进行去极值
        
        Args:
            df: 数据DataFrame
            columns: 需要去极值的列名列表
            method: 去极值方法
            n: 阈值参数
            
        Returns:
            去极值后的DataFrame
        """
        result = df.copy()
        for col in columns:
            if col in result.columns:
                result[col] = Winsorizer.winsorize(result[col], method, n)
        return result


class Standardizer:
    """标准化处理器"""
    
    @staticmethod
    def standardize(data: pd.Series, method: str = 'zscore') -> pd.Series:
        """
        标准化处理
        
        Args:
            data: 因子数据 (pd.Series)
            method: 标准化方法 ('zscore', 'minmax', 'rank', 'none')
            
        Returns:
            标准化后的因子数据
        """
        if method == 'none':
            return data.copy()
        
        result = data.copy()
        valid_mask = ~result.isna()
        valid_data = result[valid_mask]
        
        if len(valid_data) == 0:
            return result
        
        if method == 'zscore':
            # Z-Score标准化: (x - mean) / std
            mean = valid_data.mean()
            std = valid_data.std()
            if std == 0 or np.isnan(std):
                result[valid_mask] = 0
            else:
                result[valid_mask] = (valid_data - mean) / std
                
        elif method == 'minmax':
            # Min-Max标准化: (x - min) / (max - min)
            min_val = valid_data.min()
            max_val = valid_data.max()
            range_val = max_val - min_val
            if range_val == 0:
                result[valid_mask] = 0.5
            else:
                result[valid_mask] = (valid_data - min_val) / range_val
                
        elif method == 'rank':
            # 排名标准化: 将排名转换为0-1之间
            ranks = valid_data.rank(method='average')
            n = len(valid_data)
            result[valid_mask] = (ranks - 1) / (n - 1) if n > 1 else 0.5
            
        else:
            raise ValueError(f"未知的标准化方法: {method}")
        
        return result
    
    @staticmethod
    def standardize_df(df: pd.DataFrame, columns: List[str],
                       method: str = 'zscore') -> pd.DataFrame:
        """
        对DataFrame的多个列进行标准化
        
        Args:
            df: 数据DataFrame
            columns: 需要标准化的列名列表
            method: 标准化方法
            
        Returns:
            标准化后的DataFrame
        """
        result = df.copy()
        for col in columns:
            if col in result.columns:
                result[col] = Standardizer.standardize(result[col], method)
        return result


class Neutralizer:
    """中性化处理器
    
    中性化是通过回归方法去除因子与某些特征（如市值、行业）的相关性，
    取回归残差作为中性化后的因子值。
    """
    
    @staticmethod
    def neutralize(factor: pd.Series, 
                   size: pd.Series = None,
                   industry: pd.Series = None,
                   method: str = 'size_industry') -> pd.Series:
        """
        中性化处理
        
        Args:
            factor: 因子数据 (pd.Series)
            size: 市值数据 (pd.Series)，会自动取对数
            industry: 行业数据 (pd.Series)，类别型
            method: 中性化方法 ('size', 'industry', 'size_industry', 'none')
            
        Returns:
            中性化后的因子数据（回归残差）
        """
        if method == 'none':
            return factor.copy()
        
        # 对齐索引
        common_index = factor.dropna().index
        if size is not None:
            common_index = common_index.intersection(size.dropna().index)
        if industry is not None:
            common_index = common_index.intersection(industry.dropna().index)
        
        if len(common_index) < 10:
            warnings.warn("有效样本数量不足，跳过中性化")
            return factor.copy()
        
        # 准备回归数据
        y = factor.loc[common_index].values
        X_list = []
        
        # 市值（取对数）
        if method in ['size', 'size_industry'] and size is not None:
            log_size = np.log(size.loc[common_index].values + 1)
            X_list.append(log_size.reshape(-1, 1))
        
        # 行业（One-Hot编码）
        if method in ['industry', 'size_industry'] and industry is not None:
            industry_data = industry.loc[common_index]
            # One-Hot编码，去掉一个以避免多重共线性
            dummies = pd.get_dummies(industry_data, drop_first=True)
            X_list.append(dummies.values)
        
        if not X_list:
            warnings.warn("没有可用的中性化特征，跳过中性化")
            return factor.copy()
        
        # 合并特征
        X = np.hstack(X_list)
        
        # 添加截距项
        X = np.column_stack([np.ones(len(X)), X])
        
        # OLS回归求残差
        try:
            # 使用最小二乘法: beta = (X'X)^(-1) X'y
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            y_pred = X @ beta
            residuals = y - y_pred
            
            # 构建结果Series
            result = factor.copy()
            result.loc[common_index] = residuals
            
            return result
            
        except Exception as e:
            warnings.warn(f"中性化回归失败: {e}")
            return factor.copy()
    
    @staticmethod
    def neutralize_df(df: pd.DataFrame, factor_columns: List[str],
                      size_col: str = None, industry_col: str = None,
                      method: str = 'size_industry') -> pd.DataFrame:
        """
        对DataFrame的多个因子列进行中性化
        
        Only factors with neutralizable=True attribute in the factor registry
        will be neutralized. Factors not found in registry or with 
        neutralizable=False will be skipped.
        
        Args:
            df: 数据DataFrame
            factor_columns: 因子列名列表
            size_col: 市值列名
            industry_col: 行业列名
            method: 中性化方法
            
        Returns:
            中性化后的DataFrame
        """
        result = df.copy()
        
        size = df[size_col] if size_col and size_col in df.columns else None
        industry = df[industry_col] if industry_col and industry_col in df.columns else None
        
        # Filter factor columns by neutralizable attribute
        neutralizable_cols = []
        skipped_cols = []
        for col in factor_columns:
            factor = factor_registry.get(col)
            if factor is not None and factor.neutralizable:
                neutralizable_cols.append(col)
            else:
                skipped_cols.append(col)
        
        if skipped_cols:
            warnings.warn(
                f"Skipping neutralization for non-neutralizable factors: {skipped_cols}"
            )
        
        for col in neutralizable_cols:
            if col in result.columns:
                result[col] = Neutralizer.neutralize(
                    result[col], size, industry, method
                )
        
        return result


class FactorPreprocessor:
    """
    因子数据预处理器
    
    提供完整的预处理流程：缺失值 → 去极值 → 标准化 → 中性化
    
    Usage:
        preprocessor = FactorPreprocessor()
        
        # 单因子处理
        processed = preprocessor.process_factor(
            factor_series,
            missing_method='median',
            winsorize_method='mad',
            standardize_method='zscore'
        )
        
        # 批量处理DataFrame
        processed_df = preprocessor.process_dataframe(
            df,
            factor_columns=['momentum', 'volatility'],
            missing_method='median',
            winsorize_method='mad',
            standardize_method='zscore',
            neutralize_method='size_industry',
            size_col='total_mv',
            industry_col='industry'
        )
    """
    
    def __init__(self):
        self.missing_handler = MissingValueHandler()
        self.winsorizer = Winsorizer()
        self.standardizer = Standardizer()
        self.neutralizer = Neutralizer()
    
    def process_factor(self, 
                       factor: pd.Series,
                       missing_method: str = 'median',
                       winsorize_method: str = 'mad',
                       winsorize_n: float = 3.0,
                       standardize_method: str = 'zscore',
                       industry: pd.Series = None) -> pd.Series:
        """
        处理单个因子（不含中性化）
        
        Args:
            factor: 因子数据
            missing_method: 缺失值处理方法
            winsorize_method: 去极值方法
            winsorize_n: 去极值阈值
            standardize_method: 标准化方法
            industry: 行业数据（用于行业均值填充）
            
        Returns:
            处理后的因子数据
        """
        # Step 1: 缺失值处理
        result = self.missing_handler.handle(factor, missing_method, industry)
        
        # Step 2: 去极值
        result = self.winsorizer.winsorize(result, winsorize_method, winsorize_n)
        
        # Step 3: 标准化
        result = self.standardizer.standardize(result, standardize_method)
        
        return result
    
    def process_dataframe(self,
                          df: pd.DataFrame,
                          factor_columns: List[str],
                          missing_method: str = 'median',
                          winsorize_method: str = 'mad',
                          winsorize_n: float = 3.0,
                          standardize_method: str = 'zscore',
                          neutralize_method: str = 'none',
                          size_col: str = None,
                          industry_col: str = None) -> pd.DataFrame:
        """
        批量处理DataFrame中的多个因子
        
        Args:
            df: 数据DataFrame
            factor_columns: 因子列名列表
            missing_method: 缺失值处理方法
            winsorize_method: 去极值方法
            winsorize_n: 去极值阈值
            standardize_method: 标准化方法
            neutralize_method: 中性化方法
            size_col: 市值列名
            industry_col: 行业列名
            
        Returns:
            处理后的DataFrame
        """
        result = df.copy()
        industry = df[industry_col] if industry_col and industry_col in df.columns else None
        
        # 对每个因子列进行处理
        for col in factor_columns:
            if col not in result.columns:
                continue
                
            # Step 1: 缺失值处理
            result[col] = self.missing_handler.handle(
                result[col], missing_method, industry
            )
            
            # Step 2: 去极值
            result[col] = self.winsorizer.winsorize(
                result[col], winsorize_method, winsorize_n
            )
            
            # Step 3: 标准化
            result[col] = self.standardizer.standardize(
                result[col], standardize_method
            )
        
        # Step 4: 中性化（需要在标准化后进行）
        if neutralize_method != 'none':
            result = self.neutralizer.neutralize_df(
                result, factor_columns, size_col, industry_col, neutralize_method
            )
        
        return result
    
    def process_cross_sectional(self,
                                df: pd.DataFrame,
                                date_col: str,
                                factor_columns: List[str],
                                missing_method: str = 'median',
                                winsorize_method: str = 'mad',
                                winsorize_n: float = 3.0,
                                standardize_method: str = 'zscore',
                                neutralize_method: str = 'none',
                                size_col: str = None,
                                industry_col: str = None,
                                progress_callback=None) -> pd.DataFrame:
        """
        截面处理：对每个日期分别进行预处理
        
        这是因子预处理的标准做法，确保每个截面日期的处理是独立的。
        
        Args:
            df: 包含多个日期的面板数据
            date_col: 日期列名
            factor_columns: 因子列名列表
            missing_method: 缺失值处理方法
            winsorize_method: 去极值方法
            winsorize_n: 去极值阈值
            standardize_method: 标准化方法
            neutralize_method: 中性化方法
            size_col: 市值列名
            industry_col: 行业列名
            progress_callback: 进度回调函数 (current, total, date)
            
        Returns:
            处理后的DataFrame
        """
        if date_col not in df.columns:
            raise ValueError(f"日期列 '{date_col}' 不在数据中")
        
        dates = df[date_col].unique()
        total = len(dates)
        results = []
        
        for i, date in enumerate(sorted(dates)):
            if progress_callback:
                progress_callback(i + 1, total, str(date))
            
            # 获取当日截面数据
            daily_df = df[df[date_col] == date].copy()
            
            # 处理当日数据
            processed = self.process_dataframe(
                daily_df,
                factor_columns,
                missing_method,
                winsorize_method,
                winsorize_n,
                standardize_method,
                neutralize_method,
                size_col,
                industry_col
            )
            
            results.append(processed)
        
        return pd.concat(results, ignore_index=True)


class PreprocessPipeline:
    """
    预处理管道
    
    用于保存预处理配置，便于重复使用
    """
    
    def __init__(self, config: PreprocessConfig = None):
        self.config = config or PreprocessConfig()
        self.preprocessor = FactorPreprocessor()
        self._is_fitted = False
        self._stats = {}  # 保存统计量（均值、标准差等）
    
    def fit(self, df: pd.DataFrame, factor_columns: List[str]) -> 'PreprocessPipeline':
        """
        拟合管道（计算统计量）
        
        目前仅用于记录处理过的列，未来可扩展为保存统计量用于样本外数据
        """
        self._factor_columns = factor_columns
        self._is_fitted = True
        return self
    
    def transform(self, df: pd.DataFrame, 
                  factor_columns: List[str] = None) -> pd.DataFrame:
        """
        应用预处理
        """
        cols = factor_columns or getattr(self, '_factor_columns', None)
        if cols is None:
            raise ValueError("未指定因子列")
        
        return self.preprocessor.process_dataframe(
            df,
            cols,
            self.config.missing_method,
            self.config.winsorize_method,
            self.config.winsorize_n,
            self.config.standardize_method,
            self.config.neutralize_method,
            self.config.size_col,
            self.config.industry_col
        )
    
    def fit_transform(self, df: pd.DataFrame, 
                      factor_columns: List[str]) -> pd.DataFrame:
        """
        拟合并应用预处理
        """
        self.fit(df, factor_columns)
        return self.transform(df, factor_columns)


# 便捷函数
def preprocess_factor(factor: pd.Series,
                      missing: str = 'median',
                      winsorize: str = 'mad',
                      standardize: str = 'zscore') -> pd.Series:
    """
    便捷函数：预处理单个因子
    
    Args:
        factor: 因子数据
        missing: 缺失值处理方法
        winsorize: 去极值方法
        standardize: 标准化方法
        
    Returns:
        处理后的因子数据
    """
    preprocessor = FactorPreprocessor()
    return preprocessor.process_factor(
        factor,
        missing_method=missing,
        winsorize_method=winsorize,
        standardize_method=standardize
    )


def preprocess_factors(df: pd.DataFrame,
                       factor_columns: List[str],
                       missing: str = 'median',
                       winsorize: str = 'mad',
                       standardize: str = 'zscore',
                       neutralize: str = 'none',
                       size_col: str = None,
                       industry_col: str = None) -> pd.DataFrame:
    """
    便捷函数：预处理多个因子
    
    Args:
        df: 数据DataFrame
        factor_columns: 因子列名列表
        missing: 缺失值处理方法
        winsorize: 去极值方法
        standardize: 标准化方法
        neutralize: 中性化方法
        size_col: 市值列名
        industry_col: 行业列名
        
    Returns:
        处理后的DataFrame
    """
    preprocessor = FactorPreprocessor()
    return preprocessor.process_dataframe(
        df,
        factor_columns,
        missing_method=missing,
        winsorize_method=winsorize,
        standardize_method=standardize,
        neutralize_method=neutralize,
        size_col=size_col,
        industry_col=industry_col
    )
