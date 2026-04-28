from __future__ import annotations

from trading_app.services.agent_context_service import (
    TASK_MODE_GENERAL,
    TASK_MODE_POSITION_DIAGNOSIS,
    TASK_MODE_SYMBOL_ANALYSIS,
    TASK_MODE_TRADE_DECISION,
    TASK_MODE_WATCHLIST_SCAN,
)


CITATION_RULES = (
    "证据引用规则:\n"
    "- 关键结论后请用 `[E1]`、`[E1][E2]` 这样的形式标注证据来源。\n"
    "- 没有证据支持的推断请明确写成“推测”或“待补充数据”，不要伪装成确定事实。\n"
    "- 涉及买卖建议时，至少给出一个证据引用。"
)


def build_response_contract(task_mode: str) -> str:
    if task_mode == TASK_MODE_SYMBOL_ANALYSIS:
        return (
            "请严格使用以下 Markdown 结构输出:\n"
            "## 结论速览\n"
            "- 趋势判断: 一句话总结，并附证据引用\n"
            "- 操作建议: 买入/持有/卖出/观望，并附证据引用\n"
            "- 核心理由: 2-3 条\n\n"
            "## 趋势与关键位置\n"
            "- 长中短期趋势\n"
            "- 关键支撑位/压力位\n"
            "- 当前价格所处位置\n\n"
            "## 关键信号拆解\n"
            "- 量价特征\n"
            "- 均线/MACD/KDJ 等信号\n"
            "- 若存在背离或异常波动请单列说明\n\n"
            "## 风险与失效条件\n"
            "- 当前主要风险\n"
            "- 观点失效条件\n\n"
            "## 交易计划\n"
            "- 偏进攻方案\n"
            "- 偏稳健方案\n"
            "- 观察清单"
        )

    if task_mode == TASK_MODE_WATCHLIST_SCAN:
        return (
            "请严格使用以下 Markdown 结构输出:\n"
            "## 巡检结论\n"
            "- 3-5 条对分组整体状态的总结，每条尽量带证据引用\n\n"
            "## Top3 关注\n"
            "| 排名 | 代码 | 名称 | 理由 | 关注点 |\n"
            "| --- | --- | --- | --- | --- |\n\n"
            "## Top3 风险\n"
            "| 排名 | 代码 | 名称 | 风险原因 | 应对建议 |\n"
            "| --- | --- | --- | --- | --- |\n\n"
            "## 下一步观察清单\n"
            "- 3-5 条后续动作"
        )

    if task_mode == TASK_MODE_TRADE_DECISION:
        return (
            "请严格使用以下 Markdown 结构输出:\n"
            "## 多空分析\n"
            "### 看多理由\n"
            "- 列出 2-3 条看多证据，附证据引用\n\n"
            "### 看空理由\n"
            "- 列出 2-3 条看空证据，附证据引用\n\n"
            "## 综合研判\n"
            "- 权衡多空后的结论性判断\n"
            "- 当前胜率评估\n\n"
            "## 交易决策\n"
            "请在下方标签内输出严格的 JSON（不要加 markdown 代码围栏）:\n"
            "<trade_decision>\n"
            '{"action":"buy/sell/hold/reduce/add/watch/reject",'
            '"symbol_code":"000001.SZ",'
            '"symbol_name":"平安银行",'
            '"confidence":0.75,'
            '"target_price":12.50,'
            '"stop_loss_price":11.00,'
            '"current_price":11.80,'
            '"position_pct":0.15,'
            '"risk_score":0.35,'
            '"time_horizon":"short/medium/long",'
            '"invalidation":"失效条件描述",'
            '"reasoning":"一句话决策理由",'
            '"bull_case":"核心看多逻辑",'
            '"bear_case":"核心看空逻辑"}\n'
            "</trade_decision>\n\n"
            "字段说明:\n"
            "- action: 操作方向(buy/sell/hold/reduce/add/watch/reject)\n"
            "- confidence: 置信度(0.0~1.0)\n"
            "- target_price: 目标价\n"
            "- stop_loss_price: 止损价\n"
            "- current_price: 当前价格\n"
            "- position_pct: 建议仓位占总资产比例(0.0~1.0)\n"
            "- risk_score: 风险评分(0.0~1.0，越高风险越大)\n"
            "- time_horizon: 持有周期(short=1-5日, medium=1-4周, long=1月+)\n\n"
            "## 风险与失效条件\n"
            "- 主要风险点\n"
            "- 观点失效条件\n"
            "- 需要跟踪的信号"
        )

    if task_mode == TASK_MODE_POSITION_DIAGNOSIS:
        return (
            "请严格使用以下 Markdown 结构输出:\n"
            "## 仓位概览\n"
            "- 账户总体仓位、集中度、现金状态\n\n"
            "## 风险暴露\n"
            "- 按持仓、风格、波动、流动性角度说明风险\n\n"
            "## 重点持仓处理建议\n"
            "| 代码 | 当前判断 | 操作建议 | 依据 |\n"
            "| --- | --- | --- | --- |\n\n"
            "## 调整优先级\n"
            "- 最优先处理的事项\n"
            "- 可继续观察的事项\n\n"
            "## 失效条件与跟踪点\n"
            "- 后续需要验证的条件"
        )

    return (
        "请尽量使用以下 Markdown 结构输出:\n"
        "## 结论\n"
        "- 直接回答用户问题\n\n"
        "## 依据\n"
        "- 只列出当前证据能支持的事实\n\n"
        "## 风险与不确定性\n"
        "- 明确哪些结论仍需要补充数据\n\n"
        "## 下一步建议\n"
        "- 给出可执行动作"
    )


def build_contract_with_citations(task_mode: str) -> str:
    return f"{build_response_contract(task_mode)}\n\n{CITATION_RULES}"
