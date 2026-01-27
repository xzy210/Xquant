"""
Factor Library Widget - Factor management and visualization interface
"""
import os
import pandas as pd
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QSplitter, QGroupBox, QDateEdit, QMessageBox, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QTextEdit, QCheckBox, QScrollArea,
    QFrame, QGridLayout, QLineEdit, QProgressBar, QFileDialog, QApplication
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QColor, QFont

try:
    from factors import factor_registry
    from factors.financial_data import FinancialDataLoader
    from data_loader import get_stock_list, load_stock_data, load_stock_name_map
except ImportError:
    from ..factors import factor_registry
    from ..factors.financial_data import FinancialDataLoader
    from ..data_loader import get_stock_list, load_stock_data, load_stock_name_map


class BatchFactorComputeThread(QThread):
    """Background thread for batch computing factors for multiple stocks"""
    finished_signal = pyqtSignal(str, int, int)  # output_dir, success_count, fail_count
    error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int, str)  # current, total, current_stock

    def __init__(self, stock_codes, data_dir, start_date, end_date, factor_names, output_dir):
        super().__init__()
        self.stock_codes = stock_codes
        self.data_dir = data_dir
        self.start_date = start_date
        self.end_date = end_date
        self.factor_names = factor_names
        self.output_dir = output_dir

    def run(self):
        try:
            total = len(self.stock_codes)
            success_count = 0
            fail_count = 0
            
            # Ensure output directory exists
            if not os.path.exists(self.output_dir):
                os.makedirs(self.output_dir)
            
            for i, code in enumerate(self.stock_codes):
                self.progress_signal.emit(i + 1, total, code)
                
                try:
                    # Load stock data
                    df = load_stock_data(
                        code,
                        self.data_dir,
                        start_date=self.start_date,
                        end_date=self.end_date
                    )

                    if df is None or df.empty:
                        fail_count += 1
                        continue

                    # Add stock code column
                    df['code'] = code
                    
                    # Compute factors
                    for name in self.factor_names:
                        try:
                            df[name] = factor_registry.compute(name, df)
                        except Exception as e:
                            df[name] = np.nan

                    # Keep only necessary columns
                    cols = ['code', 'date'] + self.factor_names
                    cols = [c for c in cols if c in df.columns]
                    result_df = df[cols]
                    
                    # Save to individual file per stock
                    output_file = os.path.join(self.output_dir, f"{code}.csv")
                    result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
                    success_count += 1
                    
                except Exception as e:
                    print(f"Error computing factors for {code}: {e}")
                    fail_count += 1
                    continue

            if success_count == 0:
                self.error_signal.emit("未能计算任何股票的因子数据")
                return
            
            self.finished_signal.emit(self.output_dir, success_count, fail_count)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_signal.emit(f"批量计算错误: {str(e)}")


