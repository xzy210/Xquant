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

from common.broker_session_service import BrokerSessionService, get_broker_session_service
from trading_app.services.strategy_constants import load_default_etf_rotation_profile
from trading_app.services.trade_execution_service import ExecutionRequest, get_trade_execution_service

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

    def query_order_fill(self, order_id: int,
                         timeout_secs: float = 5.0) -> dict:
        """
        轮询查询订单实际成交情况（可选实现，子类覆盖以获得准确数据）。

        Returns:
            {
              'filled'      : bool   # 是否全量成交
              'filled_qty'  : int    # 实际成交数量（0 表示未知）
              'filled_price': float  # 实际成交均价（0 表示未知）
              'commission'  : float  # 实际佣金（-1 表示不可用，调用方应估算）
              'timed_out'   : bool   # 是否查询超时
            }
        """
        return {
            'filled': True, 'filled_qty': 0, 'filled_price': 0.0,
            'commission': -1.0, 'timed_out': False,
        }


class XtQuantExecutor(TradeExecutor):
    """
    基于 xtquant 的真实交易执行器

    需要外部注入已连接的 xt_trader 和 acc 对象
    （由 BrokerAccountWidget 创建连接后传入）
    """

    def __init__(self):
        self._broker_session_service: Optional[BrokerSessionService] = None
        self._xt_trader = None
        self._acc = None
        self._get_price_func: Optional[Callable] = None
        self._execution_service = get_trade_execution_service()
        default_strategy_id, default_strategy_name, default_virtual_account_id, _, _ = load_default_etf_rotation_profile()
        self._strategy_id = default_strategy_id
        self._strategy_name = default_strategy_name
        self._virtual_account_id = default_virtual_account_id

    def set_broker_session_service(self, broker_session_service: Optional[BrokerSessionService] = None):
        self._broker_session_service = broker_session_service or get_broker_session_service()
        logger.info("XtQuantExecutor: 已绑定共享 BrokerSessionService")

    def set_broker(self, xt_trader, acc):
        """注入券商连接对象"""
        self._xt_trader = xt_trader
        self._acc = acc
        logger.info("XtQuantExecutor: 券商连接已注入")

    def set_price_func(self, func: Callable):
        """注入实时价格获取函数: func(code) -> float"""
        self._get_price_func = func

    def set_strategy_context(
        self,
        *,
        strategy_id: str,
        strategy_name: str = "",
        virtual_account_id: str = "",
    ):
        self._strategy_id = (strategy_id or self._strategy_id).strip()
        self._strategy_name = (strategy_name or self._strategy_name).strip()
        self._virtual_account_id = (virtual_account_id or self._virtual_account_id).strip()

    def is_connected(self) -> bool:
        if self._broker_session_service is not None:
            return self._broker_session_service.is_connected
        return self._xt_trader is not None and self._acc is not None

    def _get_trader_and_account(self):
        if self._broker_session_service is not None:
            return (
                self._broker_session_service.xt_trader,
                self._broker_session_service.account_obj,
            )
        return self._xt_trader, self._acc

    def get_current_price(self, code: str) -> float:
        """
        获取实时价格（三级优先级）：
          1. 外部注入的价格函数（set_price_func）
          2. get_full_tick —— 交易时段的实时 tick（需已订阅行情）
          3. get_market_data —— 本地最新日线收盘价（兜底，含非交易时段）
        """
        if self._get_price_func:
            return self._get_price_func(code)

        try:
            from xtquant import xtdata
            xt_code = to_xt_code(code)

            # ── 优先：实时 tick ──
            try:
                tick = xtdata.get_full_tick([xt_code])
                if tick and xt_code in tick:
                    last = float(tick[xt_code].get('lastPrice', 0))
                    if last > 0:
                        return last
            except Exception:
                pass

            # ── 兜底：本地最新日线 K 线收盘价 ──
            try:
                data = xtdata.get_market_data(
                    ['close'], [xt_code], period='1d',
                    count=1, dividend_type='front'
                )
                if data and 'close' in data:
                    arr = data['close'].get(xt_code)
                    if arr is not None and len(arr) > 0:
                        last_close = float(arr.iloc[-1] if hasattr(arr, 'iloc') else arr[-1])
                        if last_close > 0:
                            logger.debug(
                                f"{code} tick无数据，使用最新收盘价: {last_close:.3f}")
                            return last_close
            except Exception as e2:
                logger.warning(f"{code} get_market_data 失败: {e2}")

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
                return False, f"无法获取 {code} 实时价格（tick/日线均为0，请检查行情订阅）", -1, 0, 0
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
            result = self._execution_service.execute(
                ExecutionRequest(
                    stock_code=xt_code,
                    stock_name=code,
                    order_type=order_type,
                    order_volume=quantity,
                    price_type=actual_price_type,
                    price=current_price,
                    source="etf_rotation",
                    trigger="auto",
                    strategy_name=self._strategy_name,
                    strategy_id=self._strategy_id,
                    virtual_account_id=self._virtual_account_id,
                    intent_id=f"{self._strategy_id}_buy_{code}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                    remark="ETF轮动自动买入",
                    metadata={"owner_type": "etf_rotation"},
                )
            )

            if not result.success:
                return False, result.message or f"买入委托失败 {code}", -1, current_price, quantity

            logger.info(
                "买入委托成功: %s %s股 @ %.3f, order_id=%s",
                code,
                quantity,
                current_price,
                result.broker_order_id,
            )
            return True, result.message or "买入委托成功", result.broker_order_id, current_price, quantity

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
            result = self._execution_service.execute(
                ExecutionRequest(
                    stock_code=xt_code,
                    stock_name=code,
                    order_type=order_type,
                    order_volume=quantity,
                    price_type=actual_price_type,
                    price=price or self.get_current_price(code),
                    source="etf_rotation",
                    trigger="auto",
                    strategy_name=self._strategy_name,
                    strategy_id=self._strategy_id,
                    virtual_account_id=self._virtual_account_id,
                    intent_id=f"{self._strategy_id}_sell_{code}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                    remark="ETF轮动自动卖出",
                    metadata={"owner_type": "etf_rotation"},
                )
            )

            if not result.success:
                return False, result.message or f"卖出委托失败 {code}", -1

            logger.info("卖出委托成功: %s %s股, order_id=%s", code, quantity, result.broker_order_id)
            return True, result.message or "卖出委托成功", result.broker_order_id

        except Exception as e:
            logger.error(f"卖出执行异常: {e}")
            return False, f"卖出异常: {e}", -1

    def query_position(self, code: str) -> Tuple[int, float]:
        if not self.is_connected():
            return 0, 0.0

        try:
            if self._broker_session_service is not None:
                positions = self._broker_session_service.query_stock_positions()
            else:
                positions = self._xt_trader.query_stock_positions(self._acc)
            xt_code = to_xt_code(code)
            for pos in (positions or []):
                if pos.stock_code == xt_code:
                    return pos.can_use_volume, pos.open_price
        except Exception as e:
            logger.error(f"查询持仓异常: {e}")
        return 0, 0.0

    def query_available_cash(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            assets = (
                self._broker_session_service.query_stock_asset()
                if self._broker_session_service is not None
                else self._xt_trader.query_stock_asset(self._acc)
            )
            return float(getattr(assets, "cash", 0.0) or 0.0)
        except Exception as exc:
            logger.error(f"查询可用资金失败: {exc}")
            return 0.0

    def query_total_asset(self) -> float:
        if not self.is_connected():
            return 0.0
        try:
            assets = (
                self._broker_session_service.query_stock_asset()
                if self._broker_session_service is not None
                else self._xt_trader.query_stock_asset(self._acc)
            )
            return float(getattr(assets, "total_asset", 0.0) or 0.0)
        except Exception as exc:
            logger.error(f"查询总资产失败: {exc}")
            return 0.0

    def query_order_fill(self, order_id: int,
                         timeout_secs: float = 5.0) -> dict:
        """
        轮询 miniQMT 查询委托的实际成交情况。
        在工作线程中调用（rotation_engine 的 _confirm_fill 会将此方法
        放到 daemon 线程执行，并通过 QEventLoop 保持 UI 响应）。
        """
        import time

        # 全成(56) / 部撤(53) / 已撤(54) / 废单(57) 均为终态
        TERMINAL = {53, 54, 56, 57}

        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            try:
                order = self._get_order_by_id(order_id)
                if order is not None:
                    traded_qty = int(getattr(order, 'traded_volume', 0) or 0)
                    status = int(getattr(order, 'order_status', -1) or -1)

                    # 终态，或部成(55)且已有成交量 → 可以读结果了
                    if status in TERMINAL or (status == 55 and traded_qty > 0):
                        traded_price = float(
                            getattr(order, 'traded_price', 0) or 0
                        )
                        if traded_price <= 0 and traded_qty > 0:
                            traded_price = float(
                                getattr(order, 'price', 0) or 0
                            )
                        commission = self._query_commission(order_id)
                        return {
                            'filled': status == 56,
                            'filled_qty': traded_qty,
                            'filled_price': traded_price,
                            'commission': commission,
                            'timed_out': False,
                        }
            except Exception as e:
                logger.error(f"query_order_fill 轮询异常: {e}")

            time.sleep(0.5)

        # 超时：尝试最后一次读取，返回已知成交量
        try:
            order = self._get_order_by_id(order_id)
            if order:
                traded_qty = int(getattr(order, 'traded_volume', 0) or 0)
                traded_price = float(getattr(order, 'traded_price', 0) or 0)
                commission = self._query_commission(order_id) if traded_qty > 0 else -1.0
                return {
                    'filled': False,
                    'filled_qty': traded_qty,
                    'filled_price': traded_price,
                    'commission': commission,
                    'timed_out': True,
                }
        except Exception:
            pass

        return {
            'filled': False, 'filled_qty': 0, 'filled_price': 0.0,
            'commission': -1.0, 'timed_out': True,
        }

    def _get_order_by_id(self, order_id: int):
        """查询指定委托，兼容不同版本的 xtquant API"""
        if not self.is_connected():
            return None
        try:
            # 优先使用单条查询（部分版本支持）
            if self._broker_session_service is not None:
                return self._broker_session_service.query_stock_order(order_id)
            if hasattr(self._xt_trader, 'query_stock_order'):
                return self._xt_trader.query_stock_order(self._acc, order_id)
            # 回退：查全部委托后过滤
            orders = self._xt_trader.query_stock_orders(self._acc) or []
            for o in orders:
                if getattr(o, 'order_id', None) == order_id:
                    return o
        except Exception as e:
            logger.error(f"_get_order_by_id({order_id}) 异常: {e}")
        return None

    def _query_commission(self, order_id: int) -> float:
        """
        从成交明细获取指定委托的实际佣金合计。
        无法获取时返回 -1.0（调用方回退到估算）。
        """
        try:
            deals = None
            if self._broker_session_service is not None:
                deals = self._broker_session_service.query_stock_deals()
            for method in ('query_stock_deal', 'query_stock_deals'):
                if deals is not None:
                    break
                if hasattr(self._xt_trader, method):
                    deals = getattr(self._xt_trader, method)(self._acc)
                    break
            if deals:
                total = sum(
                    float(getattr(d, 'commission', 0) or 0)
                    for d in deals
                    if getattr(d, 'order_id', None) == order_id
                )
                return total if total > 0 else -1.0
        except Exception as e:
            logger.error(f"_query_commission({order_id}) 异常: {e}")
        return -1.0

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
