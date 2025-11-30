"""
分时数据（分钟级K线）拉取模块

使用 AkShare 获取分时数据（免费，无需额外权限）
- 支持频率：1、5、15、30、60 分钟
- 数据来源：东方财富
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

# --------------------------- 日志配置 --------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fetch_minute")


def get_minute_data_akshare(
    code: str,
    trade_date: str,
    freq: str = "1",
    max_retries: int = 3,
) -> Optional[pd.DataFrame]:
    """
    使用 AkShare 获取指定股票某日的分时数据
    
    Args:
        code: 股票代码（6位）
        trade_date: 交易日期，格式 YYYYMMDD
        freq: 数据频率，可选 "1"/"5"/"15"/"30"/"60"
        max_retries: 最大重试次数
    
    Returns:
        DataFrame 包含 time, open, high, low, close, volume, amount 等列
        如果获取失败返回 None
    """
    if not HAS_AKSHARE:
        logger.error("未安装 akshare，请执行：pip install akshare")
        return None
    
    # 格式化日期
    if len(trade_date) == 8:
        date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    else:
        date_str = trade_date
    
    # 构造时间范围
    start_time = f"{date_str} 09:30:00"
    end_time = f"{date_str} 15:00:00"
    
    for attempt in range(1, max_retries + 1):
        try:
            # 使用东方财富接口获取分钟数据
            df = ak.stock_zh_a_hist_min_em(
                symbol=code,
                period=freq,
                start_date=start_time,
                end_date=end_time,
                adjust=""  # 不复权
            )
            
            if df is None or df.empty:
                logger.debug("%s %s 无分时数据", code, trade_date)
                return None
            
            # 整理数据格式，统一列名
            df = df.rename(columns={
                "时间": "time",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "pct_change",
                "涨跌额": "change",
                "振幅": "amplitude",
                "换手率": "turnover"
            })
            
            # 选择需要的列
            cols = ["time", "open", "high", "low", "close", "volume", "amount"]
            available_cols = [c for c in cols if c in df.columns]
            df = df[available_cols].copy()
            
            # 转换时间格式
            df["time"] = pd.to_datetime(df["time"])
            
            # 筛选指定日期的数据
            target_date = pd.to_datetime(date_str).date()
            df = df[df["time"].dt.date == target_date]
            
            if df.empty:
                logger.debug("%s %s 筛选后无分时数据", code, trade_date)
                return None
            
            # 按时间排序
            df = df.sort_values("time").reset_index(drop=True)
            
            logger.debug("%s %s 获取到 %d 条分时数据", code, trade_date, len(df))
            return df
            
        except Exception as e:
            logger.warning("%s 第 %d 次获取分时数据失败: %s", code, attempt, e)
            if attempt < max_retries:
                time.sleep(2)
    
    logger.error("%s %s 获取分时数据失败，已重试 %d 次", code, trade_date, max_retries)
    return None


def save_minute_data(df: pd.DataFrame, code: str, trade_date: str, data_dir: Path) -> Path:
    """
    保存分时数据到本地
    
    存储路径：{data_dir}/minute/{code}/{YYYYMMDD}.csv
    """
    minute_dir = data_dir / "minute" / code
    minute_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = minute_dir / f"{trade_date}.csv"
    df.to_csv(csv_path, index=False)
    logger.debug("分时数据已保存至 %s", csv_path)
    return csv_path


def load_minute_data(code: str, trade_date: str, data_dir: Path) -> Optional[pd.DataFrame]:
    """
    从本地加载分时数据
    
    Args:
        code: 股票代码（6位）
        trade_date: 交易日期，格式 YYYYMMDD
        data_dir: 数据目录
    
    Returns:
        DataFrame 或 None（如果文件不存在）
    """
    csv_path = data_dir / "minute" / code / f"{trade_date}.csv"
    
    if not csv_path.exists():
        return None
    
    try:
        df = pd.read_csv(csv_path, parse_dates=["time"])
        return df
    except Exception as e:
        logger.warning("读取分时数据失败 %s: %s", csv_path, e)
        return None


def fetch_minute_data_with_cache(
    code: str,
    trade_date: str,
    data_dir: Path,
    freq: str = "1",
    force_refresh: bool = False,
) -> Optional[pd.DataFrame]:
    """
    获取分时数据（优先使用本地缓存）
    
    Args:
        code: 股票代码（6位）
        trade_date: 交易日期，格式 YYYYMMDD
        data_dir: 数据目录
        freq: 数据频率，"1"/"5"/"15"/"30"/"60"
        force_refresh: 是否强制刷新
    
    Returns:
        DataFrame 或 None
    """
    # 尝试从本地加载
    if not force_refresh:
        df = load_minute_data(code, trade_date, data_dir)
        if df is not None:
            logger.debug("%s %s 使用本地缓存的分时数据", code, trade_date)
            return df
    
    # 从 AkShare 获取
    df = get_minute_data_akshare(code, trade_date, freq)
    
    if df is not None and not df.empty:
        # 保存到本地
        save_minute_data(df, code, trade_date, data_dir)
    
    return df


# --------------------------- 命令行入口 --------------------------- #
def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="使用 AkShare 拉取股票分时数据")
    parser.add_argument("code", help="股票代码（6位）")
    parser.add_argument("date", help="交易日期 YYYYMMDD")
    parser.add_argument("--freq", default="1", choices=["1", "5", "15", "30", "60"],
                        help="数据频率（默认 1 分钟）")
    parser.add_argument("--out", default="./data", help="输出目录")
    parser.add_argument("--force", action="store_true", help="强制刷新（忽略缓存）")
    args = parser.parse_args()
    
    if not HAS_AKSHARE:
        logger.error("未安装 akshare，请执行：pip install akshare")
        sys.exit(1)
    
    # 获取分时数据
    data_dir = Path(args.out)
    df = fetch_minute_data_with_cache(
        args.code,
        args.date,
        data_dir,
        args.freq,
        args.force
    )
    
    if df is not None and not df.empty:
        print(f"\n获取到 {len(df)} 条分时数据：")
        print(df.head(10))
        print("...")
        print(df.tail(5))
    else:
        print("未获取到分时数据")


if __name__ == "__main__":
    main()
