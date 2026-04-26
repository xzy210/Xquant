"""AI 实盘决策定时调度引擎
支持每日定时执行持仓巡检、自选巡检，并在完成后发送通知。
通过 QTimer 实现轻量级调度，配置持久化到 JSON 文件。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .daily_auto_trade_service import DailyAutoTradeService, get_daily_auto_trade_service
from live_rotation.holiday_calendar import is_trading_day

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "ai_scheduler_config.json"


@dataclass
class ScheduledAITask:
    task_id: str
    name: str
    enabled: bool = False
    time: str = "08:50"
    task_type: str = "ai_strategy_cycle"  # ai_strategy_cycle | position_scan | candidate_pool_scan | unmanaged_position_scan
    watchlist_group: str = ""
    model_name: str = ""
    notify_on_complete: bool = True
    auto_execute: bool = False
    last_run: str = ""
    last_result: str = ""


class AIDecisionScheduler(QObject):
    """Manages scheduled AI decision tasks."""

    task_triggered = pyqtSignal(str, dict)  # task_id, task_config dict
    task_log = pyqtSignal(str)

    def __init__(self, parent=None, daily_auto_trade: Optional[DailyAutoTradeService] = None):
        super().__init__(parent)
        self._tasks: Dict[str, ScheduledAITask] = {}
        self._timers: Dict[str, QTimer] = {}
        self.daily_auto_trade = daily_auto_trade or get_daily_auto_trade_service()
        self._load_config()
        self._setup_timers()

    def _config_path(self) -> Path:
        return _CONFIG_PATH

    def _load_config(self):
        path = self._config_path()
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            legacy_position = None
            legacy_candidate = None
            for tid, td in data.get("tasks", {}).items():
                if str(td.get("task_type", "") or "") == "watchlist_scan":
                    continue
                if tid == "daily_position_scan":
                    legacy_position = td
                    continue
                if tid == "daily_candidate_pool_scan":
                    legacy_candidate = td
                    continue
                self._tasks[tid] = ScheduledAITask(task_id=tid, **{
                    k: v for k, v in td.items() if k != "task_id"
                })
            if "daily_ai_strategy_cycle" not in self._tasks:
                merged = self._merge_legacy_strategy_tasks(legacy_position, legacy_candidate)
                if merged is not None:
                    self._tasks["daily_ai_strategy_cycle"] = merged
        except Exception as exc:
            logger.error("Failed to load AI scheduler config: %s", exc)

    @staticmethod
    def _merge_legacy_strategy_tasks(position_task: dict | None, candidate_task: dict | None) -> ScheduledAITask | None:
        if not position_task and not candidate_task:
            return None
        source = candidate_task or position_task or {}
        enabled = bool((position_task or {}).get("enabled", False) or (candidate_task or {}).get("enabled", False))
        auto_execute = bool((position_task or {}).get("auto_execute", False) or (candidate_task or {}).get("auto_execute", False))
        time_value = str((candidate_task or {}).get("time", "") or (position_task or {}).get("time", "") or "14:35")
        last_run = str((candidate_task or {}).get("last_run", "") or (position_task or {}).get("last_run", "") or "")
        last_result = str((candidate_task or {}).get("last_result", "") or (position_task or {}).get("last_result", "") or "")
        return ScheduledAITask(
            task_id="daily_ai_strategy_cycle",
            name="每日AI实盘决策任务",
            enabled=enabled,
            time=time_value,
            task_type="ai_strategy_cycle",
            model_name=str(source.get("model_name", "") or ""),
            notify_on_complete=bool(source.get("notify_on_complete", True)),
            auto_execute=auto_execute,
            last_run=last_run,
            last_result=last_result,
        )

    def _save_config(self):
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = {"tasks": {t.task_id: asdict(t) for t in self._tasks.values()}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("Failed to save AI scheduler config: %s", exc)

    def _setup_timers(self):
        for tid, task in self._tasks.items():
            if task.enabled:
                self._setup_single_timer(tid, task)

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _compute_next_target(self, task: ScheduledAITask) -> Optional[datetime]:
        try:
            hour, minute = map(int, task.time.split(":"))
        except ValueError:
            logger.error("Invalid time format for task %s: %s", task.task_id, task.time)
            return None
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now + timedelta(seconds=1):
            target += timedelta(days=1)
        while not is_trading_day(target.date()):
            target += timedelta(days=1)
        return target

    def _record_scheduler_runtime(
        self,
        task: ScheduledAITask,
        *,
        trigger_source: str,
        triggered_at: str,
        next_run_at: str = "",
        dispatch_status: str = "",
        dispatch_message: str = "",
    ) -> None:
        task_state = self.daily_auto_trade.get_task_state_for_day(task.task_id)
        scheduler_meta = dict(task_state.get("scheduler_meta", {}) or {})
        scheduler_meta.update({
            "task_name": task.name,
            "task_type": task.task_type,
            "scheduled_time": task.time,
            "trigger_source": trigger_source,
            "triggered_at": triggered_at,
            "enabled": bool(task.enabled),
            "auto_execute": bool(task.auto_execute),
            "model_name": str(task.model_name or ""),
            "notify_on_complete": bool(task.notify_on_complete),
            "updated_at": self._now(),
        })
        if next_run_at:
            scheduler_meta["next_run_at"] = next_run_at
        if dispatch_status:
            scheduler_meta["dispatch_status"] = dispatch_status
        if dispatch_message:
            scheduler_meta["dispatch_message"] = dispatch_message
        self.daily_auto_trade.update_task_state_for_day(
            task.task_id,
            scheduler_meta=scheduler_meta,
            task_name=task.name,
            task_type=task.task_type,
            scheduled_time=task.time,
            trigger_source=trigger_source,
            trigger_time=triggered_at,
            trigger_mode="auto_scheduler",
        )

    def mark_task_dispatch(self, task_id: str, dispatch_status: str, dispatch_message: str = "") -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task_state = self.daily_auto_trade.get_task_state_for_day(task_id)
        scheduler_meta = dict(task_state.get("scheduler_meta", {}) or {})
        scheduler_meta.update({
            "dispatch_status": dispatch_status,
            "dispatch_message": dispatch_message,
            "updated_at": self._now(),
        })
        self.daily_auto_trade.update_task_state_for_day(
            task_id,
            scheduler_meta=scheduler_meta,
        )

    def get_task_runtime_display(self, task_id: str) -> Dict[str, str]:
        task = self._tasks.get(task_id)
        fallback_run = str(getattr(task, "last_run", "") or "") if task else ""
        fallback_result = str(getattr(task, "last_result", "") or "") if task else ""

        latest_state = self.daily_auto_trade.get_latest_task_state(task_id)
        if not latest_state:
            return {
                "last_run": fallback_run,
                "last_result": fallback_result,
                "source": "config",
            }

        scheduler_meta = dict(latest_state.get("scheduler_meta", {}) or {})
        last_run = (
            str(scheduler_meta.get("triggered_at", "") or "")
            or str(latest_state.get("trigger_time", "") or "")
            or str(latest_state.get("started_at", "") or "")
            or fallback_run
        )
        last_result = (
            str(scheduler_meta.get("last_result_text", "") or "")
            or str(latest_state.get("error", "") or "")
            or str(latest_state.get("status", "") or "")
            or fallback_result
        )
        return {
            "last_run": last_run,
            "last_result": last_result,
            "source": "structured_state",
        }

    def _setup_single_timer(self, task_id: str, task: ScheduledAITask):
        if task_id in self._timers:
            self._timers[task_id].stop()
        target = self._compute_next_target(task)
        if target is None:
            return
        now = datetime.now()
        ms_until = int((target - now).total_seconds() * 1000)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda tid=task_id: self._on_timer(tid))
        timer.start(ms_until)
        self._timers[task_id] = timer
        logger.info("AI task '%s' scheduled at %s (in %d s)", task_id, target, ms_until // 1000)
        return target

    def _on_timer(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        if not is_trading_day(datetime.now().date()):
            logger.info("AI task '%s' skipped on non-trading day", task_id)
            self._setup_single_timer(task_id, task)
            return
        task.last_run = self._now()
        self._save_config()
        logger.info("AI task '%s' triggered at %s", task_id, task.last_run)
        self.task_log.emit(f"[调度] 定时任务「{task.name}」已触发 ({task.last_run})")
        next_target = self._setup_single_timer(task_id, task)
        self._record_scheduler_runtime(
            task,
            trigger_source="scheduled",
            triggered_at=task.last_run,
            next_run_at=next_target.strftime("%Y-%m-%d %H:%M:%S") if next_target else "",
            dispatch_status="triggered",
            dispatch_message="定时任务已触发，等待进入执行编排",
        )
        self.task_triggered.emit(task_id, asdict(task))

    # ── Public API ──

    def get_tasks(self) -> Dict[str, ScheduledAITask]:
        return dict(self._tasks)

    def add_or_update_task(self, task: ScheduledAITask):
        self._tasks[task.task_id] = task
        self._save_config()
        if task_id_timer := self._timers.pop(task.task_id, None):
            task_id_timer.stop()
        if task.enabled:
            self._setup_single_timer(task.task_id, task)

    def remove_task(self, task_id: str):
        self._tasks.pop(task_id, None)
        if timer := self._timers.pop(task_id, None):
            timer.stop()
        self._save_config()

    def toggle_task(self, task_id: str, enabled: bool):
        task = self._tasks.get(task_id)
        if not task:
            return
        task.enabled = enabled
        self._save_config()
        if enabled:
            self._setup_single_timer(task_id, task)
        elif task_id in self._timers:
            self._timers[task_id].stop()
            del self._timers[task_id]

    def run_now(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        task.last_run = self._now()
        self._save_config()
        next_target = self._compute_next_target(task)
        self._record_scheduler_runtime(
            task,
            trigger_source="manual",
            triggered_at=task.last_run,
            next_run_at=next_target.strftime("%Y-%m-%d %H:%M:%S") if next_target else "",
            dispatch_status="triggered",
            dispatch_message="手动触发调度任务，等待进入执行编排",
        )
        self.task_triggered.emit(task_id, asdict(task))

    def mark_task_result(self, task_id: str, result: str, dispatch_status: str = ""):
        task = self._tasks.get(task_id)
        if task:
            task.last_result = result
            self._save_config()
            task_state = self.daily_auto_trade.get_task_state_for_day(task_id)
            scheduler_meta = dict(task_state.get("scheduler_meta", {}) or {})
            scheduler_meta.update({
                "last_result_text": result,
                "last_result_at": self._now(),
                "updated_at": self._now(),
            })
            if dispatch_status:
                scheduler_meta["dispatch_status"] = dispatch_status
            self.daily_auto_trade.update_task_state_for_day(
                task_id,
                scheduler_meta=scheduler_meta,
                last_result_text=result,
                last_result_at=self._now(),
            )

    def stop(self):
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()

    def ensure_defaults(self):
        """Create default tasks if none exist."""
        changed = False
        for legacy_id in ("daily_position_scan", "daily_candidate_pool_scan", "daily_watchlist_scan"):
            if legacy_id in self._tasks:
                self._tasks.pop(legacy_id, None)
                changed = True
        if "daily_ai_strategy_cycle" not in self._tasks:
            self._tasks["daily_ai_strategy_cycle"] = ScheduledAITask(
                task_id="daily_ai_strategy_cycle",
            name="每日AI实盘决策任务",
                enabled=False,
                time="14:35",
                task_type="ai_strategy_cycle",
                notify_on_complete=True,
                auto_execute=True,
            )
            changed = True
        if "daily_unmanaged_position_scan" not in self._tasks:
            self._tasks["daily_unmanaged_position_scan"] = ScheduledAITask(
                task_id="daily_unmanaged_position_scan",
                name="未管理持仓AI巡检",
                enabled=False,
                time="14:40",
                task_type="unmanaged_position_scan",
                notify_on_complete=True,
                auto_execute=False,
            )
            changed = True
        if not changed:
            return
        self._save_config()
