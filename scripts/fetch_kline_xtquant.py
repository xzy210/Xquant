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
import threading
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
_XTDATA_LOCK = threading.RLock()
_CONNECTION_ERROR_KEYWORDS = ("connect", "timeout", "断开", "无法连接", "行情服务")
_LAST_SESSION_REFRESH_DAY = None

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


def _try_reconnect() -> bool:
    """
    尝试刷新 xtdata 与 miniQMT 的连接。
    miniQMT 长时间运行后连接可能僵死（download_history_data 静默返回旧缓存），
    主动 reconnect 可恢复，无需用户手动重启客户端。
    """
    if not HAS_XTQUANT:
        return False
    with _XTDATA_LOCK:
        try:
            if hasattr(xtdata, 'reconnect'):
                xtdata.reconnect()
                logger.debug("xtdata.reconnect() 已调用")
                return True
            elif hasattr(xtdata, 'connect'):
                xtdata.connect()
                logger.debug("xtdata.connect() 已调用")
                return True
        except Exception as e:
            logger.warning("xtdata 重连尝试失败（不影响后续操作）: %s", e)
    return False


def _ensure_session_fresh_for_today():
    """
    跨天后的首次 xtdata 访问前主动重连一次。

    miniQMT 若昨天已打开、今天继续复用，可能不会抛连接异常，
    但历史接口会静默返回前一交易日缓存。这里在新的一天首次访问时
    主动刷新连接，避免“连接看似正常但数据停留在昨天”的情况。
    """
    global _LAST_SESSION_REFRESH_DAY

    today = dt.date.today()
    if _LAST_SESSION_REFRESH_DAY == today:
        return

    logger.info("检测到新交易日/首次访问，主动刷新 miniQMT 连接")
    if _try_reconnect():
        # 给 miniQMT 一点时间完成会话刷新，降低随后立刻读到旧缓存的概率。
        time.sleep(0.5)
        _LAST_SESSION_REFRESH_DAY = today


def _is_connection_error(exc: Exception) -> bool:
    """判断异常是否与 miniQMT 连接有关。"""
    error_msg = str(exc).lower()
    return any(keyword in error_msg for keyword in _CONNECTION_ERROR_KEYWORDS)


def _call_xtdata_locked(func, *, reconnect_on_failure: bool = False):
    """
    串行访问 xtdata，避免历史数据抓取和实时行情轮询同时击穿连接。
    """
    attempts = 2 if reconnect_on_failure else 1
    last_exc = None

    for attempt in range(attempts):
        try:
            with _XTDATA_LOCK:
                _ensure_session_fresh_for_today()
                return func()
        except Exception as exc:
            last_exc = exc
            should_retry = (
                reconnect_on_failure
                and attempt == 0
                and _is_connection_error(exc)
            )
            if should_retry:
                logger.warning("xtdata 连接异常，尝试重连后重试: %s", exc)
                _try_reconnect()
                continue
            raise

    if last_exc is not None:
        raise last_exc


