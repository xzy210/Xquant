from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict

import pandas as pd

try:
    from strategy_app.factors.financial_data import get_financial_data_loader
except ImportError:
    get_financial_data_loader = None


logger = logging.getLogger(__name__)


@dataclass
class FundamentalSnapshotResult:
    code: str
    name: str
    summary: str = ""
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class StockFundamentalService:
    """Build fundamental evidence from cached or freshly downloaded financial data."""

    def build_snapshot(
        self,
        *,
        code: str,
        name: str = "",
        raw_context: Dict[str, Any] | None = None,
    ) -> FundamentalSnapshotResult:
        code = (code or "").strip()
        name = (name or code).strip()
        raw_context = raw_context or {}
        if not code:
            return FundamentalSnapshotResult(
                code="",
                name=name,
                summary="当前没有选中的标的",
                content="- 当前上下文缺少标的代码，无法生成基本面摘要。",
            )

        token = self._resolve_tushare_token(raw_context)
        loader = self._build_loader(token)
        if loader is None:
            return FundamentalSnapshotResult(
                code=code,
                name=name,
                summary="基本面服务未就绪",
                content=(
                    "- 当前环境无法初始化基本面数据服务。\n"
                    "- 需要安装 `tushare`，并确保 `strategy_app.factors.financial_data` 可用。"
                ),
                metadata={"provider": "tushare", "available": False},
            )

        daily_basic_df = self._load_or_download_daily_basic(loader, code)
        fina_df = self._load_or_download_fina_indicator(loader, code)
        if (daily_basic_df is None or daily_basic_df.empty) and (fina_df is None or fina_df.empty):
            reasons = ["- 暂未获取到可用的基本面数据。"]
            if not token:
                reasons.append("- 原因: 当前未配置 Tushare token。")
            reasons.append("- 建议: 在调度/更新配置中填写 Tushare token，并预先缓存财务数据。")
            return FundamentalSnapshotResult(
                code=code,
                name=name,
                summary="未获取到基本面数据",
                content="\n".join(reasons),
                metadata={"provider": "tushare", "available": False},
            )

        latest_basic = self._latest_row(daily_basic_df, "trade_date")
        latest_fina = self._latest_row(fina_df, "end_date")
        summary, bias = self._build_summary(latest_basic, latest_fina)
        content = self._build_content(code, name, latest_basic, latest_fina, bias)
        metadata = {
            "provider": "tushare",
            "available": True,
            "trade_date": str(latest_basic.get("trade_date", "")) if latest_basic is not None else "",
            "report_period": str(latest_fina.get("end_date", "")) if latest_fina is not None else "",
            "bias": bias,
        }
        return FundamentalSnapshotResult(
            code=code,
            name=name,
            summary=summary,
            content=content,
            metadata=metadata,
        )

    def _build_loader(self, token: str):
        if get_financial_data_loader is None:
            return None
        try:
            return get_financial_data_loader(tushare_token=token or None)
        except Exception as exc:
            logger.warning("Failed to build financial loader: %s", exc)
            return None

    @staticmethod
    def _resolve_tushare_token(raw_context: Dict[str, Any]) -> str:
        market_data = raw_context.get("market_data", {}) or {}
        token = market_data.get("tushare_token", "")
        return str(token or os.environ.get("TUSHARE_TOKEN", "")).strip()

    @staticmethod
    def _load_or_download_daily_basic(loader, code: str) -> pd.DataFrame | None:
        try:
            df = loader.load_daily_basic(code)
            if df is not None and not df.empty:
                return df
            return loader.download_daily_basic(code)
        except Exception as exc:
            logger.warning("Failed to load daily_basic for %s: %s", code, exc)
            return None

    @staticmethod
    def _load_or_download_fina_indicator(loader, code: str) -> pd.DataFrame | None:
        try:
            df = loader.load_fina_indicator(code)
            if df is not None and not df.empty:
                return df
            return loader.download_fina_indicator(code)
        except Exception as exc:
            logger.warning("Failed to load fina_indicator for %s: %s", code, exc)
            return None

    @staticmethod
    def _latest_row(df: pd.DataFrame | None, date_col: str) -> pd.Series | None:
        if df is None or df.empty:
            return None
        frame = df.copy()
        if date_col in frame.columns:
            frame[date_col] = frame[date_col].astype(str)
            frame = frame.sort_values(date_col, ascending=False)
        return frame.iloc[0]

    def _build_summary(self, latest_basic: pd.Series | None, latest_fina: pd.Series | None) -> tuple[str, str]:
        score = 0
        evidence = []

        roe = self._to_float(latest_fina, "roe")
        if roe is not None:
            if roe >= 12:
                score += 1
                evidence.append(f"ROE {roe:.1f}%")
            elif roe < 8:
                score -= 1
                evidence.append(f"ROE {roe:.1f}%")

        netprofit_yoy = self._to_float(latest_fina, "netprofit_yoy")
        if netprofit_yoy is not None:
            if netprofit_yoy > 10:
                score += 1
                evidence.append(f"净利润同比 {netprofit_yoy:.1f}%")
            elif netprofit_yoy < -10:
                score -= 1
                evidence.append(f"净利润同比 {netprofit_yoy:.1f}%")

        debt_to_assets = self._to_float(latest_fina, "debt_to_assets")
        if debt_to_assets is not None:
            if debt_to_assets > 70:
                score -= 1
                evidence.append(f"资产负债率 {debt_to_assets:.1f}%")
            else:
                evidence.append(f"资产负债率 {debt_to_assets:.1f}%")

        pe_ttm = self._to_float(latest_basic, "pe_ttm")
        pb = self._to_float(latest_basic, "pb")
        if pe_ttm is not None:
            evidence.append(f"PE(TTM) {pe_ttm:.1f}")
        if pb is not None:
            evidence.append(f"PB {pb:.2f}")

        if score >= 2:
            bias = "基本面偏强"
        elif score <= -2:
            bias = "基本面承压"
        else:
            bias = "基本面中性"
        evidence_text = "，".join(evidence[:4]) if evidence else "关键财务指标不完整"
        return f"{bias}，{evidence_text}", bias

    def _build_content(
        self,
        code: str,
        name: str,
        latest_basic: pd.Series | None,
        latest_fina: pd.Series | None,
        bias: str,
    ) -> str:
        basic_date = str(latest_basic.get("trade_date", "")) if latest_basic is not None else "-"
        fina_date = str(latest_fina.get("end_date", "")) if latest_fina is not None else "-"
        ann_date = str(latest_fina.get("ann_date", "")) if latest_fina is not None else "-"

        sections = [
            f"- 标的: {name}({code})",
            f"- 基本面结论: {bias}",
            f"- 日频估值日期: {basic_date or '-'}",
            f"- 最近财报期: {fina_date or '-'} / 公告日: {ann_date or '-'}",
            "",
            "## 关键估值指标",
            self._format_metric_line("PE(TTM)", self._to_float(latest_basic, "pe_ttm")),
            self._format_metric_line("PB", self._to_float(latest_basic, "pb")),
            self._format_metric_line("股息率", self._to_float(latest_basic, "dv_ttm"), suffix="%"),
            self._format_metric_line("总市值", self._to_float(latest_basic, "total_mv"), suffix="万元"),
            "",
            "## 关键财务指标",
            self._format_metric_line("ROE", self._to_float(latest_fina, "roe"), suffix="%"),
            self._format_metric_line("ROA", self._to_float(latest_fina, "roa"), suffix="%"),
            self._format_metric_line("毛利率", self._to_float(latest_fina, "gross_margin"), suffix="%"),
            self._format_metric_line("净利润同比", self._to_float(latest_fina, "netprofit_yoy"), suffix="%"),
            self._format_metric_line("营收同比", self._to_float(latest_fina, "tr_yoy"), suffix="%"),
            self._format_metric_line("经营现金流同比", self._to_float(latest_fina, "ocf_yoy"), suffix="%"),
            self._format_metric_line("资产负债率", self._to_float(latest_fina, "debt_to_assets"), suffix="%"),
            self._format_metric_line("流动比率", self._to_float(latest_fina, "current_ratio")),
            "",
            "## 使用建议",
            "- 请结合技术面判断当前估值与盈利趋势是否共振。",
            "- 若财务指标缺失或财报日期过旧，回答中请明确提示“基本面数据时效不足”。",
        ]
        return "\n".join(sections)

    @staticmethod
    def _format_metric_line(label: str, value: float | None, suffix: str = "") -> str:
        if value is None or pd.isna(value):
            return f"- {label}: 待补充数据"
        if abs(value) >= 1000 and suffix != "%":
            return f"- {label}: {value:,.2f}{suffix}"
        return f"- {label}: {value:.2f}{suffix}"

    @staticmethod
    def _to_float(row: pd.Series | None, column: str) -> float | None:
        if row is None or column not in row.index:
            return None
        value = row.get(column)
        try:
            if pd.isna(value):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
