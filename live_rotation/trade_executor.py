"""
ETF轮动实盘 - 交易执行器

提供抽象接口和两种实现：
  - XtQuantExecutor: 通过 xtquant 连接券商真实下单
  - SimulatedExecutor: 模拟下单（用于测试）
"""
import logging
import math
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Tuple, Optional, Callable

logger = logging.getLogger(__name__)


def to_xt_code(code: str) -> str:
    """6位代码 → xtquant 格式"""
    if '.' in code:
        return code
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "5", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


class TradeExecutor(ABC):
    """交易执行器抽象基类"""

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def buy(self, code: str, amount: float,
            price: Optional[float] = None) -> Tuple[bool, str, int, float, int]:
        """
        买入ETF

        Args:
            code: 6位ETF代码
            amount: 拟投入金额
            price: 限价（None=市价）

        Returns:
            (成功, 消息, 券商委托号, 实际委托价, 委托数量)
        """
        ...

    @abstractmethod
    def sell(self, code: str, quantity: int,
             price: Optional[float] = None) -> Tuple[bool, str, int]:
        """
        卖出ETF

        Args:
            code: 6位ETF代码
            quantity: 卖出数量
            price: 限价（None=市价）

        Returns:
            (成功, 消息, 券商委托号)
        """
        ...

    @abstractmethod
    def get_current_price(self, code: str) -> float:
        """获取当前价格"""
        ...

    @abstractmethod
    def query_position(self, code: str) -> Tuple[int, float]:
        """
        查询持仓

        Returns:
            (可用数量, 成本价)
        """
        ...


class XtQuantExecutor(TradeExecutor):
    """
    基于 xtquant 的真实交易执行器

    需要外部注入已连接的 xt_trader 和 acc 对象
    （由 BrokerAccountWidget 创建连接后传入）
    """

    def __init__(self):
        self._xt_trader = None
        self._acc = None
        self._get_price_func: Optional[Callable] = None

    def set_broker(self, xt_trader, acc):
        """注入券商连接对象"""
        self._xt_trader = xt_trader
        self._acc = acc
        logger.info("XtQuantExecutor: 券商连接已注入")

    def set_price_func(self, func: Callable):
        """注入实时价格获取函数: func(code) -> float"""
        self._get_price_func = func

    def is_connected(self) -> bool:
        return self._xt_trader is not None and self._acc is not None

    def get_current_price(self, code: str) -> float:
        if self._get_price_func:
            return self._get_price_func(code)

        try:
            from xtquant import xtdata
            xt_code = to_xt_code(code)
            tick = xtdata.get_full_tick([xt_code])
            if tick and xt_code in tick:
                return float(tick[xt_code].get('lastPrice', 0))
        except Exception as e:
            logger.error(f"获取价格失败 {code}: {e}")
        return 0.0

    def buy(self, code: str, amount: float,
            price: Optional[float] = None) -> Tuple[bool, str, int, float, int]:
        if not self.is_connected():
            return False, "未连接券商", -1, 0, 0

        xt_code = to_xt_code(code)

        # 获取价格
        if price is None:
            current_price = self.get_current_price(code)
            if current_price <= 0:
                return False, f"获取价格失败: {code}", -1, 0, 0
        else:
            current_price = price

        # ETF最小100股
        quantity = int(amount / current_price / 100) * 100
        if quantity <= 0:
            return False, f"资金不足，最低需要 {current_price * 100:.2f} 元", -1, 0, 0

        try:
            FIX_PRICE, MARKET_PRICE = self._get_price_constants()

            if price is not None:
                actual_price_type = FIX_PRICE
                order_price = price
            else:
                actual_price_type = MARKET_PRICE
                order_price = -1

            order_type = 23  # 买入
            order_id = self._xt_trader.order_stock(
                self._acc, xt_code, order_type, quantity,
                actual_price_type, order_price, '', ''
            )

            if order_id is None or order_id == -1:
                return False, f"买入委托失败 {code}", -1, current_price, quantity

            logger.info(f"买入委托成功: {code} {quantity}股 @ {current_price:.3f}, "
                        f"order_id={order_id}")
            return True, "买入委托成功", order_id, current_price, quantity

        except Exception as e:
            logger.error(f"买入执行异常: {e}")
            return False, f"买入异常: {e}", -1, current_price, quantity

    def sell(self, code: str, quantity: int,
             price: Optional[float] = None) -> Tuple[bool, str, int]:
        if not self.is_connected():
            return False, "未连接券商", -1

        xt_code = to_xt_code(code)

        try:
            FIX_PRICE, MARKET_PRICE = self._get_price_constants()

            if price is not None:
                actual_price_type = FIX_PRICE
                order_price = price
            else:
                actual_price_type = MARKET_PRICE
                order_price = -1

            order_type = 24  # 卖出
            order_id = self._xt_trader.order_stock(
                self._acc, xt_code, order_type, quantity,
                actual_price_type, order_price, '', ''
            )

            if order_id is None or order_id == -1:
                return False, f"卖出委托失败 {code}", -1

            logger.info(f"卖出委托成功: {code} {quantity}股, order_id={order_id}")
            return True, "卖出委托成功", order_id

        except Exception as e:
            logger.error(f"卖出执行异常: {e}")
            return False, f"卖出异常: {e}", -1

    def query_position(self, code: str) -> Tuple[int, float]:
        if not self.is_connected():
            return 0, 0.0

        try:
            positions = self._xt_trader.query_stock_positions(self._acc)
            xt_code = to_xt_code(code)
            for pos in (positions or []):
                if pos.stock_code == xt_code:
                    return pos.can_use_volume, pos.open_price
        except Exception as e:
            logger.error(f"查询持仓异常: {e}")
        return 0, 0.0

    @staticmethod
    def _get_price_constants():
        try:
            from xtquant import xtconstant
            fix = xtconstant.FIX_PRICE
            market = getattr(xtconstant, 'LATEST_PRICE',
                             getattr(xtconstant, 'MARKET_PRICE', 1))
            return fix, market
        except ImportError:
            return 0, 1


