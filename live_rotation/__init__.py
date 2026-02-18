"""
ETF轮动策略实盘交易系统

基于三因子动量轮动策略的自动化实盘交易模块。

模块结构:
  - config.py          配置管理
  - state_manager.py   状态持久化
  - risk_manager.py    风控检查
  - trade_executor.py  交易执行器（真实/模拟）
  - rotation_engine.py 核心轮动引擎（依赖PyQt6）
  - notifier.py        企微通知集成
  - widget.py          UI面板（依赖PyQt6）
"""
