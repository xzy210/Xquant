from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


BUY_LABELS = {"买入"}
SELL_LABELS = {"卖出"}


@dataclass
class ImportedTrade:
    order_ref: str
    broker_order_id: int
    stock_code: str
    stock_name: str
    direction: str
    price: float
    volume: int
    amount: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    trade_date: str
    created_at: str
    source: str
    remark: str

    @property
    def exact_key(self) -> tuple[str, str, str, float, int]:
        return (
            self.order_ref or str(int(self.broker_order_id or 0)),
            str(self.stock_code or ""),
            str(self.direction or ""),
            round(float(self.price or 0.0), 4),
            int(self.volume or 0),
        )


def local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def normalize_code(value: Any) -> str:
    text = normalize_text(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        return digits.zfill(6)[-6:]
    return text


def normalize_float(value: Any) -> float:
    text = normalize_text(value).replace(",", "")
    if not text:
        return 0.0
    return float(text)


def normalize_int(value: Any) -> int:
    text = normalize_text(value).replace(",", "")
    if not text:
        return 0
    return int(float(text))


def normalize_int_safe(value: Any, default: int = -1) -> int:
    try:
        return normalize_int(value)
    except Exception:
        return default


def normalize_order_ref(value: Any) -> str:
    return normalize_text(value)


def normalize_date(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return text[:10]


def normalize_time(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return "00:00:00"
    if len(text) == 8 and text.count(":") == 2:
        return text
    for fmt in ("%H:%M", "%H%M%S", "%H%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%H:%M:%S")
        except Exception:
            continue
    return text[:8] if len(text) >= 8 else text


def created_at_from(date_text: str, time_text: str) -> str:
    date_part = normalize_date(date_text) or datetime.now().strftime("%Y-%m-%d")
    time_part = normalize_time(time_text) or "00:00:00"
    if len(time_part) == 5:
        time_part = f"{time_part}:00"
    return f"{date_part} {time_part}"


def column_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return max(result - 1, 0)


def read_xlsx_rows(path: Path) -> tuple[str, list[dict[str, str]]]:
    with zipfile.ZipFile(path) as zf:
        worksheet_entries = sorted(
            name for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if not worksheet_entries:
            raise RuntimeError(f"{path} 未找到 worksheet")
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.iter():
                if local_name(si.tag) != "si":
                    continue
                text = "".join((node.text or "") for node in si.iter() if local_name(node.tag) == "t")
                shared_strings.append(text)

        workbook_name = Path(worksheet_entries[0]).stem
        try:
            workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
            sheet_nodes = [node for node in workbook_root.iter() if local_name(node.tag) == "sheet"]
            if sheet_nodes:
                workbook_name = sheet_nodes[0].attrib.get("name", workbook_name) or workbook_name
        except Exception:
            pass

        sheet_root = ET.fromstring(zf.read(worksheet_entries[0]))
        rows_matrix: list[list[str]] = []
        for row in [node for node in sheet_root.iter() if local_name(node.tag) == "row"]:
            values: list[str] = []
            for cell in [node for node in row if local_name(node.tag) == "c"]:
                idx = column_index(cell.attrib.get("r", "A1"))
                while len(values) <= idx:
                    values.append("")
                cell_type = cell.attrib.get("t", "")
                raw = ""
                inline_node = next((node for node in cell.iter() if local_name(node.tag) == "t"), None)
                value_node = next((node for node in cell.iter() if local_name(node.tag) == "v"), None)
                if inline_node is not None and inline_node.text is not None:
                    raw = inline_node.text
                elif value_node is not None and value_node.text is not None:
                    raw = value_node.text
                if cell_type == "s" and raw.isdigit():
                    pos = int(raw)
                    raw = shared_strings[pos] if 0 <= pos < len(shared_strings) else raw
                values[idx] = normalize_text(raw)
            if any(item.strip() for item in values):
                rows_matrix.append(values)
        if not rows_matrix:
            return workbook_name, []
        headers = rows_matrix[0]
        records: list[dict[str, str]] = []
        for row in rows_matrix[1:]:
            padded = row + [""] * max(0, len(headers) - len(row))
            record = {headers[i]: padded[i] for i in range(len(headers)) if headers[i]}
            records.append(record)
        return workbook_name, records


def build_fee_lookup(settlement_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for row in settlement_rows:
        action = normalize_text(row.get("业务名称", ""))
        if action not in BUY_LABELS | SELL_LABELS:
            continue
        contract_no = normalize_text(row.get("合同编号", ""))
        if not contract_no:
            continue
        lookup[contract_no] = row
    return lookup


def build_imported_trades(
    trade_rows: list[dict[str, str]],
    settlement_lookup: dict[str, dict[str, str]],
) -> list[ImportedTrade]:
    imported: list[ImportedTrade] = []
    for row in trade_rows:
        side = normalize_text(row.get("买卖标志", ""))
        if side not in BUY_LABELS | SELL_LABELS:
            continue
        order_ref = normalize_order_ref(row.get("委托编号", ""))
        broker_order_id = normalize_int_safe(order_ref, default=-1)
        if not order_ref:
            continue
        stock_code = normalize_code(row.get("证券代码", ""))
        if not stock_code:
            continue
        stock_name = normalize_text(row.get("证券名称", ""))
        price = normalize_float(row.get("成交价格", "")) or normalize_float(row.get("委托价格", ""))
        volume = normalize_int(row.get("成交数量", "")) or normalize_int(row.get("委托数量", ""))
        amount = normalize_float(row.get("成交金额", ""))
        if price <= 0 or volume <= 0:
            continue
        if amount <= 0:
            amount = round(price * volume, 2)
        trade_date = normalize_date(row.get("成交日期", "")) or normalize_date(row.get("委托日期", ""))
        created_at = created_at_from(trade_date, row.get("成交时间", "") or row.get("委托时间", ""))
        fee_row = settlement_lookup.get(order_ref, {})
        commission = normalize_float(fee_row.get("手续费", ""))
        stamp_tax = normalize_float(fee_row.get("印花税", ""))
        transfer_fee = normalize_float(fee_row.get("过户费", ""))
        imported.append(
            ImportedTrade(
                order_ref=order_ref,
                broker_order_id=broker_order_id,
                stock_code=stock_code,
                stock_name=stock_name,
                direction="buy" if side in BUY_LABELS else "sell",
                price=round(price, 4),
                volume=volume,
                amount=round(amount, 2),
                commission=round(commission, 2),
                stamp_tax=round(stamp_tax, 2),
                transfer_fee=round(transfer_fee, 2),
                trade_date=trade_date,
                created_at=created_at,
                source="manual",
                remark=f"历史导入 合同编号:{order_ref}",
            )
        )
    return imported


def fetch_existing_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, trade_id, broker_order_id, stock_code, stock_name, direction, price, volume,
               amount, commission, stamp_tax, transfer_fee, trade_date, source, remark,
               created_at, COALESCE(strategy_id, '') AS strategy_id,
               COALESCE(virtual_account_id, '') AS virtual_account_id
        FROM trades
        """
    )
    return cur.fetchall()


def extract_order_ref_from_row(row: sqlite3.Row) -> str:
    broker_order_id = int(row["broker_order_id"] or 0)
    if broker_order_id > 0:
        return str(broker_order_id)
    remark = normalize_text(row["remark"])
    match = re.search(r"合同编号:([A-Za-z0-9]+)", remark)
    if match:
        return match.group(1)
    return ""


def exact_key_from_row(row: sqlite3.Row) -> tuple[str, str, str, float, int]:
    return (
        extract_order_ref_from_row(row),
        normalize_code(row["stock_code"]),
        normalize_text(row["direction"]),
        round(float(row["price"] or 0.0), 4),
        int(row["volume"] or 0),
    )


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.backup_{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def unique_trade_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def plan_import(existing_rows: list[sqlite3.Row], imported_trades: list[ImportedTrade]) -> dict[str, Any]:
    existing_by_key: dict[tuple[int, str, str, float, int], list[sqlite3.Row]] = {}
    for row in existing_rows:
        existing_by_key.setdefault(exact_key_from_row(row), []).append(row)

    plan: dict[str, Any] = {
        "import_rows": len(imported_trades),
        "insert_count": 0,
        "update_count": 0,
        "delete_count": 0,
        "skip_existing_strategy_count": 0,
        "skip_same_unclassified_count": 0,
        "actions": [],
    }

    for item in imported_trades:
        matched = existing_by_key.get(item.exact_key, [])
        strategy_rows = [
            row for row in matched
            if normalize_text(row["strategy_id"]) or normalize_text(row["virtual_account_id"])
        ]
        if strategy_rows:
            plan["skip_existing_strategy_count"] += 1
            plan["actions"].append({
                "type": "skip_strategy",
                "broker_order_id": item.broker_order_id,
                "stock_code": item.stock_code,
                "matched_ids": [int(row["id"]) for row in strategy_rows],
            })
            continue

        unclassified_rows = [
            row for row in matched
            if not normalize_text(row["strategy_id"]) and not normalize_text(row["virtual_account_id"])
        ]
        if unclassified_rows:
            canonical = sorted(unclassified_rows, key=lambda row: int(row["id"]))[0]
            same_as_import = (
                normalize_text(canonical["trade_date"]) == item.trade_date
                and round(float(canonical["commission"] or 0.0), 2) == item.commission
                and round(float(canonical["stamp_tax"] or 0.0), 2) == item.stamp_tax
                and round(float(canonical["transfer_fee"] or 0.0), 2) == item.transfer_fee
                and normalize_text(canonical["stock_name"]) == item.stock_name
            )
            extra_ids = [int(row["id"]) for row in unclassified_rows[1:]]
            if same_as_import and not extra_ids:
                plan["skip_same_unclassified_count"] += 1
                plan["actions"].append({
                    "type": "skip_same",
                    "broker_order_id": item.broker_order_id,
                    "stock_code": item.stock_code,
                    "matched_id": int(canonical["id"]),
                })
                continue
            if extra_ids:
                plan["delete_count"] += len(extra_ids)
            plan["update_count"] += 1
            plan["actions"].append({
                "type": "update",
                "record_id": int(canonical["id"]),
                "delete_ids": extra_ids,
                "trade": item,
            })
            continue

        plan["insert_count"] += 1
        plan["actions"].append({
            "type": "insert",
            "trade": item,
        })
    return plan


def apply_plan(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, int]:
    cur = conn.cursor()
    counts = {"inserted": 0, "updated": 0, "deleted": 0}
    for action in plan["actions"]:
        action_type = action["type"]
        if action_type == "insert":
            trade: ImportedTrade = action["trade"]
            cur.execute(
                """
                INSERT INTO trades (
                    trade_id, broker_order_id, stock_code, stock_name, direction,
                    price, volume, amount, commission, stamp_tax, transfer_fee,
                    trade_date, source, remark, created_at, strategy_id, virtual_account_id, intent_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '')
                """,
                (
                    unique_trade_id(f"hist_{trade.trade_date}_{trade.stock_code}_{trade.direction}_{trade.broker_order_id}"),
                    trade.broker_order_id,
                    trade.stock_code,
                    trade.stock_name,
                    trade.direction,
                    trade.price,
                    trade.volume,
                    trade.amount,
                    trade.commission,
                    trade.stamp_tax,
                    trade.transfer_fee,
                    trade.trade_date,
                    trade.source,
                    trade.remark,
                    trade.created_at,
                ),
            )
            counts["inserted"] += 1
        elif action_type == "update":
            trade: ImportedTrade = action["trade"]
            cur.execute(
                """
                UPDATE trades
                SET stock_name = ?, amount = ?, commission = ?, stamp_tax = ?, transfer_fee = ?,
                    trade_date = ?, source = ?, remark = ?, created_at = ?,
                    strategy_id = '', virtual_account_id = '', intent_id = ''
                WHERE id = ?
                """,
                (
                    trade.stock_name,
                    trade.amount,
                    trade.commission,
                    trade.stamp_tax,
                    trade.transfer_fee,
                    trade.trade_date,
                    trade.source,
                    trade.remark,
                    trade.created_at,
                    int(action["record_id"]),
                ),
            )
            counts["updated"] += int(cur.rowcount or 0)
            delete_ids = [int(item) for item in action.get("delete_ids", []) if int(item) > 0]
            if delete_ids:
                placeholders = ",".join("?" for _ in delete_ids)
                cur.execute(f"DELETE FROM trades WHERE id IN ({placeholders})", delete_ids)
                counts["deleted"] += int(cur.rowcount or 0)
    conn.commit()
    return counts


def serializable_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: value
        for key, value in plan.items()
        if key != "actions"
    }
    summary["sample_actions"] = []
    for action in plan["actions"][:20]:
        action_type = action["type"]
        if action_type in {"insert", "update"}:
            trade: ImportedTrade = action["trade"]
            payload = {
                "type": action_type,
                "broker_order_id": trade.broker_order_id,
                "stock_code": trade.stock_code,
                "direction": trade.direction,
                "trade_date": trade.trade_date,
                "volume": trade.volume,
            }
            if action_type == "update":
                payload["record_id"] = action["record_id"]
                payload["delete_ids"] = action.get("delete_ids", [])
            summary["sample_actions"].append(payload)
        else:
            summary["sample_actions"].append(action)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settlement", required=True)
    parser.add_argument("--trades", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settlement_name, settlement_rows = read_xlsx_rows(Path(args.settlement))
    trades_name, trade_rows = read_xlsx_rows(Path(args.trades))
    settlement_lookup = build_fee_lookup(settlement_rows)
    imported_trades = build_imported_trades(trade_rows, settlement_lookup)

    db_path = Path(args.db)
    conn = sqlite3.connect(db_path)
    try:
        existing_rows = fetch_existing_rows(conn)
        plan = plan_import(existing_rows, imported_trades)
        summary: dict[str, Any] = {
            "settlement_sheet": settlement_name,
            "settlement_rows": len(settlement_rows),
            "history_sheet": trades_name,
            "history_rows": len(trade_rows),
            "buy_sell_import_rows": len(imported_trades),
            "db_before_count": len(existing_rows),
            "plan": serializable_plan_summary(plan),
            "applied": False,
        }
        if args.apply:
            backup_path = backup_db(db_path)
            summary["backup_path"] = str(backup_path)
            counts = apply_plan(conn, plan)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM trades")
            summary["db_after_count"] = int(cur.fetchone()[0])
            summary["apply_counts"] = counts
            summary["applied"] = True
        Path(args.summary_output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        conn.close()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
