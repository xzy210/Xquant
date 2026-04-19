"""ETF 手动委托对话框。"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ETFManualOrderDialog(QDialog):
    """Manual order dialog for ETF strategy.

    与 AI 的“手动委托”保持同类交互：先打开表单，确认价格/数量后再提交，
    只是默认值会优先按 ETF 当前持仓预填成“卖出当前持仓”。
    """

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

        self.setWindowTitle("手动委托")
        self.setMinimumWidth(560)
        self.resize(560, 420)
        self._setup_ui()
        self.reload_symbol_options()
        self.prefill_from_current_holding()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.note_label = QLabel(
            "说明：默认预填当前持仓卖出，也可切换成手动买入。提交后仍由 ETF 引擎执行，并同步策略状态。"
        )
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color:#6B7280;font-size:11px;")
        layout.addWidget(self.note_label)

        form = QFormLayout()
        form.setSpacing(6)
        layout.addLayout(form)

        self.symbol_combo = QComboBox()
        self.symbol_combo.setEditable(True)
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_changed)
        form.addRow("标的:", self.symbol_combo)

        self.name_label = QLabel("-")
        form.addRow("名称:", self.name_label)

        self.action_combo = QComboBox()
        self.action_combo.addItem("买入", "BUY")
        self.action_combo.addItem("卖出", "SELL")
        self.action_combo.currentIndexChanged.connect(self._on_action_changed)
        form.addRow("方向:", self.action_combo)

        self.price_spin = QDoubleSpinBox()
        self.price_spin.setDecimals(3)
        self.price_spin.setRange(0.0, 9999.999)
        self.price_spin.setSingleStep(0.001)
        self.price_spin.setSuffix(" 元")
        form.addRow("价格:", self.price_spin)

        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setDecimals(0)
        self.amount_spin.setRange(0.0, 100_000_000.0)
        self.amount_spin.setSingleStep(1000.0)
        self.amount_spin.setSuffix(" 元")
        form.addRow("买入金额:", self.amount_spin)

        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(0, 10_000_000)
        self.quantity_spin.setSingleStep(100)
        self.quantity_spin.setSuffix(" 股")
        form.addRow("卖出数量:", self.quantity_spin)

        self.context_label = QLabel("-")
        self.context_label.setStyleSheet("color:#888888;font-size:11px;")
        self.context_label.setWordWrap(True)
        form.addRow("当前状态:", self.context_label)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        btn_row.addStretch()

        self.submit_btn = QPushButton("提交委托")
        self.submit_btn.setFixedHeight(34)
        self.submit_btn.setStyleSheet(
            "QPushButton{background:#2563EB;color:white;padding:6px 16px;border-radius:6px;font-weight:600;}"
            "QPushButton:hover{background:#1D4ED8;}"
            "QPushButton:disabled{background:#93C5FD;color:white;}"
        )
        self.submit_btn.clicked.connect(self._submit)
        btn_row.addWidget(self.submit_btn)
        layout.addLayout(btn_row)

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
            self.action_combo.setCurrentIndex(self.action_combo.findData("SELL"))
            self.symbol_combo.setEditText(holding)
        else:
            self.action_combo.setCurrentIndex(self.action_combo.findData("BUY"))
        self._refresh_symbol_context()
        self._on_action_changed()

    def _current_code(self) -> str:
        data = self.symbol_combo.currentData()
        if isinstance(data, str) and data.strip():
            text = self.symbol_combo.currentText().strip()
            if text.startswith(data):
                return data.strip()
        return self.symbol_combo.currentText().strip().split()[0] if self.symbol_combo.currentText().strip() else ""

    def _on_symbol_changed(self) -> None:
        self._refresh_symbol_context()

    def _refresh_symbol_context(self) -> None:
        code = self._current_code()
        self.name_label.setText(self._name_resolver(code) if code else "-")
        price = 0.0
        try:
            price = float(self.engine.executor.get_current_price(code)) if code else 0.0
        except Exception:
            price = 0.0
        if price > 0:
            self.price_spin.setValue(price)

        sellable = 0
        cost_price = 0.0
        try:
            if code:
                sellable, cost_price = self.engine.executor.query_sellable_position(code)
        except Exception:
            sellable, cost_price = 0, 0.0
        if sellable > 0:
            self.quantity_spin.setValue(sellable)
            self.context_label.setText(
                f"可卖数量 {sellable} 股，成本价 {cost_price:.3f} 元"
            )
        else:
            self.context_label.setText("当前无可卖持仓，可切换到买入方向手动下单。")

    def _on_action_changed(self) -> None:
        action = self.action_combo.currentData() or "BUY"
        is_buy = action == "BUY"
        self.amount_spin.setEnabled(is_buy)
        self.quantity_spin.setEnabled(not is_buy)

    def _submit(self) -> None:
        code = self._current_code()
        if not code:
            QMessageBox.warning(self, "提示", "请输入有效的 ETF 代码")
            return

        action = self.action_combo.currentData() or "BUY"
        price = float(self.price_spin.value() or 0.0)
        amount = float(self.amount_spin.value() or 0.0)
        quantity = int(self.quantity_spin.value() or 0)

        if action == "BUY" and amount <= 0:
            QMessageBox.warning(self, "提示", "买入金额必须大于 0")
            return
        if action == "SELL" and quantity <= 0:
            QMessageBox.warning(self, "提示", "卖出数量必须大于 0")
            return
        if price <= 0:
            QMessageBox.warning(self, "提示", "价格必须大于 0")
            return

        action_text = "买入" if action == "BUY" else "卖出"
        if action == "BUY":
            summary = f"确认{action_text} {code} {amount:,.0f}元"
        else:
            summary = f"确认{action_text} {code} {quantity}股"
        confirm = QMessageBox.question(
            self,
            "委托确认",
            f"{summary} @ ¥{price:.3f}？",
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
