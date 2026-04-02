"""AI 决策定时调度引擎

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

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "ai_scheduler_config.json"


@dataclass
class ScheduledAITask:
    task_id: str
    name: str
    enabled: bool = False
    time: str = "08:50"
    task_type: str = "ai_strategy_cycle"  # ai_strategy_cycle | position_scan | candidate_pool_scan
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: Dict[str, ScheduledAITask] = {}
        self._timers: Dict[str, QTimer] = {}
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
            name="每日AI策略总任务",
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

    def _setup_single_timer(self, task_id: str, task: ScheduledAITask):
        if task_id in self._timers:
            self._timers[task_id].stop()
        try:
            hour, minute = map(int, task.time.split(":"))
        except ValueError:
            logger.error("Invalid time format for task %s: %s", task_id, task.time)
            return
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now + timedelta(seconds=1):
            target += timedelta(days=1)
        ms_until = int((target - now).total_seconds() * 1000)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda tid=task_id: self._on_timer(tid))
        timer.start(ms_until)
        self._timers[task_id] = timer
        logger.info("AI task '%s' scheduled at %s (in %d s)", task_id, target, ms_until // 1000)

    def _on_timer(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        task.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_config()
        logger.info("AI task '%s' triggered at %s", task_id, task.last_run)
        self.task_log.emit(f"[调度] 定时任务「{task.name}」已触发 ({task.last_run})")
        self.task_triggered.emit(task_id, asdict(task))
        self._setup_single_timer(task_id, task)

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
        task.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_config()
        self.task_triggered.emit(task_id, asdict(task))

    def mark_task_result(self, task_id: str, result: str):
        task = self._tasks.get(task_id)
        if task:
            task.last_result = result
            self._save_config()

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
                name="每日AI策略总任务",
                enabled=False,
                time="14:35",
                task_type="ai_strategy_cycle",
                notify_on_complete=True,
                auto_execute=True,
            )
            changed = True
        if not changed:
            return
        self._save_config()
