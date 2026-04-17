# 实盘策略中心最小回归与手工验收清单

适用范围：
- `run_live_strategy_center.py`
- `trading_app/widgets/live_strategy_hub_widget.py`
- `trading_app/services/live_strategy_center/`
- `trading_app/widgets/live_strategy_*_widget.py`

目标：
- 用最小成本覆盖本次“实盘策略中心中心化改造”的关键风险
- 区分可自动执行的 smoke test 与需要人工确认的桌面交互验收

## 1. 环境

推荐在 `stock` 环境中执行。

PowerShell:

```powershell
conda activate stock
```

如果 `conda run` 在当前机器上有编码问题，也可以直接调用环境解释器：

```powershell
& "C:\ProgramData\miniforge3\envs\stock\python.exe" <command>
```

## 2. 自动回归

### A. 影子下单 smoke test

目标：
- 验证统一执行链路在 `shadow` 模式下不触发真实下单
- 验证委托记录能正确落库

命令：

```powershell
python scripts/shadow_mode_smoketest.py
```

通过标准：
- `RESULT_SUCCESS= True`
- `RESULT_SHADOW= True`
- `RESULT_MODE= shadow`
- `BROKER_ORDER_CALLS= 0`
- `ORDER_RECORD_STATUS= shadow`

### B. 自动交易 smoke test

目标：
- 验证 `DailyAutoTradeService` 与 `TradeExecutionService` 的基础联动
- 验证自动任务开始、处理、结束链路不报错

命令：

```powershell
python scripts/daily_auto_trade_smoketest.py
```

通过标准：
- `BEGIN= True`
- `CYCLE= ... True ...`
- `BROKER_ORDER_CALLS= 0`

说明：
- 如果输出中出现“预算不足”之类的业务提示，但整体 `CYCLE` 为成功，属于当前 smoke 的可接受结果，因为它验证的是编排链路不是交易盈利结果。

### C. 中心模块导入 smoke test

目标：
- 验证新增中心层模块和 hub 入口模块能在 `stock` 环境完成导入

命令：

```powershell
python -c "import sys; sys.path.insert(0, r'E:\Projects3\Xquant'); sys.path.insert(0, r'E:\Projects3\Xquant\trading_app'); import PyQt6; import trading_app.services.live_strategy_center as m; import trading_app.widgets.live_strategy_hub_widget as hub; print('imports-ok', hasattr(m, 'HubStateService'), hasattr(hub, 'LiveStrategyHubWidget'))"
```

通过标准：
- 输出 `imports-ok True True`

## 3. 最小手工验收

### A. 启动与首页

操作：
1. 运行 `python run_live_strategy_center.py`
2. 检查是否成功打开 `实盘策略中心`
3. 检查顶层 Tab 是否包含：
   - `总览`
   - `告警中心`
   - `风控中心`
   - `任务中心`
   - `异常订单`
   - `收益中心`
   - `AI策略`
   - `ETF轮动`
   - `运行日志`

通过标准：
- 窗口能正常打开
- 无明显初始化报错弹窗
- 顶层页签完整显示

### B. 总览页

操作：
1. 打开 `总览`
2. 检查 `QMT / 客户端`、`券商连接`、`执行模式`、`告警统计`、`今日日终` 字段是否有值
3. 点击：
   - `刷新总览`
   - `执行日终`
   - `打开 AI 策略`
   - `打开 ETF 轮动`
   - `打开告警中心`

通过标准：
- 页面能刷新
- 跳转按钮能切换到对应页签
- `执行日终` 能触发状态变化或提示

### C. 告警中心

操作：
1. 打开 `告警中心`
2. 切换 `状态`、`分类` 筛选
3. 选择一条记录后点击：
   - `标记已读`
   - `忽略`
   - `跳转相关页面`

通过标准：
- 表格正常显示事件
- 状态更新后刷新可见
- 跳转能切到合理页面（AI / ETF / 任务 / 异常订单 / 日志）

### D. 风控中心

操作：
1. 打开 `风控中心`
2. 检查中心级摘要是否显示：
   - 执行模式
   - 手动委托
   - 总预算
   - 已投入
   - 高风险策略数
3. 点击：
   - `暂停自动化`
   - `恢复自动化`
   - `影子模式`
   - `实盘模式`
   - `关闭自动交易`

通过标准：
- 点击后有反馈提示
- AI 与 ETF 自动化状态会同步变化
- 执行模式切换后重新刷新仍能看到变更

注意：
- `实盘模式` 只验证配置切换，不建议在未确认券商环境时直接发起真实交易。

### E. 任务中心

操作：
1. 打开 `任务中心`
2. 检查是否至少看到以下任务：
   - `启动自检`
   - `09:35 数据新鲜度检查`
   - `统一日终流程`
   - `每日 AI 策略总任务`
   - `ETF 自动轮动检查`
3. 选择任务后执行对应动作：
   - `立即执行`
   - `暂停调度`
   - `恢复调度`
   - `仅检查信号`
   - `检查并执行`

通过标准：
- 任务表格有数据
- 选择不同任务时动作列表能变化
- 执行动作后有消息反馈，状态/消息列会更新

### F. 异常订单

操作：
1. 打开 `异常订单`
2. 检查是否能列出 `blocked / failed / cancelled / rejected` 的记录
3. 选择含券商委托号的记录，测试 `撤单`
4. 选择记录测试 `忽略关联告警`

通过标准：
- 表格能显示异常记录
- 无委托号时会给出合理提示
- 有委托号时能发送撤单请求
- 关联告警能被忽略

### G. 收益中心

操作：
1. 打开 `收益中心`
2. 检查摘要是否显示：
   - 总成交数
   - 买入数
   - 卖出数
   - 胜率
   - 累计盈亏
3. 检查策略表与每日汇总表是否能正常展示

通过标准：
- 页面可打开
- 数值字段有值或为合理默认值
- 表格不报错、不空白崩溃

### H. 托盘行为

操作：
1. 最小化窗口
2. 从托盘菜单点击：
   - `打开总览`
   - `打开告警中心`
   - `显示运行日志`
   - `执行日终`

通过标准：
- 可从托盘恢复窗口
- 能切到指定页面
- 托盘动作无异常

## 4. 建议的最小发布前流程

每次改动 `实盘策略中心` 后，至少执行：

1. `shadow_mode_smoketest.py`
2. `daily_auto_trade_smoketest.py`
3. 中心模块导入 smoke
4. 手工验收中的 A、B、C、D、E

如果改到了执行链路，再额外执行：

5. `异常订单` 页面验收
6. `收益中心` 页面验收
7. 托盘行为验收

## 5. 本次已实测命令

本次在 `stock` 环境中已执行：

```powershell
& "C:\ProgramData\miniforge3\envs\stock\python.exe" "E:\Projects3\Xquant\scripts\shadow_mode_smoketest.py"
& "C:\ProgramData\miniforge3\envs\stock\python.exe" "E:\Projects3\Xquant\scripts\daily_auto_trade_smoketest.py"
& "C:\ProgramData\miniforge3\envs\stock\python.exe" -c "import sys; sys.path.insert(0, r'E:\Projects3\Xquant'); sys.path.insert(0, r'E:\Projects3\Xquant\trading_app'); import PyQt6; import trading_app.services.live_strategy_center as m; import trading_app.widgets.live_strategy_hub_widget as hub; print('imports-ok', hasattr(m, 'HubStateService'), hasattr(hub, 'LiveStrategyHubWidget'))"
```
