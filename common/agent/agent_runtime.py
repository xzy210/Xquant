from __future__ import annotations

import base64
import copy
import mimetypes
from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol
from uuid import uuid4

from common.agent.agent_context_service import (
    AgentRuntimeContext,
    TASK_MODE_GENERAL,
    TASK_MODE_POSITION_DIAGNOSIS,
    TASK_MODE_SYMBOL_ANALYSIS,
    TASK_MODE_TRADE_DECISION,
    TASK_MODE_WATCHLIST_SCAN,
)
from common.agent.agent_evidence_service import AgentEvidenceService, EvidenceBundle, EvidenceItem
from common.agent.agent_response_contract import build_contract_with_citations


class AgentToolResultProtocol(Protocol):
    tool_name: str
    title: str
    summary: str
    content: str
    metadata: Dict[str, Any]


class AgentToolRegistryProtocol(Protocol):
    def dispatch(self, name: str, execution_context: Any, **kwargs: Any) -> AgentToolResultProtocol | None:
        ...


@dataclass
class PreparedAgentRequest:
    task_mode: str
    system_prompt: str
    messages: List[Dict[str, Any]]
    augmented_user_content: Any
    response_contract: str = ""
    executed_tools: List[str] = field(default_factory=list)
    evidence_items: List[EvidenceItem] = field(default_factory=list)
    evidence_report_path: str = ""