def check_connection() -> Tuple[bool, str]:
    """
    检测 miniQMT 连接状态（会先尝试重连以刷新僵死连接）
    
    Returns:
        (connected: bool, message: str)
    """
    if not HAS_XTQUANT:
        return False, "xtquant 未安装，请从迅投官网下载安装"

    try:
        test_code = "000001.SH"
        result = _call_xtdata_locked(
            lambda: xtdata.get_market_data(
                field_list=["close"],
                stock_list=[test_code],
                period="1d",
                count=1
            ),
            reconnect_on_failure=True,
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


def _coerce_xt_tick_datetime(value) -> Optional[dt.datetime]:
    """兼容 xtquant tick 时间字段的多种返回格式。"""
    if value in (None, "", 0):
        return None

    if isinstance(value, dt.datetime):
        return value

    if isinstance(value, (int, float)):
        ivalue = int(value)
        if ivalue <= 0:
            return None
        # 兼容秒级/毫秒级时间戳
        if ivalue >= 10**12:
            return dt.datetime.fromtimestamp(ivalue / 1000)
        return dt.datetime.fromtimestamp(ivalue)

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None

        if value.isdigit():
            return _coerce_xt_tick_datetime(int(value))

        for fmt in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(value, fmt)
            except ValueError:
                continue

    return None


def _extract_tick_datetime(tick: dict) -> Optional[dt.datetime]:
    """优先从 timetag 取时间，失败时回退到 time。"""
    if not isinstance(tick, dict):
        return None

    for key in ("timetag", "time"):
        tick_dt = _coerce_xt_tick_datetime(tick.get(key))
        if tick_dt is not None:
            return tick_dt
    return None


def _extract_tick_time_ms(tick: dict) -> int:
    """将 tick 时间统一转换为毫秒时间戳，供补齐日线时写入。"""
    if not isinstance(tick, dict):
        return 0

    for key in ("time", "timetag"):
        value = tick.get(key)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, (int, float)):
            ivalue = int(value)
            if ivalue > 0:
                return ivalue if ivalue >= 10**12 else ivalue * 1000

    tick_dt = _extract_tick_datetime(tick)
    return int(tick_dt.timestamp() * 1000) if tick_dt is not None else 0


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
        def _fetch_xtdata():
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

                # 补丁：处理当日实时日线（QMT download_history_data 可能不包含当日未收盘数据）
                today_str = dt.date.today().strftime("%Y%m%d")
                if end >= today_str:
                    try:
                        # 尝试触发实时行情获取
                        xtdata.get_market_data(field_list=['lastPrice'], stock_list=[xt_code], period='tick', count=1)

                        # 尝试获取今日快照，构建今日K线
                        full_tick = xtdata.get_full_tick([xt_code])
                        if xt_code in full_tick:
                            tick = full_tick[xt_code]
                            # 只有在有成交量时才补充（排除停牌或未开盘）
                            if tick.get('volume', 0) > 0:
                                # 验证tick数据的日期是否真的是今天（避免非交易日返回旧数据）
                                tick_dt = _extract_tick_datetime(tick)
                                if tick_dt is None:
                                    logger.debug("%s tick数据缺少有效时间字段，跳过补充", code)
                                else:
                                    tick_date_str = tick_dt.strftime("%Y%m%d")
                                    if tick_date_str != today_str:
                                        logger.debug("%s tick数据日期(%s)与今日(%s)不符，跳过补充", code, tick_date_str, today_str)
                                    else:
                                        # 构建与 get_market_data_ex 一致的 DataFrame 行
                                        # 确定索引类型（通常是 int 或 str，取决于 QMT 版本和周期）
                                        idx_type = type(data[xt_code].index[0]) if (data and xt_code in data and not data[xt_code].empty) else str
                                        new_idx = idx_type(today_str)

                                        today_bar = pd.DataFrame({
                                            'open': [tick.get('open', 0)],
                                            'high': [tick.get('high', 0)],
                                            'low': [tick.get('low', 0)],
                                            'close': [tick.get('lastPrice', 0)],
                                            'volume': [tick.get('volume', 0)],
                                            'time': [_extract_tick_time_ms(tick)],
                                        }, index=[new_idx])

                                        if not data or xt_code not in data or data[xt_code].empty:
                                            data = {xt_code: today_bar}
                                        else:
                                            current_df = data[xt_code]
                                            # 强制更新逻辑：如果已有今日数据，先删除旧的再添加新的，确保是最新快照
                                            if str(current_df.index[-1]) == today_str:
                                                current_df = current_df.iloc[:-1]

                                            data[xt_code] = pd.concat([current_df, today_bar])
                                        logger.debug("%s 已补充/更新今日实时日线数据", code)
                    except Exception as e:
                        logger.debug("%s 补充今日实时数据失败: %s", code, e)
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
            return data

        data = _call_xtdata_locked(_fetch_xtdata, reconnect_on_failure=True)
        
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
        def _fetch_xtdata():
            # 下载该日的分钟数据
            xtdata.download_history_data(
                stock_code=xt_code,
                period=xt_period,
                start_time=date_str,
                end_time=date_str
            )

            # 获取数据
            return xtdata.get_market_data_ex(
                field_list=[],  # 获取所有字段
                stock_list=[xt_code],
                period=xt_period,
                start_time=date_str,
                end_time=date_str,
                dividend_type="front"
            )

        data = _call_xtdata_locked(_fetch_xtdata, reconnect_on_failure=True)
        
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
        parquet_path = out_dir / f"{code}.parquet"
        time_col = "date"
    else:
        # 分钟线存储在 minute/{code}/ 目录下
        minute_dir = out_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        # 按日期存储
        parquet_path = minute_dir / f"{end}.parquet"
        time_col = "time"
    
    # 确定增量起始日期
    incremental_start = start
    existing_df = None
    
    if period == "1d" and parquet_path.exists():
        try:
            existing_df = pd.read_parquet(parquet_path)
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
            new_df.to_parquet(parquet_path, index=False)
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
        parquet_path = out_dir / f"{code}.parquet"
        time_col = "date"
    else:
        minute_dir = out_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = minute_dir / f"{end}.parquet"
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
            new_df.to_parquet(parquet_path, index=False)
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
    parser.add_argument("--stocklist", type=Path, default=Path("./stocklist/stocklist.csv"),
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


# --------------------------- ETF 数据获取 --------------------------- #

def _to_xt_etf_code(code: str) -> str:
    """
    把ETF代码映射到 xtquant 格式
    
    Args:
        code: 6位ETF代码
    
    Returns:
        xtquant 格式代码，如 "510300.SH"
    """
    code = str(code).zfill(6)
    # 上交所ETF: 51xxxx, 56xxxx, 58xxxx, 588xxx
    if code.startswith(("51", "56", "58")):
        return f"{code}.SH"
    # 深交所ETF: 15xxxx, 16xxxx, 159xxx
    elif code.startswith(("15", "16")):
        return f"{code}.SZ"
    else:
        # 默认按上交所处理
        return f"{code}.SH"


def fetch_etf_kline(
    code: str,
    start: str,
    end: str,
    period: str = "1d"
) -> pd.DataFrame:
    """
    获取ETF K线数据
    
    Args:
        code: 6位ETF代码
        start: 起始日期 YYYYMMDD
        end: 结束日期 YYYYMMDD
        period: 周期，支持 1d/1m/5m/15m/30m/60m
    
    Returns:
        DataFrame 包含 date/time, open, close, high, low, volume
    """
    if not HAS_XTQUANT:
        logger.error("xtquant 未安装")
        return pd.DataFrame()
    
    xt_code = _to_xt_etf_code(code)
    xt_period = PERIOD_MAP.get(period, period)
    
    try:
        def _fetch_xtdata():
            xtdata.download_history_data(
                stock_code=xt_code,
                period=xt_period,
                start_time=start,
                end_time=end
            )

            # 获取数据
            if period == "1d":
                data = xtdata.get_market_data_ex(
                    field_list=[],
                    stock_list=[xt_code],
                    period=xt_period,
                    count=-1,
                    dividend_type="front"
                )

                # 补充今日实时数据
                today_str = dt.date.today().strftime("%Y%m%d")
                if end >= today_str:
                    try:
                        xtdata.get_market_data(field_list=['lastPrice'], stock_list=[xt_code], period='tick', count=1)
                        full_tick = xtdata.get_full_tick([xt_code])
                        if xt_code in full_tick:
                            tick = full_tick[xt_code]
                            if tick.get('volume', 0) > 0:
                                # 验证tick数据的日期是否真的是今天（避免非交易日返回旧数据）
                                tick_dt = _extract_tick_datetime(tick)
                                if tick_dt is None:
                                    logger.debug("%s ETF tick数据缺少有效时间字段，跳过补充", code)
                                else:
                                    tick_date_str = tick_dt.strftime("%Y%m%d")
                                    if tick_date_str != today_str:
                                        logger.debug("%s ETF tick数据日期(%s)与今日(%s)不符，跳过补充", code, tick_date_str, today_str)
                                    else:
                                        idx_type = type(data[xt_code].index[0]) if (data and xt_code in data and not data[xt_code].empty) else str
                                        new_idx = idx_type(today_str)

                                        today_bar = pd.DataFrame({
                                            'open': [tick.get('open', 0)],
                                            'high': [tick.get('high', 0)],
                                            'low': [tick.get('low', 0)],
                                            'close': [tick.get('lastPrice', 0)],
                                            'volume': [tick.get('volume', 0)],
                                            'time': [_extract_tick_time_ms(tick)],
                                        }, index=[new_idx])

                                        if not data or xt_code not in data or data[xt_code].empty:
                                            data = {xt_code: today_bar}
                                        else:
                                            current_df = data[xt_code]
                                            if str(current_df.index[-1]) == today_str:
                                                current_df = current_df.iloc[:-1]
                                            data[xt_code] = pd.concat([current_df, today_bar])
                                        logger.debug("%s ETF 已补充今日实时日线数据", code)
                    except Exception as e:
                        logger.debug("%s ETF 补充今日实时数据失败: %s", code, e)
            else:
                data = xtdata.get_market_data_ex(
                    field_list=[],
                    stock_list=[xt_code],
                    period=xt_period,
                    start_time=start,
                    end_time=end,
                    dividend_type="front"
                )
            return data

        data = _call_xtdata_locked(_fetch_xtdata, reconnect_on_failure=True)
        
        if data is None or len(data) == 0 or xt_code not in data:
            logger.debug("%s ETF 无数据", code)
            return pd.DataFrame()
        
        df = data[xt_code].copy()
        
        if df is None or df.empty:
            logger.debug("%s ETF 无数据", code)
            return pd.DataFrame()
        
        # 处理数据格式
        df = df.reset_index()
        df = df.rename(columns={"index": "_index"})
        
        if period == "1d":
            df["date"] = pd.to_datetime(df["_index"].astype(str), format="%Y%m%d", errors="coerce")
        else:
            index_str = df["_index"].astype(str)
            if len(index_str.iloc[0]) >= 12:
                df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M%S", errors="coerce")
                if df["time"].isna().all():
                    df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M", errors="coerce")
            else:
                if "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        
        # 选择需要的列
        if period == "1d":
            result_cols = ["date", "open", "high", "low", "close", "volume"]
            sort_col = "date"
        else:
            result_cols = ["time", "open", "high", "low", "close", "volume"]
            sort_col = "time"
        
        missing_cols = [col for col in result_cols if col not in df.columns]
        if missing_cols:
            logger.warning("%s ETF 缺少列: %s", code, missing_cols)
            return pd.DataFrame()
        
        df = df[result_cols].copy()
        
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        
        df = df.dropna(subset=[sort_col])
        
        if period == "1d" and start and end:
            start_dt = pd.to_datetime(start, format="%Y%m%d")
            end_dt = pd.to_datetime(end, format="%Y%m%d")
            df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        
        df = df.sort_values(sort_col).reset_index(drop=True)
        
        logger.debug("%s ETF 获取到 %d 条数据", code, len(df))
        return df
        
    except Exception as e:
        logger.error("%s ETF 获取数据失败: %s", code, e)
        import traceback
        logger.debug(traceback.format_exc())
        return pd.DataFrame()


def fetch_etf_one(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    period: str = "1d",
) -> None:
    """
    增量更新单只ETF数据
    
    Args:
        code: ETF代码
        start: 起始日期
        end: 结束日期
        out_dir: 输出目录 (将存储到 out_dir/etf/ 目录)
        period: 周期
    """
    # ETF数据存储到 etf/ 子目录
    etf_dir = out_dir / "etf"
    etf_dir.mkdir(parents=True, exist_ok=True)
    
    if period == "1d":
        parquet_path = etf_dir / f"{code}.parquet"
        time_col = "date"
    else:
        minute_dir = etf_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = minute_dir / f"{end}.parquet"
        time_col = "time"
    
    # 增量更新
    incremental_start = start
    existing_df = None
    
    if period == "1d" and parquet_path.exists():
        try:
            existing_df = pd.read_parquet(parquet_path)
            if not existing_df.empty and time_col in existing_df.columns:
                last_date = existing_df[time_col].max()
                incremental_start = last_date.strftime("%Y%m%d")
                logger.debug("%s ETF 增量更新：从 %s 开始", code, incremental_start)
        except Exception as e:
            logger.warning("%s ETF 读取现有文件失败: %s", code, e)
            existing_df = None
    
    for attempt in range(1, 4):
        try:
            new_df = fetch_etf_kline(code, incremental_start, end, period)
            
            if new_df.empty:
                if existing_df is not None and not existing_df.empty:
                    logger.debug("%s ETF 无新数据，保持现有数据", code)
                    return
                logger.debug("%s ETF 无数据，生成空表", code)
                if period == "1d":
                    new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
                else:
                    new_df = pd.DataFrame(columns=["time", "open", "close", "high", "low", "volume"])
            else:
                if period == "1d" and existing_df is not None and not existing_df.empty:
                    merged_df = pd.concat([existing_df, new_df], ignore_index=True)
                    merged_df = merged_df.drop_duplicates(subset=time_col, keep="last")
                    new_df = merged_df
            
            new_df = validate(new_df, period)
            new_df = new_df.sort_values(time_col).reset_index(drop=True)
            new_df.to_parquet(parquet_path, index=False)
            logger.debug("%s ETF %s 数据已保存", code, period)
            break
            
        except Exception as e:
            logger.warning("%s ETF 第 %d 次抓取失败: %s", code, attempt, e)
            if attempt < 3:
                time.sleep(2)
    else:
        logger.error("%s ETF 三次抓取均失败，已跳过！", code)


def fetch_etf_one_full(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    period: str = "1d",
) -> None:
    """
    全量覆盖更新单只ETF数据
    """
    etf_dir = out_dir / "etf"
    etf_dir.mkdir(parents=True, exist_ok=True)
    
    if period == "1d":
        parquet_path = etf_dir / f"{code}.parquet"
        time_col = "date"
    else:
        minute_dir = etf_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = minute_dir / f"{end}.parquet"
        time_col = "time"
    
    for attempt in range(1, 4):
        try:
            new_df = fetch_etf_kline(code, start, end, period)
            
            if new_df.empty:
                logger.debug("%s ETF 无数据，生成空表", code)
                if period == "1d":
                    new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
                else:
                    new_df = pd.DataFrame(columns=["time", "open", "close", "high", "low", "volume"])
            
            new_df = validate(new_df, period)
            new_df = new_df.sort_values(time_col).reset_index(drop=True)
            new_df.to_parquet(parquet_path, index=False)
            logger.debug("%s ETF %s 数据已保存", code, period)
            break
            
        except Exception as e:
            logger.warning("%s ETF 第 %d 次抓取失败: %s", code, attempt, e)
            if attempt < 3:
                time.sleep(2)
    else:
        logger.error("%s ETF 三次抓取均失败，已跳过！", code)


def load_etf_codes_from_config(config_path: Path) -> List[str]:
    """
    从配置文件读取ETF代码列表
    
    Args:
        config_path: ETF配置文件路径 (JSON)
    
    Returns:
        ETF代码列表
    """
    import json
    
    if not config_path.exists():
        logger.warning("ETF配置文件不存在: %s", config_path)
        return []
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        codes = []
        for category in config.get("categories", []):
            for etf in category.get("etfs", []):
                code = etf.get("code", "")
                if code:
                    codes.append(code)
        
        codes = list(dict.fromkeys(codes))  # 去重保持顺序
        logger.info("从配置文件读取到 %d 只ETF", len(codes))
        return codes
        
    except Exception as e:
        logger.error("读取ETF配置文件失败: %s", e)
        return []


def fetch_all_etf(
    config_path: Path,
    out_dir: Path,
    start: str = "20190101",
    end: str = None,
    period: str = "1d",
    full_update: bool = False,
    workers: int = 4
) -> int:
    """
    批量获取所有ETF数据
    
    Args:
        config_path: ETF配置文件路径
        out_dir: 输出目录
        start: 起始日期
        end: 结束日期 (None则为今天)
        period: 周期
        full_update: 是否全量更新
        workers: 并发线程数
    
    Returns:
        成功获取的ETF数量
    """
    if not HAS_XTQUANT:
        logger.error("xtquant 未安装")
        return 0
    
    # 检查连接
    connected, msg = check_connection()
    if not connected:
        logger.error("miniQMT 连接失败: %s", msg)
        return 0
    
    codes = load_etf_codes_from_config(config_path)
    if not codes:
        logger.error("没有找到ETF代码")
        return 0
    
    if end is None:
        end = dt.date.today().strftime("%Y%m%d")
    
    out_dir = Path(out_dir)
    
    fetch_func = fetch_etf_one_full if full_update else fetch_etf_one
    
    update_mode = "全量覆盖" if full_update else "增量更新"
    logger.info(
        "开始抓取 %d 只ETF | 周期:%s | 模式:%s | 日期:%s → %s",
        len(codes), period, update_mode, start, end
    )
    
    success_count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(fetch_func, code, start, end, out_dir, period)
            for code in codes
        ]
        for future in tqdm(as_completed(futures), total=len(futures), desc="ETF下载进度"):
            try:
                future.result()
                success_count += 1
            except Exception:
                pass
    
    logger.info("ETF数据获取完成，成功 %d/%d", success_count, len(codes))
    return success_count


