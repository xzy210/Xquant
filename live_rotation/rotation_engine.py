"""
ETF轮动实盘 - 核心轮动引擎

将策略信号计算、风控检查、交易执行、状态管理、通知推送串联起来。
支持手动触发和定时自动执行两种模式。
"""
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

# 确保项目根目录和 strategy_app 在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
_strategy_app = _project_root / "strategy_app"
for p in [str(_project_root), str(_strategy_app)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from .config import RotationConfig, ConfigManager
from .state_manager import RotationState, StateManager, TradeRecord
from .risk_manager import RiskManager
from .trade_executor import TradeExecutor, SimulatedExecutor
from .notifier import RotationNotifier

logger = logging.getLogger(__name__)


class RotationEngine(QObject):
    """
    ETF轮动实盘引擎

    信号:
        signal_generated: 产生信号 (signal_type, detail_dict)
        trade_executed: 交易已执行 (success, detail_dict)
        status_updated: 状态更新 (status_text)
        log_message: 日志消息 (message)
        scores_updated: 得分更新 (scores_dict)
    """

    signal_generated = pyqtSignal(str, dict)
    trade_executed = pyqtSignal(bool, dict)
    status_updated = pyqtSignal(str)
    log_message = pyqtSignal(str)
    scores_updated = pyqtSignal(dict)

    def __init__(self, config: Optional[RotationConfig] = None,
                 executor: Optional[TradeExecutor] = None,
                 parent=None):
        super().__init__(parent)

        # 配置与状态
        self.config_mgr = ConfigManager()
        self.config = config or self.config_mgr.load()

        self.state_mgr = StateManager()
        self.state = self.state_mgr.state

        # 组件
        self.risk_mgr = RiskManager(self.config)
        self.executor: TradeExecutor = executor or SimulatedExecutor()
        self.notifier = RotationNotifier()

        # 策略实例（延迟创建）
        self._strategy = None

        # ETF名称映射
        self._etf_name_map: Dict[str, str] = {}
        self._load_etf_names()

        # 自动调度定时器
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._on_auto_timer)
        self._auto_check_interval = 60_000  # 每分钟检查一次是否到了执行时间

        # 数据目录
        self._data_dir = str(_project_root / "data")

    # ======================================================================
    #  公开 API
    # ======================================================================

    def update_config(self, config: RotationConfig):
        """更新配置"""
        self.config = config
        self.risk_mgr.update_config(config)
        self.config_mgr.save(config)
        self._strategy = None  # 重建策略
        self.notifier.etf_name_map = self._etf_name_map
        self._log("配置已更新")

    def set_executor(self, executor: TradeExecutor):
        """设置交易执行器"""
        self.executor = executor
        self._log(f"交易执行器已设置: {type(executor).__name__}")

    def run_signal_check(self, auto_execute: bool = False) -> dict:
        """
        执行一次信号检查（核心入口）

        Args:
            auto_execute: 是否自动执行交易（False 则仅计算信号）

        Returns:
            {signal, scores, target, reason, ...}
        """
        self._log("=" * 50)
        self._log(f"开始信号检查 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        self.status_updated.emit("正在计算信号...")

        result = {
            'signal': 'ERROR',
            'scores': {},
            'target': None,
            'reason': '',
            'executed': False,
        }

        try:
            # 1. 加载历史数据 & 计算得分
            scores = self._calculate_scores()
            if not scores:
                result['reason'] = "因子得分计算失败（数据不足或加载失败）"
                self._log(f"❌ {result['reason']}")
                self.status_updated.emit("信号检查失败")
                return result

            result['scores'] = scores
            self.scores_updated.emit(scores)

            # 2. 决策逻辑
            signal, target, reason = self._make_decision(scores)
            result['signal'] = signal
            result['target'] = target
            result['reason'] = reason

            self._log(f"📊 信号: {signal} | 目标: {target} | 原因: {reason}")
            self.signal_generated.emit(signal, result)

            # 保存检查结果
            self.state_mgr.update_check_result(signal, scores)

            # 3. 通知
            if self.config.notify_on_signal:
                self.notifier.send_signal(
                    signal, scores, self.state.current_holding, target, reason
                )

            # 4. 自动执行
            if auto_execute and signal in ("SWITCH", "SELL_ALL", "BUY"):
                trade_result = self._execute_signal(signal, target, scores, reason)
                result['executed'] = True
                result['trade_result'] = trade_result

            self.status_updated.emit(
                f"信号: {signal} "
                f"{'| 已执行' if result['executed'] else '| 未执行'}"
            )

        except Exception as e:
            logger.exception("信号检查异常")
            result['reason'] = f"异常: {e}"
            self._log(f"❌ 信号检查异常: {e}")
            self.status_updated.emit("信号检查异常")

        self._log("=" * 50)
        return result

    def execute_manual(self, action: str, code: str,
                       quantity: int = 0, amount: float = 0.0) -> dict:
        """
        手动执行交易

        Args:
            action: "BUY" / "SELL"
            code: ETF代码
            quantity: 卖出数量（BUY时可为0，用amount计算）
            amount: 买入金额
        """
        result = {'success': False, 'message': ''}

        # 风控检查
        ok, msg = self.risk_mgr.pre_trade_check(self.state, action)
        if not ok:
            result['message'] = f"风控拦截: {msg}"
            self._log(f"⚠ {result['message']}")
            return result

        if action == "BUY":
            return self._do_buy(code, amount, reason="手动买入")
        elif action == "SELL":
            return self._do_sell(code, quantity, reason="手动卖出")
        else:
            result['message'] = f"未知操作: {action}"
            return result

    def start_auto(self):
        """启动自动调度"""
        self.config.auto_enabled = True
        self.config_mgr.save(self.config)
        self._auto_timer.start(self._auto_check_interval)
        self._log("✅ 自动调度已启动")
        self.status_updated.emit("自动模式运行中")

    def stop_auto(self):
        """停止自动调度"""
        self.config.auto_enabled = False
        self.config_mgr.save(self.config)
        self._auto_timer.stop()
        self._log("⏹ 自动调度已停止")
        self.status_updated.emit("自动模式已停止")

    def get_status_summary(self) -> dict:
        """获取当前状态摘要"""
        s = self.state
        current_price = 0.0
        unrealized_pnl = 0.0
        if s.current_holding:
            current_price = self.executor.get_current_price(s.current_holding)
            if current_price > 0 and s.buy_price > 0:
                unrealized_pnl = (current_price - s.buy_price) * s.buy_quantity

        return {
            'holding': s.current_holding,
            'holding_name': s.current_holding_name,
            'buy_price': s.buy_price,
            'buy_date': s.buy_date,
            'buy_quantity': s.buy_quantity,
            'current_price': current_price,
            'unrealized_pnl': unrealized_pnl,
            'last_signal': s.last_signal,
            'last_check': f"{s.last_check_date} {s.last_check_time}",
            'last_scores': s.last_scores,
            'trades_today': s.get_trades_today(),
            'auto_enabled': self.config.auto_enabled,
            'executor_connected': self.executor.is_connected(),
        }

    # ======================================================================
    #  策略计算
    # ======================================================================

    def _get_strategy(self):
        """延迟创建策略实例"""
        if self._strategy is None:
            from strategies.etf_three_factor_momentum_strategy_fast import (
                ETFThreeFactorMomentumStrategyFast
            )
            self._strategy = ETFThreeFactorMomentumStrategyFast()
            self._strategy.set_params(self.config.to_strategy_params())
        return self._strategy

    def _calculate_scores(self) -> Dict[str, float]:
        """加载数据并计算所有ETF的综合动量得分"""
        from common.data_loader import load_stock_data

        strategy = self._get_strategy()
        etf_data_dir = str(Path(self._data_dir) / "etf")

        all_data = {}
        for code in self.config.etf_pool:
            df = load_stock_data(code, etf_data_dir)
            if df is not None and len(df) >= self.config.zscore_window:
                all_data[code] = df
                self._log(f"  ✓ {self._code_name(code)}: {len(df)} 条数据")
            else:
                count = len(df) if df is not None else 0
                self._log(f"  ✗ {self._code_name(code)}: 数据不足 ({count}条)")

        if len(all_data) < 2:
            self._log("可用ETF不足2只，无法计算轮动信号")
            return {}

        scores = strategy.calculate_all_scores(all_data)
        return scores

    def _make_decision(self, scores: Dict[str, float]) -> Tuple[str, Optional[str], str]:
        """
        基于得分做出调仓决策

        Returns:
            (signal, target_code, reason)
            signal: HOLD / SWITCH / SELL_ALL / BUY / NO_ACTION
        """
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_code, top_score = sorted_scores[0]

        # 空仓信号判断
        if self.config.enable_empty_position:
            all_below = all(s < self.config.empty_threshold for _, s in sorted_scores)
            if all_below:
                if self.state.current_holding:
                    return ("SELL_ALL", None,
                            f"所有ETF得分低于阈值({self.config.empty_threshold}), "
                            f"最高={top_score:.4f}")
                else:
                    return ("NO_ACTION", None,
                            f"空仓中，所有得分仍低于阈值 {self.config.empty_threshold}")

        holding = self.state.current_holding
        holding_score = scores.get(holding) if holding else None

        # 无持仓 → 买入
        if holding is None or holding_score is None:
            return ("BUY", top_code,
                    f"初始建仓，买入最优 {self._code_name(top_code)} "
                    f"(得分={top_score:.4f})")

        # 已持仓，判断是否切换
        if top_code != holding:
            threshold = self.config.rebalance_threshold
            if top_score > holding_score * threshold:
                return ("SWITCH", top_code,
                        f"{self._code_name(top_code)}({top_score:.4f}) > "
                        f"{self._code_name(holding)}({holding_score:.4f}) × "
                        f"{threshold}")

        return ("HOLD", None,
                f"继续持有 {self._code_name(holding)} "
                f"(得分={holding_score:.4f})")

    # ======================================================================
    #  交易执行
    # ======================================================================

    def _execute_signal(self, signal: str, target: Optional[str],
                        scores: Dict[str, float], reason: str) -> dict:
        """根据信号执行交易"""
        result = {'success': False, 'trades': []}

        if signal == "SELL_ALL":
            if self.state.current_holding:
                r = self._do_sell_all(reason=reason)
                result['trades'].append(r)
                result['success'] = r.get('success', False)

        elif signal == "SWITCH":
            # 先卖后买
            if self.state.current_holding:
                sell_r = self._do_sell_all(reason=f"轮动切换: {reason}")
                result['trades'].append(sell_r)
                if not sell_r.get('success', False):
                    result['success'] = False
                    return result

            # 买入目标
            if target:
                buy_amount = self._get_available_cash()
                buy_r = self._do_buy(target, buy_amount,
                                     reason=f"轮动买入: {reason}")
                result['trades'].append(buy_r)
                result['success'] = buy_r.get('success', False)

                # 更新持仓得分
                if buy_r.get('success'):
                    self.state.current_score = scores.get(target, 0)
                    self.state_mgr.save()

        elif signal == "BUY":
            if target:
                buy_amount = self._get_available_cash()
                buy_r = self._do_buy(target, buy_amount,
                                     reason=f"建仓买入: {reason}")
                result['trades'].append(buy_r)
                result['success'] = buy_r.get('success', False)

                if buy_r.get('success'):
                    self.state.current_score = scores.get(target, 0)
                    self.state_mgr.save()

        return result

    def _do_buy(self, code: str, amount: float,
                reason: str = "") -> dict:
        """执行买入"""
        result = {'success': False, 'action': 'BUY', 'code': code, 'message': ''}

        # 风控
        ok, msg = self.risk_mgr.pre_trade_check(self.state, "BUY")
        if not ok:
            result['message'] = f"风控: {msg}"
            self._log(f"⚠ 买入被风控拦截: {msg}")
            return result

        buy_amount = amount * self.config.cash_ratio
        if buy_amount < self.config.min_trade_amount:
            result['message'] = f"金额过小 ({buy_amount:.2f})"
            self._log(f"⚠ 买入金额不足: {buy_amount:.2f}")
            return result

        # 执行
        success, message, order_id, price, qty = self.executor.buy(code, buy_amount)
        result['success'] = success
        result['message'] = message
        result['order_id'] = order_id
        result['price'] = price
        result['quantity'] = qty

        name = self._etf_name_map.get(code, "")
        now = datetime.now()

        if success:
            self._log(f"✅ 买入成功: {self._code_name(code)} "
                      f"{qty}股 @ {price:.3f}")
            self.state_mgr.update_holding(code, name, 0, price, qty)
        else:
            self._log(f"❌ 买入失败: {self._code_name(code)} - {message}")

        # 记录交易
        record = TradeRecord(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            action="BUY",
            code=code, name=name,
            price=price, quantity=qty,
            amount=price * qty if price and qty else 0,
            reason=reason,
            broker_order_id=order_id,
            success=success,
            error_msg="" if success else message,
        )
        self.state.add_trade(record)
        self.state_mgr.save()

        self.trade_executed.emit(success, result)

        # 通知
        if self.config.notify_on_trade:
            self.notifier.send_trade_result(
                "买入", code, qty, price, success, message, reason
            )

        return result

    def _do_sell(self, code: str, quantity: int,
                 reason: str = "") -> dict:
        """执行卖出（指定数量）"""
        result = {'success': False, 'action': 'SELL', 'code': code, 'message': ''}

        current_price = self.executor.get_current_price(code)

        ok, msg = self.risk_mgr.pre_trade_check(
            self.state, "SELL", current_price
        )
        if not ok:
            result['message'] = f"风控: {msg}"
            self._log(f"⚠ 卖出被风控拦截: {msg}")
            return result

        success, message, order_id = self.executor.sell(code, quantity)
        result['success'] = success
        result['message'] = message
        result['order_id'] = order_id
        result['price'] = current_price
        result['quantity'] = quantity

        name = self._etf_name_map.get(code, "")
        now = datetime.now()

        if success:
            pnl = (current_price - self.state.buy_price) * quantity
            self.state.total_pnl += pnl
            self._log(f"✅ 卖出成功: {self._code_name(code)} "
                      f"{quantity}股 @ {current_price:.3f}, 盈亏 {pnl:+.2f}")
            self.state_mgr.clear_holding()
        else:
            self._log(f"❌ 卖出失败: {self._code_name(code)} - {message}")

        record = TradeRecord(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            action="SELL",
            code=code, name=name,
            price=current_price, quantity=quantity,
            amount=current_price * quantity,
            reason=reason,
            broker_order_id=order_id,
            success=success,
            error_msg="" if success else message,
        )
        self.state.add_trade(record)
        self.state_mgr.save()

        self.trade_executed.emit(success, result)

        if self.config.notify_on_trade:
            self.notifier.send_trade_result(
                "卖出", code, quantity, current_price, success, message, reason
            )

        return result

    def _do_sell_all(self, reason: str = "") -> dict:
        """卖出当前所有持仓"""
        code = self.state.current_holding
        if not code:
            return {'success': True, 'message': '无持仓'}

        # 优先从券商查询真实可用数量
        real_qty, _ = self.executor.query_position(code)
        qty = real_qty if real_qty > 0 else self.state.buy_quantity

        if qty <= 0:
            self._log("⚠ 持仓数量为0，跳过卖出")
            self.state_mgr.clear_holding()
            return {'success': True, 'message': '持仓数量为0'}

        return self._do_sell(code, qty, reason=reason)

    def _get_available_cash(self) -> float:
        """获取可用资金"""
        if isinstance(self.executor, SimulatedExecutor):
            return self.executor.cash

        # 真实账户查询
        try:
            if hasattr(self.executor, '_xt_trader') and self.executor._xt_trader:
                assets = self.executor._xt_trader.query_stock_asset(
                    self.executor._acc
                )
                if assets:
                    return float(assets.cash)
        except Exception as e:
            logger.error(f"查询资金失败: {e}")

        return 0.0

    # ======================================================================
    #  自动调度
    # ======================================================================

    def _on_auto_timer(self):
        """自动调度定时器回调：每分钟检查是否到了执行时间"""
        now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:
            return  # 周末跳过

        current_hm = now.strftime("%H:%M")
        target_hm = self.config.check_time

        if current_hm == target_hm:
            # 避免同一分钟内重复执行
            today = now.strftime("%Y-%m-%d")
            if self.state.last_check_date == today:
                return

            self._log(f"⏰ 定时触发信号检查 ({target_hm})")
            self.run_signal_check(auto_execute=True)

    # ======================================================================
    #  辅助方法
    # ======================================================================

    def _load_etf_names(self):
        """加载ETF名称映射"""
        try:
            from common.data_loader import load_etf_name_map
            self._etf_name_map = load_etf_name_map()
        except Exception:
            self._etf_name_map = {}
        self.notifier.etf_name_map = self._etf_name_map

    def _code_name(self, code: str) -> str:
        name = self._etf_name_map.get(code, "")
        return f"{code}({name})" if name else code

    def _log(self, msg: str):
        logger.info(msg)
        self.log_message.emit(msg)
