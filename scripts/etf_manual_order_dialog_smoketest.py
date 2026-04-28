"""Headless smoketest for ETF manual order dialog + manual execution args."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from live_rotation.manual_order_dialog import ETFManualOrderDialog  # noqa: E402


class FakeExecutor:
    def get_current_price(self, code: str) -> float:
        return 1.234 if code else 0.0

    def query_sellable_position(self, code: str):
        return (1200, 1.111) if code == "510300" else (0, 0.0)


class FakeEngine:
    def __init__(self) -> None:
        self.state = SimpleNamespace(current_holding="510300")
        self.config = SimpleNamespace(etf_pool=["510300", "510500"])
        self.executor = FakeExecutor()
        self.calls = []

    def execute_manual(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "message": "委托提交成功"}


def test_prefill_from_holding() -> None:
    engine = FakeEngine()
    dialog = ETFManualOrderDialog(
        engine,
        name_resolver=lambda code: {"510300": "沪深300ETF"}.get(code, code),
    )
    assert dialog.action_combo.currentData() == "SELL"
    assert dialog._current_code() == "510300"
    assert dialog.volume_input.text() == "12"
    assert float(dialog.price_input.text()) > 0
    assert dialog.amount_label.text() == "¥1,480.80"
    print("[prefill_from_holding] OK")


def test_submit_sell_delegates_to_engine() -> None:
    engine = FakeEngine()
    dialog = ETFManualOrderDialog(engine)
    dialog.action_combo.setCurrentIndex(dialog.action_combo.findData("SELL"))
    dialog.symbol_combo.setEditText("510300")
    dialog.price_input.setText("1.235")
    dialog.volume_input.setText("8")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
         patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
        dialog._submit()

    assert engine.calls, "execute_manual should be called"
    call = engine.calls[-1]
    assert call["action"] == "SELL"
    assert call["code"] == "510300"
    assert call["quantity"] == 800
    assert abs(call["amount"] - 988.0) < 1e-9
    assert abs(call["price"] - 1.235) < 1e-9
    print("[submit_sell_delegates_to_engine] OK")


def test_submit_buy_delegates_quantity_amount_and_price() -> None:
    engine = FakeEngine()
    dialog = ETFManualOrderDialog(engine)
    dialog.action_combo.setCurrentIndex(dialog.action_combo.findData("BUY"))
    dialog.symbol_combo.setEditText("510500")
    dialog.price_input.setText("0.998")
    dialog.volume_input.setText("200")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
         patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
        dialog._submit()

    call = engine.calls[-1]
    assert call["action"] == "BUY"
    assert call["code"] == "510500"
    assert call["quantity"] == 20000
    assert abs(call["amount"] - 19960.0) < 1e-9
    assert abs(call["price"] - 0.998) < 1e-9
    print("[submit_buy_delegates_quantity_amount_and_price] OK")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    test_prefill_from_holding()
    test_submit_sell_delegates_to_engine()
    test_submit_buy_delegates_quantity_amount_and_price()
    print("ALL_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