# ======================= Index Data Functions =======================

# Default index list for common market indices
DEFAULT_INDEX_LIST = [
    {"code": "000001", "name": "上证指数", "exchange": "SH"},
    {"code": "000016", "name": "上证50", "exchange": "SH"},
    {"code": "000300", "name": "沪深300", "exchange": "SH"},
    {"code": "000905", "name": "中证500", "exchange": "SH"},
    {"code": "000852", "name": "中证1000", "exchange": "SH"},
    {"code": "399001", "name": "深证成指", "exchange": "SZ"},
    {"code": "399006", "name": "创业板指", "exchange": "SZ"},
    {"code": "399673", "name": "创业板50", "exchange": "SZ"},
    {"code": "399005", "name": "中小板指", "exchange": "SZ"},
    {"code": "688001", "name": "科创50", "exchange": "SH"},
]


def _to_xt_index_code(code: str, exchange: str = None) -> str:
    """
    Convert 6-digit index code to xtquant format
    
    Args:
        code: 6-digit index code
        exchange: Exchange code ("SH" or "SZ"), if None will guess from code
    
    Returns:
        xtquant format code, e.g. "000001.SH"
    """
    code = str(code).zfill(6)
    
    if exchange:
        return f"{code}.{exchange}"
    
    # Guess exchange from code
    # 000xxx, 399xxx are typically SH for 000 and SZ for 399
    if code.startswith("399"):
        return f"{code}.SZ"
    elif code.startswith("000"):
        return f"{code}.SH"
    elif code.startswith("688"):
        return f"{code}.SH"
    else:
        # Default to SH for other codes
        return f"{code}.SH"