class FactorLibraryWidget(QWidget):
    """Factor Library main interface"""

    def __init__(self, data_dir="../data", stocklist_path=None, tushare_token=None):
        super().__init__()
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        self.tushare_token = tushare_token
        self.batch_compute_thread = None
        self.stock_list = []
        self.name_map = {}
        self.current_df = None
        self.factor_checkboxes = {}
        self.plot_curves = {}

        # Color palette for factor plots
        self.colors = [
            '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231',
            '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#fabed4',
            '#469990', '#dcbeff', '#9A6324', '#fffac8', '#800000',
            '#aaffc3', '#808000', '#ffd8b1', '#000075', '#a9a9a9'
        ]

        self.setupUI()
        self.load_data()

    def setupUI(self):
        layout = QVBoxLayout(self)

        # Set table style for better visibility in dark theme
        table_style = """
            QTableWidget {
                gridline-color: #444444;
                background-color: #2d2d2d;
                alternate-background-color: #3a3a3a;
                color: #e0e0e0;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QTableWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
            QHeaderView::section {
                background-color: #404040;
                color: #e0e0e0;
                padding: 6px;
                border: 1px solid #555555;
                font-weight: bold;
            }
        """
        self.setStyleSheet(table_style)

        # Main splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # === Left Panel: Factor List ===
        left_panel = self.create_left_panel()
        main_splitter.addWidget(left_panel)

        # === Right Panel: Visualization and Info ===
        right_panel = self.create_right_panel()
        main_splitter.addWidget(right_panel)

        main_splitter.setSizes([300, 900])
        layout.addWidget(main_splitter)

    def create_left_panel(self):
        """Create left panel with factor tree and controls"""
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 10, 0)

        # --- Factor Tree ---
        factor_group = QGroupBox("因子列表")
        factor_layout = QVBoxLayout(factor_group)

        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入因子名称...")
        self.search_input.textChanged.connect(self.filter_factors)
        search_layout.addWidget(self.search_input)
        factor_layout.addLayout(search_layout)

        # Factor tree
        self.factor_tree = QTreeWidget()
        self.factor_tree.setHeaderLabels(["因子/类别", "选中"])
        self.factor_tree.setColumnWidth(0, 180)
        self.factor_tree.setColumnWidth(1, 50)
        self.factor_tree.itemClicked.connect(self.on_factor_clicked)
        self.populate_factor_tree()
        factor_layout.addWidget(self.factor_tree)

        # Quick selection buttons
        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(self.select_all_factors)
        self.clear_all_btn = QPushButton("清除")
        self.clear_all_btn.clicked.connect(self.clear_all_factors)
        btn_layout.addWidget(self.select_all_btn)
        btn_layout.addWidget(self.clear_all_btn)
        factor_layout.addLayout(btn_layout)

        left_layout.addWidget(factor_group)

        # --- Stock Pool Batch Compute ---
        pool_group = QGroupBox("批量计算 (股票池)")
        pool_layout = QVBoxLayout(pool_group)
        
        pool_layout.addWidget(QLabel("选择股票池:"))
        self.pool_combo = QComboBox()
        pool_layout.addWidget(self.pool_combo)
        
        # Show stock count label (create before loading pools)
        self.pool_count_label = QLabel("股票数量: -")
        self.pool_count_label.setStyleSheet("color: #888; font-size: 11px;")
        pool_layout.addWidget(self.pool_count_label)
        
        # Load pools and connect signal after label is created
        self._load_stock_pools()
        self.pool_combo.currentIndexChanged.connect(self._on_pool_changed)

        # Date range in a horizontal layout
        date_layout = QHBoxLayout()
        
        start_vbox = QVBoxLayout()
        start_vbox.addWidget(QLabel("起始日期:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.currentDate().addMonths(-6))
        start_vbox.addWidget(self.start_date_edit)
        date_layout.addLayout(start_vbox)

        end_vbox = QVBoxLayout()
        end_vbox.addWidget(QLabel("结束日期:"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate())
        end_vbox.addWidget(self.end_date_edit)
        date_layout.addLayout(end_vbox)
        
        pool_layout.addLayout(date_layout)

        # Batch compute button (for stock pool)
        self.batch_compute_btn = QPushButton("批量计算并保存")
        self.batch_compute_btn.setStyleSheet(
            "background-color: #107c10; color: white; font-weight: bold; padding: 8px;"
        )
        self.batch_compute_btn.clicked.connect(self.batch_compute_factors)
        pool_layout.addWidget(self.batch_compute_btn)

        # Progress bar for batch computation
        self.batch_progress_bar = QProgressBar()
        self.batch_progress_bar.setVisible(False)
        pool_layout.addWidget(self.batch_progress_bar)
        
        self.batch_progress_label = QLabel("")
        self.batch_progress_label.setStyleSheet("color: #888; font-size: 11px;")
        self.batch_progress_label.setVisible(False)
        pool_layout.addWidget(self.batch_progress_label)
        
        left_layout.addWidget(pool_group)

        # --- Single Stock Factor Plot ---
        plot_group = QGroupBox("因子绘制 (单股)")
        plot_layout = QVBoxLayout(plot_group)

        plot_layout.addWidget(QLabel("股票代码:"))
        self.stock_combo = QComboBox()
        self.stock_combo.setEditable(True)
        self.stock_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.stock_combo.completer().setCompletionMode(
            self.stock_combo.completer().CompletionMode.PopupCompletion
        )
        self.stock_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        plot_layout.addWidget(self.stock_combo)

        # Plot button inside the group
        self.plot_btn = QPushButton("绘制因子")
        self.plot_btn.setStyleSheet(
            "background-color: #0078d4; color: white; font-weight: bold; padding: 8px;"
        )
        self.plot_btn.clicked.connect(self.plot_factors)
        plot_layout.addWidget(self.plot_btn)

        left_layout.addWidget(plot_group)

        # --- Other Action Buttons ---
        self.export_btn = QPushButton("导出数据")
        self.export_btn.clicked.connect(self.export_data)
        left_layout.addWidget(self.export_btn)
        
        # Anomaly check button
        self.anomaly_check_btn = QPushButton("检查因子数据异常")
        self.anomaly_check_btn.setStyleSheet(
            "background-color: #d83b01; color: white; font-weight: bold; padding: 10px;"
        )
        self.anomaly_check_btn.clicked.connect(self.check_factor_anomalies)
        left_layout.addWidget(self.anomaly_check_btn)

        left_layout.addStretch()

        return left_widget

    def create_right_panel(self):
        """Create right panel with visualization and info tabs"""
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.result_tabs = QTabWidget()

        # Tab 1: Factor Chart
        chart_tab = self.create_chart_tab()
        self.result_tabs.addTab(chart_tab, "因子图表")

        # Tab 2: Factor Info
        info_tab = self.create_info_tab()
        self.result_tabs.addTab(info_tab, "因子详情")

        # Tab 3: Data Table
        data_tab = self.create_data_tab()
        self.result_tabs.addTab(data_tab, "数据预览")

        # Tab 4: Factor Statistics
        stats_tab = self.create_stats_tab()
        self.result_tabs.addTab(stats_tab, "统计分析")

        right_layout.addWidget(self.result_tabs)

        return right_widget

    def create_chart_tab(self):
        """Create chart visualization tab"""
        chart_widget = QWidget()
        chart_layout = QVBoxLayout(chart_widget)

        # Top splitter: Price chart + Factor charts
        chart_splitter = QSplitter(Qt.Orientation.Vertical)

        # Price chart
        price_group = QGroupBox("股价走势")
        price_layout = QVBoxLayout(price_group)
        self.price_chart = pg.PlotWidget()
        self.price_chart.setBackground('w')
        self.price_chart.showGrid(x=True, y=True)
        self.price_chart.setLabel('left', '价格')
        self.price_chart.addLegend()
        price_layout.addWidget(self.price_chart)
        chart_splitter.addWidget(price_group)

        # Factor chart
        factor_group = QGroupBox("因子走势")
        factor_layout = QVBoxLayout(factor_group)
        self.factor_chart = pg.PlotWidget()
        self.factor_chart.setBackground('w')
        self.factor_chart.showGrid(x=True, y=True)
        self.factor_chart.setLabel('left', '因子值')
        self.factor_chart.addLegend()

        # Link X axis with price chart
        self.factor_chart.setXLink(self.price_chart)
        factor_layout.addWidget(self.factor_chart)
        chart_splitter.addWidget(factor_group)

        chart_splitter.setSizes([300, 400])
        chart_layout.addWidget(chart_splitter)

        return chart_widget

    def create_info_tab(self):
        """Create factor information tab"""
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)

        # Factor info display
        self.factor_info_label = QLabel("选择左侧因子查看详情")
        self.factor_info_label.setStyleSheet("""
            QLabel {
                font-family: Consolas, 'Microsoft YaHei';
                font-size: 14px;
                padding: 20px;
                background-color: #f5f5f5;
                border-radius: 5px;
            }
        """)
        self.factor_info_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.factor_info_label.setWordWrap(True)

        info_layout.addWidget(self.factor_info_label)

        # All factors summary table
        summary_group = QGroupBox("所有因子一览")
        summary_layout = QVBoxLayout(summary_group)

        self.factor_summary_table = QTableWidget()
        self.factor_summary_table.setColumnCount(4)
        self.factor_summary_table.setHorizontalHeaderLabels(["因子名称", "类别", "默认窗口", "描述"])
        self.factor_summary_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.factor_summary_table.setAlternatingRowColors(True)
        self.populate_factor_summary()
        summary_layout.addWidget(self.factor_summary_table)

        info_layout.addWidget(summary_group)

        return info_widget

    def create_data_tab(self):
        """Create data preview tab"""
        data_widget = QWidget()
        data_layout = QVBoxLayout(data_widget)

        self.data_table = QTableWidget()
        self.data_table.setAlternatingRowColors(True)
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        data_layout.addWidget(self.data_table)

        return data_widget

    def create_stats_tab(self):
        """Create statistics analysis tab"""
        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)

        self.stats_table = QTableWidget()
        self.stats_table.setColumnCount(8)
        self.stats_table.setHorizontalHeaderLabels([
            "因子", "均值", "标准差", "最小值", "25%", "中位数", "75%", "最大值"
        ])
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        stats_layout.addWidget(self.stats_table)

        # Correlation matrix
        corr_group = QGroupBox("因子相关性矩阵")
        corr_layout = QVBoxLayout(corr_group)
        self.corr_table = QTableWidget()
        self.corr_table.setAlternatingRowColors(True)
        corr_layout.addWidget(self.corr_table)
        stats_layout.addWidget(corr_group)

        return stats_widget

    def populate_factor_tree(self):
        """Populate factor tree with categories"""
        self.factor_tree.clear()
        self.factor_checkboxes.clear()

        categories = factor_registry.list_categories()

        for category in sorted(categories):
            # Category item
            cat_item = QTreeWidgetItem(self.factor_tree)
            cat_item.setText(0, category.upper())
            cat_item.setFont(0, QFont("Arial", 10, QFont.Weight.Bold))
            cat_item.setExpanded(True)

            # Add category checkbox
            cat_checkbox = QCheckBox()
            cat_checkbox.stateChanged.connect(
                lambda state, c=category: self.on_category_checked(c, state)
            )
            self.factor_tree.setItemWidget(cat_item, 1, cat_checkbox)

            # Factor items
            factors = factor_registry.list_factors(category=category)
            for factor_name in sorted(factors):
                factor_item = QTreeWidgetItem(cat_item)
                factor_item.setText(0, factor_name)
                factor_item.setData(0, Qt.ItemDataRole.UserRole, factor_name)

                checkbox = QCheckBox()
                self.factor_tree.setItemWidget(factor_item, 1, checkbox)
                self.factor_checkboxes[factor_name] = checkbox

    def populate_factor_summary(self):
        """Populate factor summary table"""
        all_info = factor_registry.get_all_factor_info()
        self.factor_summary_table.setRowCount(len(all_info))

        for i, info in enumerate(all_info):
            self.factor_summary_table.setItem(i, 0, QTableWidgetItem(info['name']))
            self.factor_summary_table.setItem(i, 1, QTableWidgetItem(info['category']))
            self.factor_summary_table.setItem(i, 2, QTableWidgetItem(str(info['default_window'])))
            self.factor_summary_table.setItem(i, 3, QTableWidgetItem(info['description']))

    def load_data(self):
        """Load stock list"""
        self.stock_list = get_stock_list(self.data_dir)
        self.name_map = load_stock_name_map(self.stocklist_path) if self.stocklist_path else load_stock_name_map()

        self.stock_combo.clear()
        for code in self.stock_list:
            name = self.name_map.get(code, "")
            self.stock_combo.addItem(f"{code} {name}", code)

    def filter_factors(self, text):
        """Filter factor tree based on search text"""
        text = text.lower()

        for i in range(self.factor_tree.topLevelItemCount()):
            cat_item = self.factor_tree.topLevelItem(i)
            cat_visible = False

            for j in range(cat_item.childCount()):
                factor_item = cat_item.child(j)
                factor_name = factor_item.text(0).lower()
                visible = text in factor_name or not text
                factor_item.setHidden(not visible)
                if visible:
                    cat_visible = True

            cat_item.setHidden(not cat_visible)

    def on_category_checked(self, category, state):
        """Handle category checkbox state change"""
        checked = state == Qt.CheckState.Checked.value
        factors = factor_registry.list_factors(category=category)
        for factor_name in factors:
            if factor_name in self.factor_checkboxes:
                self.factor_checkboxes[factor_name].setChecked(checked)

    def on_factor_clicked(self, item, column):
        """Handle factor tree item click"""
        factor_name = item.data(0, Qt.ItemDataRole.UserRole)
        if factor_name:
            info = factor_registry.get_factor_info(factor_name)
            if info:
                info_text = f"""
<h2>{info['name']}</h2>
<hr>
<p><b>类别:</b> {info['category']}</p>
<p><b>默认窗口:</b> {info['default_window']}</p>
<p><b>描述:</b> {info['description']}</p>
<hr>
<h3>使用方法</h3>
<pre>
from pyqt_app.factors import factor_registry

# 计算单个因子
result = factor_registry.compute('{info['name']}', df)

# 自定义窗口
result = factor_registry.compute('{info['name']}', df, window=30)
</pre>
"""
                self.factor_info_label.setText(info_text)

    def select_all_factors(self):
        """Select all factors"""
        for checkbox in self.factor_checkboxes.values():
            checkbox.setChecked(True)

    def clear_all_factors(self):
        """Clear all factor selections"""
        for checkbox in self.factor_checkboxes.values():
            checkbox.setChecked(False)

    def get_selected_factors(self):
        """Get list of selected factor names"""
        selected = []
        for name, checkbox in self.factor_checkboxes.items():
            if checkbox.isChecked():
                selected.append(name)
        return selected

    def plot_factors(self):
        """Load and plot factors from saved factor data files"""
        selected = self.get_selected_factors()
        if not selected:
            QMessageBox.warning(self, "提示", "请至少选择一个因子")
            return

        # Get stock code
        import re
        text = self.stock_combo.currentText()
        match = re.search(r'\d{6}', text)
        if match:
            code = match.group(0)
        else:
            code = self.stock_combo.currentData()

        if not code:
            QMessageBox.warning(self, "提示", "请输入有效的6位股票代码")
            return

        # Look for factor data file in factors directory
        factors_dir = os.path.join(self.data_dir, "factors")
        factor_file = os.path.join(factors_dir, f"{code}.csv")
        
        if not os.path.exists(factor_file):
            # Let user select the factors directory
            selected_dir = QFileDialog.getExistingDirectory(
                self, "选择因子数据文件夹", factors_dir
            )
            if not selected_dir:
                return
            factor_file = os.path.join(selected_dir, f"{code}.csv")
            
            if not os.path.exists(factor_file):
                QMessageBox.warning(self, "提示", f"未找到股票 {code} 的因子数据文件\n请先使用批量计算功能计算因子数据")
                return

        try:
            # Load factor data
            df = pd.read_csv(factor_file)
            
            if df.empty:
                QMessageBox.warning(self, "提示", f"股票 {code} 的因子数据文件为空")
                return
            
            # Format date column
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            
            # Check if selected factors exist in the data
            missing_factors = [f for f in selected if f not in df.columns]
            if missing_factors:
                QMessageBox.warning(self, "提示", 
                    f"以下因子在数据中不存在:\n{', '.join(missing_factors)}\n\n请确保因子已被计算")
                # Filter to only existing factors
                selected = [f for f in selected if f in df.columns]
                if not selected:
                    return
            
            # Get date range from factor data for loading stock price
            if 'date' in df.columns and len(df) > 0:
                start_date = df['date'].min()
                end_date = df['date'].max()
            else:
                start_date = None
                end_date = None
            
            # Load stock price data for the price chart
            stock_df = load_stock_data(code, self.data_dir, start_date=start_date, end_date=end_date)
            if stock_df is not None and not stock_df.empty:
                # Merge stock data with factor data
                stock_df['date'] = pd.to_datetime(stock_df['date']).dt.strftime('%Y-%m-%d')
                # Ensure both dataframes have the same date column type
                df = pd.merge(df, stock_df[['date', 'open', 'high', 'low', 'close', 'volume']], 
                             on='date', how='left')
            
            self.current_df = df
            
            # Update charts
            self.update_charts(df, selected)
            
            # Update data table
            self.update_data_table(df, selected)
            
            # Update statistics
            self.update_statistics(df, selected)
            
            # Switch to chart tab
            self.result_tabs.setCurrentIndex(0)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "加载失败", f"加载因子数据失败:\n{str(e)}")

    def update_charts(self, df, factor_names):
        """Update price and factor charts"""
        self.price_chart.clear()
        self.factor_chart.clear()
        self.plot_curves.clear()

        if df.empty:
            return

        x = np.arange(len(df))

        # Plot price
        if 'close' in df.columns:
            close_vals = df['close'].values
            # Handle NaN in price data
            valid_mask = ~np.isnan(close_vals.astype(float))
            if valid_mask.any():
                self.price_chart.plot(
                    x, close_vals,
                    pen=pg.mkPen('#0078d4', width=2),
                    name="收盘价",
                    connect='finite'  # Skip NaN values when connecting points
                )

        # Plot factors with different colors
        legend = self.factor_chart.addLegend()
        plotted_count = 0
        for i, name in enumerate(factor_names):
            if name in df.columns:
                color = self.colors[i % len(self.colors)]
                vals = df[name].values
                
                # Check if factor has any valid (non-NaN) values
                try:
                    float_vals = vals.astype(float)
                    valid_mask = ~np.isnan(float_vals)
                    if not valid_mask.any():
                        print(f"Warning: Factor '{name}' has no valid values (all NaN)")
                        continue
                except (ValueError, TypeError):
                    print(f"Warning: Factor '{name}' cannot be converted to float")
                    continue
                
                curve = self.factor_chart.plot(
                    x, float_vals,
                    pen=pg.mkPen(color, width=1.5),
                    name=name,
                    connect='finite'  # Skip NaN values when connecting points
                )
                self.plot_curves[name] = curve
                plotted_count += 1
        
        if plotted_count == 0:
            print("Warning: No factors were plotted (all selected factors have no valid data)")

        # Set X axis ticks
        self.setup_date_axis(df)

    def setup_date_axis(self, df):
        """Setup date axis ticks"""
        if 'date' not in df.columns:
            return

        dates = df['date'].astype(str).tolist()
        n = max(1, len(dates) // 10)
        ticks = [(i, dates[i]) for i in range(0, len(dates), n)]

        for chart in [self.price_chart, self.factor_chart]:
            ax = chart.getAxis('bottom')
            ax.setTicks([ticks])

    def update_data_table(self, df, factor_names):
        """Update data preview table"""
        display_cols = ['date', 'open', 'high', 'low', 'close', 'volume'] + factor_names
        display_cols = [c for c in display_cols if c in df.columns]

        self.data_table.clear()
        self.data_table.setColumnCount(len(display_cols))
        self.data_table.setHorizontalHeaderLabels(display_cols)
        self.data_table.setRowCount(min(100, len(df)))  # Show max 100 rows

        for i in range(min(100, len(df))):
            for j, col in enumerate(display_cols):
                val = df.iloc[i][col]
                if pd.isna(val):
                    text = ""
                elif isinstance(val, float):
                    text = f"{val:.4f}"
                else:
                    text = str(val)
                self.data_table.setItem(i, j, QTableWidgetItem(text))

    def update_statistics(self, df, factor_names):
        """Update statistics tables"""
        # Basic statistics
        self.stats_table.setRowCount(len(factor_names))

        for i, name in enumerate(factor_names):
            if name not in df.columns:
                continue

            series = df[name].dropna()
            if len(series) == 0:
                continue

            stats = series.describe()

            self.stats_table.setItem(i, 0, QTableWidgetItem(name))
            self.stats_table.setItem(i, 1, QTableWidgetItem(f"{stats['mean']:.4f}"))
            self.stats_table.setItem(i, 2, QTableWidgetItem(f"{stats['std']:.4f}"))
            self.stats_table.setItem(i, 3, QTableWidgetItem(f"{stats['min']:.4f}"))
            self.stats_table.setItem(i, 4, QTableWidgetItem(f"{stats['25%']:.4f}"))
            self.stats_table.setItem(i, 5, QTableWidgetItem(f"{stats['50%']:.4f}"))
            self.stats_table.setItem(i, 6, QTableWidgetItem(f"{stats['75%']:.4f}"))
            self.stats_table.setItem(i, 7, QTableWidgetItem(f"{stats['max']:.4f}"))

        # Correlation matrix
        if len(factor_names) > 1:
            factor_df = df[factor_names].dropna()
            if len(factor_df) > 0:
                corr_matrix = factor_df.corr()

                self.corr_table.setRowCount(len(factor_names))
                self.corr_table.setColumnCount(len(factor_names))
                self.corr_table.setHorizontalHeaderLabels(factor_names)
                self.corr_table.setVerticalHeaderLabels(factor_names)

                for i, row_name in enumerate(factor_names):
                    for j, col_name in enumerate(factor_names):
                        val = corr_matrix.loc[row_name, col_name]
                        item = QTableWidgetItem(f"{val:.3f}")

                        # Color code correlation
                        if i != j:
                            if val > 0.7:
                                item.setBackground(QColor("#ff6b6b"))  # High positive
                            elif val > 0.3:
                                item.setBackground(QColor("#ffd93d"))  # Medium positive
                            elif val < -0.7:
                                item.setBackground(QColor("#6bcb77"))  # High negative
                            elif val < -0.3:
                                item.setBackground(QColor("#a8e6cf"))  # Medium negative

                        self.corr_table.setItem(i, j, item)

    def export_data(self):
        """Export computed factor data to CSV"""
        if self.current_df is None or self.current_df.empty:
            QMessageBox.warning(self, "提示", "请先计算因子数据")
            return

        from PyQt6.QtWidgets import QFileDialog

        # Default save to data/factors directory
        factors_dir = os.path.join(self.data_dir, "factors")
        if not os.path.exists(factors_dir):
            os.makedirs(factors_dir)
        default_path = os.path.join(factors_dir, f"factors_{QDate.currentDate().toString('yyyyMMdd')}.csv")

        filename, _ = QFileDialog.getSaveFileName(
            self, "导出数据", default_path, "CSV Files (*.csv);;All Files (*)"
        )

        if filename:
            try:
                self.current_df.to_csv(filename, index=False, encoding='utf-8-sig')
                QMessageBox.information(self, "成功", f"数据已导出到:\n{filename}")
            except Exception as e:
                QMessageBox.critical(self, "导出失败", str(e))

    def _get_stocklist_dir(self):
        """Get the stocklist directory path"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        return os.path.join(project_root, "stocklist")
    
    def _load_stock_pools(self):
        """Load available stock pool files from stocklist folder"""
        stocklist_dir = self._get_stocklist_dir()
        
        if not os.path.exists(stocklist_dir):
            self.pool_combo.addItem("未找到股票池文件夹", None)
            return
        
        # Find all CSV files
        csv_files = [f for f in os.listdir(stocklist_dir) if f.endswith('.csv')]
        
        if not csv_files:
            self.pool_combo.addItem("未找到股票池文件", None)
            return
        
        # Sort and add to combo box
        csv_files.sort()
        for filename in csv_files:
            display_name = filename.replace('_股票列表.csv', '').replace('.csv', '')
            file_path = os.path.join(stocklist_dir, filename)
            self.pool_combo.addItem(display_name, file_path)
        
        # Trigger initial count update
        self._on_pool_changed()
    
    def _on_pool_changed(self):
        """Handle stock pool selection change"""
        stock_codes = self._get_selected_pool_codes()
        count = len(stock_codes) if stock_codes else 0
        self.pool_count_label.setText(f"股票数量: {count}")
    
    def _get_selected_pool_codes(self):
        """Get stock codes from the selected pool file"""
        file_path = self.pool_combo.currentData()
        
        if not file_path or not os.path.exists(file_path):
            return []
        
        try:
            codes = []
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(',')
                    if parts:
                        code = parts[0].strip()
                        if code:
                            # Remove market suffix (e.g., .SH, .SZ)
                            if '.' in code:
                                code = code.split('.')[0]
                            codes.append(code)
            return codes
        except Exception as e:
            print(f"Error reading stock pool file: {e}")
            return []

    def _get_financial_factors(self) -> list:
        """Get list of financial factor names"""
        # Financial factors from financial_factors.py
        return [
            'pe', 'pe_ttm', 'pb', 'ps_ttm', 'dv_ttm', 'total_mv', 'circ_mv',
            'roe', 'roe_dt', 'roa', 'roic', 'gross_margin', 'eps',
            'netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy', 'or_yoy', 'op_yoy', 'basic_eps_yoy',
            'current_ratio', 'quick_ratio', 'debt_to_assets',
            'turnover_rate_daily', 'turnover_rate_f', 'volume_ratio_daily'
        ]
    
    def _has_financial_factors(self, selected_factors: list) -> bool:
        """Check if any financial factors are selected"""
        financial_factors = self._get_financial_factors()
        return any(f in financial_factors for f in selected_factors)
    
    def _get_tushare_token(self) -> str:
        """
        Get Tushare token from multiple sources (in priority order):
        1. Instance variable (self.tushare_token)
        2. TuShareToken.txt file in project root
        3. Environment variable TUSHARE_TOKEN
        4. QSettings configuration
        """
        # 1. Already have token
        if self.tushare_token:
            return self.tushare_token
        
        # 2. Try to read from TuShareToken.txt
        try:
            # Get project root directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            token_file = os.path.join(project_root, "TuShareToken.txt")
            
            if os.path.exists(token_file):
                with open(token_file, 'r', encoding='utf-8') as f:
                    token = f.read().strip()
                    if token:
                        self.tushare_token = token
                        return token
        except Exception as e:
            print(f"Error reading TuShareToken.txt: {e}")
        
        # 3. Try environment variable
        token = os.environ.get("TUSHARE_TOKEN", "")
        if token:
            self.tushare_token = token
            return token
        
        # 4. Try QSettings
        try:
            from PyQt6.QtCore import QSettings
            settings = QSettings("StockTradebyZ", "StockApp")
            token = settings.value("tushare_token", "")
            if token:
                self.tushare_token = token
                return token
        except:
            pass
        
        return ""
    
    def _download_financial_data_sync(self, stock_codes: list) -> tuple:
        """Download financial data synchronously (for use before batch compute)"""
        token = self._get_tushare_token()
        
        if not token:
            return 0, len(stock_codes), "Tushare Token 未配置\n请在项目根目录创建 TuShareToken.txt 文件并填入 Token"
        
        try:
            loader = FinancialDataLoader(
                data_dir=os.path.join(self.data_dir, "financial"),
                tushare_token=token
            )
            
            success_count = 0
            fail_count = 0
            total = len(stock_codes)
            
            for i, code in enumerate(stock_codes):
                self.batch_progress_label.setText(f"下载财务数据: {code} ({i+1}/{total})")
                self.batch_progress_bar.setValue(i + 1)
                QApplication.processEvents()  # Keep UI responsive
                
                try:
                    # Download daily basic data
                    result1 = loader.download_daily_basic(code)
                    # Download financial indicators
                    result2 = loader.download_fina_indicator(code)
                    
                    if (result1 is not None and not result1.empty) or \
                       (result2 is not None and not result2.empty):
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    print(f"Error downloading financial data for {code}: {e}")
                    fail_count += 1
                
                # Rate limiting for Tushare API
                import time
                time.sleep(0.15)
            
            return success_count, fail_count, None
            
        except Exception as e:
            return 0, len(stock_codes), str(e)

    def batch_compute_factors(self):
        """Batch compute factors for all stocks in selected pool and save"""
        if self.batch_compute_thread and self.batch_compute_thread.isRunning():
            return

        selected = self.get_selected_factors()
        if not selected:
            QMessageBox.warning(self, "提示", "请至少选择一个因子")
            return

        stock_codes = self._get_selected_pool_codes()
        if not stock_codes:
            QMessageBox.warning(self, "提示", "请选择有效的股票池")
            return

        # Check if financial factors are selected
        has_financial = self._has_financial_factors(selected)
        
        if has_financial:
            # Try to get tushare token from multiple sources
            token = self._get_tushare_token()
            
            if not token:
                QMessageBox.warning(self, "提示", 
                    "您选择了财务因子，但 Tushare Token 未配置\n\n"
                    "请在项目根目录创建 TuShareToken.txt 文件并填入 Token\n"
                    "或设置环境变量 TUSHARE_TOKEN\n"
                    "Token 可以从 https://tushare.pro 获取")
                return
            
            # Confirm download
            reply = QMessageBox.question(
                self, "下载财务数据",
                f"您选择了财务因子，需要从 Tushare 下载 {len(stock_codes)} 只股票的财务数据\n\n"
                f"这可能需要几分钟时间，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Default save to data/factors directory
        factors_dir = os.path.join(self.data_dir, "factors")
        if not os.path.exists(factors_dir):
            os.makedirs(factors_dir)
        
        output_dir = QFileDialog.getExistingDirectory(
            self, "选择保存目录", factors_dir
        )
        
        if not output_dir:
            return

        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")

        # Setup UI for progress
        self.batch_compute_btn.setEnabled(False)
        self.batch_progress_bar.setVisible(True)
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setMaximum(len(stock_codes))
        self.batch_progress_label.setVisible(True)
        
        # Download financial data first if needed
        if has_financial:
            self.batch_progress_label.setText("正在下载财务数据...")
            QApplication.processEvents()
            
            success, fail, error = self._download_financial_data_sync(stock_codes)
            
            if error:
                self.batch_compute_btn.setEnabled(True)
                self.batch_progress_bar.setVisible(False)
                self.batch_progress_label.setVisible(False)
                QMessageBox.critical(self, "下载失败", f"财务数据下载失败: {error}")
                return
            
            if fail > 0:
                # Some failed, but continue with computation
                self.batch_progress_label.setText(f"财务数据下载完成 (成功:{success}, 失败:{fail})，开始计算...")
            else:
                self.batch_progress_label.setText("财务数据下载完成，开始计算...")
            
            QApplication.processEvents()
        
        self.batch_progress_label.setText("正在准备计算...")
        self.batch_progress_bar.setValue(0)

        self.batch_compute_thread = BatchFactorComputeThread(
            stock_codes, self.data_dir, start_date, end_date, selected, output_dir
        )
        self.batch_compute_thread.finished_signal.connect(self.on_batch_compute_finished)
        self.batch_compute_thread.error_signal.connect(self.on_batch_compute_error)
        self.batch_compute_thread.progress_signal.connect(self.on_batch_progress)
        self.batch_compute_thread.start()

    def on_batch_progress(self, current, total, stock_code):
        """Handle batch computation progress update"""
        self.batch_progress_bar.setValue(current)
        self.batch_progress_label.setText(f"正在计算: {stock_code} ({current}/{total})")

    def on_batch_compute_finished(self, output_dir, success_count, fail_count):
        """Handle batch computation completion"""
        self.batch_compute_btn.setEnabled(True)
        self.batch_progress_bar.setVisible(False)
        self.batch_progress_label.setVisible(False)
        
        msg = f"因子数据已保存到:\n{output_dir}\n\n"
        msg += f"成功: {success_count} 只股票\n"
        if fail_count > 0:
            msg += f"失败: {fail_count} 只股票"
        
        QMessageBox.information(self, "完成", msg)

    def on_batch_compute_error(self, msg):
        """Handle batch computation error"""
        self.batch_compute_btn.setEnabled(True)
        self.batch_progress_bar.setVisible(False)
        self.batch_progress_label.setVisible(False)
        QMessageBox.critical(self, "批量计算失败", msg)

    def check_factor_anomalies(self):
        """Check for anomalies in all factor files in factors folder"""
        # Default factors directory
        factors_dir = os.path.join(self.data_dir, "factors")
        
        # Let user select the factors directory
        selected_dir = QFileDialog.getExistingDirectory(
            self, "选择因子数据文件夹", factors_dir
        )
        
        if not selected_dir:
            return
        
        # Find all CSV files in the directory
        csv_files = [f for f in os.listdir(selected_dir) if f.endswith('.csv')]
        
        if not csv_files:
            QMessageBox.warning(self, "提示", "所选文件夹中没有CSV文件")
            return
        
        # Store all anomaly information
        all_anomalies = []  # List of dict with anomaly summary per factor
        inf_details = []    # List of dict with detailed inf locations
        total_files = len(csv_files)
        processed_files = 0
        failed_files = []
        total_rows = 0
        all_factor_cols = set()
        
        # Process each file
        for csv_file in csv_files:
            file_path = os.path.join(selected_dir, csv_file)
            stock_code = csv_file.replace('.csv', '')
            
            try:
                df = pd.read_csv(file_path)
                processed_files += 1
                total_rows += len(df)
            except Exception as e:
                failed_files.append((csv_file, str(e)))
                continue
            
            # Get factor columns (exclude code, date, OHLCV)
            exclude_cols = ['code', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount']
            factor_cols = [col for col in df.columns if col not in exclude_cols]
            all_factor_cols.update(factor_cols)
            
            if not factor_cols:
                continue
            
            # Check each factor column for anomalies
            for col in factor_cols:
                col_data = df[col]
                
                # Count statistics
                total_count = len(col_data)
                nan_count = col_data.isna().sum()
                
                # Check for inf values and get their locations
                inf_mask = np.isinf(col_data.replace([np.nan], 0))
                inf_count = inf_mask.sum()
                
                # If there are inf values, record detailed locations
                if inf_count > 0:
                    inf_rows = df[inf_mask]
                    for idx, row in inf_rows.iterrows():
                        date_val = row.get('date', 'N/A')
                        inf_val = row[col]
                        inf_details.append({
                            'stock': stock_code,
                            'factor': col,
                            'date': date_val,
                            'value': inf_val
                        })
                
                # Check for extreme values (beyond 5 std)
                valid_data = col_data.replace([np.inf, -np.inf], np.nan).dropna()
                if len(valid_data) > 0:
                    mean = valid_data.mean()
                    std = valid_data.std()
                    if std > 0:
                        extreme_count = ((valid_data < mean - 5*std) | (valid_data > mean + 5*std)).sum()
                    else:
                        extreme_count = 0
                else:
                    extreme_count = 0
                
                # Aggregate anomalies by factor
                if nan_count > 0 or inf_count > 0 or extreme_count > 0:
                    # Check if this factor already exists in the report
                    existing = next((x for x in all_anomalies if x['factor'] == col), None)
                    if existing:
                        existing['nan_count'] += nan_count
                        existing['inf_count'] += inf_count
                        existing['extreme_count'] += extreme_count
                        existing['total'] += total_count
                        if inf_count > 0:
                            existing['stocks_with_inf'].add(stock_code)
                    else:
                        all_anomalies.append({
                            'factor': col,
                            'total': total_count,
                            'nan_count': nan_count,
                            'inf_count': inf_count,
                            'extreme_count': extreme_count,
                            'stocks_with_inf': {stock_code} if inf_count > 0 else set()
                        })
        
        # Calculate nan percentage
        for item in all_anomalies:
            item['nan_pct'] = item['nan_count'] / item['total'] * 100 if item['total'] > 0 else 0
            item['stocks_with_inf'] = list(item['stocks_with_inf'])  # Convert set to list for display
        
        # Show report
        self._show_anomaly_report_batch(
            selected_dir, all_anomalies, inf_details, 
            total_rows, list(all_factor_cols), 
            total_files, processed_files, failed_files
        )

    def _show_anomaly_report_batch(self, folder_path, anomaly_report, inf_details, 
                                     total_rows, factor_cols, total_files, 
                                     processed_files, failed_files):
        """Show batch anomaly report in a dialog"""
        from PyQt6.QtWidgets import QDialog, QTextEdit, QVBoxLayout, QPushButton
        
        dialog = QDialog(self)
        dialog.setWindowTitle("因子数据异常检查报告")
        dialog.setMinimumSize(900, 700)
        
        layout = QVBoxLayout(dialog)
        
        report_text = QTextEdit()
        report_text.setReadOnly(True)
        
        # Build report
        report = f"""<h2>因子数据异常检查报告</h2>
<hr>
<p><b>文件夹:</b> {folder_path}</p>
<p><b>文件总数:</b> {total_files} (成功读取: {processed_files}, 失败: {len(failed_files)})</p>
<p><b>总数据行数:</b> {total_rows}</p>
<p><b>检查因子数:</b> {len(factor_cols)}</p>
<hr>
"""
        
        # Show failed files if any
        if failed_files:
            report += "<h3 style='color: #d83b01;'>读取失败的文件:</h3>"
            report += "<ul>"
            for fname, error in failed_files[:10]:  # Show max 10
                report += f"<li>{fname}: {error}</li>"
            if len(failed_files) > 10:
                report += f"<li>... 及其他 {len(failed_files) - 10} 个文件</li>"
            report += "</ul><hr>"
        
        # Show summary of anomalies
        if not anomaly_report:
            report += "<p style='color: green;'><b>✓ 未发现异常值，所有因子数据正常！</b></p>"
        else:
            report += f"<h3 style='color: #d83b01;'>发现 {len(anomaly_report)} 个因子存在异常值:</h3>"
            report += "<table border='1' cellpadding='5' cellspacing='0' style='border-collapse: collapse; width: 100%;'>"
            report += "<tr style='background-color: #404040;'>"
            report += "<th>因子名称</th><th>缺失值(NaN)</th><th>缺失比例</th><th>无穷值(Inf)</th><th>极端值(>5σ)</th>"
            report += "</tr>"
            
            for item in anomaly_report:
                nan_color = "#ff6b6b" if item['nan_pct'] > 10 else "#ffd93d" if item['nan_pct'] > 1 else ""
                inf_color = "#ff6b6b" if item['inf_count'] > 0 else ""
                extreme_color = "#ffd93d" if item['extreme_count'] > 0 else ""
                
                report += f"<tr>"
                report += f"<td>{item['factor']}</td>"
                report += f"<td style='background-color: {nan_color};'>{item['nan_count']}</td>"
                report += f"<td style='background-color: {nan_color};'>{item['nan_pct']:.2f}%</td>"
                report += f"<td style='background-color: {inf_color};'>{item['inf_count']}</td>"
                report += f"<td style='background-color: {extreme_color};'>{item['extreme_count']}</td>"
                report += "</tr>"
            
            report += "</table>"
        
        # Show detailed Inf locations
        if inf_details:
            report += f"<hr><h3 style='color: #ff6b6b;'>无穷值(Inf)详细位置 (共 {len(inf_details)} 处):</h3>"
            report += "<table border='1' cellpadding='5' cellspacing='0' style='border-collapse: collapse; width: 100%;'>"
            report += "<tr style='background-color: #404040;'>"
            report += "<th>股票代码</th><th>因子名称</th><th>日期</th><th>异常值</th>"
            report += "</tr>"
            
            # Show max 100 records
            for item in inf_details[:100]:
                val_str = "+Inf" if item['value'] == np.inf else "-Inf" if item['value'] == -np.inf else str(item['value'])
                report += f"<tr>"
                report += f"<td>{item['stock']}</td>"
                report += f"<td>{item['factor']}</td>"
                report += f"<td>{item['date']}</td>"
                report += f"<td style='color: #ff6b6b;'>{val_str}</td>"
                report += "</tr>"
            
            if len(inf_details) > 100:
                report += f"<tr><td colspan='4'>... 及其他 {len(inf_details) - 100} 处异常值</td></tr>"
            
            report += "</table>"
            
            # Show which stocks have inf values for each factor
            report += "<h4>各因子的Inf异常股票列表:</h4>"
            for item in anomaly_report:
                if item['inf_count'] > 0 and item['stocks_with_inf']:
                    stocks_str = ", ".join(item['stocks_with_inf'][:20])
                    if len(item['stocks_with_inf']) > 20:
                        stocks_str += f" ... 及其他 {len(item['stocks_with_inf']) - 20} 只"
                    report += f"<p><b>{item['factor']}:</b> {stocks_str}</p>"
        
        report += """
<hr>
<h3>说明</h3>
<ul>
<li><b>缺失值(NaN)</b>: 因子计算结果为空，通常是因为数据不足或计算窗口期未满</li>
<li><b>无穷值(Inf)</b>: 因子计算出现除零或溢出，需要检查计算逻辑</li>
<li><b>极端值(>5σ)</b>: 超过5个标准差的异常值，可能需要截断处理</li>
</ul>
<p><i>注: 本报告仅检测异常值，不进行自动修复</i></p>
"""
        
        report_text.setHtml(report)
        layout.addWidget(report_text)
        
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.exec()
