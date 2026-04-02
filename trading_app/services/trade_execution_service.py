from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from typing import Any, Dict, Optional
from uuid import uuid4

from common.broker_session_service import BrokerSessionService, get_broker_session_service
from live_rotation.holiday_calendar import get_non_trading_reason, is_trading_day

from .agent_context_service import BrokerContext
from .auto_trade_config_service import get_auto_trade_config_service
from .risk_guard_service import RiskGuardService
from .trade_decision_models import RiskCheckResult, TradeAction, TradeDecision
from .trade_record_service import TradeDirection, TradeSource, get_trade_record_service

logger = logging.getLogger(__name__)

ORDER_STATUS_LABELS = {
    48: "未报",
    49: "待报",
    50: "已报",
    51: "已报待撤",
    52: "部成待撤",
    53: "部撤",
    54: "已撤",
    55: "部成",
    56: "已成",
    57: "废单",
}
FINAL_REJECTED_STATUSES = {53, 54, 57}
FILLED_STATUSES = {55, 56}


@dataclass
class ExecutionRequest:
    stock_code: str
    stock_name: str
    order_type: int
    order_volume: int
    price_type: int
    price: float
    source: str
    trigger: str
    strategy_name: str = ""
    remark: str = ""
    decision: Optional[TradeDecision] = None
    risk_result: Optional[RiskCheckResult] = None
    decision_record_id: str = ""
    require_approval: bool = False
    approved: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    success: bool
    message: str
    broker_order_id: int = -1
    request_id: str = ""
    execution_mode: str = "live"
    order_status: str = ""
    live_submitted: bool = False
    shadow: bool = False
    blocked: bool = False
    trade_record_id: int = 0
    order_record_id: int = 0