def fetch_index_kline(
    code: str,
    start: str,
    end: str,
    period: str = "1d",
    exchange: str = None
) -> pd.DataFrame:
    """
    Fetch index K-line data
    
    Args:
        code: 6-digit index code
        start: Start date YYYYMMDD
        end: End date YYYYMMDD
        period: Period, supports 1d/1m/5m/15m/30m/60m
        exchange: Exchange code ("SH" or "SZ"), if None will guess from code
    
    Returns:
        DataFrame with date/time, open, close, high, low, volume
    """
    if not HAS_XTQUANT:
        logger.error("xtquant not installed")
        return pd.DataFrame()
    
    xt_code = _to_xt_index_code(code, exchange)
    xt_period = PERIOD_MAP.get(period, period)
    
    try:
        def _fetch_xtdata():
            # Download history data
            xtdata.download_history_data(
                stock_code=xt_code,
                period=xt_period,
                start_time=start,
                end_time=end
            )

            # Get data
            if period == "1d":
                data = xtdata.get_market_data_ex(
                    field_list=[],
                    stock_list=[xt_code],
                    period=xt_period,
                    count=-1,
                    dividend_type="none"  # Index data without dividend adjustment
                )

                # Supplement today's realtime data
                today_str = dt.date.today().strftime("%Y%m%d")
                if end >= today_str:
                    try:
                        xtdata.get_market_data(field_list=['lastPrice'], stock_list=[xt_code], period='tick', count=1)
                        full_tick = xtdata.get_full_tick([xt_code])
                        if xt_code in full_tick:
                            tick = full_tick[xt_code]
                            if tick.get('volume', 0) > 0:
                                # Verify tick data date is really today
                                tick_dt = _extract_tick_datetime(tick)
                                if tick_dt is None:
                                    logger.debug("%s index tick missing valid time field, skip supplement", code)
                                else:
                                    tick_date_str = tick_dt.strftime("%Y%m%d")
                                    if tick_date_str != today_str:
                                        logger.debug("%s index tick date(%s) != today(%s), skip supplement", code, tick_date_str, today_str)
                                    else:
                                        idx_type = type(data[xt_code].index[0]) if (data and xt_code in data and not data[xt_code].empty) else str
                                        new_idx = idx_type(today_str)

                                        today_bar = pd.DataFrame({
                                            'open': [tick.get('open', 0)],
                                            'high': [tick.get('high', 0)],
                                            'low': [tick.get('low', 0)],
                                            'close': [tick.get('lastPrice', 0)],
                                            'volume': [tick.get('volume', 0)],
                                            'time': [_extract_tick_time_ms(tick)],
                                        }, index=[new_idx])

                                        if not data or xt_code not in data or data[xt_code].empty:
                                            data = {xt_code: today_bar}
                                        else:
                                            current_df = data[xt_code]
                                            if str(current_df.index[-1]) == today_str:
                                                current_df = current_df.iloc[:-1]
                                            data[xt_code] = pd.concat([current_df, today_bar])
                                        logger.debug("%s index supplemented today's realtime daily data", code)
                    except Exception as e:
                        logger.debug("%s index supplement today's realtime data failed: %s", code, e)
            else:
                data = xtdata.get_market_data_ex(
                    field_list=[],
                    stock_list=[xt_code],
                    period=xt_period,
                    start_time=start,
                    end_time=end,
                    dividend_type="none"
                )
            return data

        data = _call_xtdata_locked(_fetch_xtdata, reconnect_on_failure=True)
        
        if data is None or len(data) == 0 or xt_code not in data:
            logger.debug("%s index no data", code)
            return pd.DataFrame()
        
        df = data[xt_code].copy()
        
        if df is None or df.empty:
            logger.debug("%s index no data", code)
            return pd.DataFrame()
        
        # Process data format
        df = df.reset_index()
        df = df.rename(columns={"index": "_index"})
        
        if period == "1d":
            df["date"] = pd.to_datetime(df["_index"].astype(str), format="%Y%m%d", errors="coerce")
        else:
            index_str = df["_index"].astype(str)
            if len(index_str.iloc[0]) >= 12:
                df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M%S", errors="coerce")
                if df["time"].isna().all():
                    df["time"] = pd.to_datetime(index_str, format="%Y%m%d%H%M", errors="coerce")
            else:
                if "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        
        # Select required columns
        if period == "1d":
            result_cols = ["date", "open", "high", "low", "close", "volume"]
            sort_col = "date"
        else:
            result_cols = ["time", "open", "high", "low", "close", "volume"]
            sort_col = "time"
        
        missing_cols = [col for col in result_cols if col not in df.columns]
        if missing_cols:
            logger.warning("%s index missing columns: %s", code, missing_cols)
            return pd.DataFrame()
        
        df = df[result_cols].copy()
        
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        
        df = df.dropna(subset=[sort_col])
        
        if period == "1d" and start and end:
            start_dt = pd.to_datetime(start, format="%Y%m%d")
            end_dt = pd.to_datetime(end, format="%Y%m%d")
            df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        
        df = df.sort_values(sort_col).reset_index(drop=True)
        
        logger.debug("%s index got %d records", code, len(df))
        return df
        
    except Exception as e:
        logger.error("%s index fetch data failed: %s", code, e)
        import traceback
        logger.debug(traceback.format_exc())
        return pd.DataFrame()


