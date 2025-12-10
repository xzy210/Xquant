import os
import sys
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QGroupBox, QSplitter,
    QMessageBox, QProgressBar, QLineEdit, QScrollArea, QDialog, QSlider
)
from PyQt6.QtCore import Qt, QProcess, pyqtSignal, QThread
from PyQt6.QtGui import QPixmap, QWheelEvent


class ZoomableImageLabel(QLabel):
    """支持鼠标滚轮缩放的图片标签"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.original_pixmap = None
        self.scale_factor = 1.0
        self.min_scale = 0.1
        self.max_scale = 5.0
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
    def set_image(self, pixmap: QPixmap):
        """设置图片"""
        self.original_pixmap = pixmap
        self.scale_factor = 1.0
        self.update_display()
        
    def update_display(self):
        """更新显示"""
        if self.original_pixmap:
            scaled_size = self.original_pixmap.size() * self.scale_factor
            scaled_pixmap = self.original_pixmap.scaled(
                scaled_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.setPixmap(scaled_pixmap)
            self.setMinimumSize(scaled_pixmap.size())
            
    def wheelEvent(self, event: QWheelEvent):
        """鼠标滚轮缩放"""
        if self.original_pixmap:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            event.ignore()
            
    def zoom_in(self):
        """放大"""
        if self.scale_factor < self.max_scale:
            self.scale_factor *= 1.2
            self.scale_factor = min(self.scale_factor, self.max_scale)
            self.update_display()
            
    def zoom_out(self):
        """缩小"""
        if self.scale_factor > self.min_scale:
            self.scale_factor /= 1.2
            self.scale_factor = max(self.scale_factor, self.min_scale)
            self.update_display()
            
    def zoom_reset(self):
        """重置缩放"""
        self.scale_factor = 1.0
        self.update_display()
        
    def zoom_fit(self, container_size):
        """适应容器大小"""
        if self.original_pixmap:
            w_ratio = container_size.width() / self.original_pixmap.width()
            h_ratio = container_size.height() / self.original_pixmap.height()
            self.scale_factor = min(w_ratio, h_ratio, 1.0)
            self.update_display()


class ImageViewerDialog(QDialog):
    """独立的图片查看器对话框"""
    
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("预测结果")
        self.setMinimumSize(800, 600)
        self.resize(1200, 800)
        
        layout = QVBoxLayout(self)
        
        # 工具栏
        toolbar = QHBoxLayout()
        
        btn_zoom_in = QPushButton("放大")
        btn_zoom_in.setFixedWidth(60)
        btn_zoom_in.clicked.connect(self.zoom_in)
        toolbar.addWidget(btn_zoom_in)
        
        btn_zoom_out = QPushButton("缩小")
        btn_zoom_out.setFixedWidth(60)
        btn_zoom_out.clicked.connect(self.zoom_out)
        toolbar.addWidget(btn_zoom_out)
        
        btn_reset = QPushButton("原始大小")
        btn_reset.setFixedWidth(70)
        btn_reset.clicked.connect(self.zoom_reset)
        toolbar.addWidget(btn_reset)
        
        btn_fit = QPushButton("适应窗口")
        btn_fit.setFixedWidth(70)
        btn_fit.clicked.connect(self.zoom_fit)
        toolbar.addWidget(btn_fit)
        
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(60)
        toolbar.addWidget(self.zoom_label)
        
        toolbar.addStretch()
        
        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(60)
        btn_close.clicked.connect(self.close)
        toolbar.addWidget(btn_close)
        
        layout.addLayout(toolbar)
        
        # 滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.image_label = ZoomableImageLabel()
        self.scroll_area.setWidget(self.image_label)
        
        layout.addWidget(self.scroll_area)
        
        # 加载图片
        if os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            self.image_label.set_image(pixmap)
            self.zoom_fit()
        
    def zoom_in(self):
        self.image_label.zoom_in()
        self.update_zoom_label()
        
    def zoom_out(self):
        self.image_label.zoom_out()
        self.update_zoom_label()
        
    def zoom_reset(self):
        self.image_label.zoom_reset()
        self.update_zoom_label()
        
    def zoom_fit(self):
        self.image_label.zoom_fit(self.scroll_area.size())
        self.update_zoom_label()
        
    def update_zoom_label(self):
        self.zoom_label.setText(f"{int(self.image_label.scale_factor * 100)}%")


class AITradingWidget(QWidget):
    """AI 智能交易训练与预测组件"""

    def __init__(self, data_dir: str = "../data", parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        
        # Paths
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # 默认使用 V2 优化版本
        self.use_v2 = True
        self._update_script_paths()
        
        self.process = None
        self.stock_items = []  # [(code, name)]
        self.current_image_path = None
        
        self.setup_ui()
        self.load_stock_list()
    
    def _update_script_paths(self):
        """更新脚本路径"""
        if self.use_v2:
            self.train_script = os.path.join(self.project_root, "rl_trading", "train_ppo_v2.py")
            self.predict_script = os.path.join(self.project_root, "rl_trading", "predict_ppo_v2.py")
            self.model_suffix = "_v2"
            self.plot_suffix = "_v2"
        else:
            self.train_script = os.path.join(self.project_root, "rl_trading", "train_ppo.py")
            self.predict_script = os.path.join(self.project_root, "rl_trading", "predict_ppo.py")
            self.model_suffix = ""
            self.plot_suffix = ""

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        
        # Left Panel: Controls
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        # 1. Configuration Group
        config_group = QGroupBox("训练配置")
        config_layout = QVBoxLayout(config_group)
        
        # Version Selection
        version_layout = QHBoxLayout()
        version_layout.addWidget(QLabel("策略版本:"))
        self.version_combo = QComboBox()
        self.version_combo.addItem("V2 优化版 (推荐)", True)
        self.version_combo.addItem("V1 基础版", False)
        self.version_combo.currentIndexChanged.connect(self.on_version_changed)
        version_layout.addWidget(self.version_combo)
        config_layout.addLayout(version_layout)
        
        # Stock Selection
        config_layout.addWidget(QLabel("搜索股票（代码/名称）:"))
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入股票代码或名称筛选")
        self.search_input.textChanged.connect(self.filter_stock_list)
        search_layout.addWidget(self.search_input)
        config_layout.addLayout(search_layout)

        config_layout.addWidget(QLabel("股票代码:"))
        self.stock_combo = QComboBox()
        config_layout.addWidget(self.stock_combo)
        
        # Timesteps
        config_layout.addWidget(QLabel("训练步数:"))
        self.timesteps_spin = QSpinBox()
        self.timesteps_spin.setRange(1000, 10000000)
        self.timesteps_spin.setSingleStep(1000)
        self.timesteps_spin.setValue(500000)
        config_layout.addWidget(self.timesteps_spin)
        
        # Commission Settings
        commission_group = QGroupBox("费率设置")
        commission_layout = QVBoxLayout(commission_group)
        
        # Buy Commission
        buy_comm_layout = QHBoxLayout()
        buy_comm_layout.addWidget(QLabel("买入佣金:"))
        self.buy_comm_spin = QDoubleSpinBox()
        self.buy_comm_spin.setRange(0, 0.1)
        self.buy_comm_spin.setSingleStep(0.0001)
        self.buy_comm_spin.setDecimals(5)
        self.buy_comm_spin.setValue(0.0001)
        buy_comm_layout.addWidget(self.buy_comm_spin)
        commission_layout.addLayout(buy_comm_layout)
        
        buy_min_layout = QHBoxLayout()
        buy_min_layout.addWidget(QLabel("买入最低:"))
        self.buy_min_spin = QDoubleSpinBox()
        self.buy_min_spin.setRange(0, 100)
        self.buy_min_spin.setValue(5.0)
        self.buy_min_spin.setPrefix("¥")
        buy_min_layout.addWidget(self.buy_min_spin)
        commission_layout.addLayout(buy_min_layout)
        
        # Sell Commission
        sell_comm_layout = QHBoxLayout()
        sell_comm_layout.addWidget(QLabel("卖出佣金:"))
        self.sell_comm_spin = QDoubleSpinBox()
        self.sell_comm_spin.setRange(0, 0.1)
        self.sell_comm_spin.setSingleStep(0.0001)
        self.sell_comm_spin.setDecimals(5)
        self.sell_comm_spin.setValue(0.0001)
        sell_comm_layout.addWidget(self.sell_comm_spin)
        commission_layout.addLayout(sell_comm_layout)
        
        sell_min_layout = QHBoxLayout()
        sell_min_layout.addWidget(QLabel("卖出最低:"))
        self.sell_min_spin = QDoubleSpinBox()
        self.sell_min_spin.setRange(0, 100)
        self.sell_min_spin.setValue(5.0)
        self.sell_min_spin.setPrefix("¥")
        sell_min_layout.addWidget(self.sell_min_spin)
        commission_layout.addLayout(sell_min_layout)
        
        # Stamp Duty
        stamp_layout = QHBoxLayout()
        stamp_layout.addWidget(QLabel("印花税:"))
        self.stamp_spin = QDoubleSpinBox()
        self.stamp_spin.setRange(0, 0.1)
        self.stamp_spin.setSingleStep(0.0001)
        self.stamp_spin.setDecimals(5)
        self.stamp_spin.setValue(0.0005)
        stamp_layout.addWidget(self.stamp_spin)
        commission_layout.addLayout(stamp_layout)
        
        config_layout.addWidget(commission_group)
        
        # Buttons
        self.btn_train = QPushButton("开始训练")
        self.btn_train.clicked.connect(self.start_training)
        self.btn_train.setStyleSheet("background-color: #4CAF50; color: white;")
        config_layout.addWidget(self.btn_train)
        
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self.stop_process)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("background-color: #f44336; color: white;")
        config_layout.addWidget(self.btn_stop)
        
        self.btn_predict = QPushButton("运行预测 (回测)")
        self.btn_predict.clicked.connect(self.start_prediction)
        self.btn_predict.setStyleSheet("background-color: #2196F3; color: white;")
        config_layout.addWidget(self.btn_predict)
        
        left_layout.addWidget(config_group)
        left_layout.addStretch()
        
        main_layout.addWidget(left_panel, 1)
        
        # Middle Panel: Logs
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("background-color: #1e1e1e; color: #dcdcdc; font-family: Consolas;")
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group, 2)
        
        # Right Panel: Results/Plot
        result_group = QGroupBox("预测结果")
        result_layout = QVBoxLayout(result_group)
        
        # Zoom controls
        zoom_toolbar = QHBoxLayout()
        
        btn_zoom_in = QPushButton("放大")
        btn_zoom_in.setFixedWidth(50)
        btn_zoom_in.clicked.connect(self.zoom_in)
        zoom_toolbar.addWidget(btn_zoom_in)
        
        btn_zoom_out = QPushButton("缩小")
        btn_zoom_out.setFixedWidth(50)
        btn_zoom_out.clicked.connect(self.zoom_out)
        zoom_toolbar.addWidget(btn_zoom_out)
        
        btn_fit = QPushButton("适应")
        btn_fit.setFixedWidth(50)
        btn_fit.setToolTip("适应窗口大小")
        btn_fit.clicked.connect(self.zoom_fit)
        zoom_toolbar.addWidget(btn_fit)
        
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(50)
        zoom_toolbar.addWidget(self.zoom_label)
        
        zoom_toolbar.addStretch()
        
        btn_fullscreen = QPushButton("全屏查看")
        btn_fullscreen.setToolTip("在新窗口中打开")
        btn_fullscreen.clicked.connect(self.open_fullscreen)
        zoom_toolbar.addWidget(btn_fullscreen)
        
        result_layout.addLayout(zoom_toolbar)
        
        # Scrollable image area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setMinimumSize(400, 300)
        self.scroll_area.setStyleSheet("border: 1px solid #666;")
        
        self.plot_label = ZoomableImageLabel()
        self.plot_label.setText("预测曲线将显示在此处")
        self.scroll_area.setWidget(self.plot_label)
        
        result_layout.addWidget(self.scroll_area)
        
        main_layout.addWidget(result_group, 2)

    def load_stock_list(self):
        try:
            sys.path.append(os.path.join(self.project_root, "pyqt_app"))
            from data_loader import get_stock_list, load_stock_name_map
            
            codes = get_stock_list(self.data_dir)
            name_map = load_stock_name_map()
            
            self.stock_items = [(code, name_map.get(code, "")) for code in codes]
            self.filter_stock_list("")
        except Exception as e:
            self.log(f"Failed to load stock list: {e}")

    def filter_stock_list(self, text: str):
        if self.stock_items is None:
            return

        query = text.strip().lower()
        self.stock_combo.clear()

        for code, name in self.stock_items:
            label = f"{code} {name}".strip()
            if not query or query in code.lower() or query in name.lower():
                self.stock_combo.addItem(label, code)

        if self.stock_combo.count() > 0:
            self.stock_combo.setCurrentIndex(0)
    
    def on_version_changed(self, index):
        """切换策略版本"""
        self.use_v2 = self.version_combo.currentData()
        self._update_script_paths()
        version_name = "V2 优化版" if self.use_v2 else "V1 基础版"
        self.log(f"已切换到 {version_name}")

    def log(self, message):
        self.log_text.append(message)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def start_training(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            return
            
        stock_code = self.stock_combo.currentData()
        timesteps = self.timesteps_spin.value()
        
        self.log(f"--- Starting Training for {stock_code} ({timesteps} steps) ---")
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(self.process_finished)
        
        python_exe = sys.executable
        self.process.start(python_exe, [
            self.train_script, 
            "--stock_code", stock_code,
            "--timesteps", str(timesteps),
            "--buy_rate", str(self.buy_comm_spin.value()),
            "--buy_min", str(self.buy_min_spin.value()),
            "--sell_rate", str(self.sell_comm_spin.value()),
            "--sell_min", str(self.sell_min_spin.value()),
            "--stamp_duty", str(self.stamp_spin.value())
        ])
        
        self.update_ui_state(running=True)

    def start_prediction(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            return
            
        stock_code = self.stock_combo.currentData()
        
        self.log(f"--- Starting Prediction for {stock_code} ---")
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(lambda exit_code, status: self.prediction_finished(exit_code, stock_code))
        
        python_exe = sys.executable
        self.process.start(python_exe, [
            self.predict_script,
            "--stock_code", stock_code,
            "--buy_rate", str(self.buy_comm_spin.value()),
            "--buy_min", str(self.buy_min_spin.value()),
            "--sell_rate", str(self.sell_comm_spin.value()),
            "--sell_min", str(self.sell_min_spin.value()),
            "--stamp_duty", str(self.stamp_spin.value())
        ])
        
        self.update_ui_state(running=True)

    def stop_process(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.log("--- Process Stopped by User ---")

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data()
        try:
            text = data.decode('utf-8', errors='replace')
            self.log_text.insertPlainText(text)
            sb = self.log_text.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass

    def process_finished(self, exit_code, exit_status):
        self.update_ui_state(running=False)
        if exit_code == 0:
            self.log("--- Process Completed Successfully ---")
        else:
            self.log(f"--- Process Failed with Code {exit_code} ---")

    def prediction_finished(self, exit_code, stock_code):
        self.process_finished(exit_code, 0)
        if exit_code == 0:
            # Try to load image from output directory (根据版本选择正确的文件名)
            image_path = os.path.join(
                self.project_root, "rl_trading", "output", 
                f"prediction_plot_{stock_code}{self.plot_suffix}.png"
            )
            if os.path.exists(image_path):
                self.current_image_path = image_path
                pixmap = QPixmap(image_path)
                self.plot_label.set_image(pixmap)
                self.zoom_fit()
            else:
                self.plot_label.setText(f"Image not found: {image_path}")
                self.current_image_path = None

    def zoom_in(self):
        self.plot_label.zoom_in()
        self.update_zoom_label()
        
    def zoom_out(self):
        self.plot_label.zoom_out()
        self.update_zoom_label()
        
    def zoom_fit(self):
        self.plot_label.zoom_fit(self.scroll_area.size())
        self.update_zoom_label()
        
    def update_zoom_label(self):
        self.zoom_label.setText(f"{int(self.plot_label.scale_factor * 100)}%")
        
    def open_fullscreen(self):
        if self.current_image_path and os.path.exists(self.current_image_path):
            dialog = ImageViewerDialog(self.current_image_path, self)
            dialog.exec()

    def update_ui_state(self, running):
        self.btn_train.setEnabled(not running)
        self.btn_predict.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.stock_combo.setEnabled(not running)
        self.timesteps_spin.setEnabled(not running)
