# -*- coding: utf-8 -*-
"""AI 决策会话持久化。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class DecisionSessionItem:
    item_id: str
    item_type: str
    symbol_code: str = ""
    symbol_name: str = ""
    decision_record_id: str = ""
    evidence_trace_path: str = ""
    action: str = ""
    status_text: str = ""
    created_at: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionSession:
    session_id: str
    title: str
    source: str = "manual"
    mode: str = ""
    scan_scope: str = ""
    task_id: str = ""
    model_name: str = ""
    started_at: str = ""
    completed_at: str = ""
    status: str = "running"
    summary: str = ""
    items: list[DecisionSessionItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DecisionSession":
        items = [
            DecisionSessionItem(
                item_id=str(item.get("item_id", "") or ""),
                item_type=str(item.get("item_type", "") or ""),
                symbol_code=str(item.get("symbol_code", "") or ""),
                symbol_name=str(item.get("symbol_name", "") or ""),
                decision_record_id=str(item.get("decision_record_id", "") or ""),
                evidence_trace_path=str(item.get("evidence_trace_path", "") or ""),
                action=str(item.get("action", "") or ""),
                status_text=str(item.get("status_text", "") or ""),
                created_at=str(item.get("created_at", "") or ""),
                payload=dict(item.get("payload", {}) or {}),
            )
            for item in list(payload.get("items", []) or [])
            if isinstance(item, dict)
        ]
        return cls(
            session_id=str(payload.get("session_id", "") or ""),
            title=str(payload.get("title", "") or ""),
            source=str(payload.get("source", "manual") or "manual"),
            mode=str(payload.get("mode", "") or ""),
            scan_scope=str(payload.get("scan_scope", "") or ""),
            task_id=str(payload.get("task_id", "") or ""),
            model_name=str(payload.get("model_name", "") or ""),
            started_at=str(payload.get("started_at", "") or ""),
            completed_at=str(payload.get("completed_at", "") or ""),
            status=str(payload.get("status", "running") or "running"),
            summary=str(payload.get("summary", "") or ""),
            items=items,
        )


class DecisionSessionStore:
    """Store one JSON file per AI decision session."""

    def __init__(self, root: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.root = Path(root) if root is not None else project_root / "data" / "decision_sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def save_session(self, session: DecisionSession) -> Path:
        run_date = self._date_part(session.started_at or session.completed_at)
        session_id = self._safe_part(session.session_id or datetime.now().strftime("%H%M%S"))
        target_dir = self.root / run_date
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{session_id}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(session), f, ensure_ascii=False, indent=2)
        return path

    def load_session(self, session_id_or_path: str | Path) -> DecisionSession | None:
        path = Path(session_id_or_path)
        if not path.exists():
            safe_id = self._safe_part(str(session_id_or_path))
            matches = sorted(self.root.glob(f"*/{safe_id}.json"))
            path = matches[0] if matches else path
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return None
            return DecisionSession.from_dict(payload)
        except Exception:
            return None

    def list_recent(self, limit: int = 80) -> list[tuple[Path, DecisionSession]]:
        files = sorted(self.root.glob("*/*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result: list[tuple[Path, DecisionSession]] = []
        for path in files[: max(limit * 3, limit)]:
            session = self.load_session(path)
            if session is None:
                continue
            result.append((path, session))
            if len(result) >= limit:
                break
        return result

    def upsert_session(self, session: DecisionSession) -> Path:
        existing = self.load_session(session.session_id)
        if existing is not None:
            session.items = session.items or existing.items
            session.started_at = session.started_at or existing.started_at
        return self.save_session(session)

    def append_item(self, session_id: str, item: DecisionSessionItem) -> Path | None:
        session = self.load_session(session_id)
        if session is None:
            return None
        existing = [entry for entry in session.items if entry.item_id != item.item_id]
        existing.append(item)
        session.items = existing
        return self.save_session(session)

    def complete_session(self, session_id: str, *, status: str = "done", summary: str = "") -> Path | None:
        session = self.load_session(session_id)
        if session is None:
            return None
        session.status = status
        session.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if summary:
            session.summary = summary
        return self.save_session(session)

    @staticmethod
    def _date_part(value: str) -> str:
        text = str(value or "").strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _safe_part(value: str) -> str:
        text = str(value or "").strip() or "unknown"
        return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)