def fetch_index_one(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    period: str = "1d",
    exchange: str = None,
) -> None:
    """
    Incremental update single index data
    
    Args:
        code: Index code
        start: Start date
        end: End date
        out_dir: Output directory (will store in out_dir/index/)
        period: Period
        exchange: Exchange code
    """
    # Index data stored in index/ subdirectory
    index_dir = out_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    
    if period == "1d":
        parquet_path = index_dir / f"{code}.parquet"
        time_col = "date"
    else:
        minute_dir = index_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = minute_dir / f"{end}.parquet"
        time_col = "time"
    
    # Incremental update
    incremental_start = start
    existing_df = None
    
    if period == "1d" and parquet_path.exists():
        try:
            existing_df = pd.read_parquet(parquet_path)
            if not existing_df.empty and time_col in existing_df.columns:
                last_date = existing_df[time_col].max()
                incremental_start = last_date.strftime("%Y%m%d")
                logger.debug("%s index incremental update: from %s", code, incremental_start)
        except Exception as e:
            logger.warning("%s index read existing file failed: %s", code, e)
            existing_df = None
    
    for attempt in range(1, 4):
        try:
            new_df = fetch_index_kline(code, incremental_start, end, period, exchange)
            
            if new_df.empty:
                if existing_df is not None and not existing_df.empty:
                    logger.debug("%s index no new data, keep existing data", code)
                    return
                logger.debug("%s index no data, generate empty table", code)
                if period == "1d":
                    new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
                else:
                    new_df = pd.DataFrame(columns=["time", "open", "close", "high", "low", "volume"])
            else:
                if period == "1d" and existing_df is not None and not existing_df.empty:
                    merged_df = pd.concat([existing_df, new_df], ignore_index=True)
                    merged_df = merged_df.drop_duplicates(subset=time_col, keep="last")
                    new_df = merged_df
            
            new_df = validate(new_df, period)
            new_df = new_df.sort_values(time_col).reset_index(drop=True)
            new_df.to_parquet(parquet_path, index=False)
            logger.debug("%s index %s data saved", code, period)
            break
            
        except Exception as e:
            logger.warning("%s index attempt %d fetch failed: %s", code, attempt, e)
            if attempt < 3:
                time.sleep(2)
    else:
        logger.error("%s index three attempts all failed, skipped!", code)


