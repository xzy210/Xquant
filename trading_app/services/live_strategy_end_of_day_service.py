from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .daily_auto_trade_service import DailyAutoTradeService, get_daily_auto_trade_service
from .kline_full_refresh_service import KlineFullRefreshService

logger = logging.getLogger(__name__)


@dataclass
class StrategyEndOfDayResult:
    strategy_id: str
    strategy_name: str
    success: bool
    message: str
    details: Dict[str, object] = field(default_factory=dict)


class LiveStrategyEndOfDayService(QObject):
    """Coordinate shared end-of-day reconcile and per-strategy post-close hooks.

    Lifecycle:
      Phase 0  – full K-line refresh (前复权)
      Phase 1  – shared reconciliation (对账 / PnL snapshot)
      Phase 2  – per-strategy post-close hooks
    """

    status_changed = pyqtSignal(str)
    cycle_finished = pyqtSignal(bool, str, dict)

    def __init__(
        self,
        daily_auto_trade: Optional[DailyAutoTradeService] = None,
        parent=None,
        *,
        rotation_etf_pool: Optional[List[str]] = None,
    ) -> None:
        super().__init__(parent)
        self.daily_auto_trade = daily_auto_trade or get_daily_auto_trade_service()
        self._strategy_hooks: Dict[str, Callable[[str], StrategyEndOfDayResult]] = {}
        self._strategy_names: Dict[str, str] = {}
        self._rotation_etf_pool: List[str] = list(rotation_etf_pool or [])
        self.daily_auto_trade.reconcile_finished.connect(self._on_shared_reconcile_finished)

    def set_rotation_etf_pool(self, pool: List[str]) -> None:
        self._rotation_etf_pool = list(pool or [])

    def register_strategy(
        self,
        strategy_id: str,
        strategy_name: str,
        hook: Callable[[str], StrategyEndOfDayResult],
    ) -> None:
        self._strategy_hooks[strategy_id] = hook
        self._strategy_names[strategy_id] = strategy_name

    # ------------------------------------------------------------------
    # Phase 0 – full K-line refresh
    # ------------------------------------------------------------------

    def _run_kline_full_refresh(self) -> tuple[bool, str]:
        """Phase 0: full (前复权) K-line refresh for all assets."""
        self.status_changed.emit("Phase 0: 开始全量K线数据刷新...")
        svc = KlineFullRefreshService(rotation_etf_pool=self._rotation_etf_pool)
        ok, summary = svc.run_full_refresh(status_cb=lambda msg: self.status_changed.emit(msg))
        return ok, summary

    # ------------------------------------------------------------------
    # Public entry-points
    # ------------------------------------------------------------------

    def run_manual_cycle(self) -> tuple[bool, str, dict]:
        self.status_changed.emit("开始执行中心统一日终收尾...")

        refresh_ok, refresh_msg = self._run_kline_full_refresh()
        if not refresh_ok:
            logger.warning("K线全量刷新有部分失败，继续执行对账: %s", refresh_msg)

        self.status_changed.emit("Phase 1: 执行共享日终对账...")
        success, message = self.daily_auto_trade.run_end_of_day_reconcile(slot="manual")
        payload = self._build_cycle_payload(shared_success=success, shared_message=message, strategy_results={})
        if not success:
            final_message = f"共享日终对账失败: {message}"
            self.status_changed.emit(final_message)
            self.cycle_finished.emit(False, final_message, payload)
            return False, final_message, payload
        return self._run_strategy_hooks(shared_message=message, trigger="manual")

    def run_catchup_if_needed(self) -> tuple[bool, str]:
        should_run, reason = self.daily_auto_trade.should_run_reconcile_catchup()
        logger.info("检查是否需要补跑日终流程: should_run=%s reason=%s", should_run, reason)
        if not should_run:
            return False, reason
        self.status_changed.emit("检测到缺失的日终流程，优先执行共享对账...")
        logger.info("补跑日终流程开始: phase=shared_reconcile")

        success, message = self.daily_auto_trade.run_reconcile_catchup_if_needed()
        if not success:
            logger.warning("补跑共享对账失败: %s", message)
            self.status_changed.emit(message)
            return False, message

        self.status_changed.emit("共享对账已完成，继续补跑全量K线刷新...")
        logger.info("补跑共享对账完成: %s", message)
        logger.info("补跑日终流程继续: phase=kline_full_refresh")
        refresh_ok, refresh_msg = self._run_kline_full_refresh()
        shared_message = message
        if not refresh_ok:
            logger.warning("补跑模式下 K线全量刷新有部分失败: %s", refresh_msg)
            shared_message = f"{message} | K线刷新部分失败: {refresh_msg}"
        else:
            logger.info("补跑模式下 K线全量刷新完成: %s", refresh_msg)

        logger.info("补跑日终流程继续: phase=strategy_hooks")
        final_success, final_message, _ = self._run_strategy_hooks(shared_message=shared_message, trigger="catchup")
        logger.info("补跑日终流程结束: success=%s message=%s", final_success, final_message)
        return final_success, final_message

    def _on_shared_reconcile_finished(self, success: bool, message: str) -> None:
        if not success:
            final_message = f"共享日终对账失败: {message}"
            payload = self._build_cycle_payload(shared_success=False, shared_message=message, strategy_results={})
            self.status_changed.emit(final_message)
            self.cycle_finished.emit(False, final_message, payload)
            return
        self._run_strategy_hooks(shared_message=message, trigger="scheduled")

    def _run_strategy_hooks(self, *, shared_message: str, trigger: str) -> tuple[bool, str, dict]:
        snapshot_date = datetime.now().strftime("%Y-%m-%d")
        strategy_results: Dict[str, Dict[str, object]] = {}
        all_success = True
        summary_parts = [shared_message]
        logger.info("开始执行策略日终钩子: trigger=%s strategy_count=%d", trigger, len(self._strategy_hooks))

        for strategy_id, hook in self._strategy_hooks.items():
            strategy_name = self._strategy_names.get(strategy_id, strategy_id)
            logger.info("执行策略日终钩子: trigger=%s strategy_id=%s strategy_name=%s", trigger, strategy_id, strategy_name)
            try:
                result = hook(snapshot_date)
            except Exception as exc:
                logger.exception("Strategy end-of-day hook failed: %s", strategy_id)
                result = StrategyEndOfDayResult(
                    strategy_id=strategy_id,
                    strategy_name=strategy_name,
                    success=False,
                    message=f"{strategy_name} 日终流程异常: {exc}",
                )
            logger.info(
                "策略日终钩子完成: trigger=%s strategy_id=%s success=%s message=%s",
                trigger,
                strategy_id,
                result.success,
                result.message,
            )
            strategy_results[strategy_id] = {
                "strategy_name": result.strategy_name,
                "success": result.success,
                "message": result.message,
                "details": dict(result.details or {}),
            }
            all_success = all_success and result.success
            summary_parts.append(f"{result.strategy_name}: {result.message}")

        final_message = " | ".join(summary_parts)
        payload = self._build_cycle_payload(
            shared_success=True,
            shared_message=shared_message,
            strategy_results=strategy_results,
            trigger=trigger,
        )
        logger.info("策略日终钩子汇总完成: trigger=%s success=%s", trigger, all_success)
        self.status_changed.emit(final_message)
        self.cycle_finished.emit(all_success, final_message, payload)
        return all_success, final_message, payload

    @staticmethod
    def _build_cycle_payload(
        *,
        shared_success: bool,
        shared_message: str,
        strategy_results: Dict[str, Dict[str, object]],
        trigger: str = "",
    ) -> dict:
        return {
            "shared_reconcile": {
                "success": shared_success,
                "message": shared_message,
            },
            "strategy_results": strategy_results,
            "trigger": trigger,
        }
