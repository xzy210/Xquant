from __future__ import annotations

from typing import Optional

from .agent_context_service import (
    AgentRuntimeContext,
    TASK_MODE_GENERAL,
    TASK_MODE_LABELS,
    TASK_MODE_POSITION_DIAGNOSIS,
    TASK_MODE_SYMBOL_ANALYSIS,
    TASK_MODE_WATCHLIST_SCAN,
)
from .agent_response_contract import build_contract_with_citations


class AgentPromptBuilder:
    """Build system prompts and quick task prompts for the embedded agent."""

    @staticmethod
    def build_system_prompt(
        base_prompt: str,
        context: AgentRuntimeContext,
        task_mode: str = TASK_MODE_GENERAL,
    ) -> str:
        sections = [
            (base_prompt or "你是一个专业的股票投资顾问。").strip(),
            "你运行在一个股票/ETF分析桌面应用中，请优先结合当前运行上下文回答。",
            f"当前任务模式: {TASK_MODE_LABELS.get(task_mode, task_mode)}",
            "当前运行上下文:",
            *[f"- {line}" for line in context.to_summary_lines()],
            "",
            "回答要求:",
            "- 优先给出可执行、可验证的结论，而不是空泛表述。",
            "- 涉及交易建议时，请同时说明依据、风险点、失效条件。",
            "- 如果系统补充了证据摘要，请优先基于证据回答，并明确哪些结论来自现有证据。",
            "- 如果上下文不足，请明确指出缺少什么数据。",
            "",
            "输出协议:",
            build_contract_with_citations(task_mode),
        ]
        return "\n".join(section for section in sections if section is not None)

    @staticmethod
    def build_quick_task_prompt(
        task_mode: str,
        context: AgentRuntimeContext,
        extra_instructions: str = "",
    ) -> str:
        if task_mode == TASK_MODE_SYMBOL_ANALYSIS:
            symbol = context.symbol
            return (
                f"请基于当前上下文，对 {symbol.name or '-'}({symbol.code or '-'}) 输出一份结构化分析，"
                "请尽量综合技术面、消息面、基本面三类证据，至少包含：趋势判断、关键支撑/压力位、量价特征、"
                "消息面催化/风险、基本面结论、风险点、操作建议、失效条件。"
                "请严格遵循系统提示中的输出协议。"
            )

        if task_mode == TASK_MODE_WATCHLIST_SCAN:
            group_name = context.watchlist.group_name or "当前列表"
            preview = "、".join(context.watchlist.visible_codes[:12]) or "无可用标的"
            return (
                f"请对 {group_name} 做一轮 AI 巡检。标的样本包括：{preview}。"
                "请输出：最值得关注的3只、风险最高的3只、值得继续跟踪的理由，以及下一步观察清单。"
                "请严格遵循系统提示中的输出协议。"
            )

        if task_mode == TASK_MODE_POSITION_DIAGNOSIS:
            return (
                "请基于当前账户持仓上下文做持仓诊断。请输出：仓位概览、主要风险暴露、"
                "应优先复盘的持仓、需要防守的标的，以及后续跟踪建议。"
                "请严格遵循系统提示中的输出协议。"
            )

        if extra_instructions:
            return extra_instructions
        return "请结合当前股票软件上下文回答用户问题。"

    @staticmethod
    def build_context_brief(context: AgentRuntimeContext) -> str:
        return "\n".join(f"- {line}" for line in context.to_summary_lines())