def fetch_index_one_full(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
    period: str = "1d",
    exchange: str = None,
) -> None:
    """
    Full overwrite update single index data
    """
    index_dir = out_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    
    if period == "1d":
        parquet_path = index_dir / f"{code}.parquet"
        time_col = "date"
    else:
        minute_dir = index_dir / "minute" / code
        minute_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = minute_dir / f"{end}.parquet"
        time_col = "time"
    
    for attempt in range(1, 4):
        try:
            new_df = fetch_index_kline(code, start, end, period, exchange)
            
            if new_df.empty:
                logger.debug("%s index no data, generate empty table", code)
                if period == "1d":
                    new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
                else:
                    new_df = pd.DataFrame(columns=["time", "open", "close", "high", "low", "volume"])
            
            new_df = validate(new_df, period)
            new_df = new_df.sort_values(time_col).reset_index(drop=True)
            new_df.to_parquet(parquet_path, index=False)
            logger.debug("%s index %s data saved", code, period)
            break
            
        except Exception as e:
            logger.warning("%s index attempt %d fetch failed: %s", code, attempt, e)
            if attempt < 3:
                time.sleep(2)
    else:
        logger.error("%s index three attempts all failed, skipped!", code)


def load_index_codes_from_config(config_path: Path = None) -> List[dict]:
    """
    Load index list from config file
    
    Args:
        config_path: Index config file path (JSON), if None use default list
    
    Returns:
        List of index dict with code, name, exchange
    """
    import json
    
    if config_path is None:
        logger.info("Using default index list with %d indices", len(DEFAULT_INDEX_LIST))
        return DEFAULT_INDEX_LIST.copy()
    
    if not config_path.exists():
        logger.warning("Index config file not exists: %s, using default list", config_path)
        return DEFAULT_INDEX_LIST.copy()
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        indices = config.get("indices", [])
        if not indices:
            logger.warning("No indices found in config file, using default list")
            return DEFAULT_INDEX_LIST.copy()
        
        logger.info("Loaded %d indices from config file", len(indices))
        return indices
        
    except Exception as e:
        logger.error("Read index config file failed: %s", e)
        return DEFAULT_INDEX_LIST.copy()


