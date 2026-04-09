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
    _CYCLE_STATE_KEY = "live_strategy_center_eod"

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
        self._suppress_shared_reconcile_callback = False
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

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _get_cycle_state(self, snapshot_date: Optional[str] = None) -> dict:
        return self.daily_auto_trade.get_day_state_section(
            self._CYCLE_STATE_KEY,
            day=snapshot_date or self._today(),
        )

    def _update_cycle_state(self, snapshot_date: Optional[str] = None, **fields) -> None:
        self.daily_auto_trade.update_day_state_section(
            self._CYCLE_STATE_KEY,
            day=snapshot_date or self._today(),
            **fields,
        )

    def _update_cycle_phase(
        self,
        *,
        phase_key: str,
        status: str,
        snapshot_date: Optional[str] = None,
        trigger: str = "",
        message: str = "",
        details: Optional[dict] = None,
    ) -> None:
        target_day = snapshot_date or self._today()
        cycle_state = self._get_cycle_state(target_day)
        phases = dict(cycle_state.get("phases", {}) or {})
        phase_state = dict(phases.get(phase_key, {}) or {})
        now = self._now()
        phase_state.update({
            "status": status,
            "message": message,
            "updated_at": now,
        })
        if trigger:
            phase_state["trigger"] = trigger
        if details is not None:
            phase_state["details"] = dict(details or {})
        if status == "running":
            phase_state["started_at"] = now
            phase_state["completed_at"] = ""
        else:
            phase_state.setdefault("started_at", now)
            phase_state["completed_at"] = now
        phases[phase_key] = phase_state
        self._update_cycle_state(
            target_day,
            phases=phases,
            last_trigger=trigger or cycle_state.get("last_trigger", ""),
            updated_at=now,
        )
        self._refresh_cycle_overall_state(snapshot_date=target_day, trigger=trigger)

    def _refresh_cycle_overall_state(self, *, snapshot_date: Optional[str] = None, trigger: str = "") -> dict:
        target_day = snapshot_date or self._today()
        cycle_state = self._get_cycle_state(target_day)
        phases = dict(cycle_state.get("phases", {}) or {})
        reconcile_state = self.daily_auto_trade.get_day_state_section("reconcile", day=target_day)
        phase0_status = str((phases.get("phase0_kline_refresh", {}) or {}).get("status", "") or "")
        phase2_status = str((phases.get("phase2_strategy_hooks", {}) or {}).get("status", "") or "")
        shared_status = str(reconcile_state.get("status", "") or "")

        if "running" in {phase0_status, phase2_status}:
            overall_status = "running"
        elif shared_status == "completed" and phase0_status == "completed" and phase2_status == "completed":
            overall_status = "completed"
        elif phase0_status == "failed" or phase2_status == "failed":
            overall_status = "partial_failed"
        elif shared_status == "completed" or phase0_status or phase2_status:
            overall_status = "partial"
        else:
            overall_status = str(cycle_state.get("status", "") or "")

        payload = {
            "status": overall_status,
            "updated_at": self._now(),
            "shared_reconcile_status": shared_status,
        }
        if trigger:
            payload["last_trigger"] = trigger
        if overall_status == "completed":
            payload["completed_at"] = self._now()
            payload["last_error"] = ""
        elif overall_status in ("partial_failed", "failed"):
            payload["last_error"] = str((phases.get("phase2_strategy_hooks", {}) or {}).get("message", "") or "")
        self._update_cycle_state(target_day, **payload)
        merged = self._get_cycle_state(target_day)
        merged["shared_reconcile_status"] = shared_status
        return merged

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
        snapshot_date = self._today()
        self.status_changed.emit("开始执行中心统一日终收尾...")
        self._update_cycle_state(
            snapshot_date,
            status="running",
            started_at=self._now(),
            completed_at="",
            last_trigger="manual",
            last_error="",
        )

        self._update_cycle_phase(
            phase_key="phase0_kline_refresh",
            status="running",
            snapshot_date=snapshot_date,
            trigger="manual",
            message="开始全量K线数据刷新",
        )
        refresh_ok, refresh_msg = self._run_kline_full_refresh()
        self._update_cycle_phase(
            phase_key="phase0_kline_refresh",
            status="completed" if refresh_ok else "failed",
            snapshot_date=snapshot_date,
            trigger="manual",
            message=refresh_msg,
        )
        if not refresh_ok:
            logger.warning("K线全量刷新有部分失败，继续执行对账: %s", refresh_msg)

        self.status_changed.emit("Phase 1: 执行共享日终对账...")
        self._suppress_shared_reconcile_callback = True
        try:
            success, message = self.daily_auto_trade.run_end_of_day_reconcile(slot="manual")
        finally:
            self._suppress_shared_reconcile_callback = False
        payload = self._build_cycle_payload(shared_success=success, shared_message=message, strategy_results={})
        if not success:
            final_message = f"共享日终对账失败: {message}"
            self._update_cycle_state(
                snapshot_date,
                status="failed",
                completed_at=self._now(),
                last_trigger="manual",
                last_error=final_message,
            )
            self.status_changed.emit(final_message)
            self.cycle_finished.emit(False, final_message, payload)
            return False, final_message, payload
        return self._run_strategy_hooks(shared_message=message, trigger="manual")

    def run_catchup_if_needed(self) -> tuple[bool, str]:
        snapshot_date = self._today()
        cycle_state = self._get_cycle_state(snapshot_date)
        phases = dict(cycle_state.get("phases", {}) or {})
        phase0_status = str((phases.get("phase0_kline_refresh", {}) or {}).get("status", "") or "")
        phase2_status = str((phases.get("phase2_strategy_hooks", {}) or {}).get("status", "") or "")
        reconcile_state = self.daily_auto_trade.get_day_state_section("reconcile", day=snapshot_date)
        reconcile_status = str(reconcile_state.get("status", "") or "")

        should_run, reason = self.daily_auto_trade.should_run_reconcile_catchup()
        logger.info("检查是否需要补跑日终流程: should_run=%s reason=%s", should_run, reason)
        if not should_run and not (
            reconcile_status == "completed"
            and (phase0_status != "completed" or phase2_status != "completed")
        ):
            return False, reason
        self._update_cycle_state(
            snapshot_date,
            status="running",
            started_at=cycle_state.get("started_at", "") or self._now(),
            completed_at="",
            last_trigger="catchup",
        )
        if not should_run and reconcile_status == "completed":
            shared_message = str(reconcile_state.get("summary_message", "") or "今日日终对账已完成")
            if phase0_status != "completed":
                self.status_changed.emit("检测到全量K线刷新缺失，开始补跑...")
                logger.info("补跑日终流程继续: phase=kline_full_refresh_only")
                self._update_cycle_phase(
                    phase_key="phase0_kline_refresh",
                    status="running",
                    snapshot_date=snapshot_date,
                    trigger="catchup",
                    message="开始补跑全量K线数据刷新",
                )
                refresh_ok, refresh_msg = self._run_kline_full_refresh()
                self._update_cycle_phase(
                    phase_key="phase0_kline_refresh",
                    status="completed" if refresh_ok else "failed",
                    snapshot_date=snapshot_date,
                    trigger="catchup",
                    message=refresh_msg,
                )
                if not refresh_ok:
                    shared_message = f"{shared_message} | K线刷新部分失败: {refresh_msg}"
                else:
                    logger.info("补跑模式下 K线全量刷新完成: %s", refresh_msg)
            if phase2_status != "completed":
                logger.info("补跑日终流程继续: phase=strategy_hooks_only")
                final_success, final_message, _ = self._run_strategy_hooks(shared_message=shared_message, trigger="catchup")
                logger.info("补跑日终流程结束: success=%s message=%s", final_success, final_message)
                return final_success, final_message
            refreshed = self._refresh_cycle_overall_state(snapshot_date=snapshot_date, trigger="catchup")
            if str(refreshed.get("status", "") or "") == "completed":
                return False, "今日日终流程已完成"
            return True, "已补齐缺失的日终流程阶段"
        self.status_changed.emit("检测到缺失的日终流程，优先执行共享对账...")
        logger.info("补跑日终流程开始: phase=shared_reconcile")

        self._suppress_shared_reconcile_callback = True
        try:
            success, message = self.daily_auto_trade.run_reconcile_catchup_if_needed()
        finally:
            self._suppress_shared_reconcile_callback = False
        if not success:
            logger.warning("补跑共享对账失败: %s", message)
            self._update_cycle_state(
                snapshot_date,
                status="failed",
                completed_at=self._now(),
                last_trigger="catchup",
                last_error=message,
            )
            self.status_changed.emit(message)
            return False, message

        self.status_changed.emit("共享对账已完成，继续补跑全量K线刷新...")
        logger.info("补跑共享对账完成: %s", message)
        logger.info("补跑日终流程继续: phase=kline_full_refresh")
        self._update_cycle_phase(
            phase_key="phase0_kline_refresh",
            status="running",
            snapshot_date=snapshot_date,
            trigger="catchup",
            message="开始补跑全量K线数据刷新",
        )
        refresh_ok, refresh_msg = self._run_kline_full_refresh()
        self._update_cycle_phase(
            phase_key="phase0_kline_refresh",
            status="completed" if refresh_ok else "failed",
            snapshot_date=snapshot_date,
            trigger="catchup",
            message=refresh_msg,
        )
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
        if self._suppress_shared_reconcile_callback:
            logger.info("跳过共享对账完成回调：由手动/补跑流程接管后续阶段")
            return
        if not success:
            final_message = f"共享日终对账失败: {message}"
            self._update_cycle_state(
                self._today(),
                status="failed",
                completed_at=self._now(),
                last_trigger="scheduled",
                last_error=final_message,
            )
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
        self._update_cycle_state(
            snapshot_date,
            status="running",
            started_at=self._get_cycle_state(snapshot_date).get("started_at", "") or self._now(),
            completed_at="",
            last_trigger=trigger,
        )
        self._update_cycle_phase(
            phase_key="phase2_strategy_hooks",
            status="running",
            snapshot_date=snapshot_date,
            trigger=trigger,
            message="开始执行策略日终钩子",
        )
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
        self._update_cycle_phase(
            phase_key="phase2_strategy_hooks",
            status="completed" if all_success else "failed",
            snapshot_date=snapshot_date,
            trigger=trigger,
            message=final_message,
            details=payload,
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
