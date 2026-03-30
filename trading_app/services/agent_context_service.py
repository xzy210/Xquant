from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


TASK_MODE_GENERAL = "general"
TASK_MODE_SYMBOL_ANALYSIS = "symbol_analysis"
TASK_MODE_WATCHLIST_SCAN = "watchlist_scan"
TASK_MODE_POSITION_DIAGNOSIS = "position_diagnosis"
TASK_MODE_TRADE_DECISION = "trade_decision"


TASK_MODE_LABELS = {
    TASK_MODE_GENERAL: "通用聊天",
    TASK_MODE_SYMBOL_ANALYSIS: "当前标的分析",
    TASK_MODE_WATCHLIST_SCAN: "自选组巡检",
    TASK_MODE_POSITION_DIAGNOSIS: "持仓诊断",
    TASK_MODE_TRADE_DECISION: "交易决策",
}


@dataclass
class SymbolContext:
    code: str = ""
    name: str = ""
    asset_type: str = ""
    current_view: str = ""
    latest_close: float = 0.0
    latest_change_pct: float = 0.0
    latest_volume: float = 0.0
    data_points: int = 0
    date_start: str = ""
    date_end: str = ""
    indicators: List[str] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return bool(self.code)


@dataclass
class WatchlistContext:
    source_tab: str = ""
    group_name: str = ""
    visible_count: int = 0
    visible_codes: List[str] = field(default_factory=list)


@dataclass
class BrokerContext:
    connected: bool = False
    account_id: str = ""
    total_asset: float = 0.0
    available_cash: float = 0.0
    position_count: int = 0
    top_positions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentRuntimeContext:
    symbol: SymbolContext = field(default_factory=SymbolContext)
    watchlist: WatchlistContext = field(default_factory=WatchlistContext)
    broker: BrokerContext = field(default_factory=BrokerContext)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_summary_lines(self) -> List[str]:
        lines: List[str] = []
        if self.symbol.is_available:
            lines.append(
                f"当前标的: {self.symbol.name or '-'}({self.symbol.code}) / "
                f"{self.symbol.asset_type or self.symbol.current_view or '-'}"
            )
            if self.symbol.data_points > 0:
                lines.append(
                    f"K线数据: {self.symbol.data_points} 条, "
                    f"区间 {self.symbol.date_start or '-'} ~ {self.symbol.date_end or '-'}"
                )
            if self.symbol.latest_close > 0:
                lines.append(
                    f"最新收盘: {self.symbol.latest_close:.3f}, "
                    f"涨跌幅 {self.symbol.latest_change_pct:.2f}%"
                )
            if self.symbol.indicators:
                lines.append(f"图表指标: {', '.join(self.symbol.indicators)}")
        else:
            lines.append("当前标的: 未选择")

        if self.watchlist.group_name:
            preview = ", ".join(self.watchlist.visible_codes[:6])
            suffix = "..." if self.watchlist.visible_count > 6 else ""
            lines.append(
                f"当前分组: {self.watchlist.group_name}，共 {self.watchlist.visible_count} 只，"
                f"示例: {preview}{suffix}"
            )

        if self.broker.connected:
            lines.append(
                f"账户状态: 已连接，资金 {self.broker.available_cash:,.0f} / "
                f"总资产 {self.broker.total_asset:,.0f} / 持仓 {self.broker.position_count} 只"
            )
        else:
            lines.append("账户状态: 未连接")

        market_raw = self.raw.get("market_snapshot", {})
        if market_raw:
            sentiment = market_raw.get("sentiment", "")
            summary = market_raw.get("summary", "")
            if sentiment or summary:
                lines.append(f"大盘情绪: {sentiment or '-'}  {summary or ''}")

        return lines


class AgentContextService:
    """Normalize MainWindow runtime state for AI tasks."""

    @staticmethod
    def from_raw(raw_context: Optional[Dict[str, Any]]) -> AgentRuntimeContext:
        raw_context = raw_context or {}
        symbol_raw = raw_context.get("symbol", {}) or {}
        watchlist_raw = raw_context.get("watchlist", {}) or {}
        broker_raw = raw_context.get("broker", {}) or {}

        return AgentRuntimeContext(
            symbol=SymbolContext(
                code=symbol_raw.get("code", ""),
                name=symbol_raw.get("name", ""),
                asset_type=symbol_raw.get("asset_type", ""),
                current_view=symbol_raw.get("current_view", ""),
                latest_close=float(symbol_raw.get("latest_close", 0.0) or 0.0),
                latest_change_pct=float(symbol_raw.get("latest_change_pct", 0.0) or 0.0),
                latest_volume=float(symbol_raw.get("latest_volume", 0.0) or 0.0),
                data_points=int(symbol_raw.get("data_points", 0) or 0),
                date_start=symbol_raw.get("date_start", ""),
                date_end=symbol_raw.get("date_end", ""),
                indicators=list(symbol_raw.get("indicators", []) or []),
            ),
            watchlist=WatchlistContext(
                source_tab=watchlist_raw.get("source_tab", ""),
                group_name=watchlist_raw.get("group_name", ""),
                visible_count=int(watchlist_raw.get("visible_count", 0) or 0),
                visible_codes=list(watchlist_raw.get("visible_codes", []) or []),
            ),
            broker=BrokerContext(
                connected=bool(broker_raw.get("connected", False)),
                account_id=broker_raw.get("account_id", ""),
                total_asset=float(broker_raw.get("total_asset", 0.0) or 0.0),
                available_cash=float(broker_raw.get("available_cash", 0.0) or 0.0),
                position_count=int(broker_raw.get("position_count", 0) or 0),
                top_positions=list(broker_raw.get("top_positions", []) or []),
            ),
            raw=raw_context,
        )
