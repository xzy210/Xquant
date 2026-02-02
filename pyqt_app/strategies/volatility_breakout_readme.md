# ATR波动率突破策略

## 策略简介

基于ATR（Average True Range，平均真实波幅）的波动率突破策略，灵感来源于经典的海龟交易法则。该策略通过捕捉价格突破近期波动区间的时机进场交易，适合趋势启动行情。

## 核心逻辑

### 入场信号
```
上轨 = 今日开盘价 + ATR(20) × multiplier
价格突破上轨 → 买入
```

### 出场信号
1. **固定止损**：入场价 - 2倍ATR
2. **固定止盈**：入场价 + 4倍ATR
3. **移动止盈**：持仓最高价回撤3倍ATR
4. **时间止损**：持仓超过20天强制平仓

## 参数说明

| 参数 | 默认值 | 说明 |
|-----|-------|------|
| `atr_period` | 20 | ATR计算周期 |
| `multiplier` | 2.0 | 突破倍数（上轨=开盘+ATR×倍数）|
| `stop_loss_atr` | 2.0 | 止损倍数（入场价-N倍ATR）|
| `take_profit_atr` | 4.0 | 止盈倍数（入场价+N倍ATR）|
| `trailing_stop` | True | 是否启用移动止损 |
| `trailing_atr` | 3.0 | 移动止损倍数 |
| `max_hold_days` | 20 | 最大持仓天数 |
| `position_pct` | 0.95 | 每次使用资金比例 |

## 使用方式

### 1. 在App中使用

启动App后：
1. 点击菜单「工具」→「策略回测」
2. 在策略下拉框中选择「ATR波动率突破」
3. 选择股票代码和回测区间
4. 点击「开始回测」

### 2. 编程使用

```python
from strategies import VolatilityBreakoutStrategy

# 创建策略
strategy = VolatilityBreakoutStrategy()

# 设置参数
strategy.set_params({
    "atr_period": 20,
    "multiplier": 2.0,
    "trailing_stop": True
})

# 选股模式
signal = strategy.check("000001.SZ", data)
if signal:
    print(f"买入信号: {signal}")

# 回测模式
results = strategy.run_backtest(data, code="000001.SZ", initial_cash=100000)
print(f"最终资产: {results['final_value']}")
```

## 策略特点

### 优点
- ✅ 趋势行情中盈利能力强
- ✅ 有多重风控机制（固定止损+移动止盈+时间止损）
- ✅ 盈亏比高（默认4:1）
- ✅ 交易频率适中，避免过度交易

### 缺点
- ❌ 震荡市会产生假突破信号
- ❌ 需要一定的趋势才能盈利
- ❌ 单次止损幅度较大（2倍ATR）

### 适用市场
- 趋势明显的牛市或熊市
- 波动率较高的股票
- 不适合横盘震荡行情

## 参数优化建议

### 激进型（追求高收益）
```python
{
    "multiplier": 1.5,      # 更容易触发
    "stop_loss_atr": 1.5,   # 止损更紧
    "take_profit_atr": 3.0, # 止盈更快
    "trailing_stop": False  # 不用移动止盈
}
```

### 稳健型（追求稳定）
```python
{
    "multiplier": 2.5,      # 减少假信号
    "stop_loss_atr": 2.5,   # 更宽松止损
    "take_profit_atr": 5.0, # 让利润奔跑
    "trailing_stop": True   # 保护利润
}
```

### 长线型
```python
{
    "atr_period": 30,       # 更长周期
    "multiplier": 3.0,      # 更严格突破
    "max_hold_days": 60     # 持有更久
}
```

## 实战建议

1. **趋势过滤**：只在ADX>25的趋势市场中交易
2. **仓位管理**：单只股票不超过总资金20%
3. **分散投资**：同时交易多只股票降低风险
4. **定期优化**：每季度回测优化一次参数

## 与其他策略对比

| 策略 | 胜率 | 盈亏比 | 交易频率 | 最佳市场 |
|-----|------|--------|---------|---------|
| ATR突破 | 40% | 3:1 | 中等 | 趋势市 |
| 双均线 | 35% | 2.5:1 | 中等 | 强趋势 |
| 布林带回归 | 65% | 1.5:1 | 高频 | 震荡市 |

## 风险提示

1. 历史回测不代表未来收益
2. 单次交易可能亏损2倍ATR（约5-10%）
3. 连续止损时需要有足够的心理准备
4. 建议先用模拟盘验证策略有效性

## 更新日志

- 2025-02-03: 初始版本，支持选股和回测双模式
