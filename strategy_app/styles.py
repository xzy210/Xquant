# -*- coding: utf-8 -*-
"""
应用程序样式表定义
"""

DARK_THEME_QSS = """
/* 全局设置 */
QWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 14px;
    selection-background-color: #264f78;
    selection-color: #ffffff;
}

/* 主窗口 */
QMainWindow {
    background-color: #1e1e1e;
}

/* 按钮 */
QPushButton {
    background-color: #3c3c3c;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    color: #ffffff;
    padding: 6px 12px;
    min-height: 20px;
}

QPushButton:hover {
    background-color: #4c4c4c;
    border: 1px solid #4c4c4c;
}

QPushButton:pressed {
    background-color: #252526;
    border: 1px solid #0078d4;
}

QPushButton:disabled {
    background-color: #2d2d2d;
    color: #6e6e6e;
    border: 1px solid #2d2d2d;
}

/* 主要操作按钮 (蓝色) */
QPushButton[class="primary"] {
    background-color: #0078d4;
    border: 1px solid #0078d4;
}

QPushButton[class="primary"]:hover {
    background-color: #106ebe;
    border: 1px solid #106ebe;
}

QPushButton[class="primary"]:pressed {
    background-color: #005a9e;
}

/* 成功按钮 (绿色) */
QPushButton[class="success"] {
    background-color: #107c10;
    border: 1px solid #107c10;
}

QPushButton[class="success"]:hover {
    background-color: #0e6e0e;
    border: 1px solid #0e6e0e;
}

QPushButton[class="success"]:pressed {
    background-color: #0c5d0c;
}

/* 危险按钮 (红色) */
QPushButton[class="danger"] {
    background-color: #d13438;
    border: 1px solid #d13438;
}

QPushButton[class="danger"]:hover {
    background-color: #a4262c;
    border: 1px solid #a4262c;
}

QPushButton[class="danger"]:pressed {
    background-color: #8a1f24;
}

/* 警告按钮 (橙色) */
QPushButton[class="warning"] {
    background-color: #d83b01;
    border: 1px solid #d83b01;
}

QPushButton[class="warning"]:hover {
    background-color: #c23500;
    border: 1px solid #c23500;
}

QPushButton[class="warning"]:pressed {
    background-color: #a92e00;
}

/* 信息按钮 (紫色) */
QPushButton[class="info"] {
    background-color: #8764b8;
    border: 1px solid #8764b8;
}

QPushButton[class="info"]:hover {
    background-color: #7a5aa8;
    border: 1px solid #7a5aa8;
}

QPushButton[class="info"]:pressed {
    background-color: #6b4e94;
}

/* 输入框 */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {
    background-color: #252526;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    color: #d4d4d4;
    padding: 4px;
    selection-background-color: #264f78;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #0078d4;
}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    background-color: #2d2d2d;
    color: #6e6e6e;
}

/* 下拉框 */
QComboBox {
    background-color: #252526;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 4px;
    min-height: 20px;
}

QComboBox:hover {
    border: 1px solid #4c4c4c;
}

QComboBox:on {
    border: 1px solid #0078d4;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left-width: 0px;
    border-top-right-radius: 3px;
    border-bottom-right-radius: 3px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 2px solid transparent;
    border-right: 2px solid transparent;
    border-top: 2px solid #d4d4d4;
    width: 8px;
    height: 8px;
    margin-right: 5px;
}

QComboBox QAbstractItemView {
    background-color: #252526;
    border: 1px solid #3c3c3c;
    selection-background-color: #264f78;
    selection-color: #ffffff;
    outline: none;
}

/* 列表和表格 */
QTableWidget, QListWidget, QTreeWidget {
    background-color: #1e1e1e;
    border: 1px solid #3c3c3c;
    gridline-color: #3c3c3c;
    selection-background-color: #264f78;
    selection-color: #ffffff;
    outline: none;
}

QTableWidget::item, QListWidget::item, QTreeWidget::item {
    padding: 4px;
}

QTableWidget::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {
    background-color: #264f78;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #252526;
    color: #d4d4d4;
    padding: 6px;
    border: none;
    border-right: 1px solid #3c3c3c;
    border-bottom: 1px solid #3c3c3c;
    font-weight: bold;
}

QHeaderView::section:checked {
    background-color: #2d2d2d;
}

/* 标签页 */
QTabWidget::pane {
    border: 1px solid #3c3c3c;
    background-color: #1e1e1e;
    top: -1px;
}

QTabBar::tab {
    background-color: #2d2d2d;
    color: #d4d4d4;
    padding: 8px 20px;
    border: 1px solid #3c3c3c;
    border-bottom: none;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #ffffff;
    border-bottom: 1px solid #1e1e1e;
}

QTabBar::tab:hover:!selected {
    background-color: #3c3c3c;
}

/* 分组框 */
QGroupBox {
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    margin-top: 24px;
    padding-top: 10px;
    font-weight: bold;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 5px;
    left: 10px;
    color: #0078d4;
}

/* 滚动条 */
QScrollBar:vertical {
    border: none;
    background-color: #1e1e1e;
    width: 12px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: #424242;
    min-height: 20px;
    border-radius: 6px;
    margin: 2px;
}

QScrollBar::handle:vertical:hover {
    background-color: #686868;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background-color: #1e1e1e;
    height: 12px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background-color: #424242;
    min-width: 20px;
    border-radius: 6px;
    margin: 2px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #686868;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* 菜单栏 */
QMenuBar {
    background-color: #1e1e1e;
    color: #d4d4d4;
    border-bottom: 1px solid #3c3c3c;
}

QMenuBar::item {
    padding: 6px 10px;
    background-color: transparent;
}

QMenuBar::item:selected {
    background-color: #3c3c3c;
}

QMenu {
    background-color: #252526;
    border: 1px solid #3c3c3c;
    padding: 4px;
}

QMenu::item {
    padding: 6px 24px;
    color: #d4d4d4;
}

QMenu::item:selected {
    background-color: #0078d4;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background-color: #3c3c3c;
    margin: 4px 0;
}

/* 工具栏 */
QToolBar {
    background-color: #1e1e1e;
    border-bottom: 1px solid #3c3c3c;
    spacing: 6px;
    padding: 4px;
}

QToolBar::separator {
    width: 1px;
    background-color: #3c3c3c;
    margin: 0 6px;
}

/* 状态栏 */
QStatusBar {
    background-color: #0078d4;
    color: #ffffff;
    border-top: 1px solid #3c3c3c;
}

QStatusBar::item {
    border: none;
}

/* 复选框和单选框 */
QCheckBox, QRadioButton {
    spacing: 8px;
    color: #d4d4d4;
}

QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    background-color: #252526;
    border: 1px solid #3c3c3c;
    border-radius: 2px;
}

QCheckBox::indicator:checked {
    background-color: #0078d4;
    border: 1px solid #0078d4;
    image: url(none); /* 这里可以用图标，暂时用颜色区分 */
}

QCheckBox::indicator:checked:after {
    /* 模拟勾选 */
    content: "";
    position: absolute;
    left: 5px;
    top: 2px;
    width: 4px;
    height: 8px;
    border: solid white;
    border-width: 0 2px 2px 0;
    transform: rotate(45deg);
}

QRadioButton::indicator {
    border-radius: 8px;
}

QRadioButton::indicator:checked {
    background-color: #0078d4;
    border: 1px solid #0078d4;
}

/* 分割器 */
QSplitter::handle {
    background-color: #3c3c3c;
}

QSplitter::handle:hover {
    background-color: #0078d4;
}

/* 进度条 */
QProgressBar {
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    text-align: center;
    background-color: #252526;
    color: #ffffff;
}

QProgressBar::chunk {
    background-color: #0078d4;
    border-radius: 3px;
}

/* 提示框 */
QToolTip {
    background-color: #252526;
    color: #d4d4d4;
    border: 1px solid #3c3c3c;
    padding: 4px;
}

/* 高亮文本 */
QLabel[class="highlight"] {
    color: #0078d4;
    font-weight: bold;
}

/* 成功文本 */
QLabel[class="success"] {
    color: #107c10;
}

/* 错误文本 */
QLabel[class="error"] {
    color: #d13438;
}

/* 警告文本 */
QLabel[class="warning"] {
    color: #d83b01;
}

/* 状态文本颜色 */
QLabel[class="status-pending"] {
    color: #888888;
}
QLabel[class="status-processing"] {
    color: #ffaa00;
}
QLabel[class="status-info"] {
    color: #00bfff;
}
QLabel[class="status-error"] {
    color: #ff4444;
}
QLabel[class="status-success"] {
    color: #00da3c;
}
QLabel[class="status-normal"] {
    color: #ffffff;
}

/* 字体样式 */
QLabel[class="font-mono"] {
    font-family: Consolas, monospace;
}
QTextEdit[class="font-mono"] {
    font-family: Consolas, monospace;
}

/* 标题样式 */
QLabel[class="section-title"] {
    font-size: 16px;
    font-weight: bold;
    padding: 5px;
}

/* 描述文本 */
QLabel[class="description"] {
    color: #888888;
    font-size: 12px;
    padding: 5px;
}
QLabel[class="description-italic"] {
    color: #888888;
    font-style: italic;
    margin: 5px 0;
}

/* 欢迎页面标题 */
QLabel[class="welcome-title"] {
    font-size: 32px;
    font-weight: bold;
    color: #0078d4;
    margin-bottom: 20px;
}

/* 欢迎页面副标题 */
QLabel[class="welcome-subtitle"] {
    font-size: 18px;
    color: #888888;
    margin-bottom: 40px;
}

/* 欢迎页面大按钮 */
QPushButton[class="welcome-btn"] {
    font-size: 14px;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px;
}

QPushButton[class="welcome-btn-primary"] {
    background-color: #0078d4;
}
QPushButton[class="welcome-btn-primary"]:hover {
    background-color: #106ebe;
}

QPushButton[class="welcome-btn-success"] {
    background-color: #107c10;
}
QPushButton[class="welcome-btn-success"]:hover {
    background-color: #0e6e0e;
}

QPushButton[class="welcome-btn-purple"] {
    background-color: #8764b8;
}
QPushButton[class="welcome-btn-purple"]:hover {
    background-color: #7a5aa8;
}

QPushButton[class="welcome-btn-orange"] {
    background-color: #d83b01;
}
QPushButton[class="welcome-btn-orange"]:hover {
    background-color: #c23500;
}

QPushButton[class="welcome-btn-yellow"] {
    background-color: #ffb900;
    color: #333333;
}
QPushButton[class="welcome-btn-yellow"]:hover {
    background-color: #e6a700;
}
"""

# Python 颜色常量 (用于代码中直接引用)
class Colors:
    PRIMARY = "#0078d4"
    SUCCESS = "#107c10"
    DANGER = "#d13438"
    WARNING = "#d83b01"
    INFO = "#8764b8"
    
    TEXT_PRIMARY = "#d4d4d4"
    TEXT_SECONDARY = "#888888"
    TEXT_WHITE = "#ffffff"
    
    # 涨跌颜色
    UP_RED = "#ec0000"
    DOWN_GREEN = "#00da3c"
    FLAT_YELLOW = "#ffcc00"
    
    # 状态颜色
    STATUS_PROCESSING = "#ffaa00"
    STATUS_INFO = "#00bfff"
    STATUS_ERROR = "#ff4444"
    STATUS_SUCCESS = "#00da3c"
    
    # 背景色
    BG_DARK = "#1e1e1e"
    BG_LIGHTER = "#252526"
    BG_SELECTION = "#264f78"