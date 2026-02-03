from __future__ import annotations

import argparse
import datetime as dt
import logging
import random
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional
import os

import pandas as pd
import tushare as ts
from tqdm import tqdm

warnings.filterwarnings("ignore")

# --------------------------- 全局日志配置 --------------------------- #
LOG_FILE = Path("fetch.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("fetch_from_stocklist")

# --------------------------- 限流/封禁处理配置 --------------------------- #
COOLDOWN_SECS = 600
BAN_PATTERNS = (
    "访问频繁", "请稍后", "超过频率", "频繁访问",
    "too many requests", "429",
    "forbidden", "403",
    "max retries exceeded"
)

def _looks_like_ip_ban(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(pat in msg for pat in BAN_PATTERNS)

class RateLimitError(RuntimeError):
    """表示命中限流/封禁，需要长时间冷却后重试。"""
    pass

def _cool_sleep(base_seconds: int) -> None:
    jitter = random.uniform(0.9, 1.2)
    sleep_s = max(1, int(base_seconds * jitter))
    logger.warning("疑似被限流/封禁，进入冷却期 %d 秒...", sleep_s)
    time.sleep(sleep_s)

# --------------------------- 历史K线（Tushare 日线，不复权 + 复权因子） --------------------------- #
pro: Optional[ts.pro_api] = None  # 模块级会话

def set_api(session) -> None:
    """由外部(比如GUI)注入已创建好的 ts.pro_api() 会话"""
    global pro
    pro = session
    

def _to_ts_code(code: str) -> str:
    """把6位code映射到标准 ts_code 后缀。"""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def _get_kline_tushare(code: str, start: str, end: str, adj: str = None) -> pd.DataFrame:
    """获取K线数据
    
    Args:
        code: 股票代码
        start: 起始日期
        end: 结束日期
        adj: 复权类型，None=不复权，'qfq'=前复权，'hfq'=后复权
    """
    ts_code = _to_ts_code(code)
    try:
        df = ts.pro_bar(
            ts_code=ts_code,
            adj=adj,  # 复权类型
            start_date=start,
            end_date=end,
            freq="D",
            api=pro
        )
    except Exception as e:
        if _looks_like_ip_ban(e):
            raise RateLimitError(str(e)) from e
        raise

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"trade_date": "date", "vol": "volume"})[
        ["date", "open", "close", "high", "low", "volume"]
    ].copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def _get_adj_factor(code: str, start: str, end: str) -> pd.DataFrame:
    """获取复权因子"""
    ts_code = _to_ts_code(code)
    try:
        df = pro.adj_factor(
            ts_code=ts_code,
            start_date=start,
            end_date=end
        )
    except Exception as e:
        if _looks_like_ip_ban(e):
            raise RateLimitError(str(e)) from e
        raise

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"trade_date": "date"})[["date", "adj_factor"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def validate(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    if df["date"].isna().any():
        raise ValueError("存在缺失日期！")
    if (df["date"] > pd.Timestamp.today()).any():
        raise ValueError("数据包含未来日期，可能抓取错误！")
    return df

# --------------------------- 读取 stocklist.csv & 过滤板块 --------------------------- #

def _filter_by_boards_stocklist(df: pd.DataFrame, exclude_boards: set[str]) -> pd.DataFrame:
    """
    exclude_boards 子集：{'gem','star','bj'}
    - gem  : 创业板 300/301（.SZ）
    - star : 科创板 688（.SH）
    - bj   : 北交所（.BJ 或 4/8 开头）
    """
    code = df["symbol"].astype(str)
    ts_code = df["ts_code"].astype(str).str.upper()
    mask = pd.Series(True, index=df.index)

    if "gem" in exclude_boards:
        mask &= ~code.str.startswith(("300", "301"))
    if "star" in exclude_boards:
        mask &= ~code.str.startswith(("688",))
    if "bj" in exclude_boards:
        mask &= ~(ts_code.str.endswith(".BJ") | code.str.startswith(("4", "8")))

    return df[mask].copy()

def load_codes_from_stocklist(stocklist_csv: Path, exclude_boards: set[str]) -> List[str]:
    df = pd.read_csv(stocklist_csv)    
    df = _filter_by_boards_stocklist(df, exclude_boards)
    codes = df["symbol"].astype(str).str.zfill(6).tolist()
    codes = list(dict.fromkeys(codes))  # 去重保持顺序
    logger.info("从 %s 读取到 %d 只股票（排除板块：%s）",
                stocklist_csv, len(codes), ",".join(sorted(exclude_boards)) or "无")
    return codes

# --------------------------- 单只抓取（增量更新） --------------------------- #
def fetch_one(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
):
    """
    增量更新策略（使用前复权数据）：
    1. 检查本地文件是否存在
    2. 如果存在，从最后一天开始拉取新数据
    3. 合并新旧数据，去重保存
    
    注意：如果期间发生除权除息事件，历史数据的前复权价格会变化，
    此时建议使用全量更新（fetch_one_full）来确保数据准确。
    """
    parquet_path = out_dir / f"{code}.parquet"
    
    # 确定增量起始日期
    incremental_start = start
    existing_df = None
    
    # 仅检查 Parquet
    if parquet_path.exists():
        try:
            existing_df = pd.read_parquet(parquet_path)
            if not existing_df.empty and "date" in existing_df.columns:
                last_date = existing_df["date"].max()
                incremental_start = last_date.strftime("%Y%m%d")
                logger.debug("%s 增量更新：从 %s 开始", code, incremental_start)
        except Exception as e:
            logger.warning("%s 读取现有 Parquet 失败: %s", code, e)
            existing_df = None

    for attempt in range(1, 4):
        try:
            # 直接拉取前复权K线数据
            new_df = _get_kline_tushare(code, incremental_start, end, adj='qfq')
            
            if new_df.empty:
                if existing_df is not None and not existing_df.empty:
                    logger.debug("%s 无新数据，保持现有数据", code)
                    return
                logger.debug("%s 无数据，生成空表。", code)
                new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
            else:
                # 如果有旧数据，合并
                if existing_df is not None and not existing_df.empty:
                    # 移除旧数据中的 adj_factor 列（如果存在，兼容旧格式）
                    if "adj_factor" in existing_df.columns:
                        existing_df = existing_df.drop(columns=["adj_factor"])
                    
                    # 合并新旧数据
                    merged_df = pd.concat([existing_df, new_df], ignore_index=True)
                    # 按日期去重，保留最新的（新数据在后面，所以 keep='last'）
                    merged_df = merged_df.drop_duplicates(subset="date", keep="last")
                    new_df = merged_df
            
            new_df = validate(new_df)
            new_df = new_df.sort_values("date").reset_index(drop=True)
            # 统一保存为 Parquet
            new_df.to_parquet(parquet_path, index=False)
            break
        except Exception as e:
            if _looks_like_ip_ban(e):
                logger.error(f"{code} 第 {attempt} 次抓取疑似被封禁，沉睡 {COOLDOWN_SECS} 秒")
                _cool_sleep(COOLDOWN_SECS)
            else:
                silent_seconds = 60
                logger.info(f"{code} 第 {attempt} 次抓取失败，{silent_seconds} 秒后重试：{e}")
                time.sleep(silent_seconds)
    else:
        logger.error("%s 三次抓取均失败，已跳过！", code)


def fetch_one_full(
    code: str,
    start: str,
    end: str,
    out_dir: Path,
):
    """
    全量覆盖策略（用于强制刷新）
    直接获取 Tushare 服务端计算的前复权数据，确保与主流软件一致
    """
    parquet_path = out_dir / f"{code}.parquet"

    for attempt in range(1, 4):
        try:
            # 直接拉取前复权K线数据（Tushare服务端计算，更准确）
            new_df = _get_kline_tushare(code, start, end, adj='qfq')
            
            if new_df.empty:
                logger.debug("%s 无数据，生成空表。", code)
                new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
            
            new_df = validate(new_df)
            new_df = new_df.sort_values("date").reset_index(drop=True)
            # 统一保存为 Parquet
            new_df.to_parquet(parquet_path, index=False)
            break
        except Exception as e:
            if _looks_like_ip_ban(e):
                logger.error(f"{code} 第 {attempt} 次抓取疑似被封禁，沉睡 {COOLDOWN_SECS} 秒")
                _cool_sleep(COOLDOWN_SECS)
            else:
                silent_seconds = 60
                logger.info(f"{code} 第 {attempt} 次抓取失败，{silent_seconds} 秒后重试：{e}")
                time.sleep(silent_seconds)
    else:
        logger.error("%s 三次抓取均失败，已跳过！", code)


# --------------------------- 主入口 --------------------------- #
def main():
    parser = argparse.ArgumentParser(description="从 stocklist.csv 读取股票池并用 Tushare 抓取日线K线（前复权，增量更新，保存为 Parquet）")
    # 抓取范围
    parser.add_argument("--start", default="20190101", help="起始日期 YYYYMMDD 或 'today'")
    parser.add_argument("--end", default="today", help="结束日期 YYYYMMDD 或 'today'")
    # 股票清单与板块过滤
    parser.add_argument("--stocklist", type=Path, default=Path("./stocklist.csv"), help="股票清单CSV路径（需含 ts_code 或 symbol）")
    parser.add_argument(
        "--exclude-boards",
        nargs="*",
        default=[],
        choices=["gem", "star", "bj"],
        help="排除板块，可多选：gem(创业板300/301) star(科创板688) bj(北交所.BJ/4/8)"
    )
    # 更新模式
    parser.add_argument("--full", action="store_true", help="强制全量覆盖（默认为增量更新）")
    # 其它
    parser.add_argument("--out", default="./data", help="输出目录")
    parser.add_argument("--workers", type=int, default=6, help="并发线程数")
    args = parser.parse_args()

    # ---------- Tushare Token ---------- #
    os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    ts_token = os.environ.get("TUSHARE_TOKEN")
    if not ts_token:
        raise ValueError("请先设置环境变量 TUSHARE_TOKEN，例如：export TUSHARE_TOKEN=你的token")
    ts.set_token(ts_token)
    global pro
    pro = ts.pro_api()

    # ---------- 日期解析 ---------- #
    start = dt.date.today().strftime("%Y%m%d") if str(args.start).lower() == "today" else args.start
    end = dt.date.today().strftime("%Y%m%d") if str(args.end).lower() == "today" else args.end

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 从 stocklist.csv 读取股票池 ---------- #
    exclude_boards = set(args.exclude_boards or [])
    codes = load_codes_from_stocklist(args.stocklist, exclude_boards)

    if not codes:
        logger.error("stocklist 为空或被过滤后无代码，请检查。")
        sys.exit(1)

    update_mode = "全量覆盖" if args.full else "增量更新"
    logger.info(
        "开始抓取 %d 支股票 | 数据源:Tushare(日线,不复权+复权因子) | 模式:%s | 日期:%s → %s | 排除:%s",
        len(codes), update_mode, start, end, ",".join(sorted(exclude_boards)) or "无",
    )

    # 选择抓取函数
    fetch_func = fetch_one_full if args.full else fetch_one

    # ---------- 多线程抓取 ---------- #
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                fetch_func,
                code,
                start,
                end,
                out_dir,
            )
            for code in codes
        ]
        for _ in tqdm(as_completed(futures), total=len(futures), desc="下载进度"):
            pass

    logger.info("全部任务完成，数据已保存至 %s", out_dir.resolve())

if __name__ == "__main__":
    main()
