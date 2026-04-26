from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "live_strategy_center.db"
_singleton: Optional["OrderExecutionEventService"] = None


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class OrderExecutionEvent:
    event_id: str
    level: str = "info"
    category: str = "order_execution"
    source: str = "trade_execution"
    strategy_id: str = ""
    symbol: str = ""
    request_id: str = ""
    broker_order_id: int = 0
    title: str = ""
    message: str = ""
    status: str = "resolved"
    payload: Optional[dict[str, Any]] = None
    occurred_at: str = ""


class OrderExecutionEventService:
    """Lightweight writer for order lifecycle events shown by the live strategy center."""

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
            "CREATE INDEX IF NOT EXISTS idx_live_center_events_occurred_at ON live_center_events(occurred_at DESC)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_center_events_status ON live_center_events(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_live_center_events_category ON live_center_events(category)")
        conn.commit()
        conn.close()

    def add_event(self, event: OrderExecutionEvent) -> None:
        payload_json = self.dumps_payload(event.payload)
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
                str(event.event_id or "").strip(),
                event.occurred_at or _now_text(),
                str(event.level or "info").strip().lower(),
                str(event.category or "order_execution").strip(),
                str(event.source or "trade_execution").strip(),
                str(event.strategy_id or "").strip(),
                str(event.symbol or "").strip(),
                str(event.request_id or "").strip(),
                int(event.broker_order_id or 0),
                str(event.title or "").strip(),
                str(event.message or "").strip(),
                str(event.status or "resolved").strip().lower(),
                payload_json,
            ),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def dumps_payload(payload: Optional[dict[str, Any]]) -> str:
        if not payload:
            return ""
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            logger.debug("Serialize order execution event payload failed: %s", exc)
            return ""


def get_order_execution_event_service() -> OrderExecutionEventService:
    global _singleton
    if _singleton is None:
        _singleton = OrderExecutionEventService()
    return _singleton