class SimulatedExecutor(TradeExecutor):
    """
    模拟交易执行器（用于测试和模拟盘）

    不真正下单，只模拟执行过程并记录。
    """

    def __init__(self, initial_cash: float = 100000.0):
        self.cash = initial_cash
        self.positions: dict = {}  # code -> {quantity, avg_price}
        self._prices: dict = {}   # code -> price
        self._order_seq = 0
        self._connected = True

    def set_prices(self, prices: dict):
        """设置模拟价格 {code: price}"""
        self._prices.update(prices)

    def is_connected(self) -> bool:
        return self._connected

    def get_current_price(self, code: str) -> float:
        return self._prices.get(code, 0.0)

    def buy(self, code: str, amount: float,
            price: Optional[float] = None) -> Tuple[bool, str, int, float, int]:
        current_price = price or self.get_current_price(code)
        if current_price <= 0:
            return False, "无价格数据", -1, 0, 0

        quantity = int(amount / current_price / 100) * 100
        if quantity <= 0:
            return False, "资金不足", -1, 0, 0

        cost = quantity * current_price
        if cost > self.cash:
            quantity = int(self.cash / current_price / 100) * 100
            cost = quantity * current_price

        self.cash -= cost
        pos = self.positions.get(code, {'quantity': 0, 'avg_price': 0})
        total_qty = pos['quantity'] + quantity
        if total_qty > 0:
            pos['avg_price'] = (pos['quantity'] * pos['avg_price'] +
                                cost) / total_qty
        pos['quantity'] = total_qty
        self.positions[code] = pos

        self._order_seq += 1
        logger.info(f"[模拟] 买入 {code} {quantity}股 @ {current_price:.3f}, "
                    f"剩余现金 {self.cash:.2f}")
        return True, "模拟买入成功", self._order_seq, current_price, quantity

    def sell(self, code: str, quantity: int,
             price: Optional[float] = None) -> Tuple[bool, str, int]:
        pos = self.positions.get(code)
        if not pos or pos['quantity'] < quantity:
            avail = pos['quantity'] if pos else 0
            return False, f"可用数量不足 ({avail}/{quantity})", -1

        current_price = price or self.get_current_price(code)
        if current_price <= 0:
            return False, "无价格数据", -1

        proceeds = quantity * current_price
        self.cash += proceeds
        pos['quantity'] -= quantity
        if pos['quantity'] == 0:
            del self.positions[code]

        self._order_seq += 1
        logger.info(f"[模拟] 卖出 {code} {quantity}股 @ {current_price:.3f}, "
                    f"剩余现金 {self.cash:.2f}")
        return True, "模拟卖出成功", self._order_seq

    def query_position(self, code: str) -> Tuple[int, float]:
        pos = self.positions.get(code)
        if pos:
            return pos['quantity'], pos['avg_price']
        return 0, 0.0
