from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .trade_decision_models import (
    DecisionOutcome,
    DecisionRecord,
    RiskCheckResult,
    TradeDecision,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_RECORDS_FILE = _DATA_DIR / "decision_records.jsonl"


class DecisionTrackerService:
    """Persist trade decision records and provide querying / stats."""

    def __init__(self, records_path: Optional[Path] = None):
        self.records_path = records_path or _RECORDS_FILE
        self.records_path.parent.mkdir(parents=True, exist_ok=True)

    def save_decision(
        self,
        decision: TradeDecision,
        risk_result: RiskCheckResult,
        outcome: str,
        *,
        user_remark: str = "",
        broker_order_id: int = -1,
        entry_price: float = 0.0,
    ) -> DecisionRecord:
        record = DecisionRecord(
            record_id=uuid4().hex[:12],
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol_code=decision.symbol_code,
            symbol_name=decision.symbol_name,
            decision=decision.to_dict(),
            risk_result=risk_result.to_dict(),
            outcome=outcome,
            user_remark=user_remark,
            broker_order_id=broker_order_id,
            entry_price=entry_price or decision.current_price,
        )
        self._append_record(record)
        logger.info("Decision recorded: %s %s %s", record.record_id, decision.action, decision.symbol_code)
        return record

    def query_by_symbol(self, code: str, limit: int = 20) -> List[DecisionRecord]:
        records = self._load_all()
        matched = [r for r in records if r.symbol_code == code]
        matched.sort(key=lambda r: r.created_at, reverse=True)
        return matched[:limit]

    def query_recent(self, limit: int = 50) -> List[DecisionRecord]:
        records = self._load_all()
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records[:limit]

    def update_outcome(
        self,
        record_id: str,
        *,
        outcome: Optional[str] = None,
        exit_price: float = 0.0,
        actual_pnl: float = 0.0,
        actual_pnl_pct: float = 0.0,
    ) -> bool:
        records = self._load_all()
        updated = False
        for record in records:
            if record.record_id == record_id:
                if outcome:
                    record.outcome = outcome
                if exit_price > 0:
                    record.exit_price = exit_price
                if actual_pnl != 0:
                    record.actual_pnl = actual_pnl
                if actual_pnl_pct != 0:
                    record.actual_pnl_pct = actual_pnl_pct
                if exit_price > 0 or actual_pnl != 0:
                    record.closed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated = True
                break
        if updated:
            self._write_all(records)
        return updated

    def get_stats(self) -> Dict[str, Any]:
        records = self._load_all()
        executed = [r for r in records if r.outcome in (
            DecisionOutcome.EXECUTED.value, DecisionOutcome.APPROVED.value
        )]
        closed = [r for r in executed if r.closed_at]
        wins = [r for r in closed if r.actual_pnl > 0]

        total = len(records)
        executed_count = len(executed)
        closed_count = len(closed)
        win_count = len(wins)

        return {
            "total_decisions": total,
            "executed_count": executed_count,
            "closed_count": closed_count,
            "win_rate": round(win_count / closed_count, 4) if closed_count > 0 else 0.0,
            "avg_pnl_pct": (
                round(sum(r.actual_pnl_pct for r in closed) / closed_count, 4)
                if closed_count > 0 else 0.0
            ),
            "total_pnl": round(sum(r.actual_pnl for r in closed), 2),
        }

    def _append_record(self, record: DecisionRecord) -> None:
        try:
            with open(self.records_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Failed to append decision record: %s", exc)

    def _load_all(self) -> List[DecisionRecord]:
        records: List[DecisionRecord] = []
        if not self.records_path.exists():
            return records
        try:
            with open(self.records_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        records.append(DecisionRecord.from_dict(data))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception as exc:
            logger.error("Failed to load decision records: %s", exc)
        return records

    def _write_all(self, records: List[DecisionRecord]) -> None:
        try:
            with open(self.records_path, "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Failed to rewrite decision records: %s", exc)
