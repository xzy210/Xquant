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

_strategy_app = str(Path(__file__).resolve().parent.parent / "strategy_app")
if _strategy_app not in sys.path:
    sys.path.insert(0, _strategy_app)
from factors.registry import factor_registry
import factors.etf_momentum_factors_optimized  # noqa: F401


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

    # 浅色主题色板
    _THEME = {
        'bg':           '#EEF2F7',   # 整体背景（淡蓝灰）
        'panel_bg':     '#FFFFFF',   # 面板/卡片背景
        'border':       '#D0D8E0',   # 边框
        'text':         '#2C3E50',   # 主文字
        'text_secondary': '#6B7B8D', # 次要文字
        'accent':       '#3B82F6',   # 强调色（蓝）
        'table_alt':    '#F5F8FB',   # 表格交替行
        'table_header': '#E8EDF2',   # 表头背景
        'table_grid':   '#E0E6ED',   # 表格网格线
        'selected':     '#DBEAFE',   # 选中行
        'red':          '#DC2626',   # 买入/亏损红
        'green':        '#16A34A',   # 卖出/盈利绿
        'orange':       '#EA580C',   # 警告橙
        'holding_bg':   '#DCFCE7',   # 持仓行高亮（浅绿）
    }

    def _setup_ui(self):
        t = self._THEME
        # 用 * 通配符确保所有子 widget 都继承浅色背景，
        # 再用具体选择器覆盖需要特殊处理的控件。
        self.setStyleSheet(
            f"ETFRotationLiveWidget, ETFRotationLiveWidget *{{"
            f"  background-color:{t['bg']}; color:{t['text']};"
            f"}}"
            f"QGroupBox{{"
            f"  background-color:{t['panel_bg']};"
            f"  border:1px solid {t['border']}; border-radius:6px;"
            f"  margin-top:10px; padding:12px 8px 8px 8px;"
            f"  font-weight:bold; color:{t['text']};"
            f"}}"
            f"QGroupBox::title{{"
            f"  subcontrol-origin:margin;"
            f"  left:12px; padding:0 4px;"
            f"  color:{t['accent']};"
            f"}}"
            f"QGroupBox QWidget{{"
            f"  background-color:{t['panel_bg']};"
            f"}}"
            f"QLabel{{ color:{t['text']}; background:transparent; }}"
            f"QCheckBox{{ color:{t['text']}; background:transparent; }}"
            f"QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"  border:1px solid {t['border']}; border-radius:3px;"
            f"  padding:2px 4px;"
            f"}}"
            f"QComboBox QAbstractItemView{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"  selection-background-color:{t['selected']};"
            f"}}"
            f"QListWidget{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"  border:1px solid {t['border']}; border-radius:3px;"
            f"}}"
            f"QListWidget::item{{"
            f"  background:{t['panel_bg']}; color:{t['text']};"
            f"}}"
            f"QSplitter::handle{{"
            f"  background:{t['bg']};"
            f"}}"
            f"QFrame{{"
            f"  background:transparent;"
            f"}}"
            f"QPushButton{{"
            f"  background-color:{t['table_header']}; color:{t['text']};"
            f"  border:1px solid {t['border']}; border-radius:4px;"
            f"  padding:4px 10px;"
            f"}}"
            f"QPushButton:hover{{"
            f"  background-color:{t['border']};"
            f"}}"
            f"QPushButton:pressed{{"
            f"  background-color:{t['table_grid']};"
            f"}}"
            f"QPushButton:disabled{{"
            f"  background-color:{t['table_alt']}; color:{t['text_secondary']};"
            f"  border:1px solid {t['table_grid']};"
            f"}}"
            f"QSpinBox::up-button, QDoubleSpinBox::up-button,"
            f"QSpinBox::down-button, QDoubleSpinBox::down-button{{"
            f"  background:{t['table_header']}; border:1px solid {t['border']};"
            f"  width:16px;"
            f"}}"
            f"QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
            f"QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover{{"
            f"  background:{t['border']};"
            f"}}"
            f"QSpinBox::up-arrow, QDoubleSpinBox::up-arrow{{"
            f"  width:8px; height:8px;"
            f"}}"
            f"QSpinBox::down-arrow, QDoubleSpinBox::down-arrow{{"
            f"  width:8px; height:8px;"
            f"}}"
            f"QComboBox::drop-down{{"
            f"  background:{t['table_header']}; border:1px solid {t['border']};"
            f"  border-top-right-radius:3px; border-bottom-right-radius:3px;"
            f"  width:20px;"
            f"}}"
            f"QScrollBar:vertical,QScrollBar:horizontal{{"
            f"  background:{t['bg']}; border:none; width:8px; height:8px;"
            f"}}"
            f"QScrollBar::handle:vertical,QScrollBar::handle:horizontal{{"
            f"  background:{t['border']}; border-radius:4px; min-height:20px;"
            f"}}"
            f"QScrollBar::add-line,QScrollBar::sub-line{{"
            f"  height:0; width:0;"
            f"}}"
            f"QTabWidget::pane{{"
            f"  border:1px solid {t['border']}; border-radius:4px;"
            f"  background:{t['panel_bg']};"
            f"}}"
            f"QTabBar::tab{{"
            f"  background:{t['table_header']}; color:{t['text_secondary']};"
            f"  padding:6px 16px; border:1px solid {t['border']};"
            f"  border-bottom:none; border-top-left-radius:4px;"
            f"  border-top-right-radius:4px; margin-right:2px;"
            f"}}"
            f"QTabBar::tab:selected{{"
            f"  background:{t['panel_bg']}; color:{t['accent']};"
            f"  font-weight:bold;"
            f"}}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

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
        left_scroll.setStyleSheet(
            f"QScrollArea{{border:none; background:{t['bg']};}}"
            f"QScrollArea > QWidget{{background:{t['bg']};}}")

        # ── 右侧：得分表 & 日志 ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self.tabs = QTabWidget()

        _table_style = (
            f"QTableWidget{{"
            f"  background-color:{t['panel_bg']}; color:{t['text']};"
            f"  gridline-color:{t['table_grid']}; border:none;"
            f"  font-size:12px;"
            f"}}"
            f"QTableWidget::item{{"
            f"  padding:4px 6px;"
            f"}}"
            f"QTableWidget::item:alternate{{"
            f"  background-color:{t['table_alt']};"
            f"}}"
            f"QTableWidget::item:selected{{"
            f"  background-color:{t['selected']}; color:{t['text']};"
            f"}}"
            f"QHeaderView::section{{"
            f"  background-color:{t['table_header']}; color:{t['text_secondary']};"
            f"  border:none; border-bottom:1px solid {t['border']};"
            f"  padding:5px 6px; font-weight:bold; font-size:11px;"
            f"}}"
        )

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
        self.score_table.setStyleSheet(_table_style)
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
        self.trade_table.setStyleSheet(_table_style)
        self.tabs.addTab(self.trade_table, "交易记录")

        # Tab 3: 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            f"QTextEdit{{font-family:Consolas,monospace;font-size:11px;"
            f"background:{t['panel_bg']};color:{t['text']};"
            f"border:none;}}"
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
        self.lbl_holding.setStyleSheet("color:#1D4ED8;font-size:14px;")
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
        self.lbl_last_check.setStyleSheet("color:#6B7B8D;font-size:11px;")
        grid.addWidget(self.lbl_last_check, row, 1)

        row += 1
        grid.addWidget(lbl("数据状态:"), row, 0)
        self.lbl_data_status = lbl("-")
        self.lbl_data_status.setStyleSheet("font-size:11px;")
        grid.addWidget(self.lbl_data_status, row, 1)

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
            "QPushButton{background:#3B82F6;color:white;padding:8px 16px;"
            "border-radius:5px;font-weight:bold;}"
            "QPushButton:hover{background:#2563EB;}"
        )
        row1.addWidget(self.btn_check)

        self.btn_execute = QPushButton("计算并执行")
        self.btn_execute.setToolTip("计算信号后自动执行交易")
        self.btn_execute.clicked.connect(self._on_check_and_execute)
        self.btn_execute.setStyleSheet(
            "QPushButton{background:#DC2626;color:white;padding:8px 16px;"
            "border-radius:5px;font-weight:bold;}"
            "QPushButton:hover{background:#B91C1C;}"
        )
        row1.addWidget(self.btn_execute)

        layout.addLayout(row1)

        # 自动模式
        row2 = QHBoxLayout()
        self.btn_auto_start = QPushButton("启动自动")
        self.btn_auto_start.clicked.connect(self._on_start_auto)
        self.btn_auto_start.setStyleSheet(
            "QPushButton{background:#16A34A;color:white;padding:6px 12px;"
            "border-radius:5px;}"
            "QPushButton:hover{background:#15803D;}"
        )
        row2.addWidget(self.btn_auto_start)

        self.btn_auto_stop = QPushButton("停止自动")
        self.btn_auto_stop.clicked.connect(self._on_stop_auto)
        self.btn_auto_stop.setEnabled(False)
        row2.addWidget(self.btn_auto_stop)

        layout.addLayout(row2)

        self.lbl_auto_status = QLabel("自动模式: 未启动")
        self.lbl_auto_status.setStyleSheet("color:#6B7B8D;font-size:11px;")
        layout.addWidget(self.lbl_auto_status)

        # 数据更新
        row_data = QHBoxLayout()
        self.btn_update_data = QPushButton("更新ETF数据")
        self.btn_update_data.setToolTip("从miniQMT增量更新ETF池的日线数据")
        self.btn_update_data.clicked.connect(self._on_update_data)
        self.btn_update_data.setStyleSheet(
            "QPushButton{background:#7C3AED;color:white;padding:6px 12px;"
            "border-radius:5px;}"
            "QPushButton:hover{background:#6D28D9;}"
        )
        row_data.addWidget(self.btn_update_data)

        self.btn_update_data_full = QPushButton("全量重建")
        self.btn_update_data_full.setToolTip("全量拉取所有ETF历史数据（较慢）")
        self.btn_update_data_full.clicked.connect(self._on_update_data_full)
        self.btn_update_data_full.setStyleSheet(
            "QPushButton{background:#94A3B8;color:white;padding:6px 12px;"
            "border-radius:5px;}"
            "QPushButton:hover{background:#64748B;}"
        )
        row_data.addWidget(self.btn_update_data_full)
        layout.addLayout(row_data)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#D0D8E0;")
        layout.addWidget(sep)

        # 手动交易
        manual_label = QLabel("手动交易:")
        manual_label.setStyleSheet("color:#6B7B8D;font-size:11px;")
        layout.addWidget(manual_label)

        row3 = QHBoxLayout()
        self.btn_manual_sell = QPushButton("手动卖出当前持仓")
        self.btn_manual_sell.clicked.connect(self._on_manual_sell)
        self.btn_manual_sell.setStyleSheet(
            "QPushButton{background:#E2E8F0;color:#334155;padding:6px;"
            "border:1px solid #CBD5E1;border-radius:4px;}"
            "QPushButton:hover{background:#CBD5E1;}"
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
        self.lbl_etf_info.setStyleSheet("color:#6B7B8D;font-size:11px;")
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

        # 因子配置（紧凑网格）
        _ETF_FACTORS = {
            'bias_momentum_fast': '乖离动量',
            'slope_momentum_fast': '斜率动量',
            'efficiency_momentum_fast': '效率动量',
            'risk_adjusted_momentum': '风险调整动量',
            'inverse_volatility': '反向波动率',
            'volume_price_correlation': '量价相关性',
        }
        active_factors = {name: w for name, w in cfg.factor_config}
        self._live_factor_rows = []

        factor_label = QLabel("因子:")
        factor_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(factor_label, row, 0, 1, 4)
        row += 1

        for fname, display_name in _ETF_FACTORS.items():
            if factor_registry.get(fname) is None:
                continue
            is_active = fname in active_factors
            chk = QCheckBox(display_name)
            chk.setChecked(is_active)
            chk.setToolTip(fname)
            grid.addWidget(chk, row, 0, 1, 3)

            ws = QDoubleSpinBox()
            ws.setRange(0, 5); ws.setSingleStep(0.05); ws.setDecimals(2)
            ws.setValue(active_factors.get(fname, 0.2))
            ws.setEnabled(is_active)
            ws.setFixedWidth(60)
            chk.stateChanged.connect(lambda st, w=ws: w.setEnabled(st == Qt.CheckState.Checked.value))
            grid.addWidget(ws, row, 3)
            self._live_factor_rows.append((fname, chk, ws))
            row += 1

        grid.addWidget(QLabel("调仓阈值:"), row, 0)
        self.spin_threshold = QDoubleSpinBox()
        self.spin_threshold.setRange(1.0, 5.0); self.spin_threshold.setSingleStep(0.1)
        self.spin_threshold.setDecimals(2); self.spin_threshold.setValue(cfg.rebalance_threshold)
        grid.addWidget(self.spin_threshold, row, 1)
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

        # ── 风控设置 ──
        sep_risk = QLabel("── 风控 ──")
        sep_risk.setStyleSheet("color:#94A3B8;font-size:11px;")
        sep_risk.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(sep_risk, row, 0, 1, 4)
        row += 1

        self.chk_trailing_stop = QCheckBox("移动止盈")
        self.chk_trailing_stop.setChecked(cfg.enable_trailing_stop)
        grid.addWidget(self.chk_trailing_stop, row, 0, 1, 2)
        grid.addWidget(QLabel("回撤%:"), row, 2)
        self.spin_trailing_pct = QDoubleSpinBox()
        self.spin_trailing_pct.setRange(1, 30)
        self.spin_trailing_pct.setSingleStep(1)
        self.spin_trailing_pct.setDecimals(1)
        self.spin_trailing_pct.setValue(cfg.trailing_stop_pct * 100)
        grid.addWidget(self.spin_trailing_pct, row, 3)
        row += 1

        self.chk_drawdown = QCheckBox("账户回撤保护")
        self.chk_drawdown.setChecked(cfg.enable_drawdown_protection)
        grid.addWidget(self.chk_drawdown, row, 0, 1, 2)
        grid.addWidget(QLabel("最大回撤%:"), row, 2)
        self.spin_max_dd = QDoubleSpinBox()
        self.spin_max_dd.setRange(5, 50)
        self.spin_max_dd.setSingleStep(1)
        self.spin_max_dd.setDecimals(1)
        self.spin_max_dd.setValue(cfg.max_drawdown_pct * 100)
        grid.addWidget(self.spin_max_dd, row, 3)
        row += 1

        grid.addWidget(QLabel("冷却天数:"), row, 0)
        self.spin_cooldown = QSpinBox()
        self.spin_cooldown.setRange(1, 60)
        self.spin_cooldown.setValue(cfg.drawdown_cooldown_days)
        grid.addWidget(self.spin_cooldown, row, 1)
        row += 1

        grid.addWidget(QLabel("更新时间:"), row, 0)
        self.edit_update_time = QLineEdit(cfg.data_update_time)
        self.edit_update_time.setPlaceholderText("HH:MM")
        self.edit_update_time.setMaximumWidth(80)
        self.edit_update_time.setToolTip("ETF数据自动更新时间")
        grid.addWidget(self.edit_update_time, row, 1)

        grid.addWidget(QLabel("检查时间:"), row, 2)
        self.edit_time = QLineEdit(cfg.check_time)
        self.edit_time.setPlaceholderText("HH:MM")
        self.edit_time.setMaximumWidth(80)
        grid.addWidget(self.edit_time, row, 3)
        row += 1

        self.chk_notify = QCheckBox("启用通知")
        self.chk_notify.setChecked(cfg.notify_on_signal)
        grid.addWidget(self.chk_notify, row, 0, 1, 2)
        row += 1

        # 保存按钮
        self.btn_save_cfg = QPushButton("保存配置")
        self.btn_save_cfg.clicked.connect(self._on_save_config)
        self.btn_save_cfg.setStyleSheet(
            "QPushButton{background:#3B82F6;color:white;padding:5px 12px;"
            "border-radius:4px;}"
            "QPushButton:hover{background:#2563EB;}"
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
        self.lbl_auto_status.setStyleSheet("color:#16A34A;font-size:11px;")

    def _on_stop_auto(self):
        self.engine.stop_auto()
        self.btn_auto_start.setEnabled(True)
        self.btn_auto_stop.setEnabled(False)
        self.lbl_auto_status.setText("自动模式: 已停止")
        self.lbl_auto_status.setStyleSheet("color:#6B7B8D;font-size:11px;")

    def _on_update_data(self):
        self.btn_update_data.setEnabled(False)
        self.btn_update_data.setText("更新中...")
        self.engine.update_data(auto_execute_after=False)
        if self.engine._update_thread:
            self.engine._update_thread.finished_signal.connect(
                self._on_data_update_done)

    def _on_update_data_full(self):
        reply = QMessageBox.question(
            self, "确认",
            "全量重建将重新拉取所有ETF历史数据，耗时较长，确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.btn_update_data_full.setEnabled(False)
        self.btn_update_data_full.setText("重建中...")

        from .data_updater import ETFDataUpdateThread
        self._full_update_thread = ETFDataUpdateThread(
            self.engine.config.etf_pool, self.engine._data_dir,
            full=True, parent=self
        )
        self._full_update_thread.progress.connect(self.engine._on_update_progress)
        self._full_update_thread.finished_signal.connect(self._on_data_update_done)
        self._full_update_thread.start()

    def _on_data_update_done(self, success, total, errors):
        self.btn_update_data.setEnabled(True)
        self.btn_update_data.setText("更新ETF数据")
        self.btn_update_data_full.setEnabled(True)
        self.btn_update_data_full.setText("全量重建")
        self._refresh_status()

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

        fc = []
        for fname, chk, ws in self._live_factor_rows:
            if chk.isChecked():
                fc.append((fname, ws.value()))
        if not fc:
            QMessageBox.warning(self, "提示", "至少需要选中1个因子")
            return
        cfg.factor_config = fc
        cfg.rebalance_threshold = self.spin_threshold.value()
        cfg.momentum_window = self.spin_mom.value()
        cfg.zscore_window = self.spin_zscore.value()
        cfg.enable_empty_position = self.chk_empty.isChecked()
        cfg.empty_threshold = self.spin_empty.value()
        cfg.rebalance_period = self.combo_rebalance_period.currentData()
        cfg.enable_trailing_stop = self.chk_trailing_stop.isChecked()
        cfg.trailing_stop_pct = self.spin_trailing_pct.value() / 100
        cfg.enable_drawdown_protection = self.chk_drawdown.isChecked()
        cfg.max_drawdown_pct = self.spin_max_dd.value() / 100
        cfg.drawdown_cooldown_days = self.spin_cooldown.value()
        cfg.data_update_time = self.edit_update_time.text().strip() or "14:30"
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
            pnl_color = "#DC2626" if pnl >= 0 else "#16A34A"
            self.lbl_pnl.setText(f"{pnl:+,.2f}")
            self.lbl_pnl.setStyleSheet(
                f"color:{pnl_color};font-size:13px;font-weight:bold;"
            )
        else:
            self.lbl_current_price.setText("-")
            self.lbl_pnl.setText("-")
            self.lbl_pnl.setStyleSheet("font-size:13px;color:#2C3E50;")

        # 信号
        signal = summary['last_signal']
        if signal:
            signal_colors = {
                'HOLD': '#3B82F6', 'SWITCH': '#D97706',
                'SELL_ALL': '#DC2626', 'BUY': '#16A34A',
                'NO_ACTION': '#94A3B8',
                'TRAILING_STOP': '#EA580C', 'DRAWDOWN_STOP': '#DC2626',
                'COOLDOWN': '#6B7B8D',
            }
            color = signal_colors.get(signal, '#2C3E50')
            self.lbl_signal.setText(signal)
            self.lbl_signal.setStyleSheet(f"color:{color};font-weight:bold;")
        else:
            self.lbl_signal.setText("-")

        # 检查时间
        self.lbl_last_check.setText(summary['last_check'] or "-")

        # 数据状态
        data_fresh = summary.get('data_fresh', False)
        if data_fresh:
            self.lbl_data_status.setText("✓ 数据已是最新")
            self.lbl_data_status.setStyleSheet("color:#16A34A;font-size:11px;")
        else:
            self.lbl_data_status.setText("✗ 数据需要更新")
            self.lbl_data_status.setStyleSheet("color:#EA580C;font-size:11px;")

        # 执行器
        connected = summary['executor_connected']
        exec_type = type(self.engine.executor).__name__
        if connected:
            self.lbl_executor.setText(f"✓ {exec_type}")
            self.lbl_executor.setStyleSheet("color:#16A34A;")
        else:
            self.lbl_executor.setText(f"✗ {exec_type} (未连接)")
            self.lbl_executor.setStyleSheet("color:#DC2626;")

        # 自动模式状态 & 冷却期显示
        cooldown = summary.get('cooldown_remaining', 0)
        if cooldown > 0:
            self.lbl_auto_status.setText(
                f"⚠ 回撤保护冷却期（剩余 {cooldown} 天）"
            )
            self.lbl_auto_status.setStyleSheet("color:#EA580C;font-size:11px;")
        elif self.engine.config.auto_enabled:
            self.btn_auto_start.setEnabled(False)
            self.btn_auto_stop.setEnabled(True)
            self.lbl_auto_status.setText(
                f"自动模式: 运行中 (每日 {self.engine.config.check_time})"
            )
            self.lbl_auto_status.setStyleSheet("color:#16A34A;font-size:11px;")

        # 得分快照
        if summary['last_scores']:
            self._update_score_table(summary['last_scores'])

    def _update_score_table(self, scores: dict):
        t = self._THEME
        name_map = self.engine._etf_name_map
        holding = self.engine.state.current_holding
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        self.score_table.setRowCount(len(sorted_items))
        for i, (code, score) in enumerate(sorted_items):
            is_holding = (code == holding)
            bg = QColor(t['holding_bg']) if is_holding else None

            # 代码
            code_item = QTableWidgetItem(code)
            code_item.setForeground(QColor(t['text']))
            if bg:
                code_item.setBackground(bg)
            self.score_table.setItem(i, 0, code_item)

            # 名称
            name = name_map.get(code, "")
            name_item = QTableWidgetItem(name)
            name_item.setForeground(QColor(t['text_secondary']))
            if bg:
                name_item.setBackground(bg)
            self.score_table.setItem(i, 1, name_item)

            # 得分 — 正值红色、负值绿色、零灰色
            score_item = QTableWidgetItem(f"{score:+.4f}")
            score_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            if score > 0:
                score_item.setForeground(QColor(t['red']))
            elif score < 0:
                score_item.setForeground(QColor(t['green']))
            else:
                score_item.setForeground(QColor(t['text_secondary']))
            if bg:
                score_item.setBackground(bg)
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
                action_item.setForeground(QColor("#DC2626"))
            elif action in ('SELL', 'SELL_ALL'):
                action_item.setForeground(QColor("#16A34A"))
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
