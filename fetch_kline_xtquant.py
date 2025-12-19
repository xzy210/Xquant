"""
xtquant/miniQMT K线数据获取模块

使用迅投 xtquant SDK 通过 miniQMT 获取股票历史K线数据
支持日线和分钟线（1分/5分/15分/30分/60分）

注意事项：
- 需要本地运行 miniQMT 客户端
- miniQMT 需要先登录迅投 QMT 账户
- xtquant 需要从迅投官网下载安装，或者直接pip安装
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

# xtquant 可能未安装，延迟导入
try:
    from xtquant import xtdata
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False
    xtdata = None

# --------------------------- 全局日志配置 --------------------------- #
LOG_FILE = Path("fetch_xtquant.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("fetch_xtquant")

# --------------------------- 周期映射 --------------------------- #
PERIOD_MAP = {
    "1d": "1d",      # 日线
    "1m": "1m",      # 1分钟
    "5m": "5m",      # 5分钟
    "15m": "15m",    # 15分钟
    "30m": "30m",    # 30分钟
    "60m": "60m",    # 60分钟
    "1w": "1w",      # 周线
    "1mon": "1mon",  # 月线
}


def check_xtquant_available() -> bool:
    """检查 xtquant 是否可用"""
    return HAS_XTQUANT


def check_connection() -> Tuple[bool, str]:
    """
    检测 miniQMT 连接状态
    
    Returns:
        (connected: bool, message: str)
    """
    if not HAS_XTQUANT:
        return False, "xtquant 未安装，请从迅投官网下载安装"
    
    try:
        # 尝试获取一个简单的数据来测试连接
        # 使用上证指数作为测试
        test_code = "000001.SH"
        result = xtdata.get_market_data(
            field_list=["close"],
            stock_list=[test_code],
            period="1d",
            count=1
        )
        if result is not None and len(result) > 0:
            return True, "miniQMT 连接正常"
        else:
            return False, "miniQMT 返回空数据，请确保已登录"
    except Exception as e:
        error_msg = str(e)
        if "connect" in error_msg.lower() or "timeout" in error_msg.lower():
            return False, "无法连接到 miniQMT，请确保客户端已启动并登录"
        return False, f"连接测试失败: {error_msg}"


def _to_xt_code(code: str) -> str:
    """
    把6位股票代码映射到 xtquant 格式
    
    Args:
        code: 6位股票代码
    
    Returns:
        xtquant 格式代码，如 "000001.SZ"
    """
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def _parse_date(date_str: str) -> str:
    """
    解析日期字符串为 xtquant 格式 (YYYYMMDD)
    
    支持格式：YYYYMMDD, YYYY-MM-DD, today
    """
    if date_str.lower() == "today":
        return dt.date.today().strftime("%Y%m%d")
    
    # 移除可能的分隔符
    return date_str.replace("-", "").replace("/", "")


def _get_kline_xtquant(
    code: str,
    start: str,
    end: str,
    period: str = "1d"
) -> pd.DataFrame:
    """
    使用 xtquant 获取K线数据
    
    Args:
        code: 6位股票代码
        start: 起始日期 YYYYMMDD
        end: 结束日期 YYYYMMDD
        period: 周期，支持 1d/1m/5m/15m/30m/60m
    
    Returns:
        DataFrame 包含 date/time, open, close, high, low, volume
    """
    if not HAS_XTQUANT:
        logger.error("xtquant 未安装")
        return pd.DataFrame()
    
    xt_code = _to_xt_code(code)
    xt_period = PERIOD_MAP.get(period, period)
    
    try:
        # 先下载历史数据到本地
        xtdata.download_history_data(
            stock_code=xt_code,
            period=xt_period,
            start_time=start,
            end_time=end
        )
        
        # 使用 get_market_data_ex 获取数据
        # 返回格式: {股票代码: DataFrame}
        # DataFrame 的索引是 YYYYMMDD 或 YYYYMMDDHHmmss 格式
        # 包含 time, open, high, low, close, volume 等列
        if period == "1d":
            # 日线数据：使用 count=-1 获取所有数据
            data = xtdata.get_market_data_ex(
                field_list=[],  # 空列表表示获取所有字段
                stock_list=[xt_code],
                period=xt_period,
                count=-1,
                dividend_type="front"  # 前复权
            )
        else:
            # 分钟线数据：指定时间范围
            data = xtdata.get_market_data_ex(
                field_list=[],
                stock_list=[xt_code],
                period=xt_period,
                start_time=start,
                end_time=end,
                dividend_type="front"
            )
        
        if data is None or len(data) == 0:
            logger.debug("%s 无数据", code)
            return pd.DataFrame()
        
        # 检查股票代码是否在返回数据中
        if xt_code not in data:
            logger.debug("%s 数据中不包含该股票", code)
            return pd.DataFrame()
        
        df = data[xt_code].copy()
        
        if df is None or df.empty:
            logger.debug("%s 无数据", code)
            return pd.DataFrame()
        
        # df 的索引是 YYYYMMDD 或 YYYYMMDDHHmmss 格式
        # 有 time 列是毫秒时间戳，以及 open, high, low, close, volume 等列
        
        # 重置索引，将索引转为列
        df = df.reset_index()
        df = df.rename(columns={"index": "_index"})
        
        # 处理时间列
        if period == "1d":
            # 日线数据：索引是 YYYYMMDD 格式
            # 使用索引列转换日期（更可靠）
            df["date"] = pd.to_datetime(df["_index"].astype(str), format="%Y%m%d", errors="coerce")
        else:
            # 分钟线数据：索引是 YYYYMMDDHHmmss 格式
            index_str = df["_index"].astype(str)
            if len(index_str.iloc[0]) >= 12:
                # YYYYMMDDHHmmss 或 YYYYMMDDHHmm
                df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M%S", errors="coerce")
                # 如果解析失败，尝试其他格式
                if df["time"].isna().all():
                    df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M", errors="coerce")
            else:
                # 使用 time 列（毫秒时间戳）
                if "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        
        # 选择需要的列
        if period == "1d":
            result_cols = ["date", "open", "high", "low", "close", "volume"]
            sort_col = "date"
        else:
            result_cols = ["time", "open", "high", "low", "close", "volume"]
            sort_col = "time"
        
        # 检查必要的列是否存在
        missing_cols = [col for col in result_cols if col not in df.columns]
        if missing_cols:
            logger.warning("%s 缺少列: %s, 现有列: %s", code, missing_cols, df.columns.tolist())
            return pd.DataFrame()
        
        df = df[result_cols].copy()
        
        # 转换数值类型
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        
        # 过滤无效日期和指定日期范围
        df = df.dropna(subset=[sort_col])
        
        if period == "1d" and start and end:
            # 过滤日期范围
            start_dt = pd.to_datetime(start, format="%Y%m%d")
            end_dt = pd.to_datetime(end, format="%Y%m%d")
            df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        
        # 排序
        df = df.sort_values(sort_col).reset_index(drop=True)
        
        logger.debug("%s 获取到 %d 条数据", code, len(df))
        return df
        
    except Exception as e:
        logger.error("%s 获取数据失败: %s", code, e)
        import traceback
        logger.debug(traceback.format_exc())
        return pd.DataFrame()


def get_minute_data(
    code: str,
    trade_date: str,
    freq: str = "1m"
) -> Optional[pd.DataFrame]:
    """
    获取指定日期的分时数据（便捷函数，用于分时图显示）
    
    Args:
        code: 6位股票代码
        trade_date: 交易日期，格式 YYYYMMDD 或 YYYY-MM-DD
        freq: 数据频率，"1m"/"5m"/"15m"/"30m"/"60m"
    
    Returns:
        DataFrame 包含 time, open, high, low, close, volume, amount 列
        如果获取失败返回 None
    """
    if not HAS_XTQUANT:
        logger.error("xtquant 未安装")
        return None
    
    # 标准化日期格式
    date_str = trade_date.replace("-", "").replace("/", "")
    
    xt_code = _to_xt_code(code)
    xt_period = PERIOD_MAP.get(freq, freq)
    
    try:
        # 下载该日的分钟数据
        xtdata.download_history_data(
            stock_code=xt_code,
            period=xt_period,
            start_time=date_str,
            end_time=date_str
        )
        
        # 获取数据
        data = xtdata.get_market_data_ex(
            field_list=[],  # 获取所有字段
            stock_list=[xt_code],
            period=xt_period,
            start_time=date_str,
            end_time=date_str,
            dividend_type="front"
        )
        
        if data is None or len(data) == 0 or xt_code not in data:
            logger.debug("%s %s 无分时数据", code, date_str)
            return None
        
        df = data[xt_code].copy()
        
        if df is None or df.empty:
            logger.debug("%s %s 无分时数据", code, date_str)
            return None
        
        # 重置索引
        df = df.reset_index()
        df = df.rename(columns={"index": "_index"})
        
        # 处理时间列 - 索引是 YYYYMMDDHHmmss 格式
        index_str = df["_index"].astype(str)
        if len(index_str.iloc[0]) >= 12:
            df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M%S", errors="coerce")
            if df["time"].isna().all():
                df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M", errors="coerce")
        else:
            # 使用 time 列（毫秒时间戳）
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        
        # 确保 amount 列存在（用于计算均价）
        # xtquant 返回: volume 单位是手，amount 单位是元
        if "amount" not in df.columns:
            # 估算成交额 = 成交量(手) * 100(股/手) * 均价(元/股) = 元
            df["amount"] = df["volume"] * 100 * (df["high"] + df["low"] + df["close"]) / 3
        
        # 选择需要的列
        result_cols = ["time", "open", "high", "low", "close", "volume", "amount"]
        available_cols = [c for c in result_cols if c in df.columns]
        df = df[available_cols].copy()
        
        # 转换数值类型
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        
        # 过滤无效数据并排序
        df = df.dropna(subset=["time"])
        df = df.sort_values("time").reset_index(drop=True)
        
        # xtquant 返回的 volume 单位已经是手，与 AkShare 一致，无需转换
        
        logger.debug("%s %s 获取到 %d 条分时数据", code, date_str, len(df))
        return df
        
    except Exception as e:
        logger.error("%s %s 获取分时数据失败: %s", code, date_str, e)
        import traceback
        logger.debug(traceback.format_exc())
        return None


def validate(df: pd.DataFrame, period: str = "1d") -> pd.DataFrame:
    """验证数据有效性"""
    if df is None or df.empty:
        return df
    
    time_col = "date" if period == "1d" else "time"
    
    df = df.drop_duplicates(subset=time_col).sort_values(time_col).reset_index(drop=True)
    
    if df[time_col].isna().any():
        raise ValueError("存在缺失时间！")
    
    return df


def fetch_one(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    period: str = "1d",
) -> None:
    """
    增量更新单只股票数据
    
    Args:
        code: 股票代码
        start: 起始日期
        end: 结束日期
        out_dir: 输出目录
        period: 周期
    """
    # 确定存储路径
    if period == "1d":
        csv_path = out_dir / f"{code}.csv"
        time_col = "date"
    else:
        # 分钟线存储在 minute/{code}/ 目录下
        minute_dir = out_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        # 按日期存储
        csv_path = minute_dir / f"{end}.csv"
        time_col = "time"
    
    # 确定增量起始日期
    incremental_start = start
    existing_df = None
    
    if period == "1d" and csv_path.exists():
        try:
            existing_df = pd.read_csv(csv_path, parse_dates=[time_col])
            if not existing_df.empty and time_col in existing_df.columns:
                last_date = existing_df[time_col].max()
                incremental_start = last_date.strftime("%Y%m%d")
                logger.debug("%s 增量更新：从 %s 开始", code, incremental_start)
        except Exception as e:
            logger.warning("%s 读取现有文件失败，将全量拉取: %s", code, e)
            existing_df = None
    
    for attempt in range(1, 4):
        try:
            new_df = _get_kline_xtquant(code, incremental_start, end, period)
            
            if new_df.empty:
                if existing_df is not None and not existing_df.empty:
                    logger.debug("%s 无新数据，保持现有数据", code)
                    return
                logger.debug("%s 无数据，生成空表", code)
                if period == "1d":
                    new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
                else:
                    new_df = pd.DataFrame(columns=["time", "open", "close", "high", "low", "volume"])
            else:
                # 如果有旧数据，合并
                if period == "1d" and existing_df is not None and not existing_df.empty:
                    merged_df = pd.concat([existing_df, new_df], ignore_index=True)
                    merged_df = merged_df.drop_duplicates(subset=time_col, keep="last")
                    new_df = merged_df
            
            new_df = validate(new_df, period)
            new_df = new_df.sort_values(time_col).reset_index(drop=True)
            new_df.to_csv(csv_path, index=False)
            logger.debug("%s %s 数据已保存", code, period)
            break
            
        except Exception as e:
            logger.warning("%s 第 %d 次抓取失败: %s", code, attempt, e)
            if attempt < 3:
                time.sleep(2)
    else:
        logger.error("%s 三次抓取均失败，已跳过！", code)


def fetch_one_full(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    period: str = "1d",
) -> None:
    """
    全量覆盖更新单只股票数据
    
    Args:
        code: 股票代码
        start: 起始日期
        end: 结束日期
        out_dir: 输出目录
        period: 周期
    """
    # 确定存储路径
    if period == "1d":
        csv_path = out_dir / f"{code}.csv"
        time_col = "date"
    else:
        minute_dir = out_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        csv_path = minute_dir / f"{end}.csv"
        time_col = "time"
    
    for attempt in range(1, 4):
        try:
            new_df = _get_kline_xtquant(code, start, end, period)
            
            if new_df.empty:
                logger.debug("%s 无数据，生成空表", code)
                if period == "1d":
                    new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
                else:
                    new_df = pd.DataFrame(columns=["time", "open", "close", "high", "low", "volume"])
            
            new_df = validate(new_df, period)
            new_df = new_df.sort_values(time_col).reset_index(drop=True)
            new_df.to_csv(csv_path, index=False)
            logger.debug("%s %s 数据已保存", code, period)
            break
            
        except Exception as e:
            logger.warning("%s 第 %d 次抓取失败: %s", code, attempt, e)
            if attempt < 3:
                time.sleep(2)
    else:
        logger.error("%s 三次抓取均失败，已跳过！", code)


def load_codes_from_stocklist(stocklist_csv: Path, exclude_boards: set = None) -> List[str]:
    """
    从 stocklist.csv 读取股票代码列表
    
    Args:
        stocklist_csv: 股票列表文件路径
        exclude_boards: 要排除的板块集合 {"gem", "star", "bj"}
    
    Returns:
        股票代码列表
    """
    if exclude_boards is None:
        exclude_boards = set()
    
    df = pd.read_csv(stocklist_csv)
    
    # 过滤板块
    if exclude_boards:
        code = df["symbol"].astype(str).str.zfill(6)
        ts_code = df["ts_code"].astype(str).str.upper() if "ts_code" in df.columns else code
        mask = pd.Series(True, index=df.index)
        
        if "gem" in exclude_boards:
            mask &= ~code.str.startswith(("300", "301"))
        if "star" in exclude_boards:
            mask &= ~code.str.startswith("688")
        if "bj" in exclude_boards:
            mask &= ~(ts_code.str.endswith(".BJ") | code.str.startswith(("4", "8")))
        
        df = df[mask].copy()
    
    codes = df["symbol"].astype(str).str.zfill(6).tolist()
    codes = list(dict.fromkeys(codes))  # 去重保持顺序
    
    logger.info("从 %s 读取到 %d 只股票（排除板块：%s）",
                stocklist_csv, len(codes), ",".join(sorted(exclude_boards)) or "无")
    return codes


# --------------------------- 命令行入口 --------------------------- #
def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="使用 xtquant/miniQMT 获取股票K线数据"
    )
    parser.add_argument("--start", default="20190101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default="today", help="结束日期 YYYYMMDD 或 'today'")
    parser.add_argument("--period", default="1d", choices=["1d", "1m", "5m", "15m", "30m", "60m"],
                        help="K线周期（默认日线）")
    parser.add_argument("--stocklist", type=Path, default=Path("./stocklist.csv"),
                        help="股票清单CSV路径")
    parser.add_argument("--exclude-boards", nargs="*", default=[],
                        choices=["gem", "star", "bj"],
                        help="排除板块：gem(创业板) star(科创板) bj(北交所)")
    parser.add_argument("--full", action="store_true", help="强制全量覆盖")
    parser.add_argument("--out", default="./data", help="输出目录")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--check", action="store_true", help="仅检查 miniQMT 连接状态")
    args = parser.parse_args()
    
    # 检查连接
    if args.check:
        connected, msg = check_connection()
        print(f"连接状态: {'成功' if connected else '失败'}")
        print(f"详情: {msg}")
        sys.exit(0 if connected else 1)
    
    if not HAS_XTQUANT:
        logger.error("xtquant 未安装，请从迅投官网下载安装")
        sys.exit(1)
    
    # 检查 miniQMT 连接
    connected, msg = check_connection()
    if not connected:
        logger.error("miniQMT 连接失败: %s", msg)
        sys.exit(1)
    
    logger.info("miniQMT 连接正常")
    
    # 日期解析
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # 读取股票列表
    exclude_boards = set(args.exclude_boards or [])
    codes = load_codes_from_stocklist(args.stocklist, exclude_boards)
    
    if not codes:
        logger.error("stocklist 为空或被过滤后无代码")
        sys.exit(1)
    
    update_mode = "全量覆盖" if args.full else "增量更新"
    logger.info(
        "开始抓取 %d 支股票 | 数据源:xtquant | 周期:%s | 模式:%s | 日期:%s → %s",
        len(codes), args.period, update_mode, start, end
    )
    
    fetch_func = fetch_one_full if args.full else fetch_one
    
    # 多线程抓取
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(fetch_func, code, start, end, out_dir, args.period)
            for code in codes
        ]
        for _ in tqdm(as_completed(futures), total=len(futures), desc="下载进度"):
            pass
    
    logger.info("全部任务完成，数据已保存至 %s", out_dir.resolve())


if __name__ == "__main__":
    main()

