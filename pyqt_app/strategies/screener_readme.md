# 智能选股器使用指南

## 简介

智能选股器是一个专为波动率突破策略优化的多维度选股工具，通过技术指标筛选、评分排序，帮助用户快速找到最适合交易的股票。

## 核心功能

- ✅ 多维度技术指标筛选
- ✅ 智能评分排序系统
- ✅ 批量数据处理
- ✅ 多种格式导出（CSV/JSON/Excel）
- ✅ PyQt GUI界面
- ✅ 实时进度显示

## 使用方式

### 方式1: 在App中使用（推荐）

```
来财App → 工具 → 智能选股器
```

界面操作：
1. 选择预设配置（波动率突破优化/稳健型/激进型）
2. 或自定义各项参数
3. 点击"开始选股"
4. 查看结果，双击股票可跳转到K线图
5. 可导出结果到CSV/JSON/Excel

### 方式2: 编程使用

#### 快速选股

```python
from strategies import quick_screen, screen_for_volatility_breakout

# 方法1: 使用预设优化参数
results = screen_for_volatility_breakout(
    data_dir='./data',
    stock_list=stock_list,
    top_n=30
)

# 方法2: 自定义参数
results = quick_screen(
    data_dir='./data',
    stock_list=stock_list,
    top_n=20,
    min_atr_pct=0.025,
    max_atr_pct=0.04,
    min_adx=30.0
)
```

#### 高级用法

```python
from strategies import StockScreener, ScreeningCriteria

# 创建选股器
screener = StockScreener(data_dir='./data', max_workers=4)

# 定义筛选条件
criteria = ScreeningCriteria(
    min_atr_pct=0.02,              # 最小ATR 2%
    max_atr_pct=0.05,              # 最大ATR 5%
    atr_period=20,                 # ATR计算周期
    min_adx=25.0,                  # 最小ADX 25
    adx_period=14,                 # ADX计算周期
    min_avg_amount=100_000_000,    # 最小日均成交额1亿
    volume_lookback=20,            # 成交量计算周期
    proximity_to_high=0.95,        # 接近近期高点95%
    high_lookback=20,              # 高点计算周期
    min_price=5.0,                 # 最小股价5元
    max_price=500.0,               # 最大股价500元
    exclude_st=True,               # 排除ST股
    max_stocks=50                  # 最多返回50只
)

# 执行选股
results = screener.screen(stock_list, criteria)

# 查看结果
for score in results:
    print(f"{score.code}: 总分{score.total_score:.1f}, ATR{score.atr_pct*100:.2f}%")

# 导出结果
screener.export_results(results, 'picks.csv', format='csv')
```

## 筛选条件详解

### 1. 波动率条件（最重要）

```python
min_atr_pct=0.02  # ATR至少2%
max_atr_pct=0.05  # ATR不超过5%
```

**ATR百分比说明：**

| ATR范围 | 特征 | 策略适配 |
|--------|------|---------|
| <1% | 僵尸股 | ❌ 不适用 |
| 1-2% | 大盘股 | ⚠️ 信号少 |
| **2-5%** | **活跃股** | **✅ 最佳** |
| 5-10% | 题材股 | ⚠️ 止损频繁 |
| >10% | 妖股 | ❌ 风险高 |

### 2. 趋势条件

```python
min_adx=25.0  # ADX至少25
```

**ADX趋势强度：**

| ADX值 | 趋势状态 | 建议 |
|------|---------|------|
| <20 | 无趋势 | ❌ 观望 |
| 20-25 | 弱趋势 | ⚠️ 谨慎 |
| **25-40** | **强趋势** | **✅ 交易** |
| 40-60 | 极强趋势 | ✅ 顺势 |
| >60 | 过热 | ⚠️ 注意反转 |

### 3. 成交量条件

```python
min_avg_amount=100_000_000  # 日均成交1亿
```

**流动性分级：**

