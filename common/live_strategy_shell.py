from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QSplitter, QVBoxLayout, QWidget

from common.strategy_panel_context import StrategyPanelContext
from common.strategy_trade_panel import StrategyTradePanel


class LiveStrategyShell(QWidget):
    """Reusable shell for live strategy panels."""

    def __init__(
        self,
        context: StrategyPanelContext,
        left_panel: QWidget,
        content_panel: QWidget,
        *,
        footer_panel: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.left_panel = left_panel
        self.content_panel = content_panel
        self.footer_panel = footer_panel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        horizontal_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        horizontal_splitter.addWidget(self.left_panel)
        horizontal_splitter.addWidget(self.content_panel)
        horizontal_splitter.setSizes([320, 980])
        horizontal_splitter.setMinimumHeight(320)
        self.horizontal_splitter = horizontal_splitter

        self.strategy_trade_panel = StrategyTradePanel(
            self.context.strategy_id,
            self.context.strategy_name,
            self.context.virtual_account_id,
            self,
        )
        self.strategy_trade_panel.setMinimumHeight(120)

        vertical_splitter = QSplitter(Qt.Orientation.Vertical, self)
        vertical_splitter.setHandleWidth(14)
        vertical_splitter.setChildrenCollapsible(False)
        vertical_splitter.addWidget(horizontal_splitter)
        vertical_splitter.addWidget(self.strategy_trade_panel)
        vertical_splitter.setStretchFactor(0, 1)
        vertical_splitter.setStretchFactor(1, 0)
        vertical_splitter.setSizes([700, 150])
        self.vertical_splitter = vertical_splitter
        layout.addWidget(vertical_splitter, stretch=1)

        if self.footer_panel is not None:
            footer_host = QWidget(self)
            footer_layout = QHBoxLayout(footer_host)
            footer_layout.setContentsMargins(4, 2, 4, 2)
            footer_layout.addWidget(self.footer_panel)
            layout.addWidget(footer_host)
