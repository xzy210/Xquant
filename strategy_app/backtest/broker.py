from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd

try:
    from trading_app.services.trade_record_service import TradeRecordService
except ImportError:  # pragma: no cover - standalone strategy_app execution fallback
    TradeRecordService = None


@dataclass
class OrderMatcherConfig:
    """Configurable market microstructure assumptions for backtest matching."""
    slippage_pct: float = 0.0
    volume_limit_pct: float = 1.0
    min_lot: int = 100
    allow_partial_fill: bool = True
    enforce_volume_limit: bool = True
    enforce_bar_price_range: bool = True
    enforce_price_limit: bool = True
    clip_price_to_bar: bool = True
    price_limit_tolerance: float = 0.001


@dataclass
class FeeModelConfig:
    """Optional commission override while preserving live stamp/transfer fee rules."""
    buy_commission_rate: Optional[float] = None
    sell_commission_rate: Optional[float] = None
    min_commission: Optional[float] = None

    @property
    def has_commission_override(self) -> bool:
        return (
            self.buy_commission_rate is not None
            or self.sell_commission_rate is not None
            or self.min_commission is not None
        )


@dataclass
class MatchResult:
    symbol: str
    direction: str
    requested_quantity: int
    filled_quantity: int
    requested_price: float
    fill_price: float
    blocked_reason: str = ""
    partial: bool = False
    slippage_amount: float = 0.0
    max_volume_quantity: Optional[int] = None

    @property
    def matched(self) -> bool:
        return self.filled_quantity > 0 and not self.blocked_reason

    @property
    def amount(self) -> float:
        return self.filled_quantity * self.fill_price