| 日均成交额 | 流动性 | 建议仓位 |
|----------|--------|---------|
| <5000万 | 差 | ❌ 避免 |
| 5000万-1亿 | 一般 | 小仓位 |
| **1-5亿** | **好** | **正常** |
| 5-20亿 | 很好 | 可重仓 |
| >20亿 | 极好 | 适合大资金 |

### 4. 形态条件

```python
proximity_to_high=0.95  # 价格接近近期高点的95%
```

**用途：** 寻找即将突破或刚突破的股票

- 95-99%: 即将突破 ✅
- 99-100%: 刚突破或突破中 ✅
- <90%: 回调中，等待 ⚠️

## 评分系统

每只股票会从5个维度进行评分（各0-100分）：

### 评分权重

```
总分 = 波动率得分 × 25% +
      趋势得分 × 25% +
      成交量得分 × 20% +
      形态得分 × 15% +
      动量得分 × 15%
```

### 各项评分标准

#### 波动率得分
- 100分：ATR在2.5%-4%之间（理想区间）
- 80分：ATR在2%-5%之间
- 其他：根据偏离程度递减

#### 趋势得分
- 100分：ADX ≥ 40
- 80分：ADX ≥ 30
- 60分：ADX ≥ 25
- 其他：ADX × 2

#### 成交量得分
- 100分：日均成交 ≥ 5亿
- 80分：日均成交 ≥ 2亿
- 60分：日均成交 ≥ 1亿
- 其他：按比例递减

#### 形态得分
- 100分：价格在近期高点的95-99%（即将突破）
- 80分：价格在近期高点的90-95%
- 60分：价格 ≥ 近期高点的99%（已突破）

#### 动量得分
- 100分：20日涨幅在5%-30%之间
- 70分：20日涨幅在0-5%之间
- 50分：20日涨幅 > 30%（可能过热）

## 预设配置

### 1. 波动率突破优化（推荐）

```python
min_atr_pct=0.02
max_atr_pct=0.05
min_adx=25.0
min_avg_amount=100_000_000
proximity_to_high=0.95
```

**适用场景：** 大多数情况下的最佳选择

### 2. 稳健型

```python
min_atr_pct=0.025
max_atr_pct=0.04
min_adx=30.0
min_avg_amount=200_000_000
proximity_to_high=0.97
```

**适用场景：** 
- 大资金账户
- 风险厌恶型投资者
- 市场波动较大时

### 3. 激进型

```python
min_atr_pct=0.015
max_atr_pct=0.06
min_adx=20.0
min_avg_amount=50_000_000
proximity_to_high=0.90
```

**适用场景：**
- 小资金追求高收益
- 牛市环境
- 能承受较大回撤

## 结果解读

### 高分股票特征（总分>80）

- 波动率适中（2.5%-4%）
- ADX强趋势（>30）
- 流动性好（成交>2亿）
- 即将突破或刚突破
- 近期有适度上涨（5-20%）

### 谨慎对待的情况

| 情况 | 说明 | 建议 |
|-----|------|------|
| 总分高但RSI>70 | 可能超买 | 等待回调 |
| ATR突然放大 | 波动加剧 | 减小仓位 |
| ADX>60 | 趋势过热 | 注意反转 |
| 涨幅>50% | 已大幅上涨 | 追高风险 |

## 实战流程

### 每日收盘后

```python
# Step 1: 全市场选股
results = screen_for_volatility_breakout(
    data_dir='./data',
    stock_list=get_all_stocks(),
    top_n=30
)

# Step 2: 精选前10名加入观察池
watchlist = results[:10]

# Step 3: 次日开盘前人工复核
# - 检查是否有重大利空消息
# - 检查板块轮动情况
# - 确认大盘环境

# Step 4: 符合买入条件的建仓
for stock in watchlist:
    if check_buy_signal(stock.code):
        position_size = calculate_position(stock.total_score)
        buy(stock.code, position_size)
```

### 持仓管理

