"""
ETF轮动实盘 - 核心轮动引擎

将策略信号计算、风控检查、交易执行、状态管理、通知推送串联起来。
支持手动触发和定时自动执行两种模式。
"""
import sys
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QEventLoop

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
from .data_updater import (
    load_etf_parquet, update_etf_pool, check_data_freshness,
    ETFDataUpdateThread, _default_data_dir,
)

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

        # 独立数据目录（live_rotation/data/）
        self._data_dir = _default_data_dir()

        # 数据更新线程
        self._update_thread: Optional[ETFDataUpdateThread] = None
        self._update_pending_auto_execute = False

        # 专用资金初始化（真实账户首次启动时写入账本）
        self._init_dedicated_capital()

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
            # ── Phase 0: 风控前置检查 ──

            # 0a. 账户回撤冷却期
            if self._in_drawdown_cooldown():
                result['signal'] = 'COOLDOWN'
                result['reason'] = (
                    f"回撤保护冷却期（剩余{self.state.cooldown_remaining}天）"
                )
                self._log(f"⏸ {result['reason']}")
                self.signal_generated.emit(result['signal'], result)
                self.state_mgr.update_check_result(result['signal'], {})
                self.status_updated.emit(result['reason'])
                self._log("=" * 50)
                return result

            # 0b. 账户回撤保护
            dd_triggered, dd_result = self._check_drawdown_protection(
                auto_execute
            )
            if dd_triggered:
                result.update(dd_result)
                self.state_mgr.update_check_result(result['signal'], {})
                self._log("=" * 50)
                return result

            # 0c. 移动止盈
            ts_triggered, ts_result = self._check_trailing_stop(auto_execute)
            if ts_triggered:
                result.update(ts_result)
                self.state_mgr.update_check_result(result['signal'], {})
                self._log("=" * 50)
                return result

            # ── Phase 1: 调仓周期计数 ──
            self._update_check_count()

            # ── Phase 2: 加载数据 & 计算得分 ──
            scores = self._calculate_scores()
            if not scores:
                result['reason'] = "因子得分计算失败（数据不足或加载失败）"
                self._log(f"❌ {result['reason']}")
                self.status_updated.emit("信号检查失败")
                self._log("=" * 50)
                return result

            result['scores'] = scores
            self.scores_updated.emit(scores)

            # ── Phase 3: 决策逻辑 ──
            signal, target, reason = self._make_decision(scores)

            # ── Phase 4: 调仓周期过滤 ──
            # 空仓信号(SELL_ALL)和HOLD/NO_ACTION不受调仓周期限制
            if signal in ("SWITCH", "BUY") and not self._is_rebalance_day():
                original = signal
                signal = "HOLD"
                reason = (
                    f"非调仓日（周期={self.config.rebalance_period}天），"
                    f"原信号={original}，暂不执行"
                )
                self._log(f"📅 {reason}")

            result['signal'] = signal
            result['target'] = target
            result['reason'] = reason

            self._log(
                f"📊 信号: {signal} | 目标: {target} | 原因: {reason}"
            )
            self.signal_generated.emit(signal, result)

            # 保存检查结果
            self.state_mgr.update_check_result(signal, scores)

            # 通知
            if self.config.notify_on_signal:
                self.notifier.send_signal(
                    signal, scores, self.state.current_holding, target, reason
                )

            # 自动执行
            if auto_execute and signal in ("SWITCH", "SELL_ALL", "BUY"):
                trade_result = self._execute_signal(
                    signal, target, scores, reason
                )
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

    # ------------------------------------------------------------------
    #  数据更新
    # ------------------------------------------------------------------

    def update_data(self, auto_execute_after: bool = False):
        """
        启动后台线程增量更新ETF池数据。

        Args:
            auto_execute_after: 更新完成后是否自动执行信号检查
        """
        if self._update_thread and self._update_thread.isRunning():
            self._log("⚠ 数据更新正在进行中，请稍候")
            return

        self._update_pending_auto_execute = auto_execute_after
        self._log(f"🔄 开始更新 {len(self.config.etf_pool)} 只ETF数据...")
        self.status_updated.emit("正在更新ETF数据...")

        self._update_thread = ETFDataUpdateThread(
            self.config.etf_pool, self._data_dir, parent=self
        )
        self._update_thread.progress.connect(self._on_update_progress)
        self._update_thread.finished_signal.connect(self._on_update_finished)
        self._update_thread.start()

    def update_data_sync(self) -> Tuple[int, int, List[str]]:
        """同步更新ETF数据（阻塞），供手动调用。"""
        self._log(f"🔄 同步更新 {len(self.config.etf_pool)} 只ETF数据...")
        s, t, errs = update_etf_pool(self.config.etf_pool, self._data_dir)
        if errs:
            for e in errs:
                self._log(f"  ✗ {e}")
        self._log(f"✅ 数据更新完成 ({s}/{t})")
        return s, t, errs

    def is_data_fresh(self) -> bool:
        """检查ETF池数据是否都已包含今天的K线。"""
        for code in self.config.etf_pool:
            fresh, _ = check_data_freshness(self._data_dir, code)
            if not fresh:
                return False
        return True

    def _on_update_progress(self, current, total, code, message):
        self._log(f"  [{current}/{total}] {self._code_name(code)}: {message}")

    def _on_update_finished(self, success, total, errors):
        if errors:
            for e in errors:
                self._log(f"  ✗ {e}")
        self._log(f"✅ ETF数据更新完成 ({success}/{total})")
        self.status_updated.emit(f"数据更新完成 ({success}/{total})")

        if self._update_pending_auto_execute:
            self._update_pending_auto_execute = False
            self._log("⏰ 数据已更新，开始信号检查...")
            self.run_signal_check(auto_execute=True)

    def get_status_summary(self) -> dict:
        """获取当前状态摘要"""
        s = self.state
        current_price = 0.0
        unrealized_pnl = 0.0
        if s.current_holding:
            current_price = self.executor.get_current_price(s.current_holding)
            if current_price > 0 and s.buy_price > 0:
                unrealized_pnl = (current_price - s.buy_price) * s.buy_quantity

        data_fresh = self.is_data_fresh()

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
            'cooldown_remaining': s.cooldown_remaining,
            'holding_high_price': s.holding_high_price,
            'data_fresh': data_fresh,
            'data_dir': str(self._data_dir),
            'dedicated_cash': s.dedicated_cash,
            'use_dedicated_capital': self.config.use_dedicated_capital,
            'dedicated_capital': self.config.dedicated_capital,
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
        strategy = self._get_strategy()

        all_data = {}
        for code in self.config.etf_pool:
            df = load_etf_parquet(code, self._data_dir)
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

                # 卖出委托失败：直接中止，持仓状态未改变
                if not sell_r.get('success', False):
                    result['success'] = False
                    result['reason'] = f"轮动中止: 卖出失败 - {sell_r.get('message', '')}"
                    self._log(f"⚠ 轮动切换中止: 卖出失败，持仓保持不变")
                    return result

                # 部分成交：持仓未完全清空，中止买入，等待下次处理
                if sell_r.get('partial_fill', False):
                    remaining = sell_r.get('remaining', 0)
                    msg = (
                        f"卖出部分成交（剩余 {remaining} 股），"
                        f"已中止轮动切换，请确认持仓后再执行"
                    )
                    self._log(f"⚠ {msg}")
                    result['success'] = False
                    result['reason'] = msg
                    self.status_updated.emit(msg)
                    if self.config.notify_on_trade:
                        self.notifier.send_trade_result(
                            "卖出(部分成交-切换中止)",
                            self.state.current_holding or "", remaining,
                            sell_r.get('price', 0), False, msg, reason
                        )
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

    def _confirm_fill(self, order_id: int,
                      expected_qty: int, expected_price: float,
                      timeout_secs: float = 5.0) -> dict:
        """
        在后台 daemon 线程轮询 miniQMT，通过 QEventLoop 保持 UI 响应。
        模拟器或不支持查询时直接返回 commission=-1（调用方按配置估算）。

        Returns: 与 TradeExecutor.query_order_fill 相同的 dict
        """
        if isinstance(self.executor, SimulatedExecutor):
            return {
                'filled': True,
                'filled_qty': expected_qty,
                'filled_price': expected_price,
                'commission': -1.0,
                'timed_out': False,
            }

        self._log(f"⏳ 查询委托 #{order_id} 成交情况（最长 {timeout_secs:.0f} 秒）...")

        fill_result: list = [None]
        loop = QEventLoop()

        def _poll():
            fill_result[0] = self.executor.query_order_fill(
                order_id, timeout_secs
            )
            loop.quit()   # QEventLoop.quit() 是线程安全的

        t = threading.Thread(target=_poll, daemon=True)
        t.start()

        # 安全超时（内部超时 +1 秒）
        safety = QTimer()
        safety.setSingleShot(True)
        safety.timeout.connect(loop.quit)
        safety.start(int((timeout_secs + 1) * 1000))

        loop.exec()
        safety.stop()

        info = fill_result[0]
        if info is None:
            self._log("⚠ 成交查询超时，回退到估算值")
            return {
                'filled': True,
                'filled_qty': expected_qty,
                'filled_price': expected_price,
                'commission': -1.0,
                'timed_out': True,
            }

        if info.get('timed_out'):
            self._log(
                f"⚠ 委托 #{order_id} 查询超时，"
                f"已知成交量: {info.get('filled_qty', 0)} 股"
            )
        return info

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
            # ── 等待 miniQMT 成交回报，获取实际成交价/量/佣金 ──
            fill = self._confirm_fill(order_id, qty, price)

            actual_qty   = fill['filled_qty']   if fill['filled_qty'] > 0   else qty
            actual_price = fill['filled_price'] if fill['filled_price'] > 0 else price
            actual_cost  = actual_price * actual_qty

            if fill['commission'] >= 0:
                buy_commission = fill['commission']
                fee_label = "[实际]"
            else:
                buy_commission = (
                    max(self.config.min_commission,
                        actual_cost * self.config.buy_commission_rate)
                    if actual_cost > 0 else 0.0
                )
                fee_label = "[估算]"

            total_cost = actual_cost + buy_commission
            self._log(
                f"✅ 买入成功: {self._code_name(code)} "
                f"{actual_qty}股 @ {actual_price:.3f}，"
                f"花费 {total_cost:,.2f} 元（佣金 {buy_commission:.2f} {fee_label}）"
            )
            # 用实际成交数据更新持仓状态和账本
            self.state_mgr.update_holding(code, name, 0, actual_price, actual_qty)
            self._deduct_dedicated_cash(total_cost)

            # 同步 result 为实际成交值
            result['price']    = actual_price
            result['quantity'] = actual_qty
            price, qty = actual_price, actual_qty   # TradeRecord 使用实际值
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
        result = {
            'success': False, 'action': 'SELL', 'code': code, 'message': '',
            'partial_fill': False, 'remaining': 0,
        }

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

        # 默认假设全量成交；成功后用 miniQMT 回报修正
        actual_sold  = quantity
        actual_price = current_price
        buy_price_snapshot = self.state.buy_price   # 清仓前保存，用于 pnl 计算

        if success:
            # ── 等待 miniQMT 成交回报，获取实际成交价/量/佣金 ──
            fill = self._confirm_fill(order_id, quantity, current_price)

            actual_sold  = fill['filled_qty']   if fill['filled_qty'] > 0   else quantity
            actual_price = fill['filled_price'] if fill['filled_price'] > 0 else current_price
            remaining_qty = max(0, quantity - actual_sold)

            proceeds = actual_price * actual_sold
            if fill['commission'] >= 0:
                sell_commission = fill['commission']
                fee_label = "[实际]"
            else:
                sell_commission = (
                    max(self.config.min_commission,
                        proceeds * self.config.sell_commission_rate)
                    if proceeds > 0 else 0.0
                )
                fee_label = "[估算]"
            net_proceeds = proceeds - sell_commission

            pnl = (actual_price - buy_price_snapshot) * actual_sold
            self.state.total_pnl += pnl

            if remaining_qty > 0:
                result['partial_fill'] = True
                result['remaining']    = remaining_qty
                self._log(
                    f"⚠ 卖出部分成交: 委托 {quantity} 股, "
                    f"成交 {actual_sold} 股, 剩余 {remaining_qty} 股"
                )
                self.state.buy_quantity = remaining_qty
                self.state_mgr.save()
            else:
                self.state_mgr.clear_holding()

            self._log(
                f"✅ 卖出成功: {self._code_name(code)} "
                f"{actual_sold}股 @ {actual_price:.3f}, "
                f"盈亏 {pnl:+.2f}, 佣金 {sell_commission:.2f} {fee_label}"
            )
            # 专用资金账本：回收净所得（实际成交价扣佣金）
            self._add_dedicated_cash(net_proceeds)

            # 同步 result 为实际成交值
            result['price']    = actual_price
            result['quantity'] = actual_sold
        else:
            self._log(f"❌ 卖出失败: {self._code_name(code)} - {message}")

        record = TradeRecord(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            action="SELL",
            code=code, name=name,
            price=actual_price, quantity=actual_sold,
            amount=actual_price * actual_sold,
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
                "卖出", code, actual_sold, actual_price, success, message, reason
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
        """获取策略可用资金"""
        if isinstance(self.executor, SimulatedExecutor):
            return self.executor.cash

        # 真实账户：优先使用专用资金账本
        if self.config.use_dedicated_capital:
            return self.state.dedicated_cash

        # 不限制模式：查询券商账户全部可用现金
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

    def _init_dedicated_capital(self):
        """
        专用资金初始化：真实账户首次启动时将 dedicated_capital 写入账本。
        条件：真实执行器 + 启用专用资金 + 账本尚未初始化（dedicated_cash == 0）
              + 当前无持仓（有持仓说明资金已在运转中，不能重置）
        """
        if isinstance(self.executor, SimulatedExecutor):
            return
        if not self.config.use_dedicated_capital:
            return
        if self.state.dedicated_cash != 0.0:
            return
        if self.state.current_holding:
            # 有持仓但账本为0 → 说明是旧数据迁移，用 dedicated_capital 作为参考值
            self.state.dedicated_cash = self.config.dedicated_capital
            self.state_mgr.save()
            self._log(f"💰 专用资金账本迁移初始化: {self.config.dedicated_capital:,.0f} 元")
            return

        self.state.dedicated_cash = self.config.dedicated_capital
        self.state_mgr.save()
        self._log(f"💰 专用资金账本已初始化: {self.config.dedicated_capital:,.0f} 元")

    def _deduct_dedicated_cash(self, amount: float):
        """买入后从账本扣减现金（含手续费估算）"""
        if isinstance(self.executor, SimulatedExecutor):
            return
        if not self.config.use_dedicated_capital:
            return
        self.state.dedicated_cash = max(0.0, self.state.dedicated_cash - amount)
        self.state_mgr.save()

    def _add_dedicated_cash(self, amount: float):
        """卖出后向账本回收现金"""
        if isinstance(self.executor, SimulatedExecutor):
            return
        if not self.config.use_dedicated_capital:
            return
        self.state.dedicated_cash += amount
        self.state_mgr.save()

    def reset_dedicated_capital(self, new_capital: Optional[float] = None):
        """
        重置专用资金账本（手动校正入口）。
        new_capital=None 时使用 config.dedicated_capital。
        """
        cap = new_capital if new_capital is not None else self.config.dedicated_capital
        self.state.dedicated_cash = cap
        self.state_mgr.save()
        self._log(f"💰 专用资金账本已重置为: {cap:,.0f} 元")

    # ======================================================================
    #  风控检查（调仓周期 / 移动止盈 / 账户回撤保护）
    # ======================================================================

    def _in_drawdown_cooldown(self) -> bool:
        """检查是否处于账户回撤保护冷却期，每天自动递减一次"""
        if not self.config.enable_drawdown_protection:
            return False
        if self.state.cooldown_remaining <= 0:
            return False

        today = datetime.now().strftime("%Y-%m-%d")
        if self.state.cooldown_last_decrement_date != today:
            self.state.cooldown_last_decrement_date = today
            self.state.cooldown_remaining -= 1

            if self.state.cooldown_remaining <= 0:
                self.state.cooldown_remaining = 0
                total = self._get_total_asset()
                if total > 0:
                    self.state.account_peak = total
                self._log("✅ 回撤保护冷却期结束，账户峰值重置，恢复交易")
                self.state_mgr.save()
                return False

            self.state_mgr.save()

        return self.state.cooldown_remaining > 0

    def _check_drawdown_protection(self, auto_execute: bool) -> tuple:
        """
        检查账户最大回撤保护

        Returns:
            (triggered: bool, result: dict)
        """
        if not self.config.enable_drawdown_protection:
            return False, {}
        if not self.state.current_holding:
            return False, {}

        total = self._get_total_asset()
        if total <= 0:
            return False, {}

        # 首次初始化峰值
        if self.state.account_peak <= 0:
            self.state.account_peak = total
            self.state_mgr.save()
            return False, {}

        if total > self.state.account_peak:
            self.state.account_peak = total
            self.state_mgr.save()

        drawdown = (self.state.account_peak - total) / self.state.account_peak
        if drawdown < self.config.max_drawdown_pct:
            return False, {}

        reason = (
            f"账户回撤保护: 回撤 {drawdown * 100:.1f}% >= "
            f"{self.config.max_drawdown_pct * 100:.0f}%, "
            f"峰值={self.state.account_peak:,.0f}, "
            f"当前={total:,.0f}"
        )
        self._log(f"🔴 {reason}")

        result = {
            'signal': 'DRAWDOWN_STOP',
            'reason': reason,
            'executed': False,
        }

        if auto_execute:
            self._do_sell_all(reason=reason)
            result['executed'] = True

        self.state.cooldown_remaining = self.config.drawdown_cooldown_days
        self.state.cooldown_last_decrement_date = ""
        self.state_mgr.save()
        self._log(f"⏸ 进入冷却期 {self.config.drawdown_cooldown_days} 天")

        self.signal_generated.emit('DRAWDOWN_STOP', result)
        self.status_updated.emit(reason)

        if self.config.notify_on_signal:
            self.notifier.send_signal(
                'DRAWDOWN_STOP', {}, self.state.current_holding, None, reason
            )

        return True, result

    def _check_trailing_stop(self, auto_execute: bool) -> tuple:
        """
        检查移动止盈

        Returns:
            (triggered: bool, result: dict)
        """
        if not self.config.enable_trailing_stop:
            return False, {}
        if not self.state.current_holding:
            return False, {}

        price = self.executor.get_current_price(self.state.current_holding)
        if price <= 0:
            return False, {}

        # 更新持仓最高价
        if price > self.state.holding_high_price:
            self.state.holding_high_price = price
            self.state_mgr.save()

        if self.state.holding_high_price <= 0:
            return False, {}

        drop = ((self.state.holding_high_price - price)
                / self.state.holding_high_price)
        if drop < self.config.trailing_stop_pct:
            return False, {}

        reason = (
            f"移动止盈: {self._code_name(self.state.current_holding)} "
            f"从最高价 {self.state.holding_high_price:.3f} "
            f"回撤 {drop * 100:.1f}% >= "
            f"{self.config.trailing_stop_pct * 100:.0f}%"
        )
        self._log(f"🟡 {reason}")

        result = {
            'signal': 'TRAILING_STOP',
            'reason': reason,
            'executed': False,
        }

        if auto_execute:
            self._do_sell_all(reason=reason)
            result['executed'] = True

        self.signal_generated.emit('TRAILING_STOP', result)
        self.status_updated.emit(reason)

        if self.config.notify_on_signal:
            self.notifier.send_signal(
                'TRAILING_STOP', {}, self.state.current_holding, None, reason
            )

        return True, result

    def _is_rebalance_day(self) -> bool:
        """检查今天是否为调仓日"""
        period = max(1, self.config.rebalance_period)
        if period <= 1:
            return True
        return (self.state.check_count % period == 0)

    def _update_check_count(self):
        """更新信号检查计数（每个交易日只计一次）"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state.last_check_date != today:
            self.state.check_count += 1

    def _get_total_asset(self) -> float:
        """计算当前账户总资产（现金 + 持仓市值）"""
        if isinstance(self.executor, SimulatedExecutor):
            cash = self.executor.cash
            pos_val = 0.0
            if self.state.current_holding:
                p = self.executor.get_current_price(self.state.current_holding)
                if p > 0:
                    pos_val = p * self.state.buy_quantity
            return cash + pos_val

        try:
            if hasattr(self.executor, '_xt_trader') and self.executor._xt_trader:
                assets = self.executor._xt_trader.query_stock_asset(
                    self.executor._acc
                )
                if assets and hasattr(assets, 'total_asset'):
                    val = float(getattr(assets, 'total_asset', 0) or 0)
                    if val > 0:
                        return val
        except Exception as e:
            logger.error(f"查询总资产失败: {e}")

        cash = self._get_available_cash()
        if self.state.current_holding:
            p = self.executor.get_current_price(self.state.current_holding)
            if p > 0:
                return cash + p * self.state.buy_quantity
        return cash

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

        # 阶段1: 到了数据更新时间 → 先更新数据
        if current_hm == self.config.data_update_time:
            if not self.is_data_fresh() and (
                self._update_thread is None or not self._update_thread.isRunning()
            ):
                self._log(f"⏰ 定时触发数据更新 ({self.config.data_update_time})")
                self.update_data(auto_execute_after=False)
                return

        # 阶段2: 到了信号检查时间
        target_hm = self.config.check_time
        if current_hm == target_hm:
            today = now.strftime("%Y-%m-%d")
            if self.state.last_check_date == today:
                return

            if not self.is_data_fresh():
                self._log("⏰ 数据尚未更新，先更新数据再检查信号...")
                self.update_data(auto_execute_after=True)
            else:
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
