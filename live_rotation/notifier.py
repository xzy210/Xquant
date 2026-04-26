"""
ETF轮动实盘 - 通知模块

复用 trading_app 的企微通知能力，提供 ETF 轮动实盘专用的消息模板。
"""
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

# 确保 trading_app 可以被导入
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _get_notifier():
    """获取通知管理器（延迟导入避免循环依赖）"""
    try:
        from trading_app.notifier import get_notification_manager
        return get_notification_manager()
    except ImportError:
        logger.warning("无法导入通知管理器，通知功能不可用")
        return None


class RotationNotifier:
    """ETF轮动实盘通知器"""

    def __init__(self, etf_name_map: Optional[Dict[str, str]] = None):
        self.etf_name_map = etf_name_map or {}

    def _code_name(self, code: str) -> str:
        name = self.etf_name_map.get(code, "")
        return f"{code}({name})" if name else code

    def send_signal(self, signal: str, scores: Dict[str, float],
                    current_holding: Optional[str],
                    target_code: Optional[str] = None,
                    extra: str = "") -> Tuple[bool, str]:
        """发送策略信号通知"""
        mgr = _get_notifier()
        if not mgr or not mgr.is_enabled():
            return False, "通知未启用"

        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        signal_emoji = {
            "HOLD": "🔵", "SWITCH": "🔄", "SELL_ALL": "🔴",
            "BUY": "🟢", "NO_ACTION": "⚪"
        }
        emoji = signal_emoji.get(signal, "📊")

        lines = [
            f"**{emoji} ETF轮动实盘信号**",
            f"> 时间：{now}",
            f"> 信号：**{signal}**",
            ""
        ]

        # 当前持仓
        if current_holding:
            lines.append(f"当前持仓：**{self._code_name(current_holding)}**")
        else:
            lines.append("当前持仓：**空仓**")

        # 目标标的
        if target_code and signal in ("SWITCH", "BUY"):
            lines.append(f"目标标的：**{self._code_name(target_code)}**")

        # 得分列表
        if scores:
            lines.append("")
            lines.append("**各ETF得分排名：**")
            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            for i, (code, score) in enumerate(sorted_scores, 1):
                marker = " 👈" if code == current_holding else ""
                star = " ⭐" if code == target_code else ""
                color = "info" if score >= 0 else "comment"
                lines.append(
                    f"{i}. {self._code_name(code)}: "
                    f"<font color=\"{color}\">{score:+.4f}</font>"
                    f"{marker}{star}"
                )

        if extra:
            lines.append(f"\n{extra}")

        content = "\n".join(lines)
        return mgr.send_markdown(content)

    def send_trade_result(self, action: str, code: str,
                          quantity: int, price: float,
                          success: bool, message: str = "",
                          reason: str = "") -> Tuple[bool, str]:
        """发送交易执行结果通知"""
        mgr = _get_notifier()
        if not mgr or not mgr.is_enabled():
            return False, "通知未启用"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "✅ 成功" if success else "❌ 失败"
        amount = quantity * price

        lines = [
            f"**{status} ETF轮动实盘交易**",
            f"> 时间：{now}",
            f"> 操作：**{action}** {self._code_name(code)}",
            f"> 数量：{quantity} 股",
            f"> 价格：{price:.3f} 元",
            f"> 金额：{amount:,.2f} 元",
        ]

        if reason:
            lines.append(f"> 原因：{reason}")
        if not success and message:
            lines.append(f"> <font color=\"warning\">错误：{message}</font>")

        content = "\n".join(lines)
        return mgr.send_markdown(content)

    def send_daily_report(self, holding: Optional[str],
                          scores: Dict[str, float],
                          pnl_today: float = 0.0,
                          total_pnl: float = 0.0,
                          signal: str = "") -> Tuple[bool, str]:
        """发送每日简报"""
        mgr = _get_notifier()
        if not mgr or not mgr.is_enabled():
            return False, "通知未启用"

        now = datetime.now().strftime("%Y-%m-%d")
        lines = [
            f"**📋 ETF轮动实盘日报 {now}**",
            ""
        ]

        if holding:
            lines.append(f"持仓：**{self._code_name(holding)}**")
        else:
            lines.append("持仓：**空仓**")

        if signal:
            lines.append(f"今日信号：**{signal}**")

        pnl_color = "info" if pnl_today >= 0 else "warning"
        total_color = "info" if total_pnl >= 0 else "warning"
        lines.append(f"今日盈亏：<font color=\"{pnl_color}\">{pnl_today:+,.2f}</font>")
        lines.append(f"累计盈亏：<font color=\"{total_color}\">{total_pnl:+,.2f}</font>")

        if scores:
            lines.append("")
            lines.append("**得分快照：**")
            for code, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
                marker = " 👈" if code == holding else ""
                lines.append(f"  {self._code_name(code)}: {score:+.4f}{marker}")

        content = "\n".join(lines)
        return mgr.send_markdown(content)
