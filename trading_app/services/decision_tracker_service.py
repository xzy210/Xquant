from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from io import StringIO
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
        created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record = DecisionRecord(
            record_id=uuid4().hex[:12],
            created_at=created,
            symbol_code=decision.symbol_code,
            symbol_name=decision.symbol_name,
            decision=decision.to_dict(),
            risk_result=risk_result.to_dict(),
            outcome=outcome,
            user_remark=user_remark,
            broker_order_id=broker_order_id,
            entry_price=entry_price or decision.current_price,
            valid_until=DecisionRecord.calc_valid_until(created, decision.time_horizon),
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

    def query_open_executed(self, symbol_code: str = "") -> List[DecisionRecord]:
        """Find executed but unclosed records, optionally filtered by symbol."""
        records = self._load_all()
        results = []
        for r in records:
            if r.outcome != DecisionOutcome.EXECUTED.value:
                continue
            if r.closed_at:
                continue
            action = (r.decision or {}).get("action", "")
            if action not in ("buy", "add"):
                continue
            if symbol_code and r.symbol_code != symbol_code:
                continue
            results.append(r)
        results.sort(key=lambda r: r.created_at)
        return results

    def close_position(
        self,
        record_id: str,
        exit_price: float,
        *,
        volume_ratio: float = 1.0,
    ) -> bool:
        """Close an executed record with P&L calculation.
        
        volume_ratio: fraction being closed (1.0 = full close, 0.5 = half).
        """
        records = self._load_all()
        for record in records:
            if record.record_id != record_id:
                continue
            if record.entry_price <= 0 or exit_price <= 0:
                return False
            pnl_pct = (exit_price - record.entry_price) / record.entry_price * 100
            d = record.decision or {}
            position_pct = float(d.get("position_pct", 0.1) or 0.1)
            estimated_amount = record.entry_price * position_pct * 10000
            pnl_amount = estimated_amount * (pnl_pct / 100) * volume_ratio
            record.exit_price = exit_price
            record.actual_pnl = round(pnl_amount, 2)
            record.actual_pnl_pct = round(pnl_pct, 2)
            record.closed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._write_all(records)
            logger.info(
                "Position closed: %s exit=%.2f pnl=%.2f(%.2f%%)",
                record_id, exit_price, pnl_amount, pnl_pct,
            )
            return True
        return False

    def auto_close_by_symbol(self, symbol_code: str, exit_price: float) -> List[str]:
        """Close all open executed BUY/ADD records for a symbol. Returns closed record_ids."""
        open_records = self.query_open_executed(symbol_code)
        closed_ids = []
        for rec in open_records:
            if self.close_position(rec.record_id, exit_price):
                closed_ids.append(rec.record_id)
        return closed_ids

    def expire_stale_decisions(self) -> int:
        """Scan all open decisions and mark expired ones. Returns count of expired."""
        now = datetime.now()
        records = self._load_all()
        count = 0
        terminal = {
            DecisionOutcome.EXECUTED.value,
            DecisionOutcome.EXPIRED.value,
            DecisionOutcome.REJECTED_BY_RISK.value,
            DecisionOutcome.REJECTED_BY_USER.value,
            DecisionOutcome.EXECUTION_FAILED.value,
        }
        for rec in records:
            if rec.outcome in terminal or rec.closed_at:
                continue
            if not rec.valid_until:
                horizon = (rec.decision or {}).get("time_horizon", "short")
                rec.valid_until = DecisionRecord.calc_valid_until(rec.created_at, horizon)
            try:
                deadline = datetime.strptime(rec.valid_until, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            if now > deadline:
                rec.outcome = DecisionOutcome.EXPIRED.value
                count += 1
        if count > 0:
            self._write_all(records)
            logger.info("Expired %d stale decisions", count)
        return count

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

    # ── Export helpers ──

    _CSV_HEADER = [
        "ID", "创建时间", "有效期至", "代码", "名称", "操作", "置信度",
        "现价", "目标价", "止损价", "仓位%", "入场价", "出场价",
        "盈亏%", "盈亏额", "风控等级", "状态", "平仓时间", "备注",
    ]

    def export_csv(self, path: Path, *, limit: int = 0) -> int:
        records = self._load_all()
        records.sort(key=lambda r: r.created_at, reverse=True)
        if limit > 0:
            records = records[:limit]

        from .trade_decision_models import TRADE_ACTION_LABELS
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(self._CSV_HEADER)
            for r in records:
                d = r.decision or {}
                rr = r.risk_result or {}
                writer.writerow([
                    r.record_id,
                    r.created_at,
                    r.valid_until or "-",
                    r.symbol_code,
                    r.symbol_name,
                    TRADE_ACTION_LABELS.get(d.get("action", ""), d.get("action", "")),
                    f"{d.get('confidence', 0):.0%}",
                    f"{d.get('current_price', 0):.2f}",
                    f"{d.get('target_price', 0):.2f}" if d.get("target_price", 0) else "-",
                    f"{d.get('stop_loss_price', 0):.2f}" if d.get("stop_loss_price", 0) else "-",
                    f"{d.get('position_pct', 0):.0%}" if d.get("position_pct", 0) else "-",
                    f"{r.entry_price:.2f}" if r.entry_price else "-",
                    f"{r.exit_price:.2f}" if r.exit_price else "-",
                    f"{r.actual_pnl_pct:+.2f}%" if r.closed_at else "-",
                    f"{r.actual_pnl:+.2f}" if r.closed_at else "-",
                    rr.get("overall_risk_level", "-"),
                    r.outcome,
                    r.closed_at or "-",
                    r.user_remark or "",
                ])
        logger.info("Exported %d records to CSV: %s", len(records), path)
        return len(records)

    def export_html_report(self, path: Path, *, limit: int = 0) -> int:
        records = self._load_all()
        records.sort(key=lambda r: r.created_at, reverse=True)
        if limit > 0:
            records = records[:limit]
        stats = self.get_stats()

        from .trade_decision_models import TRADE_ACTION_LABELS

        action_colors = {
            "buy": "#4caf50", "add": "#4caf50",
            "sell": "#f44336", "reduce": "#ff9800",
            "hold": "#2196f3",
        }

        rows_html = []
        for r in records:
            d = r.decision or {}
            rr = r.risk_result or {}
            action = d.get("action", "")
            color = action_colors.get(action, "#999")
            pnl_color = "#4caf50" if r.actual_pnl_pct >= 0 else "#f44336"
            rows_html.append(f"""<tr>
<td>{r.created_at}</td>
<td>{r.symbol_name}<br><small style="color:#888">{r.symbol_code}</small></td>
<td style="color:{color};font-weight:bold">{TRADE_ACTION_LABELS.get(action, action)}</td>
<td>{d.get('confidence', 0):.0%}</td>
<td>{r.entry_price:.2f}</td>
<td>{d.get('target_price', 0):.2f}</td>
<td>{d.get('stop_loss_price', 0):.2f}</td>
<td style="color:{pnl_color}">{f"{r.actual_pnl_pct:+.2f}%" if r.closed_at else "-"}</td>
<td style="color:{pnl_color}">{f"¥{r.actual_pnl:+,.2f}" if r.closed_at else "-"}</td>
<td>{r.outcome}</td>
<td>{r.valid_until[:10] if r.valid_until else "-"}</td>
</tr>""")

        wr = stats.get("win_rate", 0)
        wr_color = "#4caf50" if wr >= 0.5 else "#f44336"
        pnl_total = stats.get("total_pnl", 0)
        pnl_color = "#4caf50" if pnl_total >= 0 else "#f44336"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>AI 交易决策复盘报告</title>
<style>
body {{font-family: 'Microsoft YaHei', Arial, sans-serif; max-width:1200px; margin:auto; padding:20px; background:#f5f5f5;}}
h1 {{text-align:center; color:#333;}}
.meta {{text-align:center; color:#888; margin-bottom:20px;}}
.stats-grid {{display:flex; gap:16px; margin-bottom:24px; flex-wrap:wrap; justify-content:center;}}
.stat-card {{background:#fff; border-radius:8px; padding:16px 24px; box-shadow:0 1px 3px rgba(0,0,0,.12); text-align:center; min-width:140px;}}
.stat-card .num {{font-size:24px; font-weight:bold; margin-top:4px;}}
.stat-card .label {{font-size:13px; color:#888;}}
table {{width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.12);}}
th {{background:#1e1e2e; color:#fff; padding:10px 8px; font-size:13px;}}
td {{padding:8px; border-bottom:1px solid #eee; font-size:13px; text-align:center;}}
tr:hover {{background:#f9f9f9;}}
footer {{text-align:center; color:#aaa; margin-top:24px; font-size:12px;}}
</style></head><body>
<h1>AI 交易决策复盘报告</h1>
<p class="meta">生成时间: {now_str} | 共 {len(records)} 条决策</p>
<div class="stats-grid">
  <div class="stat-card"><div class="label">总决策</div><div class="num">{stats.get("total_decisions", 0)}</div></div>
  <div class="stat-card"><div class="label">已执行</div><div class="num">{stats.get("executed_count", 0)}</div></div>
  <div class="stat-card"><div class="label">已平仓</div><div class="num">{stats.get("closed_count", 0)}</div></div>
  <div class="stat-card"><div class="label">胜率</div><div class="num" style="color:{wr_color}">{wr:.1%}</div></div>
  <div class="stat-card"><div class="label">平均盈亏%</div><div class="num">{stats.get("avg_pnl_pct", 0):+.2f}%</div></div>
  <div class="stat-card"><div class="label">累计盈亏</div><div class="num" style="color:{pnl_color}">¥{pnl_total:+,.2f}</div></div>
</div>
<table>
<thead><tr><th>时间</th><th>标的</th><th>操作</th><th>置信度</th><th>入场价</th><th>目标价</th><th>止损价</th><th>盈亏%</th><th>盈亏额</th><th>状态</th><th>有效期</th></tr></thead>
<tbody>{"".join(rows_html)}</tbody>
</table>
<footer>StockTradebyZ AI 交易决策系统 · 自动生成</footer>
</body></html>"""

        path.write_text(html, encoding="utf-8")
        logger.info("Exported HTML report: %s (%d records)", path, len(records))
        return len(records)