class TradeExecutionService:
    """Unified execution gateway for all real orders."""

    def __init__(self, broker_service: Optional[BrokerSessionService] = None):
        self.broker_service = broker_service or get_broker_session_service()
        self.trade_service = get_trade_record_service()
        self.risk_guard = RiskGuardService()
        self.config_service = get_auto_trade_config_service()
        self._recent_fingerprints: dict[str, float] = {}

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        cfg = self.config_service.get_config()
        request_id = uuid4().hex[:16]
        request = self._normalize_request(request)
        mode = self._resolve_execution_mode(request, cfg.auto_trade_mode, cfg.manual_orders_enabled)
        fingerprint = self._build_fingerprint(request)
        validation_error = self._validate_request(request, cfg, fingerprint)
        order_record = self.trade_service.add_order_record(
            request_id=request_id,
            stock_code=request.stock_code,
            stock_name=request.stock_name,
            direction=self._direction_from_order_type(request.order_type),
            order_volume=request.order_volume,
            price=request.price,
            price_type=request.price_type,
            source=request.source,
            trigger=request.trigger,
            strategy_name=request.strategy_name,
            execution_mode=mode,
            status="validating",
            broker_order_id=-1,
            fingerprint=fingerprint,
            decision_record_id=request.decision_record_id,
            remark=request.remark,
            validation_message=validation_error or "开始执行校验",
        )

        if validation_error:
            self.trade_service.update_order_record(
                request_id,
                status="blocked",
                validation_message=validation_error,
            )
            return ExecutionResult(
                success=False,
                blocked=True,
                message=validation_error,
                request_id=request_id,
                execution_mode=mode,
                order_record_id=getattr(order_record, "id", 0),
            )

        if mode in {"shadow", "paper"}:
            message = f"影子模式记录成功: {request.stock_name or request.stock_code} {request.order_volume}股"
            self.trade_service.update_order_record(
                request_id,
                status="shadow",
                validation_message=message,
            )
            self._remember_fingerprint(fingerprint)
            return ExecutionResult(
                success=True,
                shadow=True,
                message=message,
                request_id=request_id,
                execution_mode=mode,
                order_status="shadow",
                order_record_id=getattr(order_record, "id", 0),
            )

        try:
            broker_order_id = self.broker_service.order_stock(
                stock_code=request.stock_code,
                order_type=request.order_type,
                order_volume=request.order_volume,
                price_type=request.price_type,
                price=request.price,
                strategy_name=request.strategy_name,
                remark=request.remark,
            )
        except Exception as exc:
            message = f"下单异常: {exc}"
            self.trade_service.update_order_record(
                request_id,
                status="failed",
                validation_message=message,
            )
            return ExecutionResult(
                success=False,
                message=message,
                request_id=request_id,
                execution_mode=mode,
                order_record_id=getattr(order_record, "id", 0),
            )

        broker_order_id = int(broker_order_id) if isinstance(broker_order_id, (int, float)) else -1
        if broker_order_id <= 0:
            message = "券商未返回有效委托号"
            self.trade_service.update_order_record(
                request_id,
                status="failed",
                validation_message=message,
            )
            return ExecutionResult(
                success=False,
                message=message,
                request_id=request_id,
                execution_mode=mode,
                order_record_id=getattr(order_record, "id", 0),
            )

        self.trade_service.update_order_record(
            request_id,
            broker_order_id=broker_order_id,
            status="submitted",
            validation_message="委托已提交",
        )
        self._remember_fingerprint(fingerprint)

        return self._poll_order_status(request_id, broker_order_id, request, mode, getattr(order_record, "id", 0))

    def execute_agent_decision(
        self,
        decision: TradeDecision,
        *,
        stock_name: str = "",
        decision_record_id: str = "",
        risk_result: Optional[RiskCheckResult] = None,
    ) -> ExecutionResult:
        action = decision.action
        if action in (TradeAction.BUY.value, TradeAction.ADD.value):
            order_type = 23
            volume = self._calc_buy_volume(decision)
        elif action in (TradeAction.SELL.value, TradeAction.REDUCE.value):
            order_type = 24
            volume = self._calc_sell_volume(decision)
        else:
            return ExecutionResult(False, f"不可执行的操作类型: {action}")

        if volume <= 0:
            return ExecutionResult(False, "计算委托数量为0")

        return self.execute(
            ExecutionRequest(
                stock_code=decision.symbol_code,
                stock_name=stock_name or decision.symbol_name,
                order_type=order_type,
                order_volume=volume,
                price_type=5,
                price=float(decision.current_price or 0),
                source=TradeSource.AI_AGENT.value,
                trigger="manual",
                strategy_name="AI_Agent",
                remark=f"AI决策: {decision.reasoning[:50]}" if decision.reasoning else "AI智能体决策",
                decision=decision,
                risk_result=risk_result,
                decision_record_id=decision_record_id,
                require_approval=True,
                approved=True,
            )
        )

    def estimate_volume_for_decision(self, decision: TradeDecision) -> int:
        action = decision.action
        if action in (TradeAction.BUY.value, TradeAction.ADD.value):
            return self._calc_buy_volume(decision)
        if action in (TradeAction.SELL.value, TradeAction.REDUCE.value):
            return self._calc_sell_volume(decision)
        return 0

    def execute_conditional_order(
        self,
        *,
        stock_code: str,
        stock_name: str,
        order_type: int,
        order_volume: int,
        price_type: int,
        price: float,
        strategy_name: str = "ConditionalOrder",
        remark: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        return self.execute(
            ExecutionRequest(
                stock_code=stock_code,
                stock_name=stock_name,
                order_type=order_type,
                order_volume=order_volume,
                price_type=price_type,
                price=price,
                source=TradeSource.CONDITIONAL.value,
                trigger="auto",
                strategy_name=strategy_name,
                remark=remark or "条件单自动执行",
                metadata=dict(metadata or {}),
            )
        )

    def _normalize_request(self, request: ExecutionRequest) -> ExecutionRequest:
        code = (request.stock_code or "").strip().upper()
        if "." not in code and len(code) == 6:
            if code.startswith(("5", "6", "9")):
                code = f"{code}.SH"
            else:
                code = f"{code}.SZ"
        request.stock_code = code
        request.stock_name = (request.stock_name or code).strip()
        request.price = float(request.price or 0)
        request.order_volume = int(request.order_volume or 0)
        request.remark = (request.remark or "").strip()
        return request

    def _resolve_execution_mode(self, request: ExecutionRequest, auto_mode: str, manual_enabled: bool) -> str:
        if request.trigger == "manual":
            return "live" if manual_enabled else "off"
        return auto_mode

    def _validate_request(self, request: ExecutionRequest, cfg, fingerprint: str) -> str:
        if request.trigger == "manual" and not cfg.manual_orders_enabled:
            return "当前配置禁止手动下单"
        if request.trigger != "manual" and cfg.auto_trade_mode == "off":
            return "自动交易未开启"
        if not self.broker_service.is_connected:
            return "券商未连接"
        if request.require_approval and not request.approved:
            return "该决策尚未批准，不能执行"
        if request.order_volume <= 0 or request.order_volume % 100 != 0:
            return "委托数量必须为100股的整数倍"
        if request.order_type not in (23, 24):
            return f"不支持的委托方向: {request.order_type}"
        if request.price_type in (0, 5) and request.price <= 0:
            return "委托价格必须大于0"
        if cfg.require_trading_time:
            time_error = self._validate_trading_time()
            if time_error:
                return time_error
        duplicate_error = self._check_duplicate(fingerprint, cfg.duplicate_window_seconds)
        if duplicate_error:
            return duplicate_error
        if request.risk_result is not None and not request.risk_result.passed:
            return "风控未通过，禁止执行"
        decision_error = self._validate_decision_risk(request)
        if decision_error:
            return decision_error
        broker_error = self._validate_broker_constraints(request)
        if broker_error:
            return broker_error
        return ""

    def _validate_trading_time(self) -> str:
        now = datetime.now()
        if not is_trading_day(now.date()):
            return get_non_trading_reason(now.date()) or "当前不是交易日"
        current = now.time()
        morning_start = dt_time(9, 30)
        morning_end = dt_time(11, 30)
        afternoon_start = dt_time(13, 0)
        afternoon_end = dt_time(15, 0)
        if morning_start <= current <= morning_end or afternoon_start <= current <= afternoon_end:
            return ""
        return "当前不在交易时段"

    def _validate_decision_risk(self, request: ExecutionRequest) -> str:
        if request.decision is None:
            return ""
        broker_ctx = self._build_broker_context()
        risk_result = self.risk_guard.evaluate(request.decision, broker=broker_ctx)
        if not risk_result.passed:
            return "风控未通过，禁止执行"
        return ""

    def _validate_broker_constraints(self, request: ExecutionRequest) -> str:
        try:
            plain_code = self._plain_code(request.stock_code)
            if request.order_type == 23:
                asset = self.broker_service.query_stock_asset()
                available_cash = float(getattr(asset, "cash", 0) or getattr(asset, "available_cash", 0) or 0)
                total_asset = float(getattr(asset, "total_asset", 0) or 0)
                estimated_price = request.price if request.price > 0 else 0
                required_cash = estimated_price * request.order_volume
                if required_cash <= 0:
                    return "无法计算有效委托金额"
                if available_cash + 1e-6 < required_cash:
                    return f"可用资金不足，需 {required_cash:,.2f}，可用 {available_cash:,.2f}"

                if total_asset > 0:
                    single_limit = self.risk_guard.config.get("max_single_position_pct", 0.30)
                    total_limit = self.risk_guard.config.get("max_total_position_pct", 0.90)
                    projected_single = required_cash / total_asset
                    projected_total = (total_asset - available_cash + required_cash) / total_asset
                    if projected_single > single_limit + 1e-6:
                        return f"单笔仓位 {projected_single:.0%} 超过上限 {single_limit:.0%}"
                    if projected_total > total_limit + 1e-6:
                        return f"总仓位 {projected_total:.0%} 超过上限 {total_limit:.0%}"
            else:
                positions = self.broker_service.query_stock_positions() or []
                can_use = 0
                for pos in positions:
                    pos_code = self._plain_code(getattr(pos, "stock_code", "") or "")
                    if pos_code == plain_code:
                        can_use = int(getattr(pos, "can_use_volume", 0) or 0)
                        break
                if can_use < request.order_volume:
                    return f"可卖数量不足，需 {request.order_volume}，可卖 {can_use}"
        except Exception as exc:
            return f"交易前校验失败: {exc}"
        return ""

    def _build_broker_context(self) -> BrokerContext:
        try:
            assets = self.broker_service.query_stock_asset()
            positions = self.broker_service.query_stock_positions() or []
            return BrokerContext(
                connected=True,
                total_asset=float(getattr(assets, "total_asset", 0) or 0),
                available_cash=float(getattr(assets, "cash", 0) or getattr(assets, "available_cash", 0) or 0),
                position_count=len(positions),
                top_positions=[],
            )
        except Exception:
            return BrokerContext(connected=self.broker_service.is_connected)

    def _check_duplicate(self, fingerprint: str, window_seconds: int) -> str:
        now = time.time()
        expired = [key for key, ts in self._recent_fingerprints.items() if now - ts > window_seconds]
        for key in expired:
            self._recent_fingerprints.pop(key, None)
        if fingerprint in self._recent_fingerprints:
            return "短时间内检测到重复委托，请稍后再试"
        recent = self.trade_service.find_recent_order_record(fingerprint, within_seconds=window_seconds)
        if recent:
            return "近期已有相同委托记录，已拦截重复报单"
        return ""

    def _remember_fingerprint(self, fingerprint: str) -> None:
        self._recent_fingerprints[fingerprint] = time.time()

    def _build_fingerprint(self, request: ExecutionRequest) -> str:
        price = round(float(request.price or 0), 3)
        direction = self._direction_from_order_type(request.order_type)
        return "|".join([
            request.trigger,
            request.source,
            self._plain_code(request.stock_code),
            direction,
            str(int(request.order_volume)),
            str(int(request.price_type)),
            f"{price:.3f}",
        ])

    def _poll_order_status(
        self,
        request_id: str,
        broker_order_id: int,
        request: ExecutionRequest,
        mode: str,
        order_record_id: int,
    ) -> ExecutionResult:
        cfg = self.config_service.get_config()
        deadline = time.time() + cfg.status_poll_seconds
        latest_status = "submitted"
        latest_message = f"已提交委托，单号 {broker_order_id}"
        trade_record_id = 0

        while time.time() < deadline:
            try:
                order = self.broker_service.query_stock_order(broker_order_id)
            except Exception:
                order = None

            if order is not None:
                status_code = int(getattr(order, "order_status", 0) or 0)
                latest_status = ORDER_STATUS_LABELS.get(status_code, str(status_code))
                traded_volume = int(getattr(order, "traded_volume", 0) or 0)
                traded_price = float(getattr(order, "traded_price", 0) or 0)
                status_msg = str(getattr(order, "status_msg", "") or "")

                if status_code in FILLED_STATUSES or traded_volume > 0:
                    self.trade_service.sync_from_orders(
                        [order],
                        source=request.source,
                        name_map={self._plain_code(request.stock_code): request.stock_name},
                    )
                    trade = self.trade_service.get_latest_record_by_broker_order_id(broker_order_id)
                    trade_record_id = getattr(trade, "id", 0) if trade else 0
                    latest_message = f"委托已成交，单号 {broker_order_id}"
                    self.trade_service.update_order_record(
                        request_id,
                        status="filled" if status_code == 56 else "partial_fill",
                        validation_message=latest_message,
                        order_status_code=status_code,
                        order_status_text=latest_status,
                        executed_price=traded_price,
                        executed_volume=traded_volume,
                        linked_trade_record_id=trade_record_id,
                    )
                    return ExecutionResult(
                        success=True,
                        message=latest_message,
                        broker_order_id=broker_order_id,
                        request_id=request_id,
                        execution_mode=mode,
                        order_status=latest_status,
                        live_submitted=True,
                        trade_record_id=trade_record_id,
                        order_record_id=order_record_id,
                    )

                if status_code in FINAL_REJECTED_STATUSES:
                    latest_message = status_msg or f"委托状态: {latest_status}"
                    self.trade_service.update_order_record(
                        request_id,
                        status="rejected",
                        validation_message=latest_message,
                        order_status_code=status_code,
                        order_status_text=latest_status,
                    )
                    return ExecutionResult(
                        success=False,
                        message=latest_message,
                        broker_order_id=broker_order_id,
                        request_id=request_id,
                        execution_mode=mode,
                        order_status=latest_status,
                        live_submitted=False,
                        order_record_id=order_record_id,
                    )

                latest_message = status_msg or f"委托状态: {latest_status}"
                self.trade_service.update_order_record(
                    request_id,
                    status="submitted",
                    validation_message=latest_message,
                    order_status_code=status_code,
                    order_status_text=latest_status,
                    executed_price=traded_price,
                    executed_volume=traded_volume,
                )

            time.sleep(cfg.status_poll_interval_seconds)

        return ExecutionResult(
            success=True,
            message=latest_message,
            broker_order_id=broker_order_id,
            request_id=request_id,
            execution_mode=mode,
            order_status=latest_status,
            live_submitted=True,
            order_record_id=order_record_id,
        )

    def _calc_buy_volume(self, decision: TradeDecision) -> int:
        try:
            assets = self.broker_service.query_stock_asset()
            available = float(getattr(assets, "cash", 0) or getattr(assets, "available_cash", 0) or 0)
            if available <= 0 or decision.current_price <= 0:
                return 0
            target_amount = available * max(float(decision.position_pct or 0), 0.0)
            return max(int(math.floor(target_amount / decision.current_price / 100)) * 100, 0)
        except Exception:
            return 0

    def _calc_sell_volume(self, decision: TradeDecision) -> int:
        try:
            plain_code = self._plain_code(decision.symbol_code)
            positions = self.broker_service.query_stock_positions() or []
            for pos in positions:
                pos_code = self._plain_code(getattr(pos, "stock_code", "") or "")
                if pos_code != plain_code:
                    continue
                can_sell = int(getattr(pos, "can_use_volume", 0) or 0)
                if decision.action == TradeAction.REDUCE.value:
                    return max(int(math.floor(can_sell * 0.5 / 100)) * 100, 0)
                return can_sell
            return 0
        except Exception:
            return 0

    @staticmethod
    def _direction_from_order_type(order_type: int) -> str:
        return TradeDirection.BUY.value if int(order_type) == 23 else TradeDirection.SELL.value

    @staticmethod
    def _plain_code(code: str) -> str:
        value = (code or "").strip().upper()
        return value.split(".")[0] if "." in value else value


_trade_execution_service: Optional[TradeExecutionService] = None


def get_trade_execution_service() -> TradeExecutionService:
    global _trade_execution_service
    if _trade_execution_service is None:
        _trade_execution_service = TradeExecutionService()
    return _trade_execution_service