class StockAgentRuntime:
    """Prepare domain evidence before the LLM request is sent."""

    def __init__(
        self,
        tool_registry: AgentToolRegistryProtocol | None = None,
        evidence_service: AgentEvidenceService | None = None,
        execution_context_factory=None,
        symbol_code_extractor=None,
    ):
        self.tool_registry = tool_registry
        self.evidence_service = evidence_service or AgentEvidenceService()
        self.execution_context_factory = execution_context_factory
        self.symbol_code_extractor = symbol_code_extractor or (lambda text: [])

    def prepare_request(
        self,
        *,
        base_system_prompt: str,
        context: AgentRuntimeContext,
        task_mode: str,
        chat_history: List[Dict[str, Any]],
        latest_user_content: Any,
    ) -> PreparedAgentRequest:
        user_text = self._extract_text(latest_user_content)
        tool_plan = self._build_tool_plan(task_mode, context, user_text)
        tool_results = self._run_tools(tool_plan, context, user_text)
        evidence_items = [
            EvidenceItem(
                tool_name=str(item.tool_name),
                title=str(item.title),
                summary=str(item.summary),
                content=str(item.content),
                metadata=dict(item.metadata or {}),
            )
            for item in tool_results
        ]
        evidence_report_path = ""
        if evidence_items:
            bundle = EvidenceBundle(
                run_id=uuid4().hex[:12],
                task_mode=task_mode or TASK_MODE_GENERAL,
                user_input=user_text,
                created_at=self.evidence_service.now_iso(),
                context_summary=context.to_summary_lines(),
                items=evidence_items,
            )
            evidence_report_path = self.evidence_service.save_bundle(bundle)

        augmented_user_content = self._augment_user_content(
            latest_user_content=latest_user_content,
            user_text=user_text,
            evidence_items=evidence_items,
            evidence_report_path=evidence_report_path,
            task_mode=task_mode,
        )
        messages = copy.deepcopy(chat_history)
        messages.append({"role": "user", "content": augmented_user_content})
        response_contract = build_contract_with_citations(task_mode)
        system_prompt = self._build_system_prompt(base_system_prompt, evidence_items, response_contract)
        return PreparedAgentRequest(
            task_mode=task_mode,
            system_prompt=system_prompt,
            messages=messages,
            augmented_user_content=augmented_user_content,
            response_contract=response_contract,
            executed_tools=[str(result.tool_name) for result in tool_results],
            evidence_items=evidence_items,
            evidence_report_path=evidence_report_path,
        )

    def _build_tool_plan(
        self,
        task_mode: str,
        context: AgentRuntimeContext,
        user_text: str,
    ) -> List[Dict[str, Any]]:
        plan: List[Dict[str, Any]] = [{"name": "context_snapshot", "kwargs": {}}]
        image_keywords = ["截图", "图形", "形态", "k线图", "K线图", "看图", "走势"]
        deep_analysis_keywords = ["分析当前股票", "分析当前标的", "技术分析", "走势分析", "买卖建议", "看看这只", "诊断一下", "综合分析", "全面分析"]
        news_keywords = ["消息", "新闻", "公告", "事件", "催化", "利好", "利空", "舆情", "研报"]
        fundamental_keywords = ["基本面", "财务", "财报", "估值", "利润", "营收", "roe", "ROE", "pb", "PB", "pe", "PE", "股息"]

        if task_mode == TASK_MODE_TRADE_DECISION:
            plan.append({"name": "market_context_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_technical_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_news_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_fundamental_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_analysis_packet", "kwargs": {}})
            plan.append({"name": "current_kline_image", "kwargs": {}})
            if context.broker.connected:
                plan.append({"name": "position_snapshot", "kwargs": {}})
                plan.append({"name": "portfolio_industry_exposure", "kwargs": {}})
            return plan

        if task_mode == TASK_MODE_SYMBOL_ANALYSIS:
            plan.append({"name": "market_context_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_technical_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_news_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_fundamental_snapshot", "kwargs": {}})
            plan.append({"name": "symbol_analysis_packet", "kwargs": {}})
            return plan

        if task_mode == TASK_MODE_WATCHLIST_SCAN:
            plan.append({"name": "market_context_snapshot", "kwargs": {}})
            plan.append({"name": "watchlist_snapshot", "kwargs": {}})
            return plan

        if task_mode == TASK_MODE_POSITION_DIAGNOSIS:
            plan.append({"name": "market_context_snapshot", "kwargs": {}})
            plan.append({"name": "position_snapshot", "kwargs": {}})
            return plan

        if context.symbol.is_available:
            plan.append({"name": "symbol_technical_snapshot", "kwargs": {}})
            if any(token in user_text for token in deep_analysis_keywords):
                plan.append({"name": "symbol_analysis_packet", "kwargs": {}})
            if any(token in user_text for token in news_keywords):
                plan.append({"name": "symbol_news_snapshot", "kwargs": {}})
            if any(token in user_text for token in fundamental_keywords):
                plan.append({"name": "symbol_fundamental_snapshot", "kwargs": {}})
            if any(token in user_text for token in deep_analysis_keywords):
                plan.append({"name": "symbol_news_snapshot", "kwargs": {}})
                plan.append({"name": "symbol_fundamental_snapshot", "kwargs": {}})
            if any(token in user_text for token in image_keywords):
                plan.append({"name": "current_kline_image", "kwargs": {}})

        codes = list(self.symbol_code_extractor(user_text) or [])
        wants_compare = any(token in user_text for token in ["比较", "对比", "哪个好", "谁更强"])
        if len(codes) >= 2 or (wants_compare and len(codes) >= 2):
            plan.append({"name": "compare_symbols", "kwargs": {"codes": codes}})
        return plan

    def _run_tools(
        self,
        plan: List[Dict[str, Any]],
        context: AgentRuntimeContext,
        user_text: str,
    ) -> List[AgentToolResultProtocol]:
        if self.tool_registry is None or self.execution_context_factory is None:
            return []
        execution_context = self.execution_context_factory(context, user_text)
        results: List[AgentToolResultProtocol] = []
        seen = set()
        for step in plan:
            name = step.get("name", "")
            if not name or name in seen:
                continue
            result = self.tool_registry.dispatch(name, execution_context, **dict(step.get("kwargs", {}) or {}))
            seen.add(name)
            if result:
                results.append(result)
        return results

    def _augment_user_content(
        self,
        *,
        latest_user_content: Any,
        user_text: str,
        evidence_items: List[EvidenceItem],
        evidence_report_path: str,
        task_mode: str,
    ) -> Any:
        if not evidence_items:
            return latest_user_content

        image_parts = self._build_image_parts(evidence_items)
        evidence_lines = [
            "",
            "<agent_evidence>",
            "以下是本次回答前由股票领域工具生成的证据，请优先基于这些证据作答，不要虚构未出现的数据。",
        ]
        if evidence_report_path:
            evidence_lines.append(f"证据记录文件: {evidence_report_path}")
        for idx, item in enumerate(evidence_items, start=1):
            evidence_lines.extend([
                f"### E{idx} {item.title}",
                f"- 摘要: {item.summary}",
                item.content,
                "",
            ])
        evidence_lines.extend([
            "</agent_evidence>",
            "",
            "请在关键结论后使用 `[E1]`、`[E2]` 的形式标注证据来源。",
            "如果某个判断没有直接证据支持，请明确写成“推测”或“待补充数据”。",
            "请在结论中明确区分：确定性结论、风险提示、失效条件；若证据不足请直接说明。",
            "",
            "<response_contract>",
            build_contract_with_citations(task_mode),
            "</response_contract>",
        ])
        evidence_block = "\n".join(evidence_lines).strip()

        if isinstance(latest_user_content, str):
            text_part = {"type": "text", "text": f"{user_text}\n\n{evidence_block}".strip()}
            if image_parts:
                return [text_part, *image_parts]
            return text_part["text"]

        if isinstance(latest_user_content, list):
            content = copy.deepcopy(latest_user_content)
            for part in content:
                if part.get("type") == "text":
                    part["text"] = f"{part.get('text', '').strip()}\n\n{evidence_block}".strip()
                    if image_parts:
                        content.extend(image_parts)
                    return content
            if image_parts:
                content.extend(image_parts)
            content.insert(0, {"type": "text", "text": evidence_block})
            return content

        return latest_user_content

    @staticmethod
    def _build_system_prompt(
        base_system_prompt: str,
        evidence_items: List[EvidenceItem],
        response_contract: str,
    ) -> str:
        sections = [base_system_prompt]
        if evidence_items:
            sections.append(
                "补充要求:\n"
                "- 本轮回答前系统已执行股票领域工具并生成证据。\n"
                "- 回答应优先引用证据中的事实，不要臆测不存在的数据。\n"
                "- 若给出买卖建议，必须同时说明依据、风险点、失效条件。\n"
                "- 如果证据与用户问题不完全匹配，请明确指出还缺什么数据。"
            )
        if response_contract:
            sections.append(f"请遵循以下输出协议:\n{response_contract}")
        return "\n\n".join(section for section in sections if section)

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts: List[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = str(part.get("text", "")).strip()
                    if text:
                        texts.append(text)
            return "\n".join(texts).strip()
        return ""

    @staticmethod
    def _build_image_parts(evidence_items: List[EvidenceItem]) -> List[Dict[str, Any]]:
        parts: List[Dict[str, Any]] = []
        for item in evidence_items:
            image_path = str(item.metadata.get("image_path", "")).strip()
            if not image_path:
                continue
            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type or not mime_type.startswith("image/"):
                continue
            try:
                with open(image_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
            except Exception:
                continue
            parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{encoded_string}",
                },
            })
        return parts


__all__ = [
    "AgentToolRegistryProtocol",
    "AgentToolResultProtocol",
    "PreparedAgentRequest",
    "StockAgentRuntime",
]
