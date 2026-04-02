"""AI 决策结果通知推送

将巡检/决策结果通过企业微信推送。复用已有的 NotificationManager。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_notification_manager():
    try:
        from notifier import get_notification_manager
    except ImportError:
        from trading_app.notifier import get_notification_manager
    return get_notification_manager()


def notify_scan_complete(
    scan_type: str,
    results: List[Dict[str, Any]],
    *,
    group_name: str = "",
) -> bool:
    """Push a summary of scan results via the configured notification channel."""
    mgr = _get_notification_manager()
    if not mgr.is_enabled():
        logger.debug("Notification not enabled, skipping scan result push")
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title_map = {
        "position_scan": "持仓巡检",
        "watchlist_scan": "自选巡检",
        "candidate_pool_scan": "候选池巡检",
    }
    title = title_map.get(scan_type, "AI巡检")
    if group_name:
        title += f"（{group_name}）"

    lines = [
        f"**{title}完成** 🤖",
        f"> 时间：{now}",
        f"> 标的数：**{len(results)}**",
        "",
    ]

    for i, r in enumerate(results[:20], 1):
        decision = r.get("decision")
        symbol = f"{r.get('symbol_name', '')}({r.get('symbol_code', '')})"
        if decision is not None:
            action = decision.action_label if hasattr(decision, "action_label") else str(decision.get("action", ""))
            confidence = decision.confidence if hasattr(decision, "confidence") else decision.get("confidence", 0)
            line = f"{i}. **{symbol}** → {action}（置信度 {confidence:.0%}）"
        else:
            line = f"{i}. **{symbol}** → 未提取到决策"
        lines.append(line)

    if len(results) > 20:
        lines.append(f"\n... 共 {len(results)} 只，仅显示前 20 只")

    actionable = [r for r in results if r.get("decision") and getattr(r["decision"], "is_actionable", False)]
    if actionable:
        lines.append(f"\n⚠ 其中 **{len(actionable)}** 只有可执行建议（买入/卖出/加仓/减仓），请及时查看。")

    content = "\n".join(lines)
    ok, msg = mgr.send_markdown(content)
    if not ok:
        logger.warning("Failed to send scan notification: %s", msg)
    return ok


def notify_single_decision(
    symbol_code: str,
    symbol_name: str,
    action: str,
    confidence: float,
    reasoning: str = "",
) -> bool:
    """Push a single decision notification."""
    mgr = _get_notification_manager()
    if not mgr.is_enabled():
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        f"**AI 交易决策** 🤖\n"
        f"> 时间：{now}\n"
        f"> 标的：**{symbol_name}({symbol_code})**\n"
        f"> 建议：**{action}**\n"
        f"> 置信度：**{confidence:.0%}**\n"
    )
    if reasoning:
        content += f"\n{reasoning[:200]}"

    ok, msg = mgr.send_markdown(content)
    if not ok:
        logger.warning("Failed to send decision notification: %s", msg)
    return ok


def notify_alert(
    symbol_code: str,
    symbol_name: str,
    alert_type: str,
    message: str,
) -> bool:
    """Push a price alert notification."""
    mgr = _get_notification_manager()
    if not mgr.is_enabled():
        return False

    emoji = {"stop_loss": "🔴", "target_hit": "🟢", "invalidation": "⚠"}.get(alert_type, "🔔")
    now = datetime.now().strftime("%H:%M:%S")
    content = (
        f"{emoji} **价格预警** — {symbol_name}({symbol_code})\n"
        f"> {now}\n"
        f"> {message}"
    )
    ok, msg = mgr.send_markdown(content)
    return ok
