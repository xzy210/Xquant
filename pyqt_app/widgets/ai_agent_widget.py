import os
import json
import logging
import base64
import mimetypes
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, 
    QPushButton, QLabel, QComboBox, QGroupBox, QSplitter,
    QMessageBox, QScrollArea, QFrame, QSizePolicy, QFormLayout,
    QDialog, QFileDialog, QToolButton, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt6.QtGui import QIcon, QFont, QColor, QTextCursor, QAction, QPixmap
from openai import OpenAI

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MessageWidget(QFrame):
    """自定义消息气泡组件"""
    def __init__(self, role, content, parent=None, theme="light"):
        super().__init__(parent)
        self.role = role
        self.theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setup_ui(content)

    def setup_ui(self, content):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 角色标签
        role_label = QLabel("我" if self.role == "user" else "AI")
        role_label.setStyleSheet(f"""
            font-weight: bold; 
            color: {"#4CAF50" if self.role == "user" else "#2196F3"};
            font-size: 12px;
            background: transparent;
        """)
        layout.addWidget(role_label)
        
        # 内容文本
        self.text_label = QLabel(content)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        self.update_style()
        
        layout.addWidget(self.text_label)

    def update_style(self):
        if self.role == "user":
            bg_color = "#e1f5fe" if self.theme == "light" else "#2d2d2d"
            margin = "margin-left: 40px; margin-right: 5px;"
        else:
            bg_color = "#f5f5f5" if self.theme == "light" else "#383838"
            margin = "margin-right: 40px; margin-left: 5px;"
            
        text_color = "#333333" if self.theme == "light" else "#ffffff"
        
        self.setStyleSheet(f"""
            MessageWidget {{
                background-color: {bg_color};
                border-radius: 8px;
                {margin}
                margin-top: 5px;
            }}
            QLabel {{
                color: {text_color};
                background: transparent;
            }}
        """)

    def update_text(self, content):
        self.text_label.setText(content)

class AISettingsDialog(QDialog):
    """模型设置对话框 - 支持每个模型独立配置"""
    def __init__(self, models, model_configs, current_system_prompt, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 模型设置")
        self.setMinimumWidth(450)
        self.models = models
        self.model_configs = model_configs.copy()
        self.system_prompt = current_system_prompt
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # 模型选择器（在设置对话框内切换要配置的模型）
        model_select_layout = QHBoxLayout()
        model_select_layout.addWidget(QLabel("配置模型:"))
        self.model_selector = QComboBox()
        self.model_selector.addItems(self.models)
        self.model_selector.currentTextChanged.connect(self.on_model_changed)
        model_select_layout.addWidget(self.model_selector)
        layout.addLayout(model_select_layout)
        
        # API 配置分组
        self.form_group = QGroupBox("模型特定配置")
        form_layout = QFormLayout(self.form_group)
        
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.textChanged.connect(self.on_input_changed)
        form_layout.addRow("API Key:", self.api_key_input)
        
        self.base_url_input = QLineEdit()
        self.base_url_input.textChanged.connect(self.on_input_changed)
        form_layout.addRow("Base URL:", self.base_url_input)
        
        layout.addWidget(self.form_group)
        
        # 全局配置
        global_group = QGroupBox("全局配置")
        global_layout = QFormLayout(global_group)
        self.system_prompt_input = QTextEdit(self.system_prompt)
        self.system_prompt_input.setMaximumHeight(100)
        global_layout.addRow("系统提示词:", self.system_prompt_input)
        layout.addWidget(global_group)
        
        # 初始化显示第一个模型的配置
        self.on_model_changed(self.model_selector.currentText())
        
        # 底部按钮
        btn_box = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        
        btn_box.addStretch()
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)

    def on_model_changed(self, model_name):
        """当设置对话框中的模型切换时，加载该模型的配置"""
        config = self.model_configs.get(model_name, {"api_key": "", "base_url": ""})
        self.api_key_input.setText(config.get("api_key", ""))
        self.base_url_input.setText(config.get("base_url", ""))
        self.form_group.setTitle(f"配置模型: {model_name}")

    def on_input_changed(self):
        """当输入框内容变化时，实时保存到内存中的 model_configs"""
        model_name = self.model_selector.currentText()
        self.model_configs[model_name] = {
            "api_key": self.api_key_input.text().strip(),
            "base_url": self.base_url_input.text().strip()
        }

    def get_config(self):
        return {
            "model_configs": self.model_configs,
            "system_prompt": self.system_prompt_input.toPlainText().strip()
        }

