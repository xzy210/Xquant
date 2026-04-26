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
from types import SimpleNamespace
from typing import Dict, Optional, Tuple, List

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QEventLoop

# 确保项目根目录和 strategy_app 在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
_strategy_app = _project_root / "strategy_app"
for p in [str(_project_root), str(_strategy_app)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from .config import RotationConfig, ConfigManager
from .state_manager import RotationState, StateManager
from .strategy_provider import DefaultStrategyProvider
from .reconciler import StartupReconciler
from .rotation_execution_service import RotationExecutionService
from .rotation_guard_service import RotationGuardService
from .rotation_ledger_service import RotationLedgerService
from .rotation_risk_policy import ETFRotationRiskPolicy
from .rotation_signal_service import RotationDecisionService, RotationSignalService
from .trade_executor import TradeExecutor, SimulatedExecutor
from .notifier import RotationNotifier
from .data_updater import (
    load_etf_parquet, update_etf_pool, check_data_freshness,
    ETFDataUpdateThread, _default_data_dir,
)
from .holiday_calendar import is_trading_day, get_non_trading_reason
from trading_app.services.market_data_status_service import get_market_data_status_service
from trading_app.services.strategy_spec_service import get_strategy_spec_service

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
                 strategy_provider=None,
                 parent=None):
        super().__init__(parent)

        # 配置与状态
        self.config_mgr = ConfigManager()
        self.config = config or self.config_mgr.load()

        strategy_spec = get_strategy_spec_service().etf_rotation()
        strategy_id = (self.config.strategy_id or strategy_spec.strategy_id or "etf_rotation").strip() or "etf_rotation"
        self.state_mgr = StateManager(
            strategy_id=strategy_id,
            strategy_name=strategy_spec.strategy_name,
            virtual_account_id=strategy_spec.virtual_account_id if strategy_id == strategy_spec.strategy_id else f"va_{strategy_id}",
        )
        self.state = self.state_mgr.state

        # 组件
        # 注意：策略级风控统一由 ETFRotationRiskPolicy + StrategyRiskRegistry 承担，
        #   真实盘走 TradeExecutionService 统一网关触发，模拟盘走
        #   _preflight_strategy_risk_policy 触发，不再维护独立的 RiskManager。
        self.executor: TradeExecutor = executor or SimulatedExecutor()
        self.strategy_provider = strategy_provider or DefaultStrategyProvider()
        self.reconciler = StartupReconciler()
        self.notifier = RotationNotifier()

        # ETF名称映射
        self._etf_name_map: Dict[str, str] = {}
        self._load_etf_names()

        # 自动调度定时器
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._on_auto_timer)
        self._auto_check_interval = 30_000  # 每 30 秒检查一次
        self._auto_data_done_date = ""      # 当日数据更新已触发的日期
        self._auto_signal_done_date = ""    # 当日信号检查已触发的日期

        # 独立数据目录（live_rotation/data/）
        self._data_dir = _default_data_dir()

        # 信号计算与调仓决策服务
        self.signal_service = RotationSignalService(
            config=self.config,
            data_dir=self._data_dir,
            strategy_provider=self.strategy_provider,
            logger_fn=self._log,
            code_name_fn=self._code_name,
        )
        self.decision_service = RotationDecisionService(
            config=self.config,
            state=self.state,
            code_name_fn=self._code_name,
        )
        self.guard_service = RotationGuardService(
            config=self.config,
            state=self.state,
            state_saver=self.state_mgr.save,
            total_asset_fn=self._get_total_asset,
            current_price_fn=lambda code: self.executor.get_current_price(code),
            sell_all_fn=lambda reason: self._do_sell_all(reason=reason),
            logger_fn=self._log,
            code_name_fn=self._code_name,
        )
        self.ledger_service = RotationLedgerService(
            config=self.config,
            state=self.state,
            state_mgr=self.state_mgr,
            executor=self.executor,
            strategy_identity_fn=self._etf_strategy_identity,
            code_name_map_fn=lambda code: self._etf_name_map.get(code, ""),
            logger_fn=self._log,
        )
        self.execution_service = RotationExecutionService(
            config=self.config,
            state=self.state,
            state_mgr=self.state_mgr,
            executor=self.executor,
            ledger_service=self.ledger_service,
            ensure_price_fn=self._ensure_sim_price,
            preflight_risk_fn=self._preflight_strategy_risk_policy,
            confirm_fill_fn=self._confirm_fill,
            trade_event_fn=self._on_execution_trade_event,
            partial_switch_stop_fn=self._on_partial_switch_stop,
            logger_fn=self._log,
            code_name_fn=self._code_name,
            code_name_map_fn=lambda code: self._etf_name_map.get(code, ""),
        )

        # 数据更新线程
        self._update_thread: Optional[ETFDataUpdateThread] = None
        self._update_pending_auto_execute = None
        self._update_schedule_context: Optional[dict] = None

        # 专用资金初始化（真实账户首次启动时写入账本）
        self._init_dedicated_capital()

        # 策略级风控 policy 注册到统一网关
        self._strategy_risk_policy: Optional[ETFRotationRiskPolicy] = None
        self._register_strategy_risk_policy()

    # ======================================================================
    #  公开 API
    # ======================================================================

    def update_config(self, config: RotationConfig):
        """更新配置"""
        self.config = config
        # policy 通过 lambda late-binding 读取 self.config，无需显式推送
        self.config_mgr.save(config)
        self.signal_service.update_context(
            config=self.config,
            data_dir=self._data_dir,
            strategy_provider=self.strategy_provider,
            reset_strategy=True,
        )
        self.decision_service.update_context(config=self.config, state=self.state)
        self.guard_service.update_context(config=self.config, state=self.state)
        self.ledger_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        self.execution_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        self.notifier.etf_name_map = self._etf_name_map
        self._log("配置已更新")

    def set_executor(self, executor: TradeExecutor):
        """设置交易执行器"""
        self.executor = executor
        self.ledger_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        self.execution_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        self._log(f"交易执行器已设置: {type(executor).__name__}")
        self._run_startup_reconcile()
        self._init_dedicated_capital()

    # ------------------------------------------------------------------
    #  策略级风控 Policy（注册到统一网关）
    # ------------------------------------------------------------------

    def _register_strategy_risk_policy(self) -> None:
        """Register this engine's risk rules with the unified gateway registry.

        使用 override=True 保证同一 strategy_id 多次初始化（比如测试或热重载）
        不会累积出多个同类 policy。provider 采用 late-binding，因此 update_config
        / state_mgr 重新加载后 policy 能自动读到最新对象。
        """
        try:
            from trading_app.services.strategy_risk import get_strategy_risk_registry

            strategy_id, _, _ = self._etf_strategy_identity()
            policy = ETFRotationRiskPolicy(
                strategy_id=strategy_id,
                config_provider=lambda: self.config,
                state_provider=lambda: self.state,
                config_saver=self._apply_risk_policy_values,
            )
            get_strategy_risk_registry().register(policy, override=True)
            self._strategy_risk_policy = policy
            logger.info("ETF 策略 policy 已注册到统一风控 registry: strategy_id=%s", strategy_id)
        except Exception as exc:
            logger.error("注册 ETF 策略 policy 失败: %s", exc, exc_info=True)
            self._strategy_risk_policy = None

    def _apply_risk_policy_values(self, values: Dict[str, object]) -> None:
        """策略风控面板保存回调：把 UI 提交的字段写回 RotationConfig 并落盘。

        面板负责把控件显示值还原到存储单位（例如 ``15.0%`` → ``15.0``），此处
        只管透传到 :class:`RotationConfig` 对应字段、触发一次
        ``update_config``（会 persist 到 ``rotation_config.json`` 且通知引擎
        重建策略 / 通知器）。
        """
        if not values:
            return
        try:
            cfg = self.config
            for key, value in values.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
            self.update_config(cfg)
            self._log("📋 策略风控参数已更新并保存")
        except Exception as exc:
            logger.error("保存策略风控参数失败: %s", exc, exc_info=True)
            raise

    def unregister_strategy_risk_policy(self) -> None:
        """Remove the policy from the registry (call on shutdown / teardown)."""
        if self._strategy_risk_policy is None:
            return
        try:
            from trading_app.services.strategy_risk import get_strategy_risk_registry

            get_strategy_risk_registry().unregister(
                self._strategy_risk_policy.strategy_id,
                self._strategy_risk_policy,
            )
            logger.info(
                "ETF 策略 policy 已从风控 registry 卸载: strategy_id=%s",
                self._strategy_risk_policy.strategy_id,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("卸载 ETF 策略 policy 失败: %s", exc)
        self._strategy_risk_policy = None

    def _preflight_strategy_risk_policy(
        self,
        *,
        is_sell: bool,
        current_price: float = 0.0,
    ) -> Tuple[bool, str]:
        """下单前统一触发策略级风控 policy。

        - 真实盘（非 SimulatedExecutor）: 订单会走
          :class:`TradeExecutionService` 统一网关，policy 在那边已经会被触发，
          这里直接放行，避免双重评估。
        - 模拟盘（SimulatedExecutor）: 不经过统一网关，这里显式调用
          ``StrategyRiskRegistry`` 作为兜底，确保模拟盘也受同一套规则保护。

        ``warn`` 级 decision 按既有 "允许止损卖出" 语义放行。
        """
        if not isinstance(self.executor, SimulatedExecutor):
            return True, ""

        try:
            from trading_app.services.strategy_risk import (
                StrategyRiskContext,
                get_strategy_risk_registry,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("策略风控模块不可用，跳过预检: %s", exc)
            return True, ""

        strategy_id, _, _ = self._etf_strategy_identity()
        registry = get_strategy_risk_registry()
        if not registry.has(strategy_id):
            return True, ""

        fake_request = SimpleNamespace(
            strategy_id=strategy_id,
            order_type=24 if is_sell else 23,
            price=float(current_price or 0.0),
        )
        decision = registry.evaluate(fake_request, StrategyRiskContext())
        if not decision.passed:
            return False, decision.reason or "策略风控未通过"
        return True, decision.reason or "策略风控通过"

    def _run_startup_reconcile(self):
        if isinstance(self.executor, SimulatedExecutor):
            return
        if not self.executor.is_connected():
            return
        try:
            result = self.reconciler.reconcile(self)
            self._log(f"启动对账完成: {result}")
        except Exception as exc:
            logger.error(f"启动对账失败: {exc}")

    def _check_live_market_data_ready(self, *, require_minute_freshness: bool = False) -> tuple[bool, str]:
        etf_codes = list(dict.fromkeys(str(code or "").strip() for code in self.config.etf_pool if str(code or "").strip()))
        status = get_market_data_status_service().check_status(
            stock_codes=[],
            etf_codes=etf_codes,
            index_codes=[],
            realtime_probe_codes=etf_codes[:3] if etf_codes else None,
            require_minute_freshness=require_minute_freshness,
        )
        if status.can_run_live_strategy:
            return True, status.summary
        return False, status.summary

    def run_signal_check(self, auto_execute: bool = False, schedule_context: Optional[dict] = None) -> dict:
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

        schedule_done = False

        def finalize_schedule(status: str, error: str = ""):
            nonlocal schedule_done
            if schedule_done or not schedule_context:
                return
            self.state_mgr.mark_auto_signal_task(
                status=status,
                schedule_time=str(schedule_context.get("schedule_time", "") or ""),
                trigger=str(schedule_context.get("trigger", "") or ""),
                task_date=str(schedule_context.get("task_date", "") or ""),
                error=error,
            )
            schedule_done = True

        if schedule_context:
            self.state_mgr.mark_auto_signal_task(
                status="running",
                schedule_time=str(schedule_context.get("schedule_time", "") or ""),
                trigger=str(schedule_context.get("trigger", "") or ""),
                task_date=str(schedule_context.get("task_date", "") or ""),
            )

        ready, reason = self._check_live_market_data_ready()
        if not ready:
            result['signal'] = 'BLOCKED'
            result['reason'] = f"行情数据未就绪: {reason}"
            self._log(f"⛔ {result['reason']}")
            self.signal_generated.emit(result['signal'], result)
            self.state_mgr.update_check_result(result['signal'], {})
            self.status_updated.emit(result['reason'])
            finalize_schedule("failed", result['reason'])
            self._log("=" * 50)
            return result

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
                finalize_schedule("completed")
                self._log("=" * 50)
                return result

            # 0b. 账户回撤保护
            dd_triggered, dd_result = self._check_drawdown_protection(
                auto_execute
            )
            if dd_triggered:
                result.update(dd_result)
                self.state_mgr.update_check_result(result['signal'], {})
                finalize_schedule("completed")
                self._log("=" * 50)
                return result

            # 0c. 移动止盈
            ts_triggered, ts_result = self._check_trailing_stop(auto_execute)
            if ts_triggered:
                result.update(ts_result)
                self.state_mgr.update_check_result(result['signal'], {})
                finalize_schedule("completed")
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
                finalize_schedule("failed", result['reason'])
                self._log("=" * 50)
                return result

            result['scores'] = scores
            self.scores_updated.emit(scores)

            # ── Phase 3: 决策逻辑 ──
            signal, target, reason = self._make_decision(scores)

            # ── Phase 4: 调仓周期过滤 ──
            # 空仓信号(SELL_ALL)和HOLD/NO_ACTION不受调仓周期限制
            signal, target, reason, filtered = self.guard_service.filter_rebalance_signal(
                signal,
                target,
                reason,
            )
            if filtered:
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
            finalize_schedule("completed")

        except Exception as e:
            logger.exception("信号检查异常")
            result['reason'] = f"异常: {e}"
            self._log(f"❌ 信号检查异常: {e}")
            self.status_updated.emit("信号检查异常")
            finalize_schedule("failed", result['reason'])

        # 每次信号检查结束后记录当日净值快照
        self._record_daily_equity()

        self._log("=" * 50)
        return result

    def execute_manual(
        self,
        action: str,
        code: str,
        quantity: int = 0,
        amount: float = 0.0,
        price: Optional[float] = None,
    ) -> dict:
        """
        手动执行交易

        Args:
            action: "BUY" / "SELL"
            code: ETF代码
            quantity: 卖出数量（BUY时可为0，用amount计算）
            amount: 买入金额
        """
        result = {'success': False, 'message': ''}

        ready, reason = self._check_live_market_data_ready()
        if not ready:
            result['message'] = f"行情数据未就绪: {reason}"
            self._log(f"⛔ 手动委托已阻断: {result['message']}")
            self.status_updated.emit(result['message'])
            return result

        # 风控检查（仅模拟盘；真实盘由统一网关触发）
        ok, msg = self._preflight_strategy_risk_policy(
            is_sell=(action != "BUY"),
        )
        if not ok:
            result['message'] = f"风控拦截: {msg}"
            self._log(f"⚠ {result['message']}")
            return result

        if action == "BUY":
            return self._do_buy(code, amount, reason="手动买入", price=price)
        elif action == "SELL":
            return self._do_sell(code, quantity, reason="手动卖出", price=price)
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

    def update_data(self, auto_execute_after=None, schedule_context: Optional[dict] = None):
        """
        启动后台线程增量更新ETF池数据。

        Args:
            auto_execute_after: None=仅更新数据；bool=更新完成后执行一次信号检查，且该值决定是否自动下单
        """
        if self._update_thread and self._update_thread.isRunning():
            self._log("⚠ 数据更新正在进行中，请稍候")
            return

        self._update_pending_auto_execute = auto_execute_after
        self._update_schedule_context = dict(schedule_context or {}) if schedule_context else None
        if self._update_schedule_context:
            self.state_mgr.mark_auto_data_task(
                status="running",
                schedule_time=str(self._update_schedule_context.get("schedule_time", "") or ""),
                trigger=str(self._update_schedule_context.get("trigger", "") or ""),
                task_date=str(self._update_schedule_context.get("task_date", "") or ""),
            )
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

        update_ok = not errors and int(success or 0) >= int(total or 0)

        if self._update_schedule_context:
            data_status = "completed" if update_ok else "failed"
            self.state_mgr.mark_auto_data_task(
                status=data_status,
                schedule_time=str(self._update_schedule_context.get("schedule_time", "") or ""),
                trigger=str(self._update_schedule_context.get("trigger", "") or ""),
                task_date=str(self._update_schedule_context.get("task_date", "") or ""),
                error="; ".join(str(e) for e in (errors or [])),
            )

        if not update_ok:
            error_msg = "; ".join(str(e) for e in (errors or [])) or "ETF数据更新失败"
            self._log(f"⛔ 数据未就绪，已停止本次信号检查: {error_msg}")
            self.status_updated.emit("数据未就绪，已停止信号检查")
            self._update_pending_auto_execute = None
            self._update_schedule_context = None
            return

        if self._update_pending_auto_execute is not None:
            pending_auto_execute = bool(self._update_pending_auto_execute)
            self._update_pending_auto_execute = None
            self._log("⏰ 数据已更新，开始信号检查...")
            signal_context = None
            if self._update_schedule_context:
                signal_context = {
                    "trigger": str(self._update_schedule_context.get("trigger", "") or ""),
                    "task_date": str(self._update_schedule_context.get("task_date", "") or ""),
                    "schedule_time": str(self.config.check_time or ""),
                }
            self.run_signal_check(auto_execute=pending_auto_execute, schedule_context=signal_context)
        self._update_schedule_context = None

    def get_status_summary(self) -> dict:
        """获取当前状态摘要"""
        s = self.state
        current_price = 0.0
        unrealized_pnl = 0.0
        price_is_realtime = False
        if s.current_holding:
            current_price = self.executor.get_current_price(s.current_holding)
            if current_price > 0:
                price_is_realtime = True
            else:
                current_price = s.buy_price
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
            'price_is_realtime': price_is_realtime,
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
            'dedicated_cash': round(self._ledger_available_cash(), 2),
            'use_dedicated_capital': self.config.use_dedicated_capital,
            'dedicated_capital': self.config.dedicated_capital,
        }

    def get_statistics(self) -> dict:
        """计算实盘收益统计指标（从 trade_history 动态计算）"""
        history = self.state.trade_history
        sell_records = [
            r for r in history
            if r.get('action') in ('SELL', 'SELL_ALL') and r.get('success', True)
        ]

        total_trades  = len(sell_records)
        win_trades    = sum(1 for r in sell_records if r.get('pnl', 0) > 0)
        loss_trades   = sum(1 for r in sell_records if r.get('pnl', 0) < 0)
        win_rate      = (win_trades / total_trades * 100) if total_trades > 0 else 0.0
        total_trade_pnl = sum(r.get('pnl', 0) for r in sell_records)
        avg_pnl       = total_trade_pnl / total_trades if total_trades > 0 else 0.0
        best_trade    = max((r.get('pnl', 0) for r in sell_records), default=0.0)
        worst_trade   = min((r.get('pnl', 0) for r in sell_records), default=0.0)

        # 平均持仓天数（配对 BUY→SELL）
        hold_days_list = []
        for sell in sell_records:
            code, sell_date = sell.get('code', ''), sell.get('date', '')
            if code and sell_date:
                for r in reversed(history):
                    if (r.get('action') == 'BUY' and r.get('code') == code
                            and r.get('date', '') <= sell_date):
                        try:
                            bd = datetime.strptime(r['date'], "%Y-%m-%d")
                            sd = datetime.strptime(sell_date, "%Y-%m-%d")
                            hold_days_list.append((sd - bd).days)
                        except Exception:
                            pass
                        break
        avg_hold_days = (sum(hold_days_list) / len(hold_days_list)
                         if hold_days_list else 0.0)

        # 当前持仓天数
        current_hold_days = 0
        if self.state.buy_date:
            try:
                bd = datetime.strptime(self.state.buy_date, "%Y-%m-%d")
                current_hold_days = (datetime.now() - bd).days
            except Exception:
                pass

        # 最大回撤（从 daily_equity）
        equity_vals = [v for _, v in sorted(self.state.daily_equity.items())]
        max_dd = 0.0
        if len(equity_vals) > 1:
            peak = equity_vals[0]
            for v in equity_vals[1:]:
                if v > peak:
                    peak = v
                if peak > 0:
                    max_dd = max(max_dd, (peak - v) / peak)

        # 当前净值：实时价 > 今日净值快照 > 买入价兜底
        current_equity = self._ledger_available_cash()
        if self.state.current_holding and self.state.buy_quantity > 0:
            p = self.executor.get_current_price(self.state.current_holding)
            if p > 0:
                current_equity += p * self.state.buy_quantity
            else:
                today = datetime.now().strftime("%Y-%m-%d")
                today_snap = self.state.daily_equity.get(today, 0)
                if today_snap > 0:
                    current_equity = today_snap
                elif self.state.buy_price > 0:
                    current_equity += self.state.buy_price * self.state.buy_quantity

        initial_capital = self.config.dedicated_capital
        total_return_pct = (
            (current_equity - initial_capital) / initial_capital * 100
            if initial_capital > 0 else 0.0
        )

        return {
            'total_trades':     total_trades,
            'win_trades':       win_trades,
            'loss_trades':      loss_trades,
            'win_rate':         win_rate,
            'avg_pnl':          avg_pnl,
            'best_trade':       best_trade,
            'worst_trade':      worst_trade,
            'total_pnl':        self.state.total_pnl,
            'total_return_pct': total_return_pct,
            'current_equity':   current_equity,
            'initial_capital':  initial_capital,
            'max_drawdown':     max_dd * 100,   # 转为百分比
            'avg_hold_days':    avg_hold_days,
            'current_hold_days': current_hold_days,
        }

    # ------------------------------------------------------------------
    #  分析数据记录辅助方法
    # ------------------------------------------------------------------

    def _add_capital_entry(self, action: str, code: str = "", name: str = "",
                           amount: float = 0.0, commission: float = 0.0,
                           fee_source: str = ""):
        """向资金流水账本追加一条记录（委托给账本服务）。"""
        return self.ledger_service.add_capital_entry(
            action, code, name,
            amount=amount,
            commission=commission,
            fee_source=fee_source,
        )

    def _record_daily_equity(self):
        """记录当日净值快照（委托给账本服务）。"""
        return self.ledger_service.record_daily_equity()

    def _add_order_record(self, order_id: int, action: str, code: str,
                          ordered_qty: int, ordered_price: float,
                          reason: str = ""):
        """创建并保存委托记录（委托给账本服务）。"""
        return self.ledger_service.add_order_record(
            order_id, action, code, ordered_qty, ordered_price, reason
        )

    def _update_order_record(self, order_id: int, fill: dict, pnl: float = 0.0):
        """根据成交结果更新委托记录（委托给账本服务）。"""
        return self.ledger_service.update_order_record(order_id, fill, pnl=pnl)

    def _resolve_trade_fees(
        self,
        *,
        direction: str,
        amount: float,
        stock_code: str,
        actual_commission: float = -1.0,
    ) -> dict:
        """统一读取手续费配置（委托给账本服务）。"""
        return self.ledger_service.resolve_trade_fees(
            direction=direction,
            amount=amount,
            stock_code=stock_code,
            actual_commission=actual_commission,
        )

    # ======================================================================
    #  策略计算
    # ======================================================================

    def _get_strategy(self):
        """延迟创建策略实例（委托给信号计算服务）。"""
        return self.signal_service.get_strategy()

    def _calculate_scores(self) -> Dict[str, float]:
        """加载数据并计算所有ETF的综合动量得分。"""
        self.signal_service.update_context(
            config=self.config,
            data_dir=self._data_dir,
            strategy_provider=self.strategy_provider,
        )
        return self.signal_service.calculate_scores()

    def _make_decision(self, scores: Dict[str, float]) -> Tuple[str, Optional[str], str]:
        """基于得分做出调仓决策。"""
        self.decision_service.update_context(config=self.config, state=self.state)
        return self.decision_service.make_decision(scores)

    # ======================================================================
    #  交易执行
    # ======================================================================

    def _on_execution_trade_event(self, success: bool, result: dict) -> None:
        """Handle trade events emitted by RotationExecutionService."""
        self.trade_executed.emit(success, result)

        if not self.config.notify_on_trade:
            return

        action = str(result.get('action') or '')
        code = str(result.get('code') or '')
        quantity = int(result.get('quantity') or 0)
        price = float(result.get('price') or 0.0)
        message = str(result.get('message') or '')
        reason = str(result.get('reason') or '')
        action_name = "买入" if action == "BUY" else "卖出" if action == "SELL" else action
        self.notifier.send_trade_result(
            action_name, code, quantity, price, success, message, reason
        )

    def _on_partial_switch_stop(
        self,
        sell_result: dict,
        remaining: int,
        message: str,
        reason: str,
    ) -> None:
        """Handle partial sell during SWITCH and keep old UI/notification behavior."""
        self.status_updated.emit(message)
        if self.config.notify_on_trade:
            self.notifier.send_trade_result(
                "卖出(部分成交-切换中止)",
                self.state.current_holding or "",
                remaining,
                sell_result.get('price', 0),
                False,
                message,
                reason,
            )

    def _ensure_sim_price(self, code: str) -> float:
        """
        确保模拟执行器持有最新价格。
        - 若已有价格（>0），直接返回。
        - 否则从本地 parquet 读最新收盘价并注入执行器。
        对真实执行器直接返回其报价（不做额外操作）。
        """
        if not isinstance(self.executor, SimulatedExecutor):
            return self.executor.get_current_price(code)

        price = self.executor.get_current_price(code)
        if price > 0:
            return price

        try:
            df = load_etf_parquet(code, self._data_dir)
            if df is not None and len(df) > 0:
                last_close = float(df['close'].iloc[-1])
                if last_close > 0:
                    self.executor.set_prices({code: last_close})
                    self._log(
                        f"[模拟] {self._code_name(code)} "
                        f"价格从数据文件读取: {last_close:.3f}"
                    )
                    return last_close
        except Exception as e:
            logger.warning(f"读取 {code} 价格失败: {e}")
        return 0.0

    def _execute_signal(self, signal: str, target: Optional[str],
                        scores: Dict[str, float], reason: str) -> dict:
        """根据信号执行交易（委托给执行服务）。"""
        self.execution_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        return self.execution_service.execute_signal(signal, target, scores, reason)

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

    def _do_buy(
        self,
        code: str,
        amount: float,
        reason: str = "",
        price: Optional[float] = None,
    ) -> dict:
        """执行买入（委托给执行服务）。"""
        self.execution_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        return self.execution_service.buy(code, amount, reason=reason, price=price)

    def _do_sell(
        self,
        code: str,
        quantity: int,
        reason: str = "",
        price: Optional[float] = None,
    ) -> dict:
        """执行卖出（委托给执行服务）。"""
        self.execution_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        return self.execution_service.sell(code, quantity, reason=reason, price=price)

    def _do_sell_all(self, reason: str = "") -> dict:
        """卖出当前所有持仓（委托给执行服务）。"""
        self.execution_service.update_context(
            config=self.config,
            state=self.state,
            executor=self.executor,
        )
        return self.execution_service.sell_all(reason=reason)

    def _etf_strategy_identity(self) -> Tuple[str, str, str]:
        spec = get_strategy_spec_service().etf_rotation()
        strategy_id = (self.config.strategy_id or spec.strategy_id or "etf_rotation").strip() or "etf_rotation"
        virtual_account_id = spec.virtual_account_id if strategy_id == spec.strategy_id else f"va_{strategy_id}"
        return strategy_id, spec.strategy_name, virtual_account_id

    def _sync_unified_ledger_on_buy(
        self,
        *,
        code: str,
        name: str,
        price: float,
        volume: int,
        commission: float,
        stamp_tax: float,
        transfer_fee: float,
        broker_order_id: int,
        reason: str,
    ) -> None:
        """同步买入成交到统一账本（委托给账本服务）。"""
        return self.ledger_service.sync_unified_ledger_on_buy(
            code=code,
            name=name,
            price=price,
            volume=volume,
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
            broker_order_id=broker_order_id,
            reason=reason,
        )

    def _sync_unified_ledger_on_sell(
        self,
        *,
        code: str,
        name: str,
        price: float,
        volume: int,
        commission: float,
        stamp_tax: float,
        transfer_fee: float,
        broker_order_id: int,
        reason: str,
    ) -> None:
        """同步卖出成交到统一账本（委托给账本服务）。"""
        return self.ledger_service.sync_unified_ledger_on_sell(
            code=code,
            name=name,
            price=price,
            volume=volume,
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
            broker_order_id=broker_order_id,
            reason=reason,
        )

    def _get_available_cash(self) -> float:
        """获取策略可用资金（委托给账本服务）。"""
        return self.ledger_service.available_cash()

    def _ledger_available_cash(self) -> float:
        """从主账本读取本策略当前可用现金（委托给账本服务）。"""
        return self.ledger_service.ledger_available_cash()

    def _init_dedicated_capital(self):
        """初始化专用资金主账本（委托给账本服务）。"""
        return self.ledger_service.init_dedicated_capital()

    def reset_dedicated_capital(self, new_capital: Optional[float] = None):
        """重置本策略启动资金（委托给账本服务）。"""
        return self.ledger_service.reset_dedicated_capital(new_capital)

    def clear_analytics_data(self):
        """清空历史分析数据（委托给账本服务）。"""
        return self.ledger_service.clear_analytics_data()

    # ======================================================================
    #  风控检查（调仓周期 / 移动止盈 / 账户回撤保护）
    # ======================================================================

    def _in_drawdown_cooldown(self) -> bool:
        """检查是否处于账户回撤保护冷却期（委托给 guard 服务）。"""
        self.guard_service.update_context(config=self.config, state=self.state)
        return self.guard_service.in_drawdown_cooldown()

    def _check_drawdown_protection(self, auto_execute: bool) -> tuple:
        """检查账户最大回撤保护（委托给 guard 服务）。"""
        self.guard_service.update_context(config=self.config, state=self.state)
        triggered, result = self.guard_service.check_drawdown_protection(auto_execute)
        if not triggered:
            return triggered, result

        signal = str(result.get('signal') or 'DRAWDOWN_STOP')
        reason = str(result.get('reason') or '')
        self.signal_generated.emit(signal, result)
        self.status_updated.emit(reason)

        if self.config.notify_on_signal:
            self.notifier.send_signal(
                signal, {}, self.state.current_holding, None, reason
            )

        return triggered, result

    def _check_trailing_stop(self, auto_execute: bool) -> tuple:
        """检查移动止盈（委托给 guard 服务）。"""
        self.guard_service.update_context(config=self.config, state=self.state)
        triggered, result = self.guard_service.check_trailing_stop(auto_execute)
        if not triggered:
            return triggered, result

        signal = str(result.get('signal') or 'TRAILING_STOP')
        reason = str(result.get('reason') or '')
        self.signal_generated.emit(signal, result)
        self.status_updated.emit(reason)

        if self.config.notify_on_signal:
            self.notifier.send_signal(
                signal, {}, self.state.current_holding, None, reason
            )

        return triggered, result

    def _is_rebalance_day(self) -> bool:
        """检查今天是否为调仓日（委托给 guard 服务）。"""
        self.guard_service.update_context(config=self.config, state=self.state)
        return self.guard_service.is_rebalance_day()

    def _update_check_count(self):
        """更新信号检查计数（委托给 guard 服务）。"""
        self.guard_service.update_context(config=self.config, state=self.state)
        self.guard_service.update_check_count()

    def _get_total_asset(self) -> float:
        """计算策略总资产（委托给账本服务）。"""
        return self.ledger_service.total_asset()

    # ======================================================================
    #  自动调度
    # ======================================================================

    @staticmethod
    def _hm_to_minutes(hm: str) -> int:
        """将 'HH:MM' 转为当日分钟数（0~1439）"""
        h, m = map(int, hm.split(":"))
        return h * 60 + m

    def _on_auto_timer(self):
        """自动调度定时器回调：每 30 秒检查是否到了执行时间"""
        now = datetime.now()

        # 非交易日（含周末、法定节假日、调休）→ 跳过
        if not is_trading_day(now.date()):
            return

        # 只在交易时段内允许自动调度触发，避免夜间/重启后把白天错过的
        # 定时任务当成待补跑任务再次执行。
        try:
            trading_end_minutes = self._hm_to_minutes(self.config.trading_end)
        except Exception:
            trading_end_minutes = self._hm_to_minutes("14:57")
        now_minutes = now.hour * 60 + now.minute
        if now_minutes > trading_end_minutes:
            return

        today = now.strftime("%Y-%m-%d")
        data_completed_today = (
            self._auto_data_done_date == today
            or self.state_mgr.is_auto_data_task_completed(
                task_date=today,
                schedule_time=self.config.data_update_time,
                trigger="scheduled",
            )
        )
        signal_completed_today = (
            self._auto_signal_done_date == today
            or self.state_mgr.is_auto_signal_task_completed(
                task_date=today,
                schedule_time=self.config.check_time,
                trigger="scheduled",
            )
        )

        # 阶段1: 到了数据更新时间 → 先更新数据
        # 使用 >=target 判断，只要过了目标时间就触发，当日只触发一次
        data_target = self._hm_to_minutes(self.config.data_update_time)
        if (now_minutes >= data_target
                and not data_completed_today):
            if not self.is_data_fresh() and (
                self._update_thread is None or not self._update_thread.isRunning()
            ):
                self._auto_data_done_date = today
                self._log(f"⏰ 定时触发数据更新 ({self.config.data_update_time})")
                self.update_data(
                    auto_execute_after=None,
                    schedule_context={
                        "trigger": "scheduled",
                        "task_date": today,
                        "schedule_time": self.config.data_update_time,
                    },
                )
                return

        # 阶段2: 到了信号检查时间
        signal_target = self._hm_to_minutes(self.config.check_time)
        if (now_minutes >= signal_target
                and not signal_completed_today):
            if self.state_mgr.is_auto_signal_task_completed(
                task_date=today,
                schedule_time=self.config.check_time,
                trigger="scheduled",
            ):
                self._auto_signal_done_date = today
                return

            self._auto_signal_done_date = today

            if not self.is_data_fresh():
                self._log("⏰ 数据尚未更新，先更新数据再检查信号...")
                self.update_data(
                    auto_execute_after=bool(self.config.auto_execute),
                    schedule_context={
                        "trigger": "scheduled",
                        "task_date": today,
                        "schedule_time": self.config.data_update_time,
                    },
                )
            else:
                self._log(f"⏰ 定时触发信号检查 ({self.config.check_time})")
                self.run_signal_check(
                    auto_execute=bool(self.config.auto_execute),
                    schedule_context={
                        "trigger": "scheduled",
                        "task_date": today,
                        "schedule_time": self.config.check_time,
                    },
                )

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