class OrderMatcher:
    """Daily-bar order matcher with slippage, volume and A-share price-limit rules."""

    def __init__(self, config: Optional[OrderMatcherConfig] = None):
        self.config = config or OrderMatcherConfig()

    def match(self, *, symbol: str, quantity: int, price: float, bar: object = None) -> MatchResult:
        direction = "buy" if quantity > 0 else "sell"
        requested_quantity = self._round_quantity(abs(int(quantity or 0)))
        requested_price = float(price or 0.0)

        if requested_quantity <= 0:
            return self._blocked(symbol, direction, requested_quantity, requested_price, "委托数量不足最小交易单位")
        if requested_price <= 0:
            return self._blocked(symbol, direction, requested_quantity, requested_price, "委托价格无效")

        if bar is None:
            fill_price = self._apply_slippage(requested_price, direction)
            return MatchResult(symbol, direction, requested_quantity, requested_quantity, requested_price, fill_price)

        volume_check = self._match_volume(requested_quantity, bar)
        if volume_check[1]:
            return self._blocked(symbol, direction, requested_quantity, requested_price, volume_check[1], max_volume_quantity=volume_check[0])
        filled_quantity = volume_check[0]

        price_check = self._match_price(symbol, direction, requested_price, bar)
        if price_check[1]:
            return self._blocked(symbol, direction, requested_quantity, requested_price, price_check[1], max_volume_quantity=filled_quantity)

        fill_price = price_check[0]
        return MatchResult(
            symbol=symbol,
            direction=direction,
            requested_quantity=requested_quantity,
            filled_quantity=filled_quantity,
            requested_price=requested_price,
            fill_price=fill_price,
            partial=filled_quantity < requested_quantity,
            slippage_amount=fill_price - requested_price,
            max_volume_quantity=filled_quantity,
        )

    def _match_volume(self, requested_quantity: int, bar: object) -> Tuple[int, str]:
        volume = self._bar_value(bar, "volume", "vol")
        if volume is not None and float(volume or 0.0) <= 0:
            return 0, "停牌或无成交量，无法成交"

        if not self.config.enforce_volume_limit or volume is None:
            return requested_quantity, ""

        max_quantity = int(float(volume or 0.0) * max(float(self.config.volume_limit_pct or 0.0), 0.0))
        max_quantity = self._round_quantity(max_quantity)
        if max_quantity <= 0:
            return 0, "成交量约束下无可成交数量"
        if requested_quantity <= max_quantity:
            return requested_quantity, ""
        if not self.config.allow_partial_fill:
            return 0, f"委托数量超过成交量约束上限（{max_quantity}）"
        return max_quantity, ""

    def _match_price(self, symbol: str, direction: str, requested_price: float, bar: object) -> Tuple[float, str]:
        low = self._float_bar_value(bar, "low")
        high = self._float_bar_value(bar, "high")
        close = self._float_bar_value(bar, "close")
        prev_close = self._float_bar_value(bar, "pre_close", "prev_close", "last_close")

        if self.config.enforce_bar_price_range and low is not None and high is not None:
            if requested_price < low - self._price_tolerance(requested_price):
                return 0.0, "买卖价格低于当日可成交区间"
            if requested_price > high + self._price_tolerance(requested_price):
                return 0.0, "买卖价格高于当日可成交区间"

        limit_up, limit_down = self._price_limits(symbol, prev_close, close, high, low)
        if self.config.enforce_price_limit and limit_up and limit_down and close is not None:
            one_price_bar = self._is_one_price_bar(high, low, close)
            if one_price_bar and close >= limit_up - self._price_tolerance(limit_up) and direction == "buy":
                return 0.0, "一字涨停，买入不可成交"
            if one_price_bar and close <= limit_down + self._price_tolerance(limit_down) and direction == "sell":
                return 0.0, "一字跌停，卖出不可成交"

        slipped_price = self._apply_slippage(requested_price, direction)

        if self.config.enforce_price_limit and limit_up and slipped_price > limit_up:
            slipped_price = limit_up
        if self.config.enforce_price_limit and limit_down and slipped_price < limit_down:
            slipped_price = limit_down

        if self.config.enforce_bar_price_range and low is not None and high is not None:
            if self.config.clip_price_to_bar:
                slipped_price = min(max(slipped_price, low), high)
            elif slipped_price < low - self._price_tolerance(slipped_price) or slipped_price > high + self._price_tolerance(slipped_price):
                return 0.0, "滑点后价格超出当日可成交区间"

        return round(float(slipped_price), 6), ""

    def _apply_slippage(self, price: float, direction: str) -> float:
        slippage = max(float(self.config.slippage_pct or 0.0), 0.0)
        if direction == "buy":
            return float(price) * (1.0 + slippage)
        return float(price) * (1.0 - slippage)

    def _price_limits(self, symbol: str, prev_close: Optional[float], close: Optional[float], high: Optional[float], low: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
        if prev_close is None or prev_close <= 0:
            return None, None
        limit_pct = self._infer_limit_pct(symbol, prev_close, close, high, low)
        return prev_close * (1.0 + limit_pct), prev_close * (1.0 - limit_pct)

    def _infer_limit_pct(self, symbol: str, prev_close: float, close: Optional[float], high: Optional[float], low: Optional[float]) -> float:
        code = str(symbol or "").lower().replace("sh", "").replace("sz", "").replace("bj", "").lstrip(".")
        if code.startswith(("300", "301", "688", "689", "8", "4")):
            return 0.20
        observed = [v for v in (close, high, low) if v is not None and prev_close > 0]
        if observed and max(abs(float(v) / prev_close - 1.0) for v in observed) > 0.12:
            return 0.20
        return 0.10

    def _round_quantity(self, quantity: int) -> int:
        lot = max(int(self.config.min_lot or 1), 1)
        return max(int(quantity or 0), 0) // lot * lot

    def _is_one_price_bar(self, high: Optional[float], low: Optional[float], close: Optional[float]) -> bool:
        if high is None or low is None or close is None or high <= 0 or low <= 0:
            return False
        return abs(high - low) <= max(abs(close) * 0.0001, 0.01)

    @staticmethod
    def _price_tolerance(price: float) -> float:
        return max(abs(float(price or 0.0)) * 0.0001, 0.01)

    @classmethod
    def _float_bar_value(cls, bar, *keys) -> Optional[float]:
        value = cls._bar_value(bar, *keys)
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _bar_value(bar, *keys):
        for key in keys:
            try:
                value = bar.get(key) if isinstance(bar, dict) else bar[key]
            except Exception:
                value = None
            if value is not None and not pd.isna(value):
                return value
        return None

    @staticmethod
    def _blocked(symbol: str, direction: str, requested_quantity: int, requested_price: float, reason: str, max_volume_quantity: Optional[int] = None) -> MatchResult:
        return MatchResult(
            symbol=symbol,
            direction=direction,
            requested_quantity=requested_quantity,
            filled_quantity=0,
            requested_price=requested_price,
            fill_price=0.0,
            blocked_reason=reason,
            max_volume_quantity=max_volume_quantity,
        )


class SimulationBroker:
    """Backtest broker facade that owns matching and estimated trading costs."""

    def __init__(self, matcher: Optional[OrderMatcher] = None, fee_config: Optional[FeeModelConfig] = None):
        self.matcher = matcher or OrderMatcher()
        self.fee_config = fee_config or FeeModelConfig()

    @property
    def min_lot(self) -> int:
        return max(int(self.matcher.config.min_lot or 1), 1)

    def match_order(self, *, symbol: str, quantity: int, price: float, bar: object = None) -> MatchResult:
        return self.matcher.match(symbol=symbol, quantity=quantity, price=price, bar=bar)

    def estimate_trade_fees(self, *, direction: str, amount: float, stock_code: str = "") -> Dict[str, float]:
        amount = max(float(amount or 0.0), 0.0)
        direction = (direction or "").strip().lower()

        if TradeRecordService is not None:
            fees = TradeRecordService.estimate_trade_fees(
                direction=direction,
                amount=amount,
                stock_code=stock_code,
            )
        else:
            fees = {"commission": 0.0, "stamp_tax": 0.0, "transfer_fee": 0.0, "total_fee": 0.0}

        if self.fee_config.has_commission_override:
            if direction == "buy":
                rate = self.fee_config.buy_commission_rate
            else:
                rate = self.fee_config.sell_commission_rate
            if rate is None:
                rate = self.fee_config.buy_commission_rate if self.fee_config.buy_commission_rate is not None else self.fee_config.sell_commission_rate
            rate = float(rate if rate is not None else 0.0001)
            min_commission = float(self.fee_config.min_commission if self.fee_config.min_commission is not None else 5.0)
            fees["commission"] = round(max(amount * rate, min_commission), 2) if amount > 0 else 0.0
            fees["total_fee"] = round(
                fees.get("commission", 0.0)
                + fees.get("stamp_tax", 0.0)
                + fees.get("transfer_fee", 0.0),
                2,
            )
        elif TradeRecordService is None:
            commission = round(max(amount * 0.0001, 5.0), 2) if amount > 0 else 0.0
            fees = {"commission": commission, "stamp_tax": 0.0, "transfer_fee": 0.0, "total_fee": commission}

        return fees
