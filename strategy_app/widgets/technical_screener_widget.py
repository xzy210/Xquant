"""
智能选股器界面

集成到PyQt App中的选股工具
"""

import pandas as pd
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, 
    QTableWidgetItem, QProgressBar, QLabel, QHeaderView, QGroupBox,
    QDoubleSpinBox, QSpinBox, QCheckBox, QFileDialog, QMessageBox,
    QSplitter, QTextEdit, QComboBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor

try:
    from strategies.stock_screener import (
        StockScreener, ScreeningCriteria, StockScore,
        screen_for_volatility_breakout
    )
    from data_loader import get_stock_list, load_stock_name_map
except ImportError:
    from ..strategies.stock_screener import (
        StockScreener, ScreeningCriteria, StockScore,
        screen_for_volatility_breakout
    )
    from ..data_loader import get_stock_list, load_stock_name_map


class ScreeningThread(QThread):
    """选股后台线程"""
    progress_updated = pyqtSignal(int, int, str)  # current, total, code
    finished_signal = pyqtSignal(list)  # results
    error_signal = pyqtSignal(str)
    
    def __init__(self, screener, stock_list, criteria, name_map=None):
        super().__init__()
        self.screener = screener
        self.stock_list = stock_list
        self.criteria = criteria
        self.name_map = name_map or {}
    
    def run(self):
        try:
            results = self.screener.screen(
                self.stock_list, 
                self.criteria,
                self.name_map,
                progress_callback=lambda c, t, code: 
                    self.progress_updated.emit(c, t, code)
            )
            self.finished_signal.emit(results)
        except Exception as e:
            self.error_signal.emit(str(e))


