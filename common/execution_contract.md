# 执行契约说明

`common.execution_contract` 定义研究、回测和实盘执行共享的数据结构，包括 `StrategySignal`、`OrderIntent`、`RebalanceIntent`、`FillReport` 和 `OrderExecutionReport`。

## BacktestConfig live 开关

`strategy_app.backtest.BacktestConfig` 的三个 `use_live_*` 开关只用于回测侧复用实盘执行契约。开启任一开关时，调用方必须显式注入 `live_execution_gateway_factory`，由应用层提供 `TradeExecutionService` 或等价测试替身；`strategy_app` 不直接 import 实盘服务。

- `use_live_risk`：回测信号先经过实盘统一执行网关的风险拒单逻辑。被拒信号不会进入回测撮合。
- `use_live_budget`：回测信号先经过实盘策略预算服务的额度校验与预占。预算不足时不会进入回测撮合。
- `use_live_execution_gateway`：回测信号先进入 `TradeExecutionService` 的 dry-run 通路。当前回测注入配置使用 `shadow` 模式，只记录影子执行结果，不提交真实券商委托。

这些开关的输出会写入 `BacktestResult.provenance.live_gateway_summary`，用于回归断言，包括检查数量、拦截数量、拦截原因和 dry-run 执行模式。
