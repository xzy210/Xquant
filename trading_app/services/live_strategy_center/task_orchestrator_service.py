from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .models import RegisteredTask, TaskRunSummary
from .storage import LiveStrategyCenterStorage, get_live_strategy_center_storage

logger = logging.getLogger(__name__)


class TaskOrchestratorService(QObject):
    tasks_changed = pyqtSignal(list)

    def __init__(self, storage: Optional[LiveStrategyCenterStorage] = None, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage or get_live_strategy_center_storage()
        self._tasks: Dict[str, RegisteredTask] = {}
        self._runtime_overrides: Dict[str, dict] = {}
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self.emit_tasks_changed)
        self._poll_timer.start()

    def register_task(
        self,
        *,
        task_key: str,
        task_type: str,
        title: str,
        provider: Callable[[], dict],
        strategy_id: str = "",
        strategy_name: str = "",
        actions: Optional[Dict[str, Callable[[], Any]]] = None,
    ) -> None:
        self._tasks[task_key] = RegisteredTask(
            task_key=task_key,
            task_type=task_type,
            title=title,
            provider=provider,
            strategy_id=str(strategy_id or "").strip(),
            strategy_name=str(strategy_name or "").strip(),
            actions=dict(actions or {}),
        )
        self.emit_tasks_changed()

    def record_runtime(
        self,
        task_key: str,
        *,
        status: str,
        message: str,
        trigger: str = "",
        started_at: str = "",
        finished_at: str = "",
        payload: Optional[dict] = None,
    ) -> None:
        runtime = dict(self._runtime_overrides.get(task_key, {}) or {})
        runtime.update(
            {
                "status": str(status or "").strip().lower(),
                "message": str(message or ""),
                "trigger": str(trigger or ""),
                "payload": dict(payload or {}),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        if started_at:
            runtime["started_at"] = started_at
        if finished_at:
            runtime["finished_at"] = finished_at
        self._runtime_overrides[task_key] = runtime
        self.emit_tasks_changed()

    def list_tasks(self) -> list[dict]:
        results: list[dict] = []
        for task_key, registered in self._tasks.items():
            try:
                provided = dict(registered.provider() or {})
            except Exception as exc:
                logger.debug("任务 provider 执行失败 key=%s err=%s", task_key, exc)
                provided = {"status": "failed", "message": f"provider error: {exc}"}
            summary = self.storage.get_task_summary(task_key)
            stored = summary.to_dict() if summary else {}
            runtime = dict(self._runtime_overrides.get(task_key, {}) or {})
            merged = {
                **stored,
                **provided,
                **runtime,
                "task_key": task_key,
                "task_type": registered.task_type,
                "title": registered.title,
                "strategy_id": str(
                    registered.strategy_id
                    or provided.get("strategy_id", "")
                    or runtime.get("strategy_id", "")
                    or stored.get("strategy_id", "")
                    or ""
                ).strip(),
                "strategy_name": str(
                    registered.strategy_name
                    or provided.get("strategy_name", "")
                    or runtime.get("strategy_name", "")
                    or stored.get("strategy_name", "")
                    or ""
                ).strip(),
                "available_actions": list(registered.actions.keys()),
            }
            self.storage.upsert_task_summary(
                TaskRunSummary(
                    task_key=task_key,
                    task_type=str(merged.get("task_type", "") or registered.task_type),
                    title=str(merged.get("title", "") or registered.title),
                    strategy_id=str(merged.get("strategy_id", "") or ""),
                    strategy_name=str(merged.get("strategy_name", "") or ""),
                    started_at=str(merged.get("started_at", "") or ""),
                    finished_at=str(merged.get("finished_at", "") or ""),
                    status=str(merged.get("status", "") or ""),
                    trigger=str(merged.get("trigger", "") or ""),
                    message=str(merged.get("message", "") or ""),
                    payload_json=self.storage.dumps_payload(dict(merged.get("payload", {}) or {})),
                    updated_at=str(merged.get("updated_at", "") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
            )
            results.append(merged)
        return results

    def run_action(self, task_key: str, action: str) -> tuple[bool, str]:
        registered = self._tasks.get(task_key)
        if registered is None:
            return False, f"未找到任务: {task_key}"
        callback = registered.actions.get(action)
        if callback is None:
            return False, f"任务 {task_key} 不支持动作 {action}"
        try:
            result = callback()
        except Exception as exc:
            self.record_runtime(
                task_key,
                status="failed",
                message=f"{action} 执行失败: {exc}",
                trigger="manual",
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            return False, f"{action} 执行失败: {exc}"
        message = str(result or f"{action} 已执行")
        self.record_runtime(
            task_key,
            status="triggered",
            message=message,
            trigger="manual",
            started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        return True, message

    def emit_tasks_changed(self) -> None:
        self.tasks_changed.emit(self.list_tasks())
