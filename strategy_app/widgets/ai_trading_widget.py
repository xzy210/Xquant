"""
AI 策略训练与预测回测组件

支持：
1. 单股票策略训练
2. 多股票通用策略训练
3. 股票筛选（排除创业板、科创板、北交所等）
4. 批量策略预测回测
"""
import os
import sys
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QGroupBox, QSplitter,
    QMessageBox, QProgressBar, QLineEdit, QScrollArea, QDialog, QSlider,
    QCheckBox, QTabWidget, QListWidget, QListWidgetItem, QAbstractItemView,
    QFrame, QRadioButton, QButtonGroup
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
        self.setWindowTitle("策略预测结果")
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
    """AI 策略训练与预测回测组件"""

    def __init__(self, data_dir: str = "../data", stocklist_path: str = None, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        
        # Paths
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.train_script = os.path.join(self.project_root, "rl_trading", "train_ppo.py")
        self.train_multi_script = os.path.join(self.project_root, "rl_trading", "train_ppo_multi.py")
        self.train_lstm_script = os.path.join(self.project_root, "rl_trading", "train_lstm_multi.py")
        self.predict_script = os.path.join(self.project_root, "rl_trading", "predict_ppo.py")
        self.predict_multi_script = os.path.join(self.project_root, "rl_trading", "predict_ppo_multi.py")
        self.predict_lstm_script = os.path.join(self.project_root, "rl_trading", "predict_lstm_multi.py")
        self.models_dir = os.path.join(self.project_root, "rl_trading", "models")
        self.output_dir = os.path.join(self.project_root, "rl_trading", "output")
        
        self.process = None
        self.stock_items = []  # [(code, name)]
        self.current_image_path = None
        
        self.setup_ui()
        self.load_stock_list()
        self.load_available_models()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        
        # Left Panel: Controls with Tabs
        left_panel = QWidget()
        left_panel.setMinimumWidth(320)  # 确保设置面板有足够宽度
        left_layout = QVBoxLayout(left_panel)
        
        # 训练模式选择
        mode_group = QGroupBox("AI策略训练模式")
        mode_layout = QVBoxLayout(mode_group)
        
        self.mode_button_group = QButtonGroup(self)
        
        self.single_mode_radio = QRadioButton("单股票训练 (MLP)")
        self.single_mode_radio.setToolTip("针对单只股票训练专用模型，使用MLP网络")
        self.single_mode_radio.setChecked(True)
        self.single_mode_radio.toggled.connect(self.on_mode_changed)
        self.mode_button_group.addButton(self.single_mode_radio)
        mode_layout.addWidget(self.single_mode_radio)
        
        self.multi_mode_radio = QRadioButton("多股票训练 (MLP通用策略)")
        self.multi_mode_radio.setToolTip("使用多只股票训练通用AI策略模型")
        self.mode_button_group.addButton(self.multi_mode_radio)
        mode_layout.addWidget(self.multi_mode_radio)
        
        self.lstm_mode_radio = QRadioButton("多股票训练 (LSTM/GRU)")
        self.lstm_mode_radio.setToolTip("使用LSTM/GRU序列模型训练，可更好捕捉时序特征")
        self.mode_button_group.addButton(self.lstm_mode_radio)
        mode_layout.addWidget(self.lstm_mode_radio)
        
        left_layout.addWidget(mode_group)
        
        # Tabs for different config panels
        self.config_tabs = QTabWidget()
        
        # Tab 1: Single Stock Config
        self.single_stock_tab = QWidget()
        self.setup_single_stock_tab()
        self.config_tabs.addTab(self.single_stock_tab, "单股票配置")
        
        # Tab 2: Multi Stock Config
        self.multi_stock_tab = QWidget()
        self.setup_multi_stock_tab()
        self.config_tabs.addTab(self.multi_stock_tab, "多股票配置")
        
        # Tab 3: LSTM Config
        self.lstm_tab = QWidget()
        self.setup_lstm_tab()
        self.config_tabs.addTab(self.lstm_tab, "LSTM/GRU配置")
        
        # Tab 4: Common Settings
        self.common_tab = QWidget()
        self.setup_common_tab()
        self.config_tabs.addTab(self.common_tab, "通用设置")
        
        left_layout.addWidget(self.config_tabs)
        
        # Buttons
        btn_layout = QVBoxLayout()
        
        # 继续AI策略训练选项
        resume_layout = QHBoxLayout()
        self.resume_checkbox = QCheckBox("继续AI策略训练")
        self.resume_checkbox.setToolTip("从已有模型继续AI策略训练（需要先选择要继续的模型）")
        self.resume_checkbox.stateChanged.connect(self.on_resume_changed)
        resume_layout.addWidget(self.resume_checkbox)

        # 重置参数选项
        self.reset_params_checkbox = QCheckBox("重置参数")
        self.reset_params_checkbox.setToolTip("继续AI策略训练时，重置学习率和熵系数为当前设置值，而不是使用模型保存的参数")
        self.reset_params_checkbox.setEnabled(False)  # 默认禁用，只有选中继续AI策略训练时才启用
        resume_layout.addWidget(self.reset_params_checkbox)
        
        self.resume_model_combo = QComboBox()
        self.resume_model_combo.setToolTip("选择要继续AI策略训练的模型")
        self.resume_model_combo.setEnabled(False)
        resume_layout.addWidget(self.resume_model_combo, 1)
        btn_layout.addLayout(resume_layout)
        
        self.btn_train = QPushButton("🚀 开始AI策略训练")
        self.btn_train.clicked.connect(self.start_training)
        self.btn_train.setProperty("class", "success")
        btn_layout.addWidget(self.btn_train)
        
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.clicked.connect(self.stop_process)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setProperty("class", "danger")
        btn_layout.addWidget(self.btn_stop)
        
        # 预测部分
        predict_group = QGroupBox("策略预测回测")
        predict_layout = QVBoxLayout(predict_group)
        
        # 模型选择
        predict_layout.addWidget(QLabel("选择模型:"))
        self.model_combo = QComboBox()
        self.model_combo.setToolTip("选择已训练的模型")
        predict_layout.addWidget(self.model_combo)
        
        btn_refresh_models = QPushButton("刷新模型列表")
        btn_refresh_models.clicked.connect(self.load_available_models)
        predict_layout.addWidget(btn_refresh_models)
        
        self.btn_predict = QPushButton("📊 运行策略预测回测")
        self.btn_predict.clicked.connect(self.start_prediction)
        self.btn_predict.setProperty("class", "primary")
        predict_layout.addWidget(self.btn_predict)
        
        self.btn_batch_predict = QPushButton("📈 批量预测")
        self.btn_batch_predict.clicked.connect(self.start_batch_prediction)
        self.btn_batch_predict.setToolTip("使用多股票模型对多只股票进行预测")
        self.btn_batch_predict.setProperty("class", "info")
        predict_layout.addWidget(self.btn_batch_predict)
        
        btn_layout.addWidget(predict_group)
        
        left_layout.addLayout(btn_layout)
        left_layout.addStretch()
        
        main_layout.addWidget(left_panel, 1)
        
        # Middle Panel: Logs
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: Consolas, monospace;")
        log_layout.addWidget(self.log_text)
        
        # Clear log button
        btn_clear_log = QPushButton("清空日志")
        btn_clear_log.clicked.connect(lambda: self.log_text.clear())
        log_layout.addWidget(btn_clear_log)
        
        main_layout.addWidget(log_group, 2)
        
        # Right Panel: Results/Plot
        result_group = QGroupBox("策略预测结果")
        result_layout = QVBoxLayout(result_group)
        
        # Zoom controls
        zoom_toolbar = QHBoxLayout()
        
        btn_zoom_in = QPushButton("🔍+")
        btn_zoom_in.setFixedWidth(40)
        btn_zoom_in.clicked.connect(self.zoom_in)
        zoom_toolbar.addWidget(btn_zoom_in)
        
        btn_zoom_out = QPushButton("🔍-")
        btn_zoom_out.setFixedWidth(40)
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
        
        btn_fullscreen = QPushButton("🖼 全屏查看")
        btn_fullscreen.setToolTip("在新窗口中打开")
        btn_fullscreen.clicked.connect(self.open_fullscreen)
        zoom_toolbar.addWidget(btn_fullscreen)
        
        result_layout.addLayout(zoom_toolbar)
        
        # Scrollable image area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setMinimumSize(400, 300)
        # 样式已在全局定义

        
        self.plot_label = ZoomableImageLabel()
        self.plot_label.setText("策略预测曲线将显示在此处\n\n1. 先完成AI策略训练\n2. 然后运行策略预测回测")
        self.scroll_area.setWidget(self.plot_label)
        
        result_layout.addWidget(self.scroll_area)
        
        main_layout.addWidget(result_group, 2)
        
        # Initialize mode
        self.on_mode_changed()

    def setup_single_stock_tab(self):
        """单股票配置标签页"""
        layout = QVBoxLayout(self.single_stock_tab)
        
        # Stock Selection
        layout.addWidget(QLabel("搜索股票:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入股票代码或名称筛选")
        self.search_input.textChanged.connect(self.filter_stock_list)
        layout.addWidget(self.search_input)

        layout.addWidget(QLabel("选择股票:"))
        self.stock_combo = QComboBox()
        layout.addWidget(self.stock_combo)
        
        layout.addStretch()

    def setup_multi_stock_tab(self):
        """多股票配置标签页"""
        layout = QVBoxLayout(self.multi_stock_tab)
        
        # 股票筛选选项
        filter_group = QGroupBox("股票筛选")
        filter_layout = QVBoxLayout(filter_group)
        
        self.exclude_cyb_cb = QCheckBox("排除创业板 (300xxx, 301xxx)")
        self.exclude_cyb_cb.setChecked(True)
        self.exclude_cyb_cb.stateChanged.connect(self.update_stock_count)
        filter_layout.addWidget(self.exclude_cyb_cb)
        
        self.exclude_kcb_cb = QCheckBox("排除科创板 (688xxx)")
        self.exclude_kcb_cb.setChecked(True)
        self.exclude_kcb_cb.stateChanged.connect(self.update_stock_count)
        filter_layout.addWidget(self.exclude_kcb_cb)
        
        self.exclude_bse_cb = QCheckBox("排除北交所 (8xxxxx)")
        self.exclude_bse_cb.setChecked(True)
        self.exclude_bse_cb.stateChanged.connect(self.update_stock_count)
        filter_layout.addWidget(self.exclude_bse_cb)
        
        self.exclude_st_cb = QCheckBox("排除ST股票")
        self.exclude_st_cb.setChecked(True)
        self.exclude_st_cb.stateChanged.connect(self.update_stock_count)
        filter_layout.addWidget(self.exclude_st_cb)
        
        # 手动指定股票
        self.manual_stocks_cb = QCheckBox("手动指定股票代码 (优先级最高)")
        self.manual_stocks_cb.setToolTip("选中后将忽略上方的筛选条件，仅使用手动输入的股票代码")
        self.manual_stocks_cb.stateChanged.connect(self.on_manual_stocks_changed)
        filter_layout.addWidget(self.manual_stocks_cb)

        self.manual_stocks_input = QTextEdit()
        self.manual_stocks_input.setPlaceholderText("请输入股票代码，用逗号或换行分隔\n例如: 000001, 600000, 600519")
        self.manual_stocks_input.setMaximumHeight(80)
        self.manual_stocks_input.setEnabled(False)
        self.manual_stocks_input.textChanged.connect(self.update_stock_count)
        filter_layout.addWidget(self.manual_stocks_input)
        
        # 股票数量统计
        self.stock_count_label = QLabel("0")
        self.stock_count_label.setProperty("class", "highlight")
        filter_layout.addWidget(self.stock_count_label)        
        layout.addWidget(filter_group)
        
        # 最大股票数
        max_stocks_layout = QHBoxLayout()
        max_stocks_layout.addWidget(QLabel("最大股票数 (0=不限):"))
        self.max_stocks_spin = QSpinBox()
        self.max_stocks_spin.setRange(0, 9999)
        self.max_stocks_spin.setValue(0)
        self.max_stocks_spin.setToolTip("0表示使用所有符合条件的股票")
        max_stocks_layout.addWidget(self.max_stocks_spin)
        layout.addLayout(max_stocks_layout)
        
        # 最少数据天数
        min_days_layout = QHBoxLayout()
        min_days_layout.addWidget(QLabel("最少数据天数:"))
        self.min_data_days_spin = QSpinBox()
        self.min_data_days_spin.setRange(100, 5000)
        self.min_data_days_spin.setValue(500)
        self.min_data_days_spin.setToolTip("股票数据至少要有这么多天才会被用于训练")
        min_days_layout.addWidget(self.min_data_days_spin)
        layout.addLayout(min_days_layout)
        
        # 模型名称
        model_name_layout = QHBoxLayout()
        model_name_layout.addWidget(QLabel("模型名称:"))
        self.model_name_input = QLineEdit()
        self.model_name_input.setText("ppo_multi_stock")
        self.model_name_input.setToolTip("保存的模型文件名")
        model_name_layout.addWidget(self.model_name_input)
        layout.addLayout(model_name_layout)
        
        layout.addStretch()

    def setup_lstm_tab(self):
        """LSTM/GRU 配置标签页"""
        layout = QVBoxLayout(self.lstm_tab)
        
        # RNN 类型选择
        rnn_group = QGroupBox("序列模型配置")
        rnn_layout = QVBoxLayout(rnn_group)
        
        # RNN 类型
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("模型类型:"))
        self.rnn_type_combo = QComboBox()
        self.rnn_type_combo.addItem("LSTM (推荐)", "lstm")
        self.rnn_type_combo.addItem("GRU (更快)", "gru")
        self.rnn_type_combo.addItem("Transformer (实验性)", "transformer")
        self.rnn_type_combo.setToolTip("LSTM: 经典循环网络，效果稳定\nGRU: 更快，参数更少\nTransformer: 捕捉长距离依赖")
        type_layout.addWidget(self.rnn_type_combo)
        rnn_layout.addLayout(type_layout)
        
        # 隐藏层大小
        hidden_layout = QHBoxLayout()
        hidden_layout.addWidget(QLabel("隐藏层大小:"))
        self.rnn_hidden_spin = QSpinBox()
        self.rnn_hidden_spin.setRange(32, 512)
        self.rnn_hidden_spin.setValue(128)
        self.rnn_hidden_spin.setToolTip("RNN隐藏层神经元数量")
        hidden_layout.addWidget(self.rnn_hidden_spin)
        rnn_layout.addLayout(hidden_layout)
        
        # RNN 层数
        layers_layout = QHBoxLayout()
        layers_layout.addWidget(QLabel("RNN层数:"))
        self.rnn_layers_spin = QSpinBox()
        self.rnn_layers_spin.setRange(1, 4)
        self.rnn_layers_spin.setValue(2)
        self.rnn_layers_spin.setToolTip("堆叠的RNN层数，2层通常足够")
        layers_layout.addWidget(self.rnn_layers_spin)
        rnn_layout.addLayout(layers_layout)
        
        # 特征维度
        features_layout = QHBoxLayout()
        features_layout.addWidget(QLabel("特征维度:"))
        self.rnn_features_spin = QSpinBox()
        self.rnn_features_spin.setRange(32, 256)
        self.rnn_features_spin.setValue(128)
        self.rnn_features_spin.setToolTip("特征提取器输出维度")
        features_layout.addWidget(self.rnn_features_spin)
        rnn_layout.addLayout(features_layout)
        
        # Dropout
        dropout_layout = QHBoxLayout()
        dropout_layout.addWidget(QLabel("Dropout:"))
        self.rnn_dropout_spin = QDoubleSpinBox()
        self.rnn_dropout_spin.setRange(0, 0.5)
        self.rnn_dropout_spin.setSingleStep(0.05)
        self.rnn_dropout_spin.setValue(0.1)
        self.rnn_dropout_spin.setToolTip("防止过拟合，建议0.1-0.2")
        dropout_layout.addWidget(self.rnn_dropout_spin)
        rnn_layout.addLayout(dropout_layout)
        
        # 双向
        self.rnn_bidirectional_cb = QCheckBox("双向RNN")
        self.rnn_bidirectional_cb.setToolTip("双向可以同时看过去和未来的信息，但速度较慢")
        rnn_layout.addWidget(self.rnn_bidirectional_cb)
        
        layout.addWidget(rnn_group)
        
        # LSTM 模型名称
        lstm_name_layout = QHBoxLayout()
        lstm_name_layout.addWidget(QLabel("LSTM模型名称:"))
        self.lstm_model_name_input = QLineEdit()
        self.lstm_model_name_input.setText("lstm_multi_stock")
        self.lstm_model_name_input.setToolTip("LSTM/GRU模型的保存文件名")
        lstm_name_layout.addWidget(self.lstm_model_name_input)
        layout.addLayout(lstm_name_layout)
        
        # 提示信息
        info_label = QLabel(
            "💡 提示：LSTM/GRU能更好地捕捉时序特征，\n"
            "但训练速度比MLP慢。建议先用GRU尝试。"
        )
        info_label.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(info_label)
        
        layout.addStretch()

    def setup_common_tab(self):
        """通用设置标签页"""
        layout = QVBoxLayout(self.common_tab)
        
        # Training Settings
        train_group = QGroupBox("训练参数")
        train_layout = QFormLayout(train_group)
        train_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        # Timesteps
        self.timesteps_spin = QSpinBox()
        self.timesteps_spin.setRange(10000, 10000000)
        self.timesteps_spin.setSingleStep(10000)
        self.timesteps_spin.setValue(500000)
        self.timesteps_spin.setMinimumWidth(120)
        self.timesteps_spin.setMinimumHeight(28)
        train_layout.addRow("训练步数:", self.timesteps_spin)
        
        # Parallel Environments
        self.num_envs_spin = QSpinBox()
        self.num_envs_spin.setRange(1, 32)
        self.num_envs_spin.setValue(8)
        self.num_envs_spin.setToolTip("多环境并行训练，可加速训练\n建议设为CPU核心数")
        self.num_envs_spin.setMinimumWidth(120)
        self.num_envs_spin.setMinimumHeight(28)
        train_layout.addRow("并行环境数:", self.num_envs_spin)
        
        # Learning rate
        self.lr_spin = QDoubleSpinBox()
        self.lr_spin.setRange(0.00001, 0.01)
        self.lr_spin.setSingleStep(0.0001)
        self.lr_spin.setDecimals(6)
        self.lr_spin.setValue(0.0003)
        self.lr_spin.setMinimumWidth(120)
        self.lr_spin.setMinimumHeight(28)
        train_layout.addRow("学习率:", self.lr_spin)
        
        layout.addWidget(train_group)
        
        # Commission Settings
        commission_group = QGroupBox("费率设置")
        commission_layout = QFormLayout(commission_group)
        commission_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        # Buy Commission
        self.buy_comm_spin = QDoubleSpinBox()
        self.buy_comm_spin.setRange(0, 0.1)
        self.buy_comm_spin.setSingleStep(0.0001)
        self.buy_comm_spin.setDecimals(5)
        self.buy_comm_spin.setValue(0.0001)
        self.buy_comm_spin.setMinimumWidth(120)
        self.buy_comm_spin.setMinimumHeight(28)
        commission_layout.addRow("买入佣金率:", self.buy_comm_spin)
        
        self.buy_min_spin = QDoubleSpinBox()
        self.buy_min_spin.setRange(0, 100)
        self.buy_min_spin.setValue(5.0)
        self.buy_min_spin.setPrefix("¥")
        self.buy_min_spin.setMinimumWidth(120)
        self.buy_min_spin.setMinimumHeight(28)
        commission_layout.addRow("买入最低佣金:", self.buy_min_spin)
        
        # Sell Commission
        self.sell_comm_spin = QDoubleSpinBox()
        self.sell_comm_spin.setRange(0, 0.1)
        self.sell_comm_spin.setSingleStep(0.0001)
        self.sell_comm_spin.setDecimals(5)
        self.sell_comm_spin.setValue(0.0001)
        self.sell_comm_spin.setMinimumWidth(120)
        self.sell_comm_spin.setMinimumHeight(28)
        commission_layout.addRow("卖出佣金率:", self.sell_comm_spin)
        
        self.sell_min_spin = QDoubleSpinBox()
        self.sell_min_spin.setRange(0, 100)
        self.sell_min_spin.setValue(5.0)
        self.sell_min_spin.setPrefix("¥")
        self.sell_min_spin.setMinimumWidth(120)
        self.sell_min_spin.setMinimumHeight(28)
        commission_layout.addRow("卖出最低佣金:", self.sell_min_spin)
        
        # Stamp Duty
        self.stamp_spin = QDoubleSpinBox()
        self.stamp_spin.setRange(0, 0.1)
        self.stamp_spin.setSingleStep(0.0001)
        self.stamp_spin.setDecimals(5)
        self.stamp_spin.setValue(0.0005)
        self.stamp_spin.setMinimumWidth(120)
        self.stamp_spin.setMinimumHeight(28)
        commission_layout.addRow("印花税率:", self.stamp_spin)
        
        layout.addWidget(commission_group)
        layout.addStretch()

    def on_mode_changed(self):
        """训练模式切换"""
        is_single = self.single_mode_radio.isChecked()
        is_multi = self.multi_mode_radio.isChecked()
        is_lstm = self.lstm_mode_radio.isChecked()
        
        # 切换到对应的配置标签页
        if is_single:
            self.config_tabs.setCurrentIndex(0)  # 单股票配置
        elif is_multi:
            self.config_tabs.setCurrentIndex(1)  # 多股票配置
        elif is_lstm:
            self.config_tabs.setCurrentIndex(2)  # LSTM/GRU配置
        
        # 更新股票计数（多股票模式或LSTM模式）
        if is_multi or is_lstm:
            self.update_stock_count()

    def load_stock_list(self):
        """加载股票列表"""
        try:
            from common.data_portal import get_data_portal
            
            portal = get_data_portal()
            codes = portal.list_symbols(asset_type="stock", data_dir=self.data_dir)
            name_map = portal.get_name_map(asset_type="stock", stocklist_path=self.stocklist_path)
            
            self.stock_items = [(code, name_map.get(code, "")) for code in codes]
            
            # 构建名称到代码的映射（用于手动输入识别）
            self.name_to_code = {}
            for code, name in self.stock_items:
                if name:
                    self.name_to_code[name] = code
            
            self.filter_stock_list("")
            
            # 更新多股票模式的计数
            self.update_stock_count()
        except Exception as e:
            self.log(f"加载股票列表失败: {e}")

    def filter_stock_list(self, text: str):
        """筛选单股票下拉框"""
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

    def on_manual_stocks_changed(self, state):
        """手动指定股票复选框状态改变"""
        is_manual = state == Qt.CheckState.Checked.value
        self.manual_stocks_input.setEnabled(is_manual)
        
        # 禁用或启用筛选条件
        self.exclude_cyb_cb.setEnabled(not is_manual)
        self.exclude_kcb_cb.setEnabled(not is_manual)
        self.exclude_bse_cb.setEnabled(not is_manual)
        self.exclude_st_cb.setEnabled(not is_manual)
        
        self.update_stock_count()

    def get_valid_stock_codes(self, text: str) -> list:
        """解析手动输入的股票代码或名称，返回有效的代码列表"""
        if not text or not text.strip():
            return []
            
        import re
        tokens = re.split(r'[,\s\n]+', text.strip())
        valid_codes = []
        
        for token in tokens:
            if not token:
                continue
                
            # 1. 尝试直接作为代码匹配
            # 简单验证是否为数字且长度合理(4-6位)，或者直接在stock_items里找
            token_clean = token.strip()
            
            # 如果是纯数字，尝试补全或直接匹配
            if token_clean.isdigit():
                # 尝试直接匹配完整代码
                found = False
                for code, _ in self.stock_items:
                    if str(code) == token_clean:
                        valid_codes.append(str(code))
                        found = True
                        break
                if found:
                    continue
                    
                # 尝试补全 (e.g. 1 -> 000001) - 只有在找不到直接匹配时才尝试? 
                # 或者只要是6位数字就认为是代码
                if len(token_clean) == 6:
                    valid_codes.append(token_clean)
                    continue
            
            # 2. 尝试作为名称匹配
            if hasattr(self, 'name_to_code') and token_clean in self.name_to_code:
                valid_codes.append(self.name_to_code[token_clean])
                continue
                
            # 3. 模糊匹配名称 (可选，如果精确匹配失败)
            # 为防止误匹配，这里暂只做精确匹配，或者提示用户
            
            # 如果看起来像代码（6位数字），即使没在列表里也保留（可能是新股或数据未更新）
            if token_clean.isdigit() and len(token_clean) == 6:
                valid_codes.append(token_clean)
        
        # 去重
        return list(dict.fromkeys(valid_codes))

    def update_stock_count(self):
        """更新多股票模式下的股票数量统计"""
        # 手动模式
        if hasattr(self, 'manual_stocks_cb') and self.manual_stocks_cb.isChecked():
            text = self.manual_stocks_input.toPlainText().strip()
            codes = self.get_valid_stock_codes(text)
            self.stock_count_label.setText(f"手动指定股票: {len(codes)} 只")
            return

        if not self.stock_items:
            self.stock_count_label.setText("符合条件的股票: 0")
            return
        
        exclude_cyb = self.exclude_cyb_cb.isChecked()
        exclude_kcb = self.exclude_kcb_cb.isChecked()
        exclude_bse = self.exclude_bse_cb.isChecked()
        exclude_st = self.exclude_st_cb.isChecked()
        
        count = 0
        for code, name in self.stock_items:
            code_str = str(code).zfill(6)
            
            # 排除创业板
            if exclude_cyb and (code_str.startswith('300') or code_str.startswith('301')):
                continue
            
            # 排除科创板
            if exclude_kcb and code_str.startswith('688'):
                continue
            
            # 排除北交所
            if exclude_bse and (code_str.startswith('8') or code_str.startswith('43') or code_str.startswith('87')):
                continue
            
            # 排除ST
            if exclude_st and name and ('ST' in name or '*ST' in name):
                continue
            
            count += 1
        
        self.stock_count_label.setText(f"符合条件的股票: {count} 只")

    def load_available_models(self):
        """加载可用的模型列表"""
        self.model_combo.clear()
        self.resume_model_combo.clear()
        
        if not os.path.exists(self.models_dir):
            return
        
        models = []
        for f in os.listdir(self.models_dir):
            if f.endswith('.zip'):
                model_name = f[:-4]  # 去掉 .zip
                models.append(model_name)
        
        models.sort()
        
        for model in models:
            # 判断是单股票还是多股票模型
            if model.startswith('ppo_stock_'):
                # 单股票模型
                code = model.replace('ppo_stock_', '').split('_')[0]
                display_name = f"[单股票] {model}"
            elif model.startswith('ppo_multi'):
                display_name = f"[多股票] {model}"
            else:
                display_name = model
            
            self.model_combo.addItem(display_name, model)
            self.resume_model_combo.addItem(display_name, model)
        
        if self.model_combo.count() == 0:
            self.model_combo.addItem("无可用模型", None)
        
        if self.resume_model_combo.count() == 0:
            self.resume_model_combo.addItem("无可用模型", None)
        
        self.log(f"找到 {len(models)} 个可用模型")
    
    def on_resume_changed(self, state):
        """继续AI策略训练复选框状态改变"""
        is_resume = state == Qt.CheckState.Checked.value
        self.resume_model_combo.setEnabled(is_resume)
        self.reset_params_checkbox.setEnabled(is_resume)
        
        if is_resume:
            self.btn_train.setText("🔄 继续AI策略训练")
            self.btn_train.setProperty("class", "warning")
        else:
            self.btn_train.setText("🚀 开始AI策略训练")
            self.btn_train.setProperty("class", "success")
        
        # 刷新样式
        self.btn_train.style().unpolish(self.btn_train)
        self.btn_train.style().polish(self.btn_train)

    def log(self, message):
        """输出日志"""
        self.log_text.append(message)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def start_training(self):
        """开始AI策略训练"""
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "警告", "已有进程在运行中")
            return
        
        if self.lstm_mode_radio.isChecked():
            self.start_lstm_training()
        elif self.multi_mode_radio.isChecked():
            self.start_multi_stock_training()
        else:
            self.start_single_stock_training()

    def start_single_stock_training(self):
        """单股票训练"""
        stock_code = self.stock_combo.currentData()
        if not stock_code:
            QMessageBox.warning(self, "警告", "请选择一只股票")
            return
        
        timesteps = self.timesteps_spin.value()
        num_envs = self.num_envs_spin.value()
        is_resume = self.resume_checkbox.isChecked()
        
        if is_resume:
            self.log(f"\n{'='*50}")
            self.log(f"继续AI策略训练单股票模型: {stock_code}")
            self.log(f"额外训练步数: {timesteps}, 并行环境: {num_envs}")
            self.log(f"{'='*50}\n")
        else:
            self.log(f"\n{'='*50}")
            self.log(f"开始单股票训练: {stock_code}")
            self.log(f"训练步数: {timesteps}, 并行环境: {num_envs}")
            self.log(f"{'='*50}\n")
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(self.process_finished)
        
        python_exe = sys.executable
        args = [
            self.train_script, 
            "--stock_code", stock_code,
            "--timesteps", str(timesteps),
            "--num_envs", str(num_envs),
            "--learning_rate", str(self.lr_spin.value()),
            "--buy_rate", str(self.buy_comm_spin.value()),
            "--buy_min", str(self.buy_min_spin.value()),
            "--sell_rate", str(self.sell_comm_spin.value()),
            "--sell_min", str(self.sell_min_spin.value()),
            "--stamp_duty", str(self.stamp_spin.value())
        ]
        
        # 继续AI策略训练参数
        if is_resume:
            args.append("--resume")
            if self.reset_params_checkbox.isChecked():
                args.append("--reset_params")
                self.log("  - 重置参数: Yes")
            if self.reset_params_checkbox.isChecked():
                args.append("--reset_params")
                self.log("  - 重置参数: Yes")
        
        self.process.start(python_exe, args)
        self.update_ui_state(running=True)

    def start_multi_stock_training(self):
        """多股票训练"""
        timesteps = self.timesteps_spin.value()
        num_envs = self.num_envs_spin.value()
        model_name = self.model_name_input.text().strip() or "ppo_multi_stock"
        is_resume = self.resume_checkbox.isChecked()
        resume_model = self.resume_model_combo.currentData() if is_resume else None
        
        if is_resume:
            if not resume_model:
                QMessageBox.warning(self, "警告", "请选择要继续AI策略训练的模型")
                return
            self.log(f"\n{'='*50}")
            self.log(f"继续AI策略训练多股票模型")
            self.log(f"从模型: {resume_model}")
            self.log(f"额外训练步数: {timesteps}, 并行环境: {num_envs}")
            self.log(f"保存为: {model_name}")
            self.log(f"{'='*50}\n")
        else:
            self.log(f"\n{'='*50}")
            self.log(f"开始多股票训练")
            self.log(f"训练步数: {timesteps}, 并行环境: {num_envs}")
            self.log(f"模型名称: {model_name}")
            self.log(f"{'='*50}\n")
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(self.process_finished)
        
        python_exe = sys.executable
        args = [
            self.train_multi_script, 
            "--timesteps", str(timesteps),
            "--num_envs", str(num_envs),
            "--model_name", model_name,
            "--learning_rate", str(self.lr_spin.value()),
            "--min_data_days", str(self.min_data_days_spin.value()),
            "--buy_rate", str(self.buy_comm_spin.value()),
            "--buy_min", str(self.buy_min_spin.value()),
            "--sell_rate", str(self.sell_comm_spin.value()),
            "--sell_min", str(self.sell_min_spin.value()),
            "--stamp_duty", str(self.stamp_spin.value())
        ]
        
        # check for manual stocks
        manual_stocks = ""
        if hasattr(self, 'manual_stocks_cb') and self.manual_stocks_cb.isChecked():
            text = self.manual_stocks_input.toPlainText().strip()
            if text:
                codes = self.get_valid_stock_codes(text)
                manual_stocks = ",".join(codes)

        # 继续AI策略训练参数
        if is_resume and resume_model:
            args.extend(["--resume", resume_model])
            if self.reset_params_checkbox.isChecked():
                args.append("--reset_params")
                self.log("  - 重置参数: Yes")
        
        if manual_stocks:
            args.extend(["--stock_codes", manual_stocks])
            self.log(f"使用手动指定股票代码: {manual_stocks}")
        else:
            # 添加排除选项（注意：参数是 include，所以取反）
            if not self.exclude_cyb_cb.isChecked():
                args.append("--include_cyb")
            if not self.exclude_kcb_cb.isChecked():
                args.append("--include_kcb")
            if not self.exclude_bse_cb.isChecked():
                args.append("--include_bse")
            if not self.exclude_st_cb.isChecked():
                args.append("--include_st")
        
        if self.max_stocks_spin.value() > 0:
            args.extend(["--max_stocks", str(self.max_stocks_spin.value())])
        
        self.process.start(python_exe, args)
        self.update_ui_state(running=True)

    def start_lstm_training(self):
        """LSTM/GRU 多股票训练"""
        timesteps = self.timesteps_spin.value()
        num_envs = self.num_envs_spin.value()
        model_name = self.lstm_model_name_input.text().strip() or "lstm_multi_stock"
        is_resume = self.resume_checkbox.isChecked()
        resume_model = self.resume_model_combo.currentData() if is_resume else None
        
        # 获取RNN配置
        rnn_type = self.rnn_type_combo.currentData()
        hidden_size = self.rnn_hidden_spin.value()
        rnn_layers = self.rnn_layers_spin.value()
        features_dim = self.rnn_features_spin.value()
        dropout = self.rnn_dropout_spin.value()
        bidirectional = self.rnn_bidirectional_cb.isChecked()
        
        if is_resume:
            if not resume_model:
                QMessageBox.warning(self, "警告", "请选择要继续AI策略训练的模型")
                return
            self.log(f"\n{'='*50}")
            self.log(f"继续AI策略训练 {rnn_type.upper()} 多股票模型")
            self.log(f"从模型: {resume_model}")
            self.log(f"额外训练步数: {timesteps}, 并行环境: {num_envs}")
            self.log(f"保存为: {model_name}")
            self.log(f"{'='*50}\n")
        else:
            self.log(f"\n{'='*50}")
            self.log(f"开始 {rnn_type.upper()} 多股票训练")
            self.log(f"训练步数: {timesteps}, 并行环境: {num_envs}")
            self.log(f"模型名称: {model_name}")
            self.log(f"隐藏层: {hidden_size}, 层数: {rnn_layers}")
            self.log(f"{'='*50}\n")
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(self.process_finished)
        
        python_exe = sys.executable
        args = [
            self.train_lstm_script,
            "--rnn_type", rnn_type,
            "--hidden_size", str(hidden_size),
            "--rnn_layers", str(rnn_layers),
            "--features_dim", str(features_dim),
            "--dropout", str(dropout),
            "--timesteps", str(timesteps),
            "--num_envs", str(num_envs),
            "--model_name", model_name,
            "--learning_rate", str(self.lr_spin.value()),
            "--min_data_days", str(self.min_data_days_spin.value()),
            "--buy_rate", str(self.buy_comm_spin.value()),
            "--buy_min", str(self.buy_min_spin.value()),
            "--sell_rate", str(self.sell_comm_spin.value()),
            "--sell_min", str(self.sell_min_spin.value()),
            "--stamp_duty", str(self.stamp_spin.value())
        ]
        
        if bidirectional:
            args.append("--bidirectional")
            
        # check for manual stocks
        manual_stocks = ""
        if hasattr(self, 'manual_stocks_cb') and self.manual_stocks_cb.isChecked():
            text = self.manual_stocks_input.toPlainText().strip()
            if text:
                codes = self.get_valid_stock_codes(text)
                manual_stocks = ",".join(codes)
        
        # 继续AI策略训练参数
        if is_resume and resume_model:
            args.extend(["--resume", resume_model])
            if self.reset_params_checkbox.isChecked():
                args.append("--reset_params")
                self.log("  - 重置参数: Yes")
        
        if manual_stocks:
            args.extend(["--stock_codes", manual_stocks])
            self.log(f"使用手动指定股票代码: {manual_stocks}")
        else:
            # 添加排除选项（与多股票训练共用筛选条件）
            if not self.exclude_cyb_cb.isChecked():
                args.append("--include_cyb")
            if not self.exclude_kcb_cb.isChecked():
                args.append("--include_kcb")
            if not self.exclude_bse_cb.isChecked():
                args.append("--include_bse")
            if not self.exclude_st_cb.isChecked():
                args.append("--include_st")
        
        if self.max_stocks_spin.value() > 0:
            args.extend(["--max_stocks", str(self.max_stocks_spin.value())])
        
        self.process.start(python_exe, args)
        self.update_ui_state(running=True)

    def start_prediction(self):
        """开始预测"""
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "警告", "已有进程在运行中")
            return
        
        model_name = self.model_combo.currentData()
        if not model_name:
            QMessageBox.warning(self, "警告", "请选择一个模型")
            return
        
        stock_code = self.stock_combo.currentData()
        if not stock_code:
            QMessageBox.warning(self, "警告", "请选择一只股票进行预测")
            return
        
        # 判断模型类型
        is_lstm_model = model_name.startswith('lstm_') or model_name.startswith('gru_') or model_name.startswith('transformer_')
        is_multi_model = model_name.startswith('ppo_multi')
        
        self.log(f"\n{'='*50}")
        self.log(f"开始预测: {stock_code}")
        self.log(f"使用模型: {model_name}")
        if is_lstm_model:
            self.log("模型类型: LSTM/GRU 序列模型")
        elif is_multi_model:
            self.log("模型类型: MLP 多股票模型")
        else:
            self.log("模型类型: MLP 单股票模型")
        self.log(f"{'='*50}\n")
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        
        python_exe = sys.executable
        
        if is_lstm_model:
            # 使用 LSTM 预测脚本
            self.process.finished.connect(
                lambda exit_code, status: self.lstm_prediction_finished(exit_code, stock_code, model_name)
            )
            args = [
                self.predict_lstm_script,
                "--stock_code", stock_code,
                "--model_name", model_name,
                "--buy_rate", str(self.buy_comm_spin.value()),
                "--buy_min", str(self.buy_min_spin.value()),
                "--sell_rate", str(self.sell_comm_spin.value()),
                "--sell_min", str(self.sell_min_spin.value()),
                "--stamp_duty", str(self.stamp_spin.value())
            ]
        elif is_multi_model:
            # 使用多股票预测脚本
            self.process.finished.connect(
                lambda exit_code, status: self.multi_prediction_finished(exit_code, stock_code)
            )
            args = [
                self.predict_multi_script,
                "--stock_code", stock_code,
                "--model_name", model_name,
                "--buy_rate", str(self.buy_comm_spin.value()),
                "--buy_min", str(self.buy_min_spin.value()),
                "--sell_rate", str(self.sell_comm_spin.value()),
                "--sell_min", str(self.sell_min_spin.value()),
                "--stamp_duty", str(self.stamp_spin.value())
            ]
        else:
            # 单股票预测脚本
            self.process.finished.connect(
                lambda exit_code, status: self.prediction_finished(exit_code, stock_code)
            )
            args = [
                self.predict_script,
                "--stock_code", stock_code,
                "--buy_rate", str(self.buy_comm_spin.value()),
                "--buy_min", str(self.buy_min_spin.value()),
                "--sell_rate", str(self.sell_comm_spin.value()),
                "--sell_min", str(self.sell_min_spin.value()),
                "--stamp_duty", str(self.stamp_spin.value())
            ]
        
        self.process.start(python_exe, args)
        self.update_ui_state(running=True)

    def start_batch_prediction(self):
        """批量预测"""
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "警告", "已有进程在运行中")
            return
        
        model_name = self.model_combo.currentData()
        if not model_name:
            QMessageBox.warning(self, "警告", "请选择一个模型")
            return
        
        # 弹出对话框让用户输入要预测的股票代码
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "批量预测", 
            "请输入要预测的股票代码（用逗号分隔）:\n例如: 000001,000002,600000",
            QLineEdit.EchoMode.Normal,
            "000001,000002,600000,600036,601318"
        )
        
        if not ok or not text.strip():
            return
        
        stock_codes = text.strip()
        
        self.log(f"\n{'='*50}")
        self.log(f"开始批量预测")
        self.log(f"股票: {stock_codes}")
        self.log(f"使用模型: {model_name}")
        self.log(f"{'='*50}\n")
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.finished.connect(self.batch_prediction_finished)
        
        python_exe = sys.executable
        args = [
            self.predict_multi_script,
            "--stock_codes", stock_codes,
            "--model_name", model_name,
            "--buy_rate", str(self.buy_comm_spin.value()),
            "--buy_min", str(self.buy_min_spin.value()),
            "--sell_rate", str(self.sell_comm_spin.value()),
            "--sell_min", str(self.sell_min_spin.value()),
            "--stamp_duty", str(self.stamp_spin.value())
        ]
        
        self.process.start(python_exe, args)
        self.update_ui_state(running=True)

    def stop_process(self):
        """停止进程"""
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.log("\n--- 进程已被用户停止 ---\n")

    def handle_stdout(self):
        """处理标准输出"""
        data = self.process.readAllStandardOutput().data()
        try:
            text = data.decode('utf-8', errors='replace')
            self.log_text.insertPlainText(text)
            sb = self.log_text.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass

    def process_finished(self, exit_code, exit_status=0):
        """进程结束"""
        self.update_ui_state(running=False)
        if exit_code == 0:
            self.log("\n--- 进程成功完成 ---\n")
            # 刷新模型列表
            self.load_available_models()
        else:
            self.log(f"\n--- 进程失败，退出码: {exit_code} ---\n")

    def prediction_finished(self, exit_code, stock_code):
        """单股票预测完成"""
        self.process_finished(exit_code, 0)
        if exit_code == 0:
            image_path = os.path.join(self.output_dir, f"prediction_plot_{stock_code}.png")
            self.load_result_image(image_path)

    def multi_prediction_finished(self, exit_code, stock_code):
        """多股票模型预测完成"""
        self.process_finished(exit_code, 0)
        if exit_code == 0:
            image_path = os.path.join(self.output_dir, f"multi_model_prediction_{stock_code}.png")
            self.load_result_image(image_path)

    def lstm_prediction_finished(self, exit_code, stock_code, model_name):
        """LSTM/GRU模型预测完成"""
        self.process_finished(exit_code, 0)
        if exit_code == 0:
            image_path = os.path.join(self.output_dir, f"lstm_prediction_{stock_code}_{model_name}.png")
            self.load_result_image(image_path)

    def batch_prediction_finished(self, exit_code, exit_status=0):
        """批量预测完成"""
        self.process_finished(exit_code, exit_status)
        if exit_code == 0:
            self.log("\n批量预测完成！结果已保存到 output 目录。\n")

    def load_result_image(self, image_path):
        """加载结果图片"""
        if os.path.exists(image_path):
            self.current_image_path = image_path
            pixmap = QPixmap(image_path)
            self.plot_label.set_image(pixmap)
            self.zoom_fit()
            self.log(f"已加载策略预测结果图: {image_path}")
        else:
            self.plot_label.setText(f"未找到策略预测结果图:\n{image_path}")
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
        """更新UI状态"""
        self.btn_train.setEnabled(not running)
        self.btn_predict.setEnabled(not running)
        self.btn_batch_predict.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.stock_combo.setEnabled(not running)
        self.timesteps_spin.setEnabled(not running)
        self.num_envs_spin.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.single_mode_radio.setEnabled(not running)
        self.multi_mode_radio.setEnabled(not running)