def fetch_all_index(
    out_dir: Path,
    start: str = "19900101",
    end: str = None,
    period: str = "1d",
    full_update: bool = False,
    workers: int = 4,
    config_path: Path = None
) -> int:
    """
    Batch fetch all index data
    
    Args:
        out_dir: Output directory
        start: Start date
        end: End date (None means today)
        period: Period
        full_update: Whether to full update
        workers: Concurrent thread count
        config_path: Index config file path
    
    Returns:
        Successfully fetched index count
    """
    if not HAS_XTQUANT:
        logger.error("xtquant not installed")
        return 0
    
    # Check connection
    connected, msg = check_connection()
    if not connected:
        logger.error("miniQMT connection failed: %s", msg)
        return 0
    
    indices = load_index_codes_from_config(config_path)
    if not indices:
        logger.error("No index codes found")
        return 0
    
    if end is None:
        end = dt.date.today().strftime("%Y%m%d")
    
    out_dir = Path(out_dir)
    
    fetch_func = fetch_index_one_full if full_update else fetch_index_one
    
    update_mode = "Full overwrite" if full_update else "Incremental update"
    logger.info(
        "Starting fetch %d indices | period:%s | mode:%s | date:%s → %s",
        len(indices), period, update_mode, start, end
    )
    
    success_count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(fetch_func, idx.get("code"), start, end, out_dir, period, idx.get("exchange"))
            for idx in indices
        ]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Index download progress"):
            try:
                future.result()
                success_count += 1
            except Exception:
                pass
    
    logger.info("Index data fetch completed, success %d/%d", success_count, len(indices))
    return success_count


if __name__ == "__main__":
    main()

