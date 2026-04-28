"""ETF 手动委托对话框。"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ETFManualOrderDialog(QDialog):
    """Manual order dialog for ETF strategy."""

    def __init__(
        self,
        engine,
        *,
        name_resolver: Optional[Callable[[str], str]] = None,
        refresh_callback: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self._name_resolver = name_resolver or (lambda code: code or "")
        self._refresh_callback = refresh_callback
        self._form_updating = False

        self.setWindowTitle("手动委托")
        self.setMinimumWidth(560)
        self.resize(560, 560)
        self._setup_ui()
        self.reload_symbol_options()
        self.prefill_from_current_holding()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        title = QLabel("手动委托")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(6)
        layout.addLayout(form)

        self.symbol_combo = QComboBox()
        self.symbol_combo.setEditable(True)
        self.symbol_combo.setPlaceholderText("输入 ETF 代码，如 510300 或 159949")
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_changed)
        form.addRow("代码:", self.symbol_combo)

        self.name_label = QLabel("-")
        form.addRow("名称:", self.name_label)

        self.action_combo = QComboBox()
        self.action_combo.addItem("买入", "BUY")
        self.action_combo.addItem("卖出", "SELL")
        self.action_combo.currentIndexChanged.connect(self._on_action_changed)
        form.addRow("方向:", self.action_combo)

        self.risk_label = QLabel("ETF手动委托")
        form.addRow("风控:", self.risk_label)

        self.price_input = QLineEdit()
        self.price_input.setPlaceholderText("委托价格")
        self.fetch_tick_price_btn = QPushButton("取一档价")
        self.fetch_tick_price_btn.setToolTip("按当前委托方向自动填入买一/卖一价格，失败时回退到实时价")
        self.fetch_tick_price_btn.clicked.connect(self._fill_price_from_tick)
        price_row = QWidget()
        price_layout = QHBoxLayout(price_row)
        price_layout.setContentsMargins(0, 0, 0, 0)
        price_layout.setSpacing(6)
        price_layout.addWidget(self.price_input, stretch=1)
        price_layout.addWidget(self.fetch_tick_price_btn)
        form.addRow("价格:", price_row)

        self.volume_input = QLineEdit()
        self.volume_input.setPlaceholderText("委托数量(手,1手=100股)")
        form.addRow("数量(手):", self.volume_input)

        self.amount_label = QLabel("-")
        form.addRow("委托金额:", self.amount_label)

        self.context_label = QLabel("-")
        self.context_label.setStyleSheet("color:#888888;font-size:11px;")
        self.context_label.setWordWrap(True)
        form.addRow("当前状态:", self.context_label)

        self.decision_note = QPlainTextEdit()
        self.decision_note.setReadOnly(True)
        self.decision_note.setPlaceholderText("这里会展示 ETF 轮动手动委托说明。")
        self.decision_note.setMaximumHeight(150)
        layout.addWidget(self.decision_note)

        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton("清空委托")
        self.clear_btn.clicked.connect(self._clear_order_form)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()

        self.submit_btn = QPushButton("提交委托")
        self.submit_btn.setFixedHeight(38)
        self.submit_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: white; font-size: 14px; "
            "font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #106ebe; }"
            "QPushButton:disabled { background-color: #999999; }"
        )
        self.submit_btn.clicked.connect(self._submit)
        btn_row.addWidget(self.submit_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

        self.price_input.textChanged.connect(self._update_amount)
        self.volume_input.textChanged.connect(self._update_amount)

    def reload_symbol_options(self) -> None:
        current_text = self.symbol_combo.currentText().strip()
        codes: list[str] = []
        holding = str(getattr(self.engine.state, "current_holding", "") or "").strip()
        if holding:
            codes.append(holding)
        for code in list(getattr(self.engine.config, "etf_pool", []) or []):
            code = str(code or "").strip()
            if code and code not in codes:
                codes.append(code)

        self.symbol_combo.blockSignals(True)
        self.symbol_combo.clear()
        for code in codes:
            label = self._name_resolver(code)
            self.symbol_combo.addItem(f"{code}  {label}".strip(), code)
        self.symbol_combo.setEditText(current_text or (codes[0] if codes else ""))
        self.symbol_combo.blockSignals(False)
        self._refresh_symbol_context()

    def prefill_from_current_holding(self) -> None:
        holding = str(getattr(self.engine.state, "current_holding", "") or "").strip()
        if holding:
            self._set_direction("SELL")
            self.symbol_combo.setEditText(holding)
        else:
            self._set_direction("BUY")
        self._refresh_symbol_context()
        self._refresh_volume_suggestion()
        self._update_note()
        self._update_amount()

    def _set_direction(self, action: str) -> None:
        idx = self.action_combo.findData("SELL" if action == "SELL" else "BUY")
        if idx < 0:
            idx = 0
        self.action_combo.setCurrentIndex(idx)

    def _selected_action(self) -> str:
        return str(self.action_combo.currentData() or "BUY")

    def _current_code(self) -> str:
        data = self.symbol_combo.currentData()
        text = self.symbol_combo.currentText().strip()
        if isinstance(data, str) and data.strip() and text.startswith(data):
            return data.strip()
        return text.split()[0] if text else ""

    def _on_symbol_changed(self, _text: str = "") -> None:
        if self._form_updating:
            return
        self._refresh_symbol_context()
        self._refresh_volume_suggestion()
        self._update_note()
        self._update_amount()

    def _on_action_changed(self, _index: int = 0) -> None:
        if self._form_updating:
            return
        self._refresh_volume_suggestion()
        self._update_note()
        self._update_amount()

    def _refresh_symbol_context(self) -> None:
        code = self._current_code()
        self.name_label.setText(self._name_resolver(code) if code else "-")
        price = 0.0
        try:
            price = float(self.engine.executor.get_current_price(code)) if code else 0.0
        except Exception:
            price = 0.0
        if price > 0 and not self.price_input.text().strip():
            self.price_input.setText(f"{price:.3f}".rstrip("0").rstrip("."))

        sellable = 0
        cost_price = 0.0
        try:
            if code:
                sellable, cost_price = self.engine.executor.query_sellable_position(code)
        except Exception:
            sellable, cost_price = 0, 0.0
        if sellable > 0:
            self.context_label.setText(
                f"可卖数量 {sellable} 股（{sellable // 100}手），成本价 {cost_price:.3f} 元"
            )
        elif code:
            self.context_label.setText("当前无可卖持仓，可切换到买入方向手动下单。")
        else:
            self.context_label.setText("请输入 ETF 代码。")

    def _refresh_volume_suggestion(self) -> None:
        code = self._current_code()
        if not code:
            return
        if self._selected_action() != "SELL":
            return
        try:
            sellable, _ = self.engine.executor.query_sellable_position(code)
        except Exception:
            sellable = 0
        lots = int(sellable or 0) // 100
        if lots > 0:
            self.volume_input.setText(str(lots))

    @staticmethod
    def _normalize_xt_code(code: str) -> str:
        value = str(code or "").strip().upper()
        if not value:
            return ""
        if "." in value:
            return value
        if value.startswith(("5", "6", "9")):
            return f"{value}.SH"
        if value.startswith(("0", "1", "2", "3")):
            return f"{value}.SZ"
        return value

    def _resolve_level1_price_from_tick(self, code: str, action: str) -> tuple[float, str]:
        xt_code = self._normalize_xt_code(code)
        if not xt_code:
            return 0.0, "请先输入有效的 ETF 代码"
        try:
            from xtquant import xtdata

            full_tick = xtdata.get_full_tick([xt_code]) or {}
            tick = full_tick.get(xt_code)
            if isinstance(tick, dict) and tick:
                price_key = "askPrice" if action == "BUY" else "bidPrice"
                price_label = "卖一价" if action == "BUY" else "买一价"
                prices = list(tick.get(price_key, []) or [])
                if prices:
                    level1 = float(prices[0] or 0.0)
                    if level1 > 0:
                        return level1, price_label
                fallback = float(tick.get("lastPrice", 0) or 0.0)
                if fallback > 0:
                    return fallback, f"{price_label}缺失，已回退到最新价"
        except Exception:
            pass

        try:
            price = float(self.engine.executor.get_current_price(code) or 0.0)
        except Exception:
            price = 0.0
        if price > 0:
            return price, "一档价不可用，已回退到实时价"
        return 0.0, f"{xt_code} 未返回可用行情价格"

    def _fill_price_from_tick(self) -> None:
        code = self._current_code()
        if not code:
            QMessageBox.warning(self, "提示", "请先输入 ETF 代码")
            return
        price, message = self._resolve_level1_price_from_tick(code, self._selected_action())
        if price <= 0:
            QMessageBox.warning(self, "提示", message)
            return
        self.price_input.setText(f"{price:.3f}".rstrip("0").rstrip("."))
        self._update_amount()
        self._update_note(message)

    def _update_amount(self) -> None:
        try:
            price = float(self.price_input.text().strip())
            lots = int(self.volume_input.text().strip())
            amount = price * lots * 100
            self.amount_label.setText(f"¥{amount:,.2f}")
        except (ValueError, TypeError):
            self.amount_label.setText("-")

    def _update_note(self, extra: str = "") -> None:
        action_text = "买入" if self._selected_action() == "BUY" else "卖出"
        code = self._current_code() or "-"
        parts = [
            f"操作建议: 手动{action_text}",
            f"标的代码: {code}",
            "说明: 该委托来自 ETF 轮动手动委托，提交后仍由 ETF 引擎执行，并同步策略状态、台账和交易记录。",
            "数量口径: 1手=100股，买入/卖出均按手数提交。",
        ]
        if extra:
            parts.append(f"行情: {extra}")
        self.decision_note.setPlainText("\n".join(parts))

    def _clear_order_form(self) -> None:
        self._form_updating = True
        self.symbol_combo.setEditText("")
        self.name_label.setText("-")
        self._set_direction("BUY")
        self.price_input.clear()
        self.volume_input.clear()
        self.amount_label.setText("-")
        self.context_label.setText("请输入 ETF 代码。")
        self.decision_note.clear()
        self._form_updating = False

    def _submit(self) -> None:
        code = self._current_code()
        if not code:
            QMessageBox.warning(self, "提示", "请输入有效的 ETF 代码")
            return

        action = self._selected_action()
        try:
            price = float(self.price_input.text().strip())
            lots = int(self.volume_input.text().strip())
        except (ValueError, TypeError):
            QMessageBox.warning(self, "提示", "请输入有效的价格和数量")
            return

        quantity = lots * 100
        amount = price * quantity
        if price <= 0:
            QMessageBox.warning(self, "提示", "价格必须大于 0")
            return
        if quantity <= 0:
            QMessageBox.warning(self, "提示", "委托数量必须大于 0")
            return

        action_text = "买入" if action == "BUY" else "卖出"
        confirm = QMessageBox.question(
            self,
            "委托确认",
            f"确认{action_text} {code} {lots}手(={quantity}股) @ ¥{price:.3f}？\n"
            f"委托金额: ¥{amount:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        result = self.engine.execute_manual(
            action=action,
            code=code,
            quantity=quantity,
            amount=amount,
            price=price,
        )
        if self._refresh_callback:
            self._refresh_callback()

        if result.get("success"):
            QMessageBox.information(self, "提示", result.get("message") or "委托提交成功")
            self.accept()
        else:
            QMessageBox.warning(self, "提示", result.get("message") or "委托提交失败")