```python
# 每周更新一次评分
for position in current_positions:
    new_score = screener.screen_single(position.code, criteria)
    
    if new_score.total_score < 50:
        # 评分大幅下降，考虑减仓
        reduce_position(position.code)
    elif new_score.total_score > 80:
        # 评分保持高位，可考虑加仓
        pass
```

## 常见问题

### Q: 为什么选股结果为空？

可能原因：
1. 数据文件不存在或格式错误
2. 筛选条件过于严格（特别是ADX和ATR）
3. 股票池本身不适合当前策略

**解决方法：**
- 放宽min_adx到20
- 扩大ATR范围到1.5%-6%
- 检查数据文件路径

### Q: 如何找到波动率适中的股票？

**方法1：** 使用预设的"波动率突破优化"配置

**方法2：** 自定义参数
```python
criteria = ScreeningCriteria(
    min_atr_pct=0.025,  # 至少2.5%
    max_atr_pct=0.035   # 不超过3.5%
)
```

### Q: 选出的股票太多怎么办？

**方法1：** 减小max_stocks
```python
criteria = ScreeningCriteria(max_stocks=10)
```

**方法2：** 提高筛选门槛
```python
criteria = ScreeningCriteria(
    min_adx=30,              # ADX要求更高
    min_avg_amount=200_000_000  # 成交量要求更高
)
```

**方法3：** 只取评分最高的
```python
results = screener.screen(stock_list, criteria)
top_picks = [r for r in results if r.total_score >= 75]  # 只取75分以上
```

### Q: 如何避免追高？

**方法1：** 限制近期涨幅
```python
# 在筛选后过滤
results = [r for r in results if r.return_20d < 0.30]  # 排除20日涨幅>30%的
```

**方法2：** 调整proximity_to_high
```python
proximity_to_high=0.92  # 放宽到92%，找到更早的启动点
```

## 高级技巧

### 组合多个条件

```python
# 同时满足波动率突破和均线多头排列
criteria = ScreeningCriteria(min_adx=25)

results = []
for code in stock_list:
    score = screener.screen_single(code, criteria)
    if score and score.total_score > 60:
        # 额外检查均线排列
        data = screener.load_data(code)
        if is_ma_bullish(data):  # 自定义函数
            results.append(score)
```

### 动态调整参数

```python
def get_adaptive_criteria(market_regime):
    """根据市场环境动态调整参数"""
    if market_regime == "bull":
        return ScreeningCriteria(min_adx=20, min_atr_pct=0.015)
    elif market_regime == "bear":
        return ScreeningCriteria(min_adx=30, min_atr_pct=0.03)
    else:  #震荡
        return ScreeningCriteria(min_adx=25, min_atr_pct=0.025)
```

### 回测验证

```python
# 验证选股效果
from strategies import VolatilityBreakoutStrategy

strategy = VolatilityBreakoutStrategy()

for score in results[:10]:
    data = screener.load_data(score.code)
    result = strategy.run_backtest(data, score.code)
    print(f"{score.code}: 得分{score.total_score:.1f}, 回测收益{result['return']:.2f}%")
```

## 性能优化

### 大数据量处理

```python
# 使用多进程加速（已内置）
screener = StockScreener(data_dir, max_workers=8)

# 分批处理
batch_size = 100
all_results = []
for i in range(0, len(stock_list), batch_size):
    batch = stock_list[i:i+batch_size]
    results = screener.screen(batch, criteria)
    all_results.extend(results)
```

### 数据缓存

```python
# 选股器自动缓存数据
# 同一次运行中重复加载同一只股票会使用缓存

# 手动清除缓存
screener._cache.clear()
```

## 更新日志

- 2025-02-03: 初始版本，支持基础选股和GUI界面

## 注意事项

1. **历史表现不代表未来**：选股器基于历史数据，不能保证未来表现
2. **建议人工复核**：选股结果应结合基本面和市场环境综合判断
3. **参数需要优化**：不同市场环境下，最优参数可能不同
4. **避免过度优化**：不要在历史数据上过度拟合参数
