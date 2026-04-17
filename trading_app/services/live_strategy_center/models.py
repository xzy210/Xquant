from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class LiveCenterEvent:
    event_id: str
    occurred_at: str = ""
    level: str = "info"
    category: str = ""
    source: str = ""
    strategy_id: str = ""
    symbol: str = ""
    request_id: str = ""
    broker_order_id: int = 0
    title: str = ""
    message: str = ""
    status: str = "open"
    payload_json: str = ""

    def __post_init__(self) -> None:
        if not self.occurred_at:
            self.occurred_at = _now_text()
        self.level = str(self.level or "info").strip().lower()
        self.category = str(self.category or "").strip()
        self.source = str(self.source or "").strip()
        self.strategy_id = str(self.strategy_id or "").strip()
        self.symbol = str(self.symbol or "").strip()
        self.request_id = str(self.request_id or "").strip()
        self.title = str(self.title or "").strip()
        self.message = str(self.message or "").strip()
        self.status = str(self.status or "open").strip().lower()
        self.broker_order_id = int(self.broker_order_id or 0)
        self.payload_json = str(self.payload_json or "")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "LiveCenterEvent":
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})


@dataclass
class TaskRunSummary:
    task_key: str
    task_type: str = ""
    title: str = ""
    started_at: str = ""
    finished_at: str = ""
    status: str = ""
    trigger: str = ""
    message: str = ""
    payload_json: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.task_key = str(self.task_key or "").strip()
        self.task_type = str(self.task_type or "").strip()
        self.title = str(self.title or "").strip()
        self.started_at = str(self.started_at or "")
        self.finished_at = str(self.finished_at or "")
        self.status = str(self.status or "").strip().lower()
        self.trigger = str(self.trigger or "").strip()
        self.message = str(self.message or "").strip()
        self.payload_json = str(self.payload_json or "")
        if not self.updated_at:
            self.updated_at = _now_text()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "TaskRunSummary":
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})


@dataclass
class RegisteredTask:
    task_key: str
    task_type: str
    title: str
    provider: Any
    actions: Dict[str, Any] = field(default_factory=dict)
