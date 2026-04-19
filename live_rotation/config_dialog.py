"""ETF strategy configuration dialog."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from common.strategy_config_dialog_base import BaseStrategyConfigDialog

if TYPE_CHECKING:
    from .widget import ETFRotationLiveWidget


class ETFStrategyConfigDialog(BaseStrategyConfigDialog):
    """Standalone configuration dialog for the ETF strategy."""

    def __init__(self, owner: "ETFRotationLiveWidget", parent: Optional[object] = None) -> None:
        super().__init__(title="ETF 策略配置", min_width=780, initial_height=680, parent=parent)
        self.owner = owner
        self.content_layout.addWidget(self.owner._etf_panel)
        self.content_layout.addWidget(self.owner._config_panel)
        self.btn_close, self.btn_unlock = self.setup_footer(
            close_text="关闭",
            close_handler=self.reject,
            unlock_text="🔓 解锁编辑",
            unlock_handler=self._unlock_for_edit,
        )

    def prepare_for_open(self) -> None:
        self.owner._reload_config_dialog_data()
        self.owner._etf_panel.setVisible(True)
        self.owner._config_panel.setVisible(True)
        self.owner._lock_config_panels()
        self.reset_unlock_state()

    def reset_unlock_state(self) -> None:
        self.btn_unlock.setText("🔓 解锁编辑")
        self.btn_unlock.setEnabled(True)

    def _unlock_for_edit(self) -> None:
        if self.owner.request_unlock_config():
            self.btn_unlock.setText("✏ 编辑中…")
            self.btn_unlock.setEnabled(False)

    def reject(self) -> None:
        self.owner._lock_config_panels()
        self.reset_unlock_state()
        super().reject()
