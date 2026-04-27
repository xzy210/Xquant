from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from common.data_portal import get_data_portal


@dataclass
class WatchlistScanSummary:
    code: str
    name: str
    asset_type: str
    latest_close: float
    pct_20d: float
    pct_60d: float
    pct_120d: float
    volume_ratio_5_20: float
    price_percentile_120d: float
    trend_label: str
    ma_structure: str
    macd_label: str
    kdj_label: str
    risk_label: str


class AgentWatchlistScanService:
    """Build structured watchlist scan input from locally computed technical features."""

    MAX_SCAN_ITEMS = 12

    @classmethod
    def build_scan_prompt(cls, raw_context: Dict) -> Optional[Dict[str, str]]:
        watchlist = raw_context.get("watchlist", {}) or {}
        visible_items = list(watchlist.get("visible_items", []) or [])
        if not visible_items:
            return None

        data_dir = raw_context.get("data_dir", "")
        group_name = watchlist.get("group_name") or watchlist.get("source_tab") or "当前列表"

        summaries: List[WatchlistScanSummary] = []
        for item in visible_items[: cls.MAX_SCAN_ITEMS]:
            summary = cls._summarize_symbol(
                code=item.get("code", ""),
                name=item.get("name", "") or item.get("code", ""),
                asset_type=item.get("asset_type", "股票"),
                data_dir=data_dir,
            )
            if summary:
                summaries.append(summary)

        if not summaries:
            return None

        summary_table = cls._build_summary_table(summaries)
        prompt = (
            f"请对分组“{group_name}”做一轮结构化巡检。\n\n"
            "你必须只基于下面给出的技术摘要进行结论，不要虚构不存在的数据。\n"
            "请严格使用以下输出结构：\n"
            "## 巡检结论\n"
            "- 用3-5条要点总结该分组当前整体状态\n\n"
            "## Top3 关注\n"
            "| 排名 | 代码 | 名称 | 理由 | 关注点 |\n"
            "| --- | --- | --- | --- | --- |\n\n"
            "## Top3 风险\n"
            "| 排名 | 代码 | 名称 | 风险原因 | 应对建议 |\n"
            "| --- | --- | --- | --- | --- |\n\n"
            "## 继续观察清单\n"
            "- 给出3-5条后续观察建议\n\n"
            "要求：\n"
            "- Top3 关注和 Top3 风险必须从提供的标的中选择，不能重复推荐同一只。\n"
            "- 优先结合趋势、量能、位置、MACD/KDJ 状态和均线结构做判断。\n"
            "- 结论要简洁、可执行。\n\n"
            f"### 分组样本摘要（共 {len(summaries)} 只）\n"
            f"{summary_table}"
        )
        user_display = f"请巡检当前分组“{group_name}”，输出结构化结论和 Top3 推荐"
        return {
            "user_display": user_display,
            "prompt": prompt,
        }

    @classmethod
    def _summarize_symbol(
        cls,
        code: str,
        name: str,
        asset_type: str,
        data_dir: str,
    ) -> Optional[WatchlistScanSummary]:
        if not code:
            return None
        df = get_data_portal().get_daily_bars(
            code,
            data_dir=data_dir,
            asset_type="etf" if asset_type == "ETF" else "stock",
            use_cache=True,
        )
        if df is None or df.empty or len(df) < 40:
            return None

        df = df.copy().sort_values("date").reset_index(drop=True)
        for col in ("close", "high", "low", "volume"):
            if col not in df.columns:
                return None
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close", "high", "low"])
        if len(df) < 40:
            return None

        close = df["close"]
        latest_close = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(min(60, len(close))).mean())
        ma120 = float(close.tail(min(120, len(close))).mean())

        pct_20d = cls._period_pct(close, 20)
        pct_60d = cls._period_pct(close, 60)
        pct_120d = cls._period_pct(close, 120)

        vol5 = float(df["volume"].tail(5).mean()) if "volume" in df.columns else 0.0
        vol20 = float(df["volume"].tail(min(20, len(df))).mean()) if "volume" in df.columns else 0.0
        volume_ratio = vol5 / vol20 if vol20 > 0 else 0.0

        high_120 = float(df["high"].tail(min(120, len(df))).max())
        low_120 = float(df["low"].tail(min(120, len(df))).min())
        if high_120 > low_120:
            price_percentile = (latest_close - low_120) / (high_120 - low_120) * 100
        else:
            price_percentile = 50.0

        dif, dea, macd_hist = cls._calc_macd(close)
        k_val, d_val, j_val = cls._calc_kdj(df)

        trend_label = cls._label_trend(latest_close, ma20, ma60, ma120, pct_20d, pct_60d)
        ma_structure = cls._label_ma_structure(latest_close, ma20, ma60, ma120)
        macd_label = cls._label_macd(dif, dea, macd_hist)
        kdj_label = cls._label_kdj(k_val, d_val, j_val)
        risk_label = cls._label_risk(price_percentile, volume_ratio, pct_20d, macd_hist, j_val)

        return WatchlistScanSummary(
            code=code,
            name=name,
            asset_type=asset_type,
            latest_close=latest_close,
            pct_20d=pct_20d,
            pct_60d=pct_60d,
            pct_120d=pct_120d,
            volume_ratio_5_20=volume_ratio,
            price_percentile_120d=price_percentile,
            trend_label=trend_label,
            ma_structure=ma_structure,
            macd_label=macd_label,
            kdj_label=kdj_label,
            risk_label=risk_label,
        )

    @staticmethod
    def _period_pct(close: pd.Series, period: int) -> float:
        if len(close) <= period:
            base = float(close.iloc[0])
        else:
            base = float(close.iloc[-period - 1])
        latest = float(close.iloc[-1])
        return (latest - base) / base * 100 if base > 0 else 0.0

    @staticmethod
    def _calc_macd(close: pd.Series):
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = (dif - dea) * 2
        return float(dif.iloc[-1]), float(dea.iloc[-1]), float(hist.iloc[-1])

    @staticmethod
    def _calc_kdj(df: pd.DataFrame):
        low_n = df["low"].rolling(window=9, min_periods=1).min()
        high_n = df["high"].rolling(window=9, min_periods=1).max()
        rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, pd.NA) * 100
        rsv = rsv.fillna(50.0)
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        return float(k.iloc[-1]), float(d.iloc[-1]), float(j.iloc[-1])

    @staticmethod
    def _label_trend(latest_close, ma20, ma60, ma120, pct_20d, pct_60d):
        if latest_close > ma20 > ma60 and pct_60d > 8:
            return "中期偏强"
        if latest_close > ma20 and pct_20d > 3:
            return "短中期转强"
        if latest_close < ma20 < ma60 and pct_60d < -8:
            return "中期偏弱"
        return "震荡观察"

    @staticmethod
    def _label_ma_structure(latest_close, ma20, ma60, ma120):
        if latest_close > ma20 > ma60 > ma120:
            return "多头排列"
        if latest_close < ma20 < ma60 < ma120:
            return "空头排列"
        if latest_close > ma20 and ma20 > ma60:
            return "短中期向上"
        if latest_close < ma20 and ma20 < ma60:
            return "短中期承压"
        return "均线纠缠"

    @staticmethod
    def _label_macd(dif, dea, hist):
        if dif > dea and hist > 0:
            return "金叉上方"
        if dif < dea and hist < 0:
            return "死叉下方"
        if hist > 0:
            return "红柱延续"
        return "绿柱延续"

    @staticmethod
    def _label_kdj(k, d, j):
        if j >= 100:
            return "高位钝化"
        if j <= 0:
            return "低位钝化"
        if k > d and j > 50:
            return "偏强区"
        if k < d and j < 50:
            return "偏弱区"
        return "震荡区"

    @staticmethod
    def _label_risk(price_percentile, volume_ratio, pct_20d, macd_hist, j_val):
        risks = []
        if price_percentile >= 85:
            risks.append("接近区间高位")
        if volume_ratio < 0.8 and pct_20d > 0:
            risks.append("缩量上涨")
        if macd_hist < 0:
            risks.append("动能转弱")
        if j_val > 95:
            risks.append("短线过热")
        return "、".join(risks[:2]) if risks else "风险中性"

    @staticmethod
    def _build_summary_table(summaries: List[WatchlistScanSummary]) -> str:
        lines = [
            "| 代码 | 名称 | 类型 | 趋势 | 20日% | 60日% | 120日% | 量比5/20 | 120日位置% | 均线结构 | MACD | KDJ | 风险标签 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in summaries:
            lines.append(
                "| {code} | {name} | {asset_type} | {trend} | {pct20:.1f} | {pct60:.1f} | {pct120:.1f} | {vol_ratio:.2f} | {pctile:.1f} | {ma} | {macd} | {kdj} | {risk} |".format(
                    code=item.code,
                    name=item.name,
                    asset_type=item.asset_type,
                    trend=item.trend_label,
                    pct20=item.pct_20d,
                    pct60=item.pct_60d,
                    pct120=item.pct_120d,
                    vol_ratio=item.volume_ratio_5_20,
                    pctile=item.price_percentile_120d,
                    ma=item.ma_structure,
                    macd=item.macd_label,
                    kdj=item.kdj_label,
                    risk=item.risk_label,
                )
            )
        return "\n".join(lines)
