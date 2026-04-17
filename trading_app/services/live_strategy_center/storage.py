from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .models import LiveCenterEvent, TaskRunSummary

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "live_strategy_center.db"
_storage_singleton: Optional["LiveStrategyCenterStorage"] = None


class LiveStrategyCenterStorage:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS live_center_events (
                event_id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                level TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                strategy_id TEXT DEFAULT '',
                symbol TEXT DEFAULT '',
                request_id TEXT DEFAULT '',
                broker_order_id INTEGER DEFAULT 0,
                title TEXT DEFAULT '',
                message TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                payload_json TEXT DEFAULT ''
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS task_run_summaries (
                task_key TEXT PRIMARY KEY,
                task_type TEXT DEFAULT '',
                title TEXT DEFAULT '',
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                status TEXT DEFAULT '',
                trigger TEXT DEFAULT '',
                message TEXT DEFAULT '',
                payload_json TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_center_events_occurred_at ON live_center_events(occurred_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_center_events_status ON live_center_events(status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_center_events_category ON live_center_events(category)"
        )
        # 风控中心已废弃，清理旧表（存在则丢弃）。
        cursor.execute("DROP TABLE IF EXISTS risk_snapshots")
        conn.commit()
        conn.close()

    def add_event(self, event: LiveCenterEvent) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO live_center_events (
                event_id, occurred_at, level, category, source, strategy_id, symbol,
                request_id, broker_order_id, title, message, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.occurred_at,
                event.level,
                event.category,
                event.source,
                event.strategy_id,
                event.symbol,
                event.request_id,
                event.broker_order_id,
                event.title,
                event.message,
                event.status,
                event.payload_json,
            ),
        )
        conn.commit()
        conn.close()

    def get_event(self, event_id: str) -> Optional[LiveCenterEvent]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM live_center_events WHERE event_id = ?",
            (str(event_id or "").strip(),),
        )
        row = cursor.fetchone()
        conn.close()
        return LiveCenterEvent.from_row(dict(row)) if row else None

    def list_events(
        self,
        *,
        status: str = "",
        category: str = "",
        start_time: str = "",
        end_time: str = "",
        include_ignored: bool = True,
        limit: int = 300,
    ) -> List[LiveCenterEvent]:
        conn = self._get_connection()
        cursor = conn.cursor()
        conditions = []
        params: List[object] = []
        if start_time:
            conditions.append("occurred_at >= ?")
            params.append(str(start_time))
        if end_time:
            conditions.append("occurred_at <= ?")
            params.append(str(end_time))
        if status:
            conditions.append("status = ?")
            params.append(status)
        elif not include_ignored:
            conditions.append("status != ?")
            params.append("ignored")
        if category:
            conditions.append("category = ?")
            params.append(category)
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        cursor.execute(
            f"SELECT * FROM live_center_events WHERE {where_clause} ORDER BY occurred_at DESC LIMIT ?",
            [*params, max(int(limit or 0), 1)],
        )
        rows = cursor.fetchall()
        conn.close()
        return [LiveCenterEvent.from_row(dict(row)) for row in rows]

    def update_event_status(self, event_id: str, status: str) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE live_center_events SET status = ? WHERE event_id = ?",
            (str(status or "").strip().lower(), str(event_id or "").strip()),
        )
        conn.commit()
        conn.close()

    def get_event_counts(self) -> Dict[str, int]:
        conn = self._get_connection()
        cursor = conn.cursor()
        counts = {"open": 0, "read": 0, "ignored": 0, "resolved": 0, "total": 0}
        cursor.execute("SELECT status, COUNT(*) AS cnt FROM live_center_events GROUP BY status")
        for row in cursor.fetchall():
            key = str(row["status"] or "").strip().lower()
            counts[key] = int(row["cnt"] or 0)
            counts["total"] += int(row["cnt"] or 0)
        conn.close()
        return counts

    def upsert_task_summary(self, summary: TaskRunSummary) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO task_run_summaries (
                task_key, task_type, title, started_at, finished_at, status,
                trigger, message, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.task_key,
                summary.task_type,
                summary.title,
                summary.started_at,
                summary.finished_at,
                summary.status,
                summary.trigger,
                summary.message,
                summary.payload_json,
                summary.updated_at,
            ),
        )
        conn.commit()
        conn.close()

    def list_task_summaries(self) -> List[TaskRunSummary]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM task_run_summaries ORDER BY updated_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [TaskRunSummary.from_row(dict(row)) for row in rows]

    def get_task_summary(self, task_key: str) -> Optional[TaskRunSummary]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM task_run_summaries WHERE task_key = ?", (str(task_key or "").strip(),))
        row = cursor.fetchone()
        conn.close()
        return TaskRunSummary.from_row(dict(row)) if row else None

    @staticmethod
    def dumps_payload(payload: Optional[dict]) -> str:
        if not payload:
            return ""
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            logger.debug("序列化中心 payload 失败: %s", exc)
            return ""

    @staticmethod
    def loads_payload(payload_json: str) -> dict:
        if not payload_json:
            return {}
        try:
            return dict(json.loads(payload_json))
        except Exception:
            return {}


def get_live_strategy_center_storage() -> LiveStrategyCenterStorage:
    global _storage_singleton
    if _storage_singleton is None:
        _storage_singleton = LiveStrategyCenterStorage()
    return _storage_singleton
