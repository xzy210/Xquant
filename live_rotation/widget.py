"""
ETF轮动实盘 - UI面板

可作为独立Tab嵌入 trading_app 的 MainWindow。
显示持仓状态、ETF得分、交易历史、参数配置，并提供手动/自动执行入口。
"""
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QTextEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QComboBox, QLineEdit, QMessageBox, QTabWidget,
    QScrollArea, QListWidget, QListWidgetItem, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from .config import RotationConfig, ConfigManager
from .rotation_engine import RotationEngine
from .trade_executor import TradeExecutor, SimulatedExecutor, XtQuantExecutor


class ETFRotationLiveWidget(QWidget):
    """ETF轮动实盘操作面板"""

    def __init__(self, engine: Optional[RotationEngine] = None, parent=None):
        super().__init__(parent)

        # 引擎
        self.engine = engine or RotationEngine()
        self.engine.log_message.connect(self._on_log)
        self.engine.signal_generated.connect(self._on_signal)
        self.engine.trade_executed.connect(self._on_trade)
        self.engine.scores_updated.connect(self._on_scores)
        self.engine.status_updated.connect(self._on_status)

        # 状态刷新定时器
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_status)
        self._refresh_timer.start(5000)

        self._setup_ui()
        self._refresh_status()

    # ==================================================================
    #  UI 构建
    # ==================================================================

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左侧：状态 & 控制 ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(self._build_status_panel())
        left_layout.addWidget(self._build_action_panel())
        left_layout.addWidget(self._build_etf_panel())
        left_layout.addWidget(self._build_config_panel())
        left_layout.addStretch()

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left)
        left_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("QScrollArea{border:none;}")

        # ── 右侧：得分表 & 日志 ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self.tabs = QTabWidget()

        # Tab 1: 得分面板
        self.score_table = QTableWidget()
        self.score_table.setColumnCount(3)
        self.score_table.setHorizontalHeaderLabels(["ETF代码", "名称", "综合得分"])
        self.score_table.horizontalHeader().setStretchLastSection(True)
        self.score_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.score_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.score_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.score_table, "ETF得分")

        # Tab 2: 交易历史
        self.trade_table = QTableWidget()
        self.trade_table.setColumnCount(8)
        self.trade_table.setHorizontalHeaderLabels([
            "日期", "时间", "操作", "代码", "名称",
            "价格", "数量", "原因"
        ])
        self.trade_table.horizontalHeader().setStretchLastSection(True)
        self.trade_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.trade_table.setAlternatingRowColors(True)
        self.tabs.addTab(self.trade_table, "交易记录")

        # Tab 3: 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            "QTextEdit{font-family:Consolas,monospace;font-size:11px;"
            "background:#1a1a2e;color:#e0e0e0;}"
        )
        self.tabs.addTab(self.log_text, "运行日志")

        right_layout.addWidget(self.tabs)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setSizes([340, 660])

        layout.addWidget(splitter)

    # ── 状态面板 ──

    def _build_status_panel(self) -> QGroupBox:
        grp = QGroupBox("当前状态")
        grid = QGridLayout(grp)
        grid.setSpacing(6)

        def lbl(text, bold=False):
            l = QLabel(text)
            if bold:
                f = l.font()
                f.setBold(True)
                l.setFont(f)
            return l

        row = 0
        grid.addWidget(lbl("持仓标的:"), row, 0)
        self.lbl_holding = lbl("-", bold=True)
        self.lbl_holding.setStyleSheet("color:#FFD700;font-size:14px;")
        grid.addWidget(self.lbl_holding, row, 1)

        row += 1
        grid.addWidget(lbl("买入价格:"), row, 0)
        self.lbl_buy_price = lbl("-")
        grid.addWidget(self.lbl_buy_price, row, 1)

        row += 1
        grid.addWidget(lbl("当前价格:"), row, 0)
        self.lbl_current_price = lbl("-")
        grid.addWidget(self.lbl_current_price, row, 1)

        row += 1
        grid.addWidget(lbl("浮动盈亏:"), row, 0)
        self.lbl_pnl = lbl("-")
        self.lbl_pnl.setStyleSheet("font-size:13px;font-weight:bold;")
        grid.addWidget(self.lbl_pnl, row, 1)

        row += 1
        grid.addWidget(lbl("最近信号:"), row, 0)
        self.lbl_signal = lbl("-")
        grid.addWidget(self.lbl_signal, row, 1)

        row += 1
        grid.addWidget(lbl("最近检查:"), row, 0)
        self.lbl_last_check = lbl("-")
        self.lbl_last_check.setStyleSheet("color:#888;font-size:11px;")
        grid.addWidget(self.lbl_last_check, row, 1)

        row += 1
        grid.addWidget(lbl("执行器:"), row, 0)
        self.lbl_executor = lbl("-")
        grid.addWidget(self.lbl_executor, row, 1)

        return grp

    # ── 操作面板 ──

    def _build_action_panel(self) -> QGroupBox:
        grp = QGroupBox("操作")
        layout = QVBoxLayout(grp)

        # 信号检查按钮
        row1 = QHBoxLayout()

        self.btn_check = QPushButton("计算信号")
        self.btn_check.setToolTip("仅计算信号，不自动执行交易")
        self.btn_check.clicked.connect(self._on_check_signal)
        self.btn_check.setStyleSheet(
            "QPushButton{background:#0078d4;color:white;padding:8px 16px;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#1a8ae8;}"
        )
        row1.addWidget(self.btn_check)

        self.btn_execute = QPushButton("计算并执行")
        self.btn_execute.setToolTip("计算信号后自动执行交易")
        self.btn_execute.clicked.connect(self._on_check_and_execute)
        self.btn_execute.setStyleSheet(
            "QPushButton{background:#d83b01;color:white;padding:8px 16px;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#ea4c12;}"
        )
        row1.addWidget(self.btn_execute)

        layout.addLayout(row1)

        # 自动模式
        row2 = QHBoxLayout()
        self.btn_auto_start = QPushButton("启动自动")
        self.btn_auto_start.clicked.connect(self._on_start_auto)
        self.btn_auto_start.setStyleSheet(
            "QPushButton{background:#107c10;color:white;padding:6px 12px;"
            "border-radius:4px;}"
            "QPushButton:hover{background:#1a9a1a;}"
        )
        row2.addWidget(self.btn_auto_start)

        self.btn_auto_stop = QPushButton("停止自动")
        self.btn_auto_stop.clicked.connect(self._on_stop_auto)
        self.btn_auto_stop.setEnabled(False)
        row2.addWidget(self.btn_auto_stop)

        layout.addLayout(row2)

        self.lbl_auto_status = QLabel("自动模式: 未启动")
        self.lbl_auto_status.setStyleSheet("color:#888;font-size:11px;")
        layout.addWidget(self.lbl_auto_status)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#3c3c3c;")
        layout.addWidget(sep)

        # 手动交易
        manual_label = QLabel("手动交易:")
        manual_label.setStyleSheet("color:#aaa;font-size:11px;")
        layout.addWidget(manual_label)

        row3 = QHBoxLayout()
        self.btn_manual_sell = QPushButton("手动卖出当前持仓")
        self.btn_manual_sell.clicked.connect(self._on_manual_sell)
        self.btn_manual_sell.setStyleSheet(
            "QPushButton{background:#444;color:#fff;padding:6px;border-radius:3px;}"
            "QPushButton:hover{background:#555;}"
        )
        row3.addWidget(self.btn_manual_sell)
        layout.addLayout(row3)

        return grp

    # ── ETF 标的池面板 ──

    DEFAULT_ETF_POOL = [
        ('510880', '红利ETF'),
        ('159949', '创业板50ETF'),
        ('513100', '纳指ETF'),
        ('518880', '黄金ETF'),
    ]

    EXTENDED_ETF_POOL = [
        ('510300', '沪深300ETF'),
        ('510500', '中证500ETF'),
        ('159915', '创业板ETF'),
        ('512100', '中证1000ETF'),
        ('159901', '深证100ETF'),
        ('510050', '上证50ETF'),
        ('512010', '医药ETF'),
        ('512880', '证券ETF'),
        ('515180', '红利ETF基金'),
        ('512690', '酒ETF'),
        ('512480', '半导体ETF'),
        ('515790', '光伏ETF'),
        ('512660', '军工ETF'),
        ('159869', '游戏ETF'),
        ('513050', '中概互联ETF'),
        ('159941', '纳指ETF(QDII)'),
        ('513500', '标普500ETF'),
        ('518800', '黄金基金ETF'),
        ('511010', '国债ETF'),
        ('511260', '十年国债ETF'),
    ]

    def _build_etf_panel(self) -> QGroupBox:
        grp = QGroupBox("ETF标的池")
        layout = QVBoxLayout(grp)
        layout.setSpacing(4)

        # 批量操作按钮
        btn_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.setFixedHeight(22)
        btn_all.clicked.connect(self._etf_select_all)
        btn_row.addWidget(btn_all)

        btn_none = QPushButton("全不选")
        btn_none.setFixedHeight(22)
        btn_none.clicked.connect(self._etf_deselect_all)
        btn_row.addWidget(btn_none)

        btn_default = QPushButton("默认")
        btn_default.setFixedHeight(22)
        btn_default.setToolTip("恢复默认的4只ETF")
        btn_default.clicked.connect(self._etf_select_default)
        btn_row.addWidget(btn_default)

        layout.addLayout(btn_row)

        # ETF 列表
        self.etf_list = QListWidget()
        self.etf_list.setMinimumHeight(180)
        self.etf_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)

        # 加载 ETF 名称映射
        try:
            from common.data_loader import load_etf_name_map
            self._ui_etf_name_map = load_etf_name_map()
        except Exception:
            self._ui_etf_name_map = {}

        current_pool = set(self.engine.config.etf_pool)
        added = set()

        for code, name in self.DEFAULT_ETF_POOL:
            self._add_etf_item(code, name, checked=(code in current_pool))
            added.add(code)

        for code, name in self.EXTENDED_ETF_POOL:
            if code not in added:
                self._add_etf_item(code, name, checked=(code in current_pool))
                added.add(code)

        for code in current_pool:
            if code not in added:
                name = self._ui_etf_name_map.get(code, '')
                self._add_etf_item(code, name, checked=True)
                added.add(code)

        layout.addWidget(self.etf_list)

        # 手动添加/删除行
        custom_row = QHBoxLayout()
        self.etf_input = QComboBox()
        self.etf_input.setEditable(True)
        self.etf_input.setPlaceholderText("输入ETF代码")
        self.etf_input.lineEdit().setPlaceholderText("输入ETF代码")
        custom_row.addWidget(self.etf_input, 1)

        btn_add = QPushButton("+")
        btn_add.setFixedSize(26, 26)
        btn_add.setToolTip("添加自定义ETF")
        btn_add.clicked.connect(self._etf_add_custom)
        custom_row.addWidget(btn_add)

        btn_rm = QPushButton("-")
        btn_rm.setFixedSize(26, 26)
        btn_rm.setToolTip("删除选中的ETF")
        btn_rm.clicked.connect(self._etf_remove_selected)
        custom_row.addWidget(btn_rm)

        layout.addLayout(custom_row)

        self.lbl_etf_info = QLabel()
        self.lbl_etf_info.setStyleSheet("color:#888;font-size:11px;")
        self._etf_update_info()
        layout.addWidget(self.lbl_etf_info)

        return grp

    def _add_etf_item(self, code: str, name: str, checked: bool = False):
        display = f"{code}  {name}" if name else code
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, code)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        self.etf_list.addItem(item)

    def _etf_select_all(self):
        for i in range(self.etf_list.count()):
            self.etf_list.item(i).setCheckState(Qt.CheckState.Checked)
        self._etf_update_info()

    def _etf_deselect_all(self):
        for i in range(self.etf_list.count()):
            self.etf_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._etf_update_info()

    def _etf_select_default(self):
        defaults = {code for code, _ in self.DEFAULT_ETF_POOL}
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            code = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if code in defaults
                else Qt.CheckState.Unchecked
            )
        self._etf_update_info()

    def _etf_add_custom(self):
        code = self.etf_input.currentText().strip()
        if not code:
            return
        for i in range(self.etf_list.count()):
            if self.etf_list.item(i).data(Qt.ItemDataRole.UserRole) == code:
                self.etf_list.item(i).setCheckState(Qt.CheckState.Checked)
                self.etf_list.scrollToItem(self.etf_list.item(i))
                self.etf_input.clearEditText()
                self._etf_update_info()
                return
        name = self._ui_etf_name_map.get(code, '')
        self._add_etf_item(code, name, checked=True)
        self.etf_input.clearEditText()
        self.etf_list.scrollToItem(
            self.etf_list.item(self.etf_list.count() - 1)
        )
        self._etf_update_info()

    def _etf_remove_selected(self):
        selected = self.etf_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先点击选中要删除的ETF条目")
            return
        for item in selected:
            self.etf_list.takeItem(self.etf_list.row(item))
        self._etf_update_info()

    def _etf_update_info(self):
        total = self.etf_list.count()
        checked = sum(
            1 for i in range(total)
            if self.etf_list.item(i).checkState() == Qt.CheckState.Checked
        )
        self.lbl_etf_info.setText(f"共 {total} 只ETF，已选 {checked} 只")

    def _get_selected_etf_codes(self) -> list:
        """返回当前勾选的 ETF 代码列表"""
        codes = []
        for i in range(self.etf_list.count()):
            item = self.etf_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                codes.append(item.data(Qt.ItemDataRole.UserRole))
        return codes

    # ── 配置面板 ──

    def _build_config_panel(self) -> QGroupBox:
        grp = QGroupBox("策略参数")
        grid = QGridLayout(grp)
        grid.setSpacing(4)

        cfg = self.engine.config
        row = 0

        # 权重
        grid.addWidget(QLabel("乖离权重:"), row, 0)
        self.spin_bias = QDoubleSpinBox()
        self.spin_bias.setRange(0, 1); self.spin_bias.setSingleStep(0.1)
        self.spin_bias.setDecimals(2); self.spin_bias.setValue(cfg.bias_weight)
        grid.addWidget(self.spin_bias, row, 1)

        grid.addWidget(QLabel("斜率权重:"), row, 2)
        self.spin_slope = QDoubleSpinBox()
        self.spin_slope.setRange(0, 1); self.spin_slope.setSingleStep(0.1)
        self.spin_slope.setDecimals(2); self.spin_slope.setValue(cfg.slope_weight)
        grid.addWidget(self.spin_slope, row, 3)
        row += 1

        grid.addWidget(QLabel("效率权重:"), row, 0)
        self.spin_eff = QDoubleSpinBox()
        self.spin_eff.setRange(0, 1); self.spin_eff.setSingleStep(0.1)
        self.spin_eff.setDecimals(2); self.spin_eff.setValue(cfg.efficiency_weight)
        grid.addWidget(self.spin_eff, row, 1)

        grid.addWidget(QLabel("调仓阈值:"), row, 2)
        self.spin_threshold = QDoubleSpinBox()
        self.spin_threshold.setRange(1.0, 5.0); self.spin_threshold.setSingleStep(0.1)
        self.spin_threshold.setDecimals(2); self.spin_threshold.setValue(cfg.rebalance_threshold)
        grid.addWidget(self.spin_threshold, row, 3)
        row += 1

        grid.addWidget(QLabel("动量窗口:"), row, 0)
        self.spin_mom = QSpinBox()
        self.spin_mom.setRange(10, 60); self.spin_mom.setValue(cfg.momentum_window)
        grid.addWidget(self.spin_mom, row, 1)

        grid.addWidget(QLabel("ZScore窗口:"), row, 2)
        self.spin_zscore = QSpinBox()
        self.spin_zscore.setRange(20, 120); self.spin_zscore.setValue(cfg.zscore_window)
        grid.addWidget(self.spin_zscore, row, 3)
        row += 1

        self.chk_empty = QCheckBox("启用空仓信号")
        self.chk_empty.setChecked(cfg.enable_empty_position)
        grid.addWidget(self.chk_empty, row, 0, 1, 2)

        grid.addWidget(QLabel("空仓阈值:"), row, 2)
        self.spin_empty = QDoubleSpinBox()
        self.spin_empty.setRange(-3, 1); self.spin_empty.setSingleStep(0.1)
        self.spin_empty.setDecimals(2); self.spin_empty.setValue(cfg.empty_threshold)
        grid.addWidget(self.spin_empty, row, 3)
        row += 1

        grid.addWidget(QLabel("调仓周期:"), row, 0)
        self.combo_rebalance_period = QComboBox()
        _period_options = [
            ("每日 (1天)", 1), ("每2天", 2), ("每3天", 3),
            ("每周 (5天)", 5), ("每两周 (10天)", 10), ("每月 (20天)", 20),
        ]
        _cur_period = getattr(cfg, 'rebalance_period', 1)
        for label, val in _period_options:
            self.combo_rebalance_period.addItem(label, val)
        for i, (_, val) in enumerate(_period_options):
            if val == _cur_period:
                self.combo_rebalance_period.setCurrentIndex(i)
                break
        grid.addWidget(self.combo_rebalance_period, row, 1, 1, 3)
        row += 1

        grid.addWidget(QLabel("检查时间:"), row, 0)
        self.edit_time = QLineEdit(cfg.check_time)
        self.edit_time.setPlaceholderText("HH:MM")
        self.edit_time.setMaximumWidth(80)
        grid.addWidget(self.edit_time, row, 1)

        self.chk_notify = QCheckBox("启用通知")
        self.chk_notify.setChecked(cfg.notify_on_signal)
        grid.addWidget(self.chk_notify, row, 2, 1, 2)
        row += 1

        # 保存按钮
        self.btn_save_cfg = QPushButton("保存配置")
        self.btn_save_cfg.clicked.connect(self._on_save_config)
        self.btn_save_cfg.setStyleSheet(
            "QPushButton{background:#333;color:#ddd;padding:5px 12px;"
            "border:1px solid #555;border-radius:3px;}"
            "QPushButton:hover{background:#444;}"
        )
        grid.addWidget(self.btn_save_cfg, row, 2, 1, 2)

        return grp

    # ==================================================================
    #  事件处理
    # ==================================================================

    def _on_check_signal(self):
        self.btn_check.setEnabled(False)
        self.btn_check.setText("计算中...")
        try:
            self.engine.run_signal_check(auto_execute=False)
        finally:
            self.btn_check.setEnabled(True)
            self.btn_check.setText("计算信号")

    def _on_check_and_execute(self):
        reply = QMessageBox.question(
            self, "确认",
            "将计算信号并自动执行交易，确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.btn_execute.setEnabled(False)
        self.btn_execute.setText("执行中...")
        try:
            self.engine.run_signal_check(auto_execute=True)
        finally:
            self.btn_execute.setEnabled(True)
            self.btn_execute.setText("计算并执行")

    def _on_start_auto(self):
        self.engine.start_auto()
        self.btn_auto_start.setEnabled(False)
        self.btn_auto_stop.setEnabled(True)
        self.lbl_auto_status.setText(
            f"自动模式: 运行中 (每日 {self.engine.config.check_time} 检查)"
        )
        self.lbl_auto_status.setStyleSheet("color:#107c10;font-size:11px;")

    def _on_stop_auto(self):
        self.engine.stop_auto()
        self.btn_auto_start.setEnabled(True)
        self.btn_auto_stop.setEnabled(False)
        self.lbl_auto_status.setText("自动模式: 已停止")
        self.lbl_auto_status.setStyleSheet("color:#888;font-size:11px;")

    def _on_manual_sell(self):
        holding = self.engine.state.current_holding
        if not holding:
            QMessageBox.information(self, "提示", "当前无持仓")
            return

        reply = QMessageBox.question(
            self, "确认卖出",
            f"确定卖出当前持仓 {holding} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.engine._do_sell_all(reason="手动卖出")

    def _on_save_config(self):
        cfg = self.engine.config

        selected_etfs = self._get_selected_etf_codes()
        if len(selected_etfs) < 2:
            QMessageBox.warning(self, "提示", "至少需要选中2只ETF")
            return
        cfg.etf_pool = selected_etfs

        cfg.bias_weight = self.spin_bias.value()
        cfg.slope_weight = self.spin_slope.value()
        cfg.efficiency_weight = self.spin_eff.value()
        cfg.rebalance_threshold = self.spin_threshold.value()
        cfg.momentum_window = self.spin_mom.value()
        cfg.zscore_window = self.spin_zscore.value()
        cfg.enable_empty_position = self.chk_empty.isChecked()
        cfg.empty_threshold = self.spin_empty.value()
        cfg.rebalance_period = self.combo_rebalance_period.currentData()
        cfg.check_time = self.edit_time.text().strip() or "14:50"
        cfg.notify_on_signal = self.chk_notify.isChecked()
        cfg.notify_on_trade = self.chk_notify.isChecked()

        self.engine.update_config(cfg)
        QMessageBox.information(self, "提示",
            f"配置已保存（ETF池: {len(selected_etfs)} 只）")

    # ==================================================================
    #  信号回调
    # ==================================================================

    def _on_log(self, msg: str):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_signal(self, signal: str, detail: dict):
        self._refresh_status()

    def _on_trade(self, success: bool, detail: dict):
        self._refresh_status()
        self._refresh_trade_history()

    def _on_scores(self, scores: dict):
        self._update_score_table(scores)

    def _on_status(self, text: str):
        pass

    # ==================================================================
    #  数据刷新
    # ==================================================================

    def _refresh_status(self):
        summary = self.engine.get_status_summary()

        # 持仓信息
        if summary['holding']:
            self.lbl_holding.setText(
                f"{summary['holding']} {summary['holding_name']}"
            )
            self.lbl_buy_price.setText(
                f"{summary['buy_price']:.3f} "
                f"({summary['buy_quantity']}股, {summary['buy_date']})"
            )
        else:
            self.lbl_holding.setText("空仓")
            self.lbl_buy_price.setText("-")

        # 当前价格 & 盈亏
        if summary['current_price'] > 0:
            self.lbl_current_price.setText(f"{summary['current_price']:.3f}")
            pnl = summary['unrealized_pnl']
            pnl_color = "#FF4444" if pnl >= 0 else "#44FF44"
            self.lbl_pnl.setText(f"{pnl:+,.2f}")
            self.lbl_pnl.setStyleSheet(
                f"color:{pnl_color};font-size:13px;font-weight:bold;"
            )
        else:
            self.lbl_current_price.setText("-")
            self.lbl_pnl.setText("-")
            self.lbl_pnl.setStyleSheet("font-size:13px;")

        # 信号
        signal = summary['last_signal']
        if signal:
            signal_colors = {
                'HOLD': '#0078d4', 'SWITCH': '#FFD700',
                'SELL_ALL': '#FF4444', 'BUY': '#44FF44',
                'NO_ACTION': '#888',
            }
            color = signal_colors.get(signal, '#fff')
            self.lbl_signal.setText(signal)
            self.lbl_signal.setStyleSheet(f"color:{color};font-weight:bold;")
        else:
            self.lbl_signal.setText("-")

        # 检查时间
        self.lbl_last_check.setText(summary['last_check'] or "-")

        # 执行器
        connected = summary['executor_connected']
        exec_type = type(self.engine.executor).__name__
        if connected:
            self.lbl_executor.setText(f"✓ {exec_type}")
            self.lbl_executor.setStyleSheet("color:#44FF44;")
        else:
            self.lbl_executor.setText(f"✗ {exec_type} (未连接)")
            self.lbl_executor.setStyleSheet("color:#FF4444;")

        # 自动模式状态
        if self.engine.config.auto_enabled:
            self.btn_auto_start.setEnabled(False)
            self.btn_auto_stop.setEnabled(True)
            self.lbl_auto_status.setText(
                f"自动模式: 运行中 (每日 {self.engine.config.check_time})"
            )
            self.lbl_auto_status.setStyleSheet("color:#107c10;font-size:11px;")

        # 得分快照
        if summary['last_scores']:
            self._update_score_table(summary['last_scores'])

    def _update_score_table(self, scores: dict):
        name_map = self.engine._etf_name_map
        holding = self.engine.state.current_holding
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        self.score_table.setRowCount(len(sorted_items))
        for i, (code, score) in enumerate(sorted_items):
            # 代码
            code_item = QTableWidgetItem(code)
            if code == holding:
                code_item.setBackground(QColor(0, 80, 0))
            self.score_table.setItem(i, 0, code_item)

            # 名称
            name = name_map.get(code, "")
            name_item = QTableWidgetItem(name)
            if code == holding:
                name_item.setBackground(QColor(0, 80, 0))
            self.score_table.setItem(i, 1, name_item)

            # 得分
            score_item = QTableWidgetItem(f"{score:+.4f}")
            score_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            if score > 0:
                score_item.setForeground(QColor("#FF4444"))
            elif score < 0:
                score_item.setForeground(QColor("#44FF44"))
            if code == holding:
                score_item.setBackground(QColor(0, 80, 0))
            self.score_table.setItem(i, 2, score_item)

    def _refresh_trade_history(self):
        history = self.engine.state.trade_history
        self.trade_table.setRowCount(len(history))

        for i, rec in enumerate(reversed(history)):
            row = i
            self.trade_table.setItem(row, 0, QTableWidgetItem(rec.get('date', '')))
            self.trade_table.setItem(row, 1, QTableWidgetItem(rec.get('time', '')))

            action = rec.get('action', '')
            action_item = QTableWidgetItem(action)
            if action == 'BUY':
                action_item.setForeground(QColor("#FF4444"))
            elif action in ('SELL', 'SELL_ALL'):
                action_item.setForeground(QColor("#44FF44"))
            self.trade_table.setItem(row, 2, action_item)

            self.trade_table.setItem(row, 3, QTableWidgetItem(rec.get('code', '')))
            self.trade_table.setItem(row, 4, QTableWidgetItem(rec.get('name', '')))

            price = rec.get('price', 0)
            self.trade_table.setItem(row, 5, QTableWidgetItem(
                f"{price:.3f}" if price else "-"
            ))

            qty = rec.get('quantity', 0)
            self.trade_table.setItem(row, 6, QTableWidgetItem(str(qty)))
            self.trade_table.setItem(row, 7, QTableWidgetItem(rec.get('reason', '')))

    # ==================================================================
    #  外部集成接口
    # ==================================================================

    def set_executor(self, executor: TradeExecutor):
        """供 MainWindow 注入真实交易执行器"""
        self.engine.set_executor(executor)
        self._refresh_status()

    def inject_broker(self, xt_trader, acc):
        """
        供 BrokerAccountWidget 连接成功后调用，注入券商对象

        用法（在 trading_app/main_window.py 中）:
            self.rotation_widget.inject_broker(self.broker_widget.xt_trader,
                                               self.broker_widget.acc)
        """
        executor = XtQuantExecutor()
        executor.set_broker(xt_trader, acc)
        self.engine.set_executor(executor)
        self._refresh_status()
