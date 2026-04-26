from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class BrokerOrderRequest:
    """Broker-level order request shared by live and simulation adapters."""

    stock_code: str
    order_type: int
    order_volume: int
    price_type: int = 5
    price: float = 0.0
    strategy_name: str = ""
    remark: str = ""
    request_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stock_code", str(self.stock_code or "").strip().upper())
        object.__setattr__(self, "order_type", int(self.order_type or 0))
        object.__setattr__(self, "order_volume", abs(int(self.order_volume or 0)))
        object.__setattr__(self, "price_type", int(self.price_type or 5))
        object.__setattr__(self, "price", float(self.price or 0.0))
        object.__setattr__(self, "strategy_name", str(self.strategy_name or "").strip())
        object.__setattr__(self, "remark", str(self.remark or "").strip())
        object.__setattr__(self, "request_id", str(self.request_id or "").strip())
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def side(self) -> str:
        return "buy" if self.order_type == 23 else "sell"

    @property
    def signed_quantity(self) -> int:
        return self.order_volume if self.side == "buy" else -self.order_volume


@dataclass(frozen=True)
class BrokerSubmitResult:
    """Normalized submit result returned by any broker implementation."""

    accepted: bool
    broker_order_id: int = -1
    message: str = ""
    status: str = ""
    submitted: bool = False
    filled: bool = False
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerCancelResult:
    """Normalized cancel result returned by any broker implementation."""

    success: bool
    order_id: int
    message: str = ""
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class BrokerProtocol(Protocol):
    """Common broker boundary for live trading and backtest simulation."""

    @property
    def is_connected(self) -> bool:
        ...

    def submit(self, request: BrokerOrderRequest) -> BrokerSubmitResult:
        ...

    def cancel(self, order_id: int) -> BrokerCancelResult:
        ...

    def query_order(self, order_id: int) -> Any:
        ...

    def query_position(self, symbol: str = "") -> Any:
        ...

    def query_asset(self) -> Any:
        ...


class LiveBrokerAdapter:
    """BrokerProtocol adapter over BrokerSessionService."""

    def __init__(self, broker_service: Optional[Any] = None):
        if broker_service is None:
            from common.broker_session_service import get_broker_session_service

            broker_service = get_broker_session_service()
        self.broker_service = broker_service

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.broker_service, "is_connected", False))

    def submit(self, request: BrokerOrderRequest) -> BrokerSubmitResult:
        authorize_order = getattr(self.broker_service, "authorize_order_stock", None)
        if callable(authorize_order):
            with authorize_order("TradeExecutionService", request_id=request.request_id) as authorization_token:
                raw_order_id = self.broker_service.order_stock(
                    stock_code=request.stock_code,
                    order_type=request.order_type,
                    order_volume=request.order_volume,
                    price_type=request.price_type,
                    price=request.price,
                    strategy_name=request.strategy_name,
                    remark=request.remark,
                    _authorization_request_id=request.request_id,
                    _authorization_token=authorization_token,
                )
        else:
            raw_order_id = self.broker_service.order_stock(
                stock_code=request.stock_code,
                order_type=request.order_type,
                order_volume=request.order_volume,
                price_type=request.price_type,
                price=request.price,
                strategy_name=request.strategy_name,
                remark=request.remark,
            )
        broker_order_id = int(raw_order_id) if isinstance(raw_order_id, (int, float)) else -1
        accepted = broker_order_id > 0
        return BrokerSubmitResult(
            accepted=accepted,
            broker_order_id=broker_order_id,
            message="委托已提交" if accepted else "券商未返回有效委托号",
            status="submitted" if accepted else "failed",
            submitted=accepted,
            raw=raw_order_id,
        )

    def cancel(self, order_id: int) -> BrokerCancelResult:
        normalized_order_id = int(order_id or 0)
        cancel_method = getattr(self.broker_service, "cancel_order_stock", None)
        if not callable(cancel_method):
            return BrokerCancelResult(False, normalized_order_id, "当前券商适配器不支持撤单")
        raw = cancel_method(normalized_order_id)
        success = raw is None or raw is True or raw == 0
        return BrokerCancelResult(
            success=bool(success),
            order_id=normalized_order_id,
            message="撤单请求已提交" if success else f"撤单失败: {raw}",
            raw=raw,
        )

    def query_order(self, order_id: int) -> Any:
        return self.broker_service.query_stock_order(int(order_id or 0))

    def query_position(self, symbol: str = "") -> Any:
        positions = list(self.broker_service.query_stock_positions() or [])
        plain_symbol = self._plain_code(symbol)
        if not plain_symbol:
            return positions
        for position in positions:
            if self._plain_code(getattr(position, "stock_code", "") or "") == plain_symbol:
                return position
        return None

    def query_asset(self) -> Any:
        return self.broker_service.query_stock_asset()

    @staticmethod
    def _plain_code(code: str) -> str:
        value = str(code or "").strip().upper()
        return value.split(".")[0] if "." in value else value