class TechnicalScreenerWidget(QWidget):
    """智能选股器主界面"""
    
    stock_selected = pyqtSignal(str, str)  # code, name
    
    def __init__(self, data_dir="../data", stocklist_path=None):
        super().__init__()
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        self.screener = None
        self.screening_thread = None
        self.current_results = []
        
        self.setupUI()
        self.load_data()
    
    def setupUI(self):
        """设置界面"""
        layout = QHBoxLayout(self)
        
        # 左侧面板：设置
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 10, 0)
        
        # 预设配置
        preset_group = QGroupBox("快速配置")
        preset_layout = QVBoxLayout(preset_group)
        
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("自定义", "custom")
        self.preset_combo.addItem("波动率突破优化", "volatility_breakout")
        self.preset_combo.addItem("稳健型", "conservative")
        self.preset_combo.addItem("激进型", "aggressive")
        self.preset_combo.currentIndexChanged.connect(self.on_preset_changed)
        preset_layout.addWidget(self.preset_combo)
        
        left_layout.addWidget(preset_group)
        
        # 波动率设置
        vol_group = QGroupBox("波动率条件")
        vol_layout = QVBoxLayout(vol_group)
        
        vol_layout.addWidget(QLabel("最小ATR%:"))
        self.min_atr_spin = QDoubleSpinBox()
        self.min_atr_spin.setRange(0.5, 20.0)
        self.min_atr_spin.setValue(2.0)
        self.min_atr_spin.setSuffix("%")
        self.min_atr_spin.setDecimals(2)
        vol_layout.addWidget(self.min_atr_spin)
        
        vol_layout.addWidget(QLabel("最大ATR%:"))
        self.max_atr_spin = QDoubleSpinBox()
        self.max_atr_spin.setRange(1.0, 30.0)
        self.max_atr_spin.setValue(5.0)
        self.max_atr_spin.setSuffix("%")
        self.max_atr_spin.setDecimals(2)
        vol_layout.addWidget(self.max_atr_spin)
        
        left_layout.addWidget(vol_group)
        
        # 趋势设置
        trend_group = QGroupBox("趋势条件")
        trend_layout = QVBoxLayout(trend_group)
        
        trend_layout.addWidget(QLabel("最小ADX:"))
        self.min_adx_spin = QDoubleSpinBox()
        self.min_adx_spin.setRange(0, 100)
        self.min_adx_spin.setValue(25.0)
        self.min_adx_spin.setDecimals(1)
        trend_layout.addWidget(self.min_adx_spin)
        
        left_layout.addWidget(trend_group)
        
        # 成交量设置
        vol2_group = QGroupBox("成交量条件")
        vol2_layout = QVBoxLayout(vol2_group)
        
        vol2_layout.addWidget(QLabel("最小日均成交额(亿):"))
        self.min_amount_spin = QDoubleSpinBox()
        self.min_amount_spin.setRange(0, 100)
        self.min_amount_spin.setValue(1.0)
        self.min_amount_spin.setDecimals(2)
        vol2_layout.addWidget(self.min_amount_spin)
        
        left_layout.addWidget(vol2_group)
        
        # 形态设置
        pattern_group = QGroupBox("形态条件")
        pattern_layout = QVBoxLayout(pattern_group)
        
        pattern_layout.addWidget(QLabel("接近近期高点比例:"))
        self.proximity_spin = QDoubleSpinBox()
        self.proximity_spin.setRange(0.5, 1.0)
        self.proximity_spin.setValue(0.95)
        self.proximity_spin.setSingleStep(0.05)
        self.proximity_spin.setDecimals(2)
        pattern_layout.addWidget(self.proximity_spin)
        
        left_layout.addWidget(pattern_group)
        
        # 其他设置
        other_group = QGroupBox("其他设置")
        other_layout = QVBoxLayout(other_group)
        
        other_layout.addWidget(QLabel("最大结果数量:"))
        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(5, 200)
        self.max_results_spin.setValue(50)
        other_layout.addWidget(self.max_results_spin)
        
        self.exclude_st_check = QCheckBox("排除ST股票")
        self.exclude_st_check.setChecked(True)
        other_layout.addWidget(self.exclude_st_check)
        
        left_layout.addWidget(other_group)
        
        # 操作按钮
        btn_layout = QHBoxLayout()
        
        self.run_btn = QPushButton("开始选股")
        self.run_btn.setProperty("class", "primary")
        self.run_btn.clicked.connect(self.start_screening)
        btn_layout.addWidget(self.run_btn)
        
        self.export_btn = QPushButton("导出结果")
        self.export_btn.clicked.connect(self.export_results)
        self.export_btn.setEnabled(False)
        btn_layout.addWidget(self.export_btn)
        
        left_layout.addLayout(btn_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("就绪")
        left_layout.addWidget(self.status_label)
        
        left_layout.addStretch()
        
        layout.addWidget(left_panel, stretch=1)
        
        # 右侧面板：结果
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(10)
        self.result_table.setHorizontalHeaderLabels([
            "排名", "代码", "名称", "总分", "ATR%", "ADX", 
            "成交额(亿)", "距高点%", "20日涨幅%", "操作"
        ])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.result_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.result_table.cellDoubleClicked.connect(self.on_cell_double_clicked)
        right_layout.addWidget(self.result_table)
        
        # 统计信息
        self.stats_text = QTextEdit()
        self.stats_text.setMaximumHeight(100)
        self.stats_text.setReadOnly(True)
        right_layout.addWidget(self.stats_text)
        
        layout.addWidget(right_panel, stretch=3)
    
    def load_data(self):
        """加载股票数据"""
        self.status_label.setText("正在加载股票列表...")
        
        try:
            self.stock_list = get_stock_list(self.data_dir)
            self.name_map = load_stock_name_map(self.stocklist_path)
            self.status_label.setText(f"已加载 {len(self.stock_list)} 只股票")
            
            # 初始化选股器
            self.screener = StockScreener(self.data_dir)
        except Exception as e:
            self.status_label.setText(f"数据加载失败: {e}")
            self.stock_list = []
    
    def on_preset_changed(self, index):
        """预设配置切换"""
        preset = self.preset_combo.currentData()
        
        if preset == "volatility_breakout":
            self.min_atr_spin.setValue(2.0)
            self.max_atr_spin.setValue(5.0)
            self.min_adx_spin.setValue(25.0)
            self.min_amount_spin.setValue(1.0)
            self.proximity_spin.setValue(0.95)
        elif preset == "conservative":
            self.min_atr_spin.setValue(2.5)
            self.max_atr_spin.setValue(4.0)
            self.min_adx_spin.setValue(30.0)
            self.min_amount_spin.setValue(2.0)
            self.proximity_spin.setValue(0.97)
        elif preset == "aggressive":
            self.min_atr_spin.setValue(1.5)
            self.max_atr_spin.setValue(6.0)
            self.min_adx_spin.setValue(20.0)
            self.min_amount_spin.setValue(0.5)
            self.proximity_spin.setValue(0.90)
    
    def get_criteria(self) -> ScreeningCriteria:
        """获取当前设置的条件"""
        return ScreeningCriteria(
            min_atr_pct=self.min_atr_spin.value() / 100,
            max_atr_pct=self.max_atr_spin.value() / 100,
            min_adx=self.min_adx_spin.value(),
            min_avg_amount=self.min_amount_spin.value() * 100_000_000,
            proximity_to_high=self.proximity_spin.value(),
            max_stocks=self.max_results_spin.value(),
            exclude_st=self.exclude_st_check.isChecked()
        )
    
    def start_screening(self):
        """开始选股"""
        if not self.stock_list:
            QMessageBox.warning(self, "警告", "股票列表为空")
            return
        
        if self.screening_thread and self.screening_thread.isRunning():
            return
        
        criteria = self.get_criteria()
        
        self.run_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(self.stock_list))
        self.progress_bar.setValue(0)
        self.status_label.setText("正在选股...")
        
        # 创建并启动线程
        self.screening_thread = ScreeningThread(
            self.screener, 
            self.stock_list,
            criteria,
            self.name_map
        )
        self.screening_thread.progress_updated.connect(self.on_progress)
        self.screening_thread.finished_signal.connect(self.on_finished)
        self.screening_thread.error_signal.connect(self.on_error)
        self.screening_thread.start()
    
    def on_progress(self, current, total, code):
        """更新进度"""
        self.progress_bar.setValue(current)
        self.status_label.setText(f"正在分析: {code} ({current}/{total})")
    
    def on_finished(self, results):
        """选股完成"""
        self.current_results = results
        self.run_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"选股完成: {len(results)} 只股票通过筛选")
        
        self.update_table()
        self.update_statistics()
    
    def on_error(self, error_msg):
        """处理错误"""
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"错误: {error_msg}")
        QMessageBox.critical(self, "错误", f"选股过程出错:\n{error_msg}")
    
    def update_table(self):
        """更新结果表格"""
        self.result_table.setRowCount(len(self.current_results))
        
        for i, score in enumerate(self.current_results):
            name = self.name_map.get(score.code, "")
            
            # 排名
            self.result_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            # 代码
            self.result_table.setItem(i, 1, QTableWidgetItem(score.code))
            # 名称
            self.result_table.setItem(i, 2, QTableWidgetItem(name))
            # 总分
            item = QTableWidgetItem(f"{score.total_score:.1f}")
            if score.total_score >= 80:
                item.setBackground(QColor(200, 255, 200))  # 绿色
            elif score.total_score >= 60:
                item.setBackground(QColor(255, 255, 200))  # 黄色
            self.result_table.setItem(i, 3, item)
            # ATR%
            self.result_table.setItem(i, 4, QTableWidgetItem(f"{score.atr_pct*100:.2f}"))
            # ADX
            self.result_table.setItem(i, 5, QTableWidgetItem(f"{score.adx:.1f}"))
            # 成交额
            self.result_table.setItem(i, 6, QTableWidgetItem(f"{score.avg_amount/1e8:.2f}"))
            # 距高点
            self.result_table.setItem(i, 7, QTableWidgetItem(f"{score.distance_to_high*100:.1f}"))
            # 20日涨幅
            item = QTableWidgetItem(f"{score.return_20d*100:.1f}")
            if score.return_20d > 0:
                item.setForeground(QColor(255, 0, 0))  # 红色表示涨
            else:
                item.setForeground(QColor(0, 128, 0))  # 绿色表示跌
            self.result_table.setItem(i, 8, item)
            # 操作按钮
            btn = QPushButton("查看")
            btn.clicked.connect(lambda checked, c=score.code: self.view_stock(c))
            self.result_table.setCellWidget(i, 9, btn)
    
    def update_statistics(self):
        """更新统计信息"""
        if not self.current_results:
            self.stats_text.setText("无数据")
            return
        
        stats = self.screener.get_statistics(self.current_results)
        
        text = f"""
选股统计:
- 通过筛选: {stats['count']} 只
- 平均得分: {stats['avg_score']:.1f}
- 得分区间: {stats['score_range'][0]:.1f} - {stats['score_range'][1]:.1f}
- 平均ATR: {stats['avg_atr_pct']:.2f}%
- 平均ADX: {stats['avg_adx']:.1f}
- TOP5: {', '.join(stats['top_5'])}
        """
        self.stats_text.setText(text)
    
    def view_stock(self, code):
        """查看股票详情"""
        name = self.name_map.get(code, "")
        self.stock_selected.emit(code, name)
    
    def on_cell_double_clicked(self, row, column):
        """双击行查看股票"""
        if row < len(self.current_results):
            code = self.current_results[row].code
            self.view_stock(code)
    
    def export_results(self):
        """导出结果"""
        if not self.current_results:
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出选股结果", 
            f"screening_result_{datetime.now().strftime('%Y%m%d')}.csv",
            "CSV文件 (*.csv);;JSON文件 (*.json);;Excel文件 (*.xlsx)"
        )
        
        if file_path:
            format_map = {
                '.csv': 'csv',
                '.json': 'json',
                '.xlsx': 'excel'
            }
            ext = Path(file_path).suffix
            fmt = format_map.get(ext, 'csv')
            
            try:
                self.screener.export_results(self.current_results, file_path, fmt)
                QMessageBox.information(self, "成功", f"结果已导出到:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败:\n{e}")


# ============== 命令行测试 ==============

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    widget = TechnicalScreenerWidget(data_dir="../data")
    widget.setWindowTitle("技术指标选股器")
    widget.resize(1200, 800)
    widget.show()
    
    sys.exit(app.exec())
