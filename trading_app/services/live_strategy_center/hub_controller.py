from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional, Sequence

from PyQt6.QtCore import QObject

from trading_app.services.strategy_constants import (
    AI_STOCK_STRATEGY_ID,
    AI_STOCK_STRATEGY_NAME,
    UNMANAGED_STRATEGY_ID,
    UNMANAGED_STRATEGY_NAME,
)

logger = logging.getLogger(__name__)


class LiveStrategyHubController(QObject):
    """Non-visual orchestration for the live strategy hub."""

    CENTER_STRATEGY_ID = "center"
    CENTER_STRATEGY_NAME = "实盘策略中心"

    def __init__(
        self,
        *,
        task_service,
        hub_state_service,
        eod_service,
        strategy_adapters: Optional[Sequence[object]] = None,
        startup_orchestrator=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.task_service = task_service
        self.hub_state_service = hub_state_service
        self.eod_service = eod_service
        self.strategy_adapters = list(strategy_adapters or [])
        self.startup_orchestrator = startup_orchestrator
        self.ai_strategy_adapter = self._find_adapter(AI_STOCK_STRATEGY_ID)
        self.etf_strategy_adapter = self._find_first_non_ai_adapter()
        self._startup_message_provider: Callable[[], str] = lambda: ""

    def register_center_tasks(
        self,
        *,
        startup_action: Callable[[], None],
        morning_freshness_action: Callable[[], None],
        end_of_day_action: Callable[[], None],
        ai_task_action: Callable[[], None],
        unmanaged_scan_action: Callable[[], None],
        etf_scan_action: Callable[[], None],
        etf_execute_action: Callable[[], None],
        startup_message_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        if startup_message_provider is not None:
            self._startup_message_provider = startup_message_provider

        self.task_service.register_task(
            task_key="startup_check",
            task_type="system",
            title="启动自检",
            provider=self._task_provider_startup,
            strategy_id=self.CENTER_STRATEGY_ID,
            strategy_name=self.CENTER_STRATEGY_NAME,
            actions={"立即执行": lambda: self._run_action(startup_action, "已触发启动自检")},
        )
        self.task_service.register_task(
            task_key="morning_freshness",
            task_type="system",
            title="数据新鲜度检查",
            provider=self._task_provider_morning_freshness,
            strategy_id=self.CENTER_STRATEGY_ID,
            strategy_name=self.CENTER_STRATEGY_NAME,
            actions={"立即执行": lambda: self._run_action(morning_freshness_action, "已触发盘中新鲜度检查")},
        )
        self.task_service.register_task(
            task_key="end_of_day_cycle",
            task_type="eod",
            title="统一日终流程",
            provider=self._task_provider_end_of_day,
            strategy_id=self.CENTER_STRATEGY_ID,
            strategy_name=self.CENTER_STRATEGY_NAME,
            actions={"立即执行": lambda: self._run_action(end_of_day_action, "已触发统一日终流程")},
        )
        self.task_service.register_task(
            task_key="daily_ai_strategy_cycle",
            task_type="ai",
            title="每日 AI 策略总任务",
            provider=self._task_provider_ai_scheduler,
            strategy_id=self._adapter_strategy_id(self.ai_strategy_adapter, AI_STOCK_STRATEGY_ID),
            strategy_name=self._adapter_strategy_name(self.ai_strategy_adapter, AI_STOCK_STRATEGY_NAME),
            actions={
                "立即执行": lambda: self._run_action(ai_task_action, "已触发 AI 定时任务"),
                "暂停调度": self._pause_ai_automation,
                "恢复调度": self._resume_ai_automation,
            },
        )
        self.task_service.register_task(
            task_key="daily_unmanaged_position_scan",
            task_type="ai",
            title="未管理持仓 AI 巡检",
            provider=self._task_provider_unmanaged_ai_scheduler,
            strategy_id=UNMANAGED_STRATEGY_ID,
            strategy_name=UNMANAGED_STRATEGY_NAME,
            actions={"立即执行": lambda: self._run_action(unmanaged_scan_action, "已触发未管理持仓 AI 巡检")},
        )
        self.task_service.register_task(
            task_key="etf_rotation_auto_check",
            task_type="etf",
            title="ETF 自动轮动检查",
            provider=self._task_provider_etf_rotation,
            strategy_id=self._adapter_strategy_id(self.etf_strategy_adapter, ""),
            strategy_name=self._adapter_strategy_name(self.etf_strategy_adapter, "ETF轮动"),
            actions={
                "仅检查信号": lambda: self._run_action(etf_scan_action, "已触发 ETF 信号检查"),
                "检查并执行": lambda: self._run_action(etf_execute_action, "已触发 ETF 信号检查并执行"),
                "暂停调度": self._pause_etf_automation,
                "恢复调度": self._resume_etf_automation,
            },
        )

    def refresh_public_views(self, refreshers: Iterable[Callable[[], None]] = ()) -> str:
        self.refresh_state()
        for refresher in list(refreshers or []):
            try:
                refresher()
            except Exception as exc:
                logger.debug("刷新中心公共视图失败: %s", exc)
        return "中心公共视图已刷新"

    def toggle_center_automation(self, resume: bool) -> str:
        message = self.resume_center_automation() if resume else self.pause_center_automation()
        self.refresh_state()
        return message

    def pause_center_automation(self) -> str:
        messages = [self._call_adapter_text(adapter, "pause_automation") for adapter in self.strategy_adapters]
        return "；".join([item for item in messages if item])

    def resume_center_automation(self) -> str:
        messages = [self._call_adapter_text(adapter, "resume_automation") for adapter in self.strategy_adapters]
        return "；".join([item for item in messages if item])

    def refresh_state(self) -> None:
        try:
            self.hub_state_service.refresh_state()
        except Exception as exc:
            logger.debug("刷新中心状态失败: %s", exc)

    def refresh_strategies_after_eod(self) -> None:
        for adapter in list(self.strategy_adapters or []):
            try:
                adapter.refresh_after_eod()
            except Exception as exc:
                logger.debug("刷新策略日终 UI 失败 strategy_id=%s err=%s", getattr(adapter, "strategy_id", ""), exc)

    def collect_rotation_pool(self) -> list[str]:
        pool: list[str] = []
        for adapter in list(self.strategy_adapters or []):
            try:
                symbols = adapter.get_rotation_pool()
            except Exception as exc:
                logger.debug("读取策略轮动池失败 strategy_id=%s err=%s", getattr(adapter, "strategy_id", ""), exc)
                symbols = []
            for symbol in list(symbols or []):
                if symbol and symbol not in pool:
                    pool.append(symbol)
        return pool

    def sync_rotation_pool(self) -> None:
        if self.eod_service is None:
            return
        self.eod_service.set_rotation_etf_pool(self.collect_rotation_pool())

    def _task_provider_startup(self) -> dict:
        return {
            "status": "running" if bool(getattr(self.startup_orchestrator, "is_running", False)) else "idle",
            "message": self._safe_startup_message(),
            "last_run": "",
            "schedule_time": "启动后自动 / 手动触发",
        }

    @staticmethod
    def _task_provider_morning_freshness() -> dict:
        return {
            "status": "scheduled",
            "message": "交易日 09:35 自动检查",
            "schedule_time": "09:35",
        }

    def _task_provider_end_of_day(self) -> dict:
        cycle_state = self._get_eod_cycle_state()
        return {
            "status": str(cycle_state.get("status", "") or "idle"),
            "message": str(cycle_state.get("last_error", "") or cycle_state.get("updated_at", "") or ""),
            "last_run": str(cycle_state.get("completed_at", "") or cycle_state.get("updated_at", "") or ""),
            "schedule_time": "收盘后 / 手动触发",
        }

    def _task_provider_ai_scheduler(self) -> dict:
        adapter = self.ai_strategy_adapter
        if adapter is None:
            return {}
        try:
            rows = adapter.get_task_summaries()
        except Exception as exc:
            logger.debug("读取 AI 策略任务失败: %s", exc)
            rows = []
        return dict(rows[0] if rows else {})

    def _task_provider_unmanaged_ai_scheduler(self) -> dict:
        adapter = self.ai_strategy_adapter
        if adapter is None:
            return {}
        try:
            return dict(adapter.get_task_summary("daily_unmanaged_position_scan") or {})
        except Exception as exc:
            logger.debug("读取未管理持仓巡检任务失败: %s", exc)
            return {}

    def _task_provider_etf_rotation(self) -> dict:
        adapter = self.etf_strategy_adapter
        if adapter is None:
            return {}
        try:
            rows = adapter.get_task_summaries()
        except Exception as exc:
            logger.debug("读取 ETF 轮动任务失败: %s", exc)
            rows = []
        return dict(rows[0] if rows else {})

    def _pause_ai_automation(self) -> str:
        return self._call_adapter_text(self.ai_strategy_adapter, "pause_automation")

    def _resume_ai_automation(self) -> str:
        return self._call_adapter_text(self.ai_strategy_adapter, "resume_automation")

    def _pause_etf_automation(self) -> str:
        return self._call_adapter_text(self.etf_strategy_adapter, "pause_automation")

    def _resume_etf_automation(self) -> str:
        return self._call_adapter_text(self.etf_strategy_adapter, "resume_automation")

    @staticmethod
    def _run_action(callback: Callable[[], None], message: str) -> str:
        callback()
        return message

    def _safe_startup_message(self) -> str:
        try:
            return str(self._startup_message_provider() or "")
        except Exception:
            return ""

    def _get_eod_cycle_state(self) -> dict:
        method = getattr(self.eod_service, "_get_cycle_state", None)
        if not callable(method):
            return {}
        try:
            return dict(method() or {})
        except Exception as exc:
            logger.debug("读取日终状态失败: %s", exc)
            return {}

    def _find_adapter(self, strategy_id: str):
        target = str(strategy_id or "").strip()
        for adapter in self.strategy_adapters:
            if str(getattr(adapter, "strategy_id", "") or "").strip() == target:
                return adapter
        return None

    def _find_first_non_ai_adapter(self):
        for adapter in self.strategy_adapters:
            strategy_id = str(getattr(adapter, "strategy_id", "") or "").strip()
            if strategy_id and strategy_id not in {AI_STOCK_STRATEGY_ID, UNMANAGED_STRATEGY_ID}:
                return adapter
        return None

    @staticmethod
    def _adapter_strategy_id(adapter, default: str = "") -> str:
        return str(getattr(adapter, "strategy_id", "") or default or "").strip()

    @staticmethod
    def _adapter_strategy_name(adapter, default: str = "") -> str:
        return str(getattr(adapter, "strategy_name", "") or default or "").strip()

    @staticmethod
    def _call_adapter_text(adapter, method_name: str) -> str:
        if adapter is None:
            return ""
        method = getattr(adapter, method_name, None)
        if not callable(method):
            return ""
        try:
            return str(method() or "")
        except Exception as exc:
            logger.warning("执行策略控制失败 strategy_id=%s method=%s err=%s", getattr(adapter, "strategy_id", ""), method_name, exc)
            return f"{getattr(adapter, 'strategy_name', '策略')} 操作失败: {exc}"
