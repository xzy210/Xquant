"""
ETF rotation runtime orchestration service.

This module owns the live runtime workflow: signal-check orchestration, data
update orchestration, and automatic schedule triggering. RotationEngine keeps Qt
signals and compatibility wrappers while delegating the run loop here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Tuple

from common.execution_contract import StrategySignal

from .config import ConfigManager, RotationConfig
from .holiday_calendar import is_trading_day
from .rotation_data_service import RotationDataService
from .rotation_guard_service import RotationGuardService
from .rotation_ledger_service import RotationLedgerService
from .rotation_signal_service import RotationDecisionService, RotationSignalService
from .state_manager import RotationState, StateManager
from .trade_executor import TradeExecutor
from trading_app.services.market_data_status_service import get_market_data_status_service
from trading_app.services.strategy_spec_service import get_strategy_spec_service

logger = logging.getLogger(__name__)


class RotationRuntimeService:
    """Coordinate signal checks, data updates, and scheduled execution."""

    def __init__(
        self,
        *,
        config: RotationConfig,
        state: RotationState,
        state_mgr: StateManager,
        config_mgr: ConfigManager,
        executor: TradeExecutor,
        data_dir: Path,
        signal_service: RotationSignalService,
        decision_service: RotationDecisionService,
        guard_service: RotationGuardService,
        ledger_service: RotationLedgerService,
        auto_timer,
        update_parent=None,
        logger_fn: Optional[Callable[[str], None]] = None,
        status_fn: Optional[Callable[[str], None]] = None,
        signal_fn: Optional[Callable[[str, dict], None]] = None,
        scores_fn: Optional[Callable[[dict], None]] = None,
        notify_signal_fn: Optional[Callable[[str, dict, Optional[str], Optional[str], str], None]] = None,
        code_name_fn: Optional[Callable[[str], str]] = None,
        data_service: Optional[RotationDataService] = None,
        now_fn: Callable[[], datetime] = datetime.now,
        trading_day_fn: Callable[[object], bool] = is_trading_day,
    ) -> None:
        self.config = config
        self.state = state
        self.state_mgr = state_mgr
        self.config_mgr = config_mgr
        self.executor = executor
        self.data_dir = Path(data_dir)
        self.data_service = data_service or RotationDataService(self.data_dir)
        self.signal_service = signal_service
        self.decision_service = decision_service
        self.guard_service = guard_service
        self.ledger_service = ledger_service
        self.auto_timer = auto_timer
        self.update_parent = update_parent
        self.logger_fn = logger_fn or (lambda message: None)
        self.status_fn = status_fn or (lambda message: None)
        self.signal_fn = signal_fn or (lambda signal, result: None)
        self.scores_fn = scores_fn or (lambda scores: None)
        self.notify_signal_fn = notify_signal_fn or (
            lambda signal, scores, current, target, reason: None
        )
        self.code_name_fn = code_name_fn or (lambda code: code)
        self.now_fn = now_fn
        self.trading_day_fn = trading_day_fn

        self.auto_check_interval = 30_000
        self.auto_data_done_date = ""
        self.auto_signal_done_date = ""
        self.update_thread: Optional[object] = None
        self.update_pending_signal_check = False
        self.update_schedule_context: Optional[dict] = None

    def update_context(
        self,
        *,
        config: RotationConfig,
        state: RotationState,
        executor: TradeExecutor,
        data_dir: Optional[Path] = None,
    ) -> None:
        """Refresh mutable runtime dependencies."""
        self.config = config
        self.state = state
        self.executor = executor
        if data_dir is not None:
            self.data_dir = Path(data_dir)
            self.data_service.update_context(data_dir=self.data_dir)

    def check_live_market_data_ready(
        self,
        *,
        require_minute_freshness: bool = False,
    ) -> Tuple[bool, str]:
        """Check whether market data is ready for a live strategy run."""
        etf_codes = list(
            dict.fromkeys(
                str(code or "").strip()
                for code in self.config.etf_pool
                if str(code or "").strip()
            )
        )
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

    def run_signal_check(
        self,
        *,
        schedule_context: Optional[dict] = None,
    ) -> dict:
        """Run one full signal check and return pure strategy signals."""
        self.logger_fn("=" * 50)
        self.logger_fn(f"开始信号检查 [{self.now_fn().strftime('%Y-%m-%d %H:%M:%S')}]")
        self.status_fn("正在计算信号...")

        result = {
            "signal": "ERROR",
            "scores": {},
            "target": None,
            "reason": "",
            "executed": False,
        }

        schedule_done = False

        def finalize_schedule(status: str, error: str = "") -> None:
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

        ready, reason = self.check_live_market_data_ready()
        if not ready:
            result["signal"] = "BLOCKED"
            result["reason"] = f"行情数据未就绪: {reason}"
            self.logger_fn(f"⛔ {result['reason']}")
            self.signal_fn(result["signal"], result)
            self.state_mgr.update_check_result(result["signal"], {})
            self.status_fn(result["reason"])
            finalize_schedule("failed", result["reason"])
            self.logger_fn("=" * 50)
            return result

        try:
            if self.guard_service.in_drawdown_cooldown():
                result["signal"] = "COOLDOWN"
                result["reason"] = f"回撤保护冷却期（剩余{self.state.cooldown_remaining}天）"
                self.logger_fn(f"⏸ {result['reason']}")
                self.signal_fn(result["signal"], result)
                self.state_mgr.update_check_result(result["signal"], {})
                self.status_fn(result["reason"])
                finalize_schedule("completed")
                self.logger_fn("=" * 50)
                return result

            dd_triggered, dd_result = self.guard_service.check_drawdown_protection()
            if dd_triggered:
                result.update(dd_result)
                result["strategy_signals"] = [item.to_dict() for item in self.build_strategy_signals(result["signal"], None, result["reason"])]
                self._emit_guard_signal(result)
                self.state_mgr.update_check_result(result["signal"], {})
                finalize_schedule("completed")
                self.logger_fn("=" * 50)
                return result

            ts_triggered, ts_result = self.guard_service.check_trailing_stop()
            if ts_triggered:
                result.update(ts_result)
                result["strategy_signals"] = [item.to_dict() for item in self.build_strategy_signals(result["signal"], None, result["reason"])]
                self._emit_guard_signal(result)
                self.state_mgr.update_check_result(result["signal"], {})
                finalize_schedule("completed")
                self.logger_fn("=" * 50)
                return result

            self.guard_service.update_check_count()

            self.signal_service.update_context(config=self.config, data_dir=self.data_dir)
            scores = self.signal_service.calculate_scores()
            if not scores:
                result["reason"] = "因子得分计算失败（数据不足或加载失败）"
                self.logger_fn(f"❌ {result['reason']}")
                self.status_fn("信号检查失败")
                finalize_schedule("failed", result["reason"])
                self.logger_fn("=" * 50)
                return result

            result["scores"] = scores
            self.scores_fn(scores)

            self.decision_service.update_context(config=self.config, state=self.state)
            signal, target, reason = self.decision_service.make_decision(scores)
            signal, target, reason, filtered = self.guard_service.filter_rebalance_signal(
                signal,
                target,
                reason,
            )
            if filtered:
                self.logger_fn(f"📅 {reason}")

            result["signal"] = signal
            result["target"] = target
            result["reason"] = reason
            result["strategy_signals"] = [item.to_dict() for item in self.build_strategy_signals(signal, target, reason)]

            self.logger_fn(f"📊 信号: {signal} | 目标: {target} | 原因: {reason}")
            self.signal_fn(signal, result)
            self.state_mgr.update_check_result(signal, scores)

            if self.config.notify_on_signal:
                self.notify_signal_fn(
                    signal,
                    scores,
                    self.state.current_holding,
                    target,
                    reason,
                )

            self.status_fn(
                f"信号: {signal} "
                f"{'| 已生成执行信号' if result.get('strategy_signals') else '| 无需执行'}"
            )
            finalize_schedule("completed")

        except Exception as e:
            logger.exception("信号检查异常")
            result["reason"] = f"异常: {e}"
            self.logger_fn(f"❌ 信号检查异常: {e}")
            self.status_fn("信号检查异常")
            finalize_schedule("failed", result["reason"])

        self.ledger_service.record_daily_equity()
        self.logger_fn("=" * 50)
        return result

    def _emit_guard_signal(self, result: dict) -> None:
        """Emit status/notification for guard-generated signal results."""
        signal = str(result.get("signal") or "")
        reason = str(result.get("reason") or "")
        self.signal_fn(signal, result)
        self.status_fn(reason)
        if self.config.notify_on_signal:
            self.notify_signal_fn(signal, {}, self.state.current_holding, None, reason)

    def build_strategy_signals(
        self,
        signal: str,
        target: Optional[str],
        reason: str,
        *,
        price: Optional[float] = None,
    ) -> list[StrategySignal]:
        """Build unified StrategySignal objects from one ETF rotation decision."""
        strategy_id = str(getattr(self.config, "strategy_id", "") or "etf_rotation").strip() or "etf_rotation"
        spec = get_strategy_spec_service().get(strategy_id, fallback_name="ETF轮动策略")
        metadata = {
            "virtual_account_id": spec.virtual_account_id,
            "source": "live_strategy_center",
            "trigger": "strategy_center",
            "rotation_signal": signal,
        }
        signals: list[StrategySignal] = []
        if signal in {"SWITCH", "SELL_ALL", "DRAWDOWN_STOP", "TRAILING_STOP"} and self.state.current_holding:
            code = str(self.state.current_holding or "").strip()
            resolved_price = float(price or self.executor.get_current_price(code) or 0.0)
            signals.append(
                StrategySignal(
                    symbol=code,
                    action="sell",
                    strategy_id=strategy_id,
                    strategy_name=spec.strategy_name,
                    price=resolved_price if resolved_price > 0 else None,
                    reason=reason,
                    metadata={**metadata, "leg": "exit"},
                )
            )
        if signal in {"BUY", "SWITCH"} and target:
            resolved_price = float(price or self.executor.get_current_price(target) or 0.0)
            signals.append(
                StrategySignal(
                    symbol=target,
                    action="buy",
                    strategy_id=strategy_id,
                    strategy_name=spec.strategy_name,
                    target_percent=float(getattr(self.config, "cash_ratio", 1.0) or 1.0),
                    price=resolved_price if resolved_price > 0 else None,
                    reason=reason,
                    metadata={**metadata, "leg": "entry"},
                )
            )
        return signals

    def start_auto(self) -> None:
        """Start automatic schedule polling."""
        self.config.auto_enabled = True
        self.config_mgr.save(self.config)
        self.auto_timer.start(self.auto_check_interval)
        self.logger_fn("✅ 自动调度已启动")
        self.status_fn("自动模式运行中")

    def stop_auto(self) -> None:
        """Stop automatic schedule polling."""
        self.config.auto_enabled = False
        self.config_mgr.save(self.config)
        self.auto_timer.stop()
        self.logger_fn("⏹ 自动调度已停止")
        self.status_fn("自动模式已停止")

    def update_data(
        self,
        *,
        run_signal_check_after: bool = False,
        schedule_context: Optional[dict] = None,
    ) -> None:
        """Start asynchronous ETF data update and optionally run a signal check after it."""
        if self.update_thread and self.update_thread.isRunning():
            self.logger_fn("⚠ 数据更新正在进行中，请稍候")
            return

        self.update_pending_signal_check = bool(run_signal_check_after)
        self.update_schedule_context = dict(schedule_context or {}) if schedule_context else None
        if self.update_schedule_context:
            self.state_mgr.mark_auto_data_task(
                status="running",
                schedule_time=str(self.update_schedule_context.get("schedule_time", "") or ""),
                trigger=str(self.update_schedule_context.get("trigger", "") or ""),
                task_date=str(self.update_schedule_context.get("task_date", "") or ""),
            )
        self.logger_fn(f"🔄 开始更新 {len(self.config.etf_pool)} 只ETF数据...")
        self.status_fn("正在更新ETF数据...")

        self.update_thread = self.data_service.create_update_thread(
            self.config.etf_pool,
            parent=self.update_parent,
        )
        self.update_thread.progress.connect(self.on_update_progress)
        self.update_thread.finished_signal.connect(self.on_update_finished)
        self.update_thread.start()

    def update_data_sync(self) -> Tuple[int, int, list[str]]:
        """Synchronously update ETF data."""
        self.logger_fn(f"🔄 同步更新 {len(self.config.etf_pool)} 只ETF数据...")
        success, total, errors = self.data_service.update_pool(self.config.etf_pool)
        if errors:
            for error in errors:
                self.logger_fn(f"  ✗ {error}")
        self.logger_fn(f"✅ 数据更新完成 ({success}/{total})")
        return success, total, errors

    def is_data_fresh(self) -> bool:
        """Check whether all ETF pool data includes today's bars."""
        return self.data_service.is_pool_fresh(self.config.etf_pool)

    def on_update_progress(self, current, total, code, message) -> None:
        """Log asynchronous data update progress."""
        self.logger_fn(f"  [{current}/{total}] {self.code_name_fn(code)}: {message}")

    def on_update_finished(self, success, total, errors) -> None:
        """Handle asynchronous data update completion."""
        if errors:
            for error in errors:
                self.logger_fn(f"  ✗ {error}")
        self.logger_fn(f"✅ ETF数据更新完成 ({success}/{total})")
        self.status_fn(f"数据更新完成 ({success}/{total})")

        update_ok = not errors and int(success or 0) >= int(total or 0)

        if self.update_schedule_context:
            data_status = "completed" if update_ok else "failed"
            self.state_mgr.mark_auto_data_task(
                status=data_status,
                schedule_time=str(self.update_schedule_context.get("schedule_time", "") or ""),
                trigger=str(self.update_schedule_context.get("trigger", "") or ""),
                task_date=str(self.update_schedule_context.get("task_date", "") or ""),
                error="; ".join(str(error) for error in (errors or [])),
            )

        if not update_ok:
            error_msg = "; ".join(str(error) for error in (errors or [])) or "ETF数据更新失败"
            self.logger_fn(f"⛔ 数据未就绪，已停止本次信号检查: {error_msg}")
            self.status_fn("数据未就绪，已停止信号检查")
            self.update_pending_signal_check = False
            self.update_schedule_context = None
            return

        if self.update_pending_signal_check:
            self.update_pending_signal_check = False
            self.logger_fn("⏰ 数据已更新，开始信号检查...")
            signal_context = None
            if self.update_schedule_context:
                signal_context = {
                    "trigger": str(self.update_schedule_context.get("trigger", "") or ""),
                    "task_date": str(self.update_schedule_context.get("task_date", "") or ""),
                    "schedule_time": str(self.config.check_time or ""),
                }
            self.run_signal_check(schedule_context=signal_context)
        self.update_schedule_context = None

    @staticmethod
    def hm_to_minutes(hm: str) -> int:
        """Convert 'HH:MM' to minutes since midnight."""
        hour, minute = map(int, hm.split(":"))
        return hour * 60 + minute

    def on_auto_timer(self) -> None:
        """Poll automatic schedule and trigger data update or signal check once per day."""
        now = self.now_fn()
        if not self.trading_day_fn(now.date()):
            return

        try:
            trading_end_minutes = self.hm_to_minutes(self.config.trading_end)
        except Exception:
            trading_end_minutes = self.hm_to_minutes("14:57")
        now_minutes = now.hour * 60 + now.minute
        if now_minutes > trading_end_minutes:
            return

        today = now.strftime("%Y-%m-%d")
        data_completed_today = (
            self.auto_data_done_date == today
            or self.state_mgr.is_auto_data_task_completed(
                task_date=today,
                schedule_time=self.config.data_update_time,
                trigger="scheduled",
            )
        )
        signal_completed_today = (
            self.auto_signal_done_date == today
            or self.state_mgr.is_auto_signal_task_completed(
                task_date=today,
                schedule_time=self.config.check_time,
                trigger="scheduled",
            )
        )

        data_target = self.hm_to_minutes(self.config.data_update_time)
        if now_minutes >= data_target and not data_completed_today:
            if not self.is_data_fresh() and (
                self.update_thread is None or not self.update_thread.isRunning()
            ):
                self.auto_data_done_date = today
                self.logger_fn(f"⏰ 定时触发数据更新 ({self.config.data_update_time})")
                self.update_data(
                    run_signal_check_after=False,
                    schedule_context={
                        "trigger": "scheduled",
                        "task_date": today,
                        "schedule_time": self.config.data_update_time,
                    },
                )
                return

        signal_target = self.hm_to_minutes(self.config.check_time)
        if now_minutes >= signal_target and not signal_completed_today and bool(getattr(self.config, "auto_signal_enabled", True)):
            if self.state_mgr.is_auto_signal_task_completed(
                task_date=today,
                schedule_time=self.config.check_time,
                trigger="scheduled",
            ):
                self.auto_signal_done_date = today
                return

            self.auto_signal_done_date = today

            if not self.is_data_fresh():
                self.logger_fn("⏰ 数据尚未更新，先更新数据再检查信号...")
                self.update_data(
                    run_signal_check_after=True,
                    schedule_context={
                        "trigger": "scheduled",
                        "task_date": today,
                        "schedule_time": self.config.data_update_time,
                    },
                )
            else:
                self.logger_fn(f"⏰ 定时触发信号检查 ({self.config.check_time})")
                self.run_signal_check(
                    schedule_context={
                        "trigger": "scheduled",
                        "task_date": today,
                        "schedule_time": self.config.check_time,
                    },
                )
