"""Account and runtime status widgets for the ETF rotation live panel."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel, QSizePolicy, QVBoxLayout, QWidget


class ETFRotationStatusPanel(QWidget):
    """Render ETF strategy account and runtime status labels."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        asset_group = QGroupBox("账户概览（ETF策略主账本）", self)
        asset_form = QFormLayout(asset_group)
        asset_form.setSpacing(4)
        self.lbl_strategy_total_asset = QLabel("-", self)
        self.lbl_strategy_available_cash = QLabel("-", self)
        self.lbl_strategy_market_value = QLabel("-", self)
        self.lbl_strategy_total_pnl = QLabel("-", self)
        for label in (
            self.lbl_strategy_total_asset,
            self.lbl_strategy_available_cash,
            self.lbl_strategy_market_value,
            self.lbl_strategy_total_pnl,
        ):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet("font-weight:bold;")
        self.lbl_strategy_total_asset.setStyleSheet("color:#0078d4;font-size:13px;font-weight:bold;")
        self.lbl_strategy_total_pnl.setStyleSheet("font-size:13px;font-weight:bold;")
        asset_form.addRow("总资产:", self.lbl_strategy_total_asset)
        asset_form.addRow("可用资金:", self.lbl_strategy_available_cash)
        asset_form.addRow("持仓市值:", self.lbl_strategy_market_value)
        asset_form.addRow("总盈亏:", self.lbl_strategy_total_pnl)
        layout.addWidget(asset_group)

        status_group = QGroupBox("当前状态（ETF轮动实盘）", self)
        status_form = QFormLayout(status_group)
        status_form.setSpacing(4)

        self.lbl_holding = QLabel("-", self)
        self.lbl_holding.setStyleSheet("color:#0078d4;font-size:14px;font-weight:bold;")
        self.lbl_buy_price = QLabel("-", self)
        self.lbl_current_price = QLabel("-", self)
        self.lbl_pnl = QLabel("-", self)
        self.lbl_pnl.setStyleSheet("font-size:13px;font-weight:bold;")
        self.lbl_signal = QLabel("-", self)
        self.lbl_last_check = QLabel("-", self)
        self.lbl_last_check.setStyleSheet("color:#888888;font-size:11px;")
        self.lbl_data_status = QLabel("-", self)
        self.lbl_data_status.setStyleSheet("font-size:11px;")
        self.lbl_data_status.setWordWrap(True)
        self.lbl_data_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_data_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_data_version = QLabel("-", self)
        self.lbl_data_version.setStyleSheet("font-size:11px;color:#94A3B8;")
        self.lbl_data_version.setWordWrap(True)
        self.lbl_data_version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_data_version.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_executor = QLabel("-", self)

        status_form.addRow("持仓标的:", self.lbl_holding)
        status_form.addRow("买入价格:", self.lbl_buy_price)
        status_form.addRow("当前价格:", self.lbl_current_price)
        status_form.addRow("浮动盈亏:", self.lbl_pnl)
        status_form.addRow("最近信号:", self.lbl_signal)
        status_form.addRow("最近检查:", self.lbl_last_check)
        status_form.addRow("数据状态:", self.lbl_data_status)
        status_form.addRow("数据版本:", self.lbl_data_version)
        status_form.addRow("执行器:", self.lbl_executor)
        layout.addWidget(status_group)

    def set_holding(self, text: str) -> None:
        self.lbl_holding.setText(text)

    def set_buy_price(self, text: str) -> None:
        self.lbl_buy_price.setText(text)

    def set_current_price(self, text: str, tooltip: str = "") -> None:
        self.lbl_current_price.setText(text)
        self.lbl_current_price.setToolTip(tooltip)

    def set_position_pnl(self, text: str, style_sheet: str) -> None:
        self.lbl_pnl.setText(text)
        self.lbl_pnl.setStyleSheet(style_sheet)

    def set_signal(self, text: str, style_sheet: str | None = None) -> None:
        self.lbl_signal.setText(text)
        if style_sheet is not None:
            self.lbl_signal.setStyleSheet(style_sheet)

    def set_last_check(self, text: str) -> None:
        self.lbl_last_check.setText(text)

    def set_account_values(
        self,
        *,
        total_asset: float,
        available_cash: float,
        market_value: float,
        total_pnl: float,
    ) -> None:
        self.lbl_strategy_total_asset.setText(f"{total_asset:,.2f} 元")
        self.lbl_strategy_available_cash.setText(f"{available_cash:,.2f} 元")
        self.lbl_strategy_market_value.setText(f"{market_value:,.2f} 元")
        pnl_color = "#DC2626" if total_pnl >= 0 else "#16A34A"
        self.lbl_strategy_total_pnl.setText(f"{total_pnl:+,.2f} 元")
        self.lbl_strategy_total_pnl.setStyleSheet(f"color:{pnl_color};font-size:13px;font-weight:bold;")

    def set_data_status(self, text: str, *, tooltip: str, ok: bool, error_color: str = "#DC2626") -> None:
        self.lbl_data_status.setToolTip(tooltip)
        self.lbl_data_status.setText(text)
        color = "#16A34A" if ok else error_color
        self.lbl_data_status.setStyleSheet(f"color:{color};font-size:11px;")

    def set_data_version(self, text: str, *, tooltip: str, ok: bool) -> None:
        self.lbl_data_version.setText(text)
        self.lbl_data_version.setToolTip(tooltip)
        color = "#94A3B8" if ok else "#EA580C"
        self.lbl_data_version.setStyleSheet(f"font-size:11px;color:{color};")

    def set_executor(self, text: str, *, connected: bool) -> None:
        self.lbl_executor.setText(text)
        self.lbl_executor.setStyleSheet("color:#16A34A;" if connected else "color:#DC2626;")
