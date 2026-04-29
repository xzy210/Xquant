# -*- coding: utf-8 -*-
"""AI 决策证据轨迹持久化。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class EvidenceStep:
    title: str
    detail: str = ""
    status: str = "done"
    children: list["EvidenceStep"] = field(default_factory=list)


@dataclass
class ToolCallTrace:
    tool_name: str
    title: str = ""
    summary: str = ""
    content_preview: str = ""
    file_path: str = ""
    image_path: str = ""


@dataclass
class EvidenceTrace:
    trace_id: str
    session_id: str
    decision_record_id: str
    symbol_code: str
    symbol_name: str
    mode: str
    scan_scope: str
    source: str
    run_at: str
    completed_at: str = ""
    status: str = "running"
    model_name: str = ""
    steps: list[EvidenceStep] = field(default_factory=list)
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    response_preview: str = ""
    decision_summary: dict[str, Any] = field(default_factory=dict)
    risk_summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceTrace":
        def _step(item: dict[str, Any]) -> EvidenceStep:
            return EvidenceStep(
                title=str(item.get("title", "") or ""),
                detail=str(item.get("detail", "") or ""),
                status=str(item.get("status", "done") or "done"),
                children=[_step(child) for child in list(item.get("children", []) or []) if isinstance(child, dict)],
            )

        def _tool(item: dict[str, Any]) -> ToolCallTrace:
            return ToolCallTrace(
                tool_name=str(item.get("tool_name", "") or ""),
                title=str(item.get("title", "") or ""),
                summary=str(item.get("summary", "") or ""),
                content_preview=str(item.get("content_preview", "") or ""),
                file_path=str(item.get("file_path", "") or ""),
                image_path=str(item.get("image_path", "") or ""),
            )

        return cls(
            trace_id=str(payload.get("trace_id", "") or ""),
            session_id=str(payload.get("session_id", "") or ""),
            decision_record_id=str(payload.get("decision_record_id", "") or ""),
            symbol_code=str(payload.get("symbol_code", "") or ""),
            symbol_name=str(payload.get("symbol_name", "") or ""),
            mode=str(payload.get("mode", "") or ""),
            scan_scope=str(payload.get("scan_scope", "") or ""),
            source=str(payload.get("source", "") or ""),
            run_at=str(payload.get("run_at", "") or ""),
            completed_at=str(payload.get("completed_at", "") or ""),
            status=str(payload.get("status", "running") or "running"),
            model_name=str(payload.get("model_name", "") or ""),
            steps=[_step(item) for item in list(payload.get("steps", []) or []) if isinstance(item, dict)],
            tool_calls=[_tool(item) for item in list(payload.get("tool_calls", []) or []) if isinstance(item, dict)],
            artifacts=[dict(item) for item in list(payload.get("artifacts", []) or []) if isinstance(item, dict)],
            response_preview=str(payload.get("response_preview", "") or ""),
            decision_summary=dict(payload.get("decision_summary", {}) or {}),
            risk_summary=dict(payload.get("risk_summary", {}) or {}),
        )


class EvidenceTraceStore:
    """Store traces as one JSON file per symbol per scan session."""

    def __init__(self, root: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.root = Path(root) if root is not None else project_root / "data" / "decision_evidence"
        self.root.mkdir(parents=True, exist_ok=True)

    def save_trace(self, trace: EvidenceTrace) -> Path:
        run_date = self._date_part(trace.completed_at or trace.run_at)
        session_id = self._safe_part(trace.session_id or "single")
        symbol = self._safe_part(trace.symbol_code or "unknown")
        trace_id = self._safe_part(trace.trace_id or f"{symbol}_{datetime.now().strftime('%H%M%S')}")
        target_dir = self.root / run_date / session_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{symbol}_{trace_id}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(trace), f, ensure_ascii=False, indent=2)
        return path

    def load_trace(self, path: str | Path) -> EvidenceTrace | None:
        try:
            with Path(path).open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return None
            return EvidenceTrace.from_dict(payload)
        except Exception:
            return None

    def list_recent(self, limit: int = 100) -> list[tuple[Path, EvidenceTrace]]:
        files = sorted(self.root.glob("*/*/*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result: list[tuple[Path, EvidenceTrace]] = []
        for path in files[: max(limit * 3, limit)]:
            trace = self.load_trace(path)
            if trace is None:
                continue
            result.append((path, trace))
            if len(result) >= limit:
                break
        return result

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