class ChatThread(QThread):
    """聊天后台线程，用于异步调用大模型 API"""
    message_received = pyqtSignal(str, bool)  # content, is_error
    finished_signal = pyqtSignal()

    def __init__(self, api_key, base_url, model, system_prompt, messages):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.system_prompt = system_prompt
        self.messages = messages

    def run(self):
        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            
            # 构建完整的对话消息
            full_messages = [{"role": "system", "content": self.system_prompt}]
            full_messages.extend(self.messages)
            
            response = client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                stream=True
            )
            
            full_content = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_content += content
                    self.message_received.emit(content, False)
            
            logger.info(f"AI Response finished. Length: {len(full_content)}")
            
        except Exception as e:
            logger.error(f"Chat error: {e}")
            self.message_received.emit(f"\n错误: {str(e)}", True)
        finally:
            self.finished_signal.emit()

class AttachmentThumbnail(QFrame):
    """附件缩略图组件"""
    deleted = pyqtSignal(str)  # 发送要删除的文件路径

    def __init__(self, file_path, parent=None, theme="light"):
        super().__init__(parent)
        self.file_path = file_path
        self.theme = theme
        self.setup_ui()

    def setup_ui(self):
        self.setFixedSize(80, 80)
        self.update_style()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 预览图或图标
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        mime_type, _ = mimetypes.guess_type(self.file_path)
        if mime_type and mime_type.startswith('image/'):
            pixmap = QPixmap(self.file_path)
            self.icon_label.setPixmap(pixmap.scaled(70, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            # 文本文件显示图标
            self.icon_label.setText("📄")
            self.icon_label.setStyleSheet("font-size: 24px;")
            
        layout.addWidget(self.icon_label)

        # 文件名
        self.name_label = QLabel(os.path.basename(self.file_path))
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)
        
        self.update_style() # 再次调用以设置子组件样式

        # 删除按钮 (X)
        self.del_btn = QPushButton("×", self)
        self.del_btn.setFixedSize(16, 16)
        self.del_btn.move(62, 2)
        self.del_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 150);
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #f44336;
            }
        """)
        self.del_btn.clicked.connect(lambda: self.deleted.emit(self.file_path))

    def update_style(self):
        if self.theme == "light":
            bg_color = "#eeeeee"
            border_color = "#cccccc"
            text_color = "#666666"
        else:
            bg_color = "#3c3c3c"
            border_color = "#555555"
            text_color = "#bbbbbb"
            
        self.setStyleSheet(f"""
            AttachmentThumbnail {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 4px;
            }}
            AttachmentThumbnail:hover {{
                border-color: #0078d4;
            }}
            QLabel {{
                color: {text_color};
                font-size: 9px;
            }}
        """)

    def mousePressEvent(self, event):
        """点击打开大图或内容查看"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.preview_content()

    def preview_content(self):
        mime_type, _ = mimetypes.guess_type(self.file_path)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"预览: {os.path.basename(self.file_path)}")
        dialog.setMinimumSize(600, 400)
        layout = QVBoxLayout(dialog)
        
        if mime_type and mime_type.startswith('image/'):
            label = QLabel()
            pixmap = QPixmap(self.file_path)
            label.setPixmap(pixmap.scaled(800, 600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scroll = QScrollArea()
            scroll.setWidget(label)
            layout.addWidget(scroll)
        else:
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            try:
                with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text_edit.setText(f.read())
            except Exception as e:
                text_edit.setText(f"无法读取文件: {e}")
            layout.addWidget(text_edit)
            
        dialog.exec()

class MessageInput(QTextEdit):
    """支持拖放和粘贴图片的自定义输入框"""
    files_dropped = pyqtSignal(list)
    image_pasted = pyqtSignal(QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            file_paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if file_paths:
                self.files_dropped.emit(file_paths)
                event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def insertFromMimeData(self, source):
        """处理粘贴事件，支持图片粘贴"""
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QPixmap):
                self.image_pasted.emit(image)
            else:
                # 某些情况下 imageData() 返回 QImage
                from PyQt6.QtGui import QImage
                if isinstance(image, QImage):
                    self.image_pasted.emit(QPixmap.fromImage(image))
            return
        
        # 处理文件路径粘贴 (例如在文件管理器中复制文件后在此处粘贴)
        if source.hasUrls():
            file_paths = [url.toLocalFile() for url in source.urls() if url.isLocalFile()]
            if file_paths:
                self.files_dropped.emit(file_paths)
                return

        super().insertFromMimeData(source)

class AIAgentWidget(QWidget):
    """智能体版块组件 - Cursor 风格优化版"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            "config", "ai_config.json"
        )
        self.chat_history = []
        self.current_ai_msg_widget = None
        self.attached_files = []  # 存储当前待发送的文件路径
        self.model_configs = {}  # 存储每个模型的 api_key 和 base_url
        self.system_prompt = "你是一个专业的股票投资顾问。"
        self.theme = "light" # 强制设为浅色
        self.setup_ui()
        self.load_config()

    def setup_ui(self):
        """初始化界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # --- 顶部导航栏 (Header) ---
        self.header = QFrame()
        self.header.setObjectName("Header")
        self.header.setFixedHeight(50)
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(15, 0, 15, 0)
        
        # 模型选择器
        self.model_label = QLabel("模型:")
        self.model_label.setObjectName("HeaderLabel")
        self.header_layout.addWidget(self.model_label)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("ModelCombo")
        self.model_combo.addItems([
            "deepseek-chat", 
            "deepseek-reasoner", 
            "gpt-4o", 
            "gpt-4o-mini",
            "gemini-3-pro-preview", 
            "gemini-3-flash-preview",
            "claude-3-5-sonnet"
        ])
        self.model_combo.setFixedWidth(180)
        self.header_layout.addWidget(self.model_combo)
        
        self.header_layout.addStretch()
        
        # 设置按钮
        self.settings_btn = QPushButton("⚙ 设置")
        self.settings_btn.setObjectName("HeaderBtn")
        self.settings_btn.setFlat(True)
        self.settings_btn.clicked.connect(self.open_settings)
        self.header_layout.addWidget(self.settings_btn)
        
        # 清空按钮
        self.clear_btn = QPushButton("🗑 清空")
        self.clear_btn.setObjectName("HeaderBtn")
        self.clear_btn.setFlat(True)
        self.clear_btn.clicked.connect(self.clear_chat)
        self.header_layout.addWidget(self.clear_btn)
        
        main_layout.addWidget(self.header)
        
        # --- 中间对话区域 (Scroll Area) ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("ScrollArea")
        self.scroll_area.setWidgetResizable(True)
        
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("ScrollContent")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setSpacing(10)
        self.scroll_layout.addStretch()
        
        self.scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll_area, stretch=10) # 给对话区域最大的权重
        
        # --- 底部输入区域 (Input Area) ---
        self.input_frame = QFrame()
        self.input_frame.setObjectName("InputFrame")
        input_vbox = QVBoxLayout(self.input_frame)
        input_vbox.setContentsMargins(10, 5, 10, 10)
        input_vbox.setSpacing(2) # 极小间距
        
        # 附件和工具栏 (尽可能调小)
        tool_layout = QHBoxLayout()
        tool_layout.setContentsMargins(0, 0, 0, 0)
        self.attach_btn = QToolButton()
        self.attach_btn.setObjectName("AttachBtn")
        self.attach_btn.setText("+ 附件")
        self.attach_btn.setToolTip("上传文件或图片")
        self.attach_btn.clicked.connect(self.on_attach_clicked)
        tool_layout.addWidget(self.attach_btn)
        
        tool_layout.addStretch()
        input_vbox.addLayout(tool_layout)

        # 附件预览区域 (缩略图列表)
        self.attachment_scroll = QScrollArea()
        self.attachment_scroll.setFixedHeight(95)
        self.attachment_scroll.setWidgetResizable(True)
        self.attachment_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.attachment_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.attachment_scroll.setStyleSheet("background-color: transparent; border: none;")
        self.attachment_scroll.setVisible(False) # 初始隐藏
        
        self.attachment_container = QWidget()
        self.attachment_layout = QHBoxLayout(self.attachment_container)
        self.attachment_layout.setContentsMargins(0, 5, 0, 5)
        self.attachment_layout.setSpacing(10)
        self.attachment_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        self.attachment_scroll.setWidget(self.attachment_container)
        input_vbox.addWidget(self.attachment_scroll)
        
        # 文本输入框 (使用自定义 MessageInput)
        input_hbox = QHBoxLayout()
        input_hbox.setContentsMargins(0, 0, 0, 0)
        self.message_input = MessageInput()
        self.message_input.setObjectName("MessageInput")
        self.message_input.files_dropped.connect(self.handle_files_dropped)
        self.message_input.image_pasted.connect(self.handle_image_pasted)
        self.message_input.setPlaceholderText("输入消息，或者直接拖入/粘贴图片或文件... (Ctrl+Enter 发送)")
        self.message_input.setMaximumHeight(120)
        input_hbox.addWidget(self.message_input)
        
        # 发送按钮
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("SendBtn")
        self.send_btn.setFixedWidth(60)
        self.send_btn.setFixedHeight(40)
        self.send_btn.clicked.connect(self.send_message)
        input_hbox.addWidget(self.send_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        
        input_vbox.addLayout(input_hbox)
        main_layout.addWidget(self.input_frame)
        
        # 初始应用主题样式
        self.update_widget_styles()
        
        # 快捷键
        self.message_input.installEventFilter(self)

    def on_attach_clicked(self):
        """点击附件按钮"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择附件", "", "图片和文件 (*.png *.jpg *.jpeg *.txt *.csv *.py);;所有文件 (*.*)"
        )
        if file_paths:
            self.add_attachments(file_paths)

    def handle_files_dropped(self, file_paths):
        """处理鼠标拖入的文件"""
        if file_paths:
            self.add_attachments(file_paths)
            logger.info(f"Dropped files: {file_paths}")

    def handle_image_pasted(self, pixmap):
        """处理剪切板粘贴的图片"""
        import tempfile
        import time
        
        # 创建临时文件保存图片
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, f"pasted_image_{int(time.time())}.png")
        
        if pixmap.save(file_path, "PNG"):
            self.add_attachments([file_path])
            logger.info(f"Pasted image saved to: {file_path}")

    def add_attachments(self, file_paths):
        """统一添加附件并显示缩略图"""
        for path in file_paths:
            if path not in self.attached_files:
                self.attached_files.append(path)
                thumb = AttachmentThumbnail(path, theme=self.theme)
                thumb.deleted.connect(self.remove_attachment)
                self.attachment_layout.addWidget(thumb)
        
        self.update_attachment_visibility()

    def remove_attachment(self, file_path):
        """移除特定附件"""
        if file_path in self.attached_files:
            self.attached_files.remove(file_path)
            
            # 从布局中移除对应的 widget
            for i in range(self.attachment_layout.count()):
                item = self.attachment_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), AttachmentThumbnail):
                    if item.widget().file_path == file_path:
                        item.widget().deleteLater()
                        break
        
        self.update_attachment_visibility()

    def update_attachment_visibility(self):
        """根据是否有附件决定预览区是否显示"""
        has_files = len(self.attached_files) > 0
        self.attachment_scroll.setVisible(has_files)

    def update_theme(self, theme_name):
        """设置主题并更新样式"""
        self.theme = theme_name
        self.update_widget_styles()
        
        # 更新已有的消息气泡主题
        for i in range(self.scroll_layout.count()):
            item = self.scroll_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), MessageWidget):
                item.widget().theme = theme_name
                item.widget().update_style()

    def update_widget_styles(self):
        """更新组件的具体样式表，强制覆盖全局主题"""
        if self.theme == "light":
            bg_main = "#ffffff"
            bg_panel = "#f8f9fa"
            bg_input = "#ffffff"
            text_main = "#333333"
            text_dim = "#666666"
            border_color = "#e0e0e0"
            combo_bg = "#ffffff"
            header_btn_hover = "#e9ecef"
        else:
            bg_main = "#1e1e1e"
            bg_panel = "#252526"
            bg_input = "#3c3c3c"
            text_main = "#ffffff"
            text_dim = "#aaaaaa"
            border_color = "#3c3c3c"
            combo_bg = "#3c3c3c"
            header_btn_hover = "#3c3c3c"

        # 强制设置背景色，并确保不继承父窗口的深色背景
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        style = f"""
            AIAgentWidget {{
                background-color: {bg_main};
                color: {text_main};
            }}
            QFrame#Header {{
                background-color: {bg_panel};
                border-bottom: 1px solid {border_color};
            }}
            QLabel#HeaderLabel {{
                color: {text_main};
                font-weight: bold;
                background: transparent;
            }}
            QComboBox#ModelCombo {{
                background-color: {combo_bg};
                color: {text_main};
                border: 1px solid {border_color};
                border-radius: 4px;
                padding: 2px 10px;
                min-width: 150px;
            }}
            QComboBox#ModelCombo::drop-down {{
                border: none;
            }}
            QComboBox#ModelCombo QAbstractItemView {{
                background-color: {combo_bg};
                color: {text_main};
                selection-background-color: #0078d4;
            }}
            QPushButton#HeaderBtn {{
                background-color: transparent;
                color: {text_dim};
                border: none;
                padding: 5px 10px;
                font-weight: normal;
            }}
            QPushButton#HeaderBtn:hover {{
                background-color: {header_btn_hover};
                color: {text_main};
                border-radius: 4px;
            }}
            QScrollArea#ScrollArea {{
                background-color: {bg_main};
                border: none;
            }}
            QWidget#ScrollContent {{
                background-color: {bg_main};
            }}
            QFrame#InputFrame {{
                background-color: {bg_panel};
                border-top: 1px solid {border_color};
            }}
            QToolButton#AttachBtn {{
                background-color: transparent;
                color: {text_dim};
                border: 1px solid {border_color};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }}
            QToolButton#AttachBtn:hover {{
                background-color: {header_btn_hover};
                color: {text_main};
            }}
            QTextEdit#MessageInput {{
                background-color: {bg_input};
                color: {text_main};
                border: 1px solid {border_color};
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
                line-height: 1.5;
            }}
            QPushButton#SendBtn {{
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                padding: 8px;
            }}
            QPushButton#SendBtn:hover {{
                background-color: #1a8cdb;
            }}
            QPushButton#SendBtn:disabled {{
                background-color: {border_color};
                color: {text_dim};
            }}
            QLabel {{
                background: transparent;
                color: {text_main};
            }}
        """
        self.setStyleSheet(style)

    def open_settings(self):
        """打开设置对话框"""
        models = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
        dialog = AISettingsDialog(models, self.model_configs, self.system_prompt, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_config = dialog.get_config()
            self.model_configs = new_config["model_configs"]
            self.system_prompt = new_config["system_prompt"]
            self.save_config()

    def load_config(self):
        """加载 API 配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.model_configs = config.get("model_configs", {})
                    self.system_prompt = config.get("system_prompt", "你是一个专业的股票投资顾问。")
                    
                    # 兼容旧版本配置
                    if not self.model_configs and "api_key" in config:
                        model = config.get("model", "deepseek-chat")
                        self.model_configs[model] = {
                            "api_key": config.get("api_key", ""),
                            "base_url": config.get("base_url", "")
                        }

                    # 设置当前模型
                    model = config.get("selected_model", config.get("model", ""))
                    index = self.model_combo.findText(model)
                    if index >= 0:
                        self.model_combo.setCurrentIndex(index)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

    def save_config(self):
        """保存 API 配置"""
        config = {
            "model_configs": self.model_configs,
            "selected_model": self.model_combo.currentText(),
            "system_prompt": self.system_prompt
        }
        
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def clear_chat(self):
        """清空对话历史"""
        self.chat_history = []
        # 清空布局中的所有消息组件
        for i in reversed(range(self.scroll_layout.count())):
            item = self.scroll_layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
        self.scroll_layout.addStretch()

    def append_to_display(self, role, content, is_new=True):
        """在显示区域添加消息组件"""
        if is_new:
            # 移除之前的弹簧
            for i in reversed(range(self.scroll_layout.count())):
                if self.scroll_layout.itemAt(i).spacerItem():
                    self.scroll_layout.removeItem(self.scroll_layout.itemAt(i))
                    break
            
            msg_widget = MessageWidget(role, content, theme=self.theme)
            self.scroll_layout.addWidget(msg_widget)
            self.scroll_layout.addStretch() # 重新添加弹簧
            
            if role == "assistant":
                self.current_ai_msg_widget = msg_widget
        else:
            # 流式追加更新
            if self.current_ai_msg_widget:
                current_text = self.current_ai_msg_widget.text_label.text()
                self.current_ai_msg_widget.update_text(current_text + content)
        
        # 自动滚动到底部
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

    def send_message(self):
        """发送消息并获取回复"""
        content = self.message_input.toPlainText().strip()
        if not content and not self.attached_files:
            return
            
        model = self.model_combo.currentText()
        config = self.model_configs.get(model, {})
        api_key = config.get("api_key", "")
        base_url = config.get("base_url", "")
        
        if not api_key:
            QMessageBox.warning(self, "警告", f"请先在设置中配置模型 {model} 的 API Key")
            self.open_settings()
            return
            
        # 准备消息内容 (支持多模态)
        message_content = []
        if content:
            message_content.append({"type": "text", "text": content})
            
        # 处理附件
        for file_path in self.attached_files:
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type and mime_type.startswith('image/'):
                # 图片附件 - Base64 编码
                try:
                    with open(file_path, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                        message_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{encoded_string}"
                            }
                        })
                except Exception as e:
                    logger.error(f"Failed to encode image {file_path}: {e}")
            else:
                # 文本附件 - 读取内容并拼接到文本中
                try:
                    with open(file_path, "r", encoding='utf-8', errors='ignore') as f:
                        file_text = f.read()
                        file_name = os.path.basename(file_path)
                        text_part = f"\n\n[文件附件: {file_name}]\n{file_text}"
                        # 如果 message_content 里已有文本，追加到第一个文本部分
                        if message_content and message_content[0]["type"] == "text":
                            message_content[0]["text"] += text_part
                        else:
                            message_content.insert(0, {"type": "text", "text": text_part})
                except Exception as e:
                    logger.error(f"Failed to read file {file_path}: {e}")

        # 如果只有文本且没有图片，可以简化为纯字符串结构以兼容某些模型
        final_content = message_content
        has_images = any(part["type"] == "image_url" for part in message_content)
        if not has_images and len(message_content) == 1 and message_content[0]["type"] == "text":
            final_content = message_content[0]["text"]

        # 禁用输入
        self.message_input.clear()
        # 清空已选附件及其 UI
        self.attached_files = []
        for i in reversed(range(self.attachment_layout.count())):
            item = self.attachment_layout.itemAt(i)
            if item.widget():
                item.widget().deleteLater()
        self.attachment_scroll.setVisible(False)
        
        self.message_input.setEnabled(False)
        self.send_btn.setEnabled(False)
        
        # 添加到显示和历史
        display_text = content if content else "[发送了附件]"
        self.append_to_display("user", display_text)
        self.chat_history.append({"role": "user", "content": final_content})
        
        # 准备 AI 回复组件
        self.append_to_display("assistant", "", is_new=True)
        
        # 启动后台线程
        self.chat_thread = ChatThread(
            api_key, base_url, model, self.system_prompt, self.chat_history
        )
        self.chat_thread.message_received.connect(self.on_message_received)
        self.chat_thread.finished_signal.connect(self.on_chat_finished)
        self.chat_thread.start()

    def on_message_received(self, content, is_error):
        """处理流式返回的内容"""
        self.append_to_display("assistant", content, is_new=False)
        if not is_error:
            if not self.chat_history or self.chat_history[-1]["role"] != "assistant":
                self.chat_history.append({"role": "assistant", "content": content})
            else:
                self.chat_history[-1]["content"] += content

    def on_chat_finished(self):
        """对话结束"""
        self.message_input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.message_input.setFocus()
        self.save_config() # 自动保存最后选中的模型

    def eventFilter(self, obj, event):
        """处理输入框的 Ctrl+Enter 发送快捷键"""
        if obj is self.message_input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self.send_message()
                return True
        return super().eventFilter(obj, event)

