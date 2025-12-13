# styles.py - 应用程序样式表

DARK_THEME = """
QMainWindow {
    background-color: #1e1e1e;
}
QWidget {
    background-color: #1e1e1e;
    color: #ffffff;
}
QMenuBar {
    background-color: #2d2d2d;
    color: #ffffff;
    padding: 2px;
}
QMenuBar::item:selected {
    background-color: #3c3c3c;
}
QMenu {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3c3c3c;
}
QMenu::item:selected {
    background-color: #0078d4;
}
QToolBar {
    background-color: #2d2d2d;
    border: none;
    spacing: 5px;
    padding: 5px;
}
QStatusBar {
    background-color: #007acc;
    color: #ffffff;
}
QGroupBox {
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}
QCheckBox {
    color: #ffffff;
    spacing: 5px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QComboBox {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 5px;
    min-width: 100px;
}
QComboBox:hover {
    border-color: #0078d4;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    color: #ffffff;
    selection-background-color: #0078d4;
}
QPushButton {
    background-color: #0078d4;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 8px 16px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #1a8cdb;
}
QPushButton:pressed {
    background-color: #005a9e;
}
QDateEdit {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 5px;
}
QSplitter::handle {
    background-color: #3c3c3c;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSplitter::handle:vertical {
    height: 2px;
}

/* Tab Widget */
QTabWidget::pane {
    border: 1px solid #3c3c3c;
    background-color: #1e1e1e;
}
QTabBar::tab {
    background-color: #2d2d2d;
    color: #b0b0b0;
    padding: 8px 20px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #ffffff;
    border-bottom: 2px solid #0078d4;
}
QTabBar::tab:hover {
    background-color: #3c3c3c;
    color: #ffffff;
}

/* SpinBox & DoubleSpinBox */
QSpinBox, QDoubleSpinBox {
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 20px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: #3c3c3c;
    border: none;
    width: 16px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #505050;
}

/* TableWidget */
QTableWidget {
    background-color: #1e1e1e;
    color: #ffffff;
    gridline-color: #3c3c3c;
    border: 1px solid #3c3c3c;
}
QHeaderView::section {
    background-color: #2d2d2d;
    color: #ffffff;
    padding: 5px;
    border: 1px solid #3c3c3c;
}
QTableWidget::item:selected {
    background-color: #0078d4;
}
QTableWidget::item {
    padding: 5px;
}
"""
