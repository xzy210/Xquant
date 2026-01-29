import os
import json
import logging
import base64
import mimetypes
import httpx
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, 
    QPushButton, QLabel, QComboBox, QGroupBox, QSplitter,
    QMessageBox, QScrollArea, QFrame, QSizePolicy, QFormLayout,
    QDialog, QFileDialog, QToolButton, QMenu, QCheckBox,
    QListWidget, QListWidgetItem, QPlainTextEdit, QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt6.QtGui import QIcon, QFont, QColor, QTextCursor, QAction, QPixmap
from openai import OpenAI
from google import genai as google_genai
from google.genai import types as genai_types
import pandas as pd

# Import stock analyzer service
try:
    from services.stock_analyzer import get_analyzer, StockAnalyzer
except ImportError:
    from pyqt_app.services.stock_analyzer import get_analyzer, StockAnalyzer

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

    def __init__(self, api_key, base_url, model, system_prompt, messages, use_web_search=False):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.system_prompt = system_prompt
        self.messages = messages
        self.use_web_search = use_web_search

    def run(self):
        try:
            model_lower = self.model.lower()
            # 如果是 Gemini 模型且开启了联网搜索，优先使用 Google 官方 SDK
            if "gemini" in model_lower and self.use_web_search:
                self.run_gemini_native()
            # 如果是 Kimi 模型，使用 Kimi API（支持 $web_search 联网功能）
            elif "kimi" in model_lower:
                self.run_kimi_api()
            else:
                self.run_openai_compatible()
                
        except Exception as e:
            logger.error(f"Chat error: {e}")
            self.message_received.emit(f"\n错误: {str(e)}", True)
        finally:
            self.finished_signal.emit()

    def run_gemini_native(self):
        """使用 Google Generative AI SDK 调用 Gemini (支持联网搜索)"""
        client = google_genai.Client(api_key=self.api_key)
        
        model_name = self.model.lower()

        # 定义工具：Google Search (内置工具)
        google_search_tool = genai_types.Tool(
            google_search=genai_types.GoogleSearch()
        )
        
        config = genai_types.GenerateContentConfig(
            tools=[google_search_tool],
            system_instruction=self.system_prompt.strip() if self.system_prompt else None
        )

        # 构建历史记录和新消息
        contents = []
        
        # 转换历史记录
        for m in self.messages[:-1]:
            role = m["role"]
            content = m["content"]
            
            parts = []
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        text_val = part.get("text", "").strip()
                        if text_val:
                            parts.append(genai_types.Part(text=text_val))
            elif isinstance(content, str):
                if content.strip():
                    parts.append(genai_types.Part(text=content))
            
            if parts:
                # 使用 Content 对象
                contents.append(genai_types.Content(role=role, parts=parts))

        # 添加当前用户消息
        last_msg = self.messages[-1]["content"]
        
        if isinstance(last_msg, str):
            if last_msg.strip():
                contents.append(genai_types.Content(
                    role="user", 
                    parts=[genai_types.Part(text=last_msg.strip())]
                ))
        elif isinstance(last_msg, list):
            parts = []
            for part in last_msg:
                if part.get("type") == "text":
                    text_val = part.get("text", "").strip()
                    if text_val:
                        parts.append(genai_types.Part(text=text_val))
                elif part.get("type") == "image_url":
                    try:
                        url = part.get("image_url", {}).get("url", "")
                        if "," in url:
                            img_data = base64.b64decode(url.split(",")[1])
                            parts.append(genai_types.Part(
                                inline_data=genai_types.Blob(
                                    mime_type="image/jpeg",
                                    data=img_data
                                )
                            ))
                    except Exception as e:
                        logger.error(f"Failed to decode image: {e}")
            
            if parts:
                contents.append(genai_types.Content(role="user", parts=parts))

        # 确保至少有一个消息
        if not contents:
            contents.append(genai_types.Content(
                role="user", 
                parts=[genai_types.Part(text=" ")]
            ))

        try:
            # 发送消息并获取流式响应
            full_content = ""
            
            response = client.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=config
            )
            
            for chunk in response:
                if chunk.text:
                    self.message_received.emit(chunk.text, False)
                    full_content += chunk.text
            
            logger.info(f"Gemini Native Response finished. Length: {len(full_content)}")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Gemini Error: {error_msg}")
            self.message_received.emit(f"\nAPI 调用错误: {error_msg}", True)

    def run_kimi_api(self):
        """使用 Kimi K2.5 API 调用（支持多模态图片、联网搜索和 thinking 模式）
        
        根据官方文档: https://platform.moonshot.ai/docs/guide/kimi-k2-5-quickstart
        - 模型名称: kimi-k2.5
        - 支持多模态: 图片使用 image_url 类型，base64 编码
        - 支持联网搜索: 使用 $web_search 内置工具
        - 支持 thinking 模式: 默认启用，需要在 assistant 消息中保留 reasoning_content
        
        注意：使用 httpx 直接发送请求，确保 reasoning_content 字段能正确传递
        （OpenAI SDK 会过滤掉不认识的字段）
        """
        base_url = self.base_url if self.base_url else "https://api.moonshot.cn/v1"
        api_endpoint = f"{base_url}/chat/completions"
        
        logger.info(f"[Kimi K2.5] Starting API call, model={self.model}, use_web_search={self.use_web_search}")
        
        # 构建完整的对话消息
        full_messages = []
        
        # 构建系统提示词（Kimi K2.5 要求 system 消息内容不能为空）
        system_content = self.system_prompt.strip() if self.system_prompt else "你是 Kimi，由 Moonshot AI 提供的智能助手。"
        
        # 如果开启联网搜索，注入引导语让模型主动使用联网搜索工具
        if self.use_web_search:
            web_search_guide = (
                "【重要】用户已开启联网搜索功能。请在回答问题时主动使用联网搜索工具获取最新、最准确的信息。"
                "尤其对于涉及时效性内容（如新闻、股票行情、天气、体育赛事、最新政策等）的问题，"
                "必须先进行联网搜索再回答，不要依赖训练数据中的旧信息。"
            )
            system_content = f"{web_search_guide}\n\n{system_content}"
        
        full_messages.append({"role": "system", "content": system_content})
        
        # 处理消息历史，确保多模态消息格式正确（Kimi K2.5 使用 image_url 类型）
        for msg in self.messages:
            if msg["role"] == "user" and isinstance(msg.get("content"), list):
                # 多模态消息：转换为 Kimi K2.5 格式
                kimi_content = []
                for part in msg["content"]:
                    if part.get("type") == "text":
                        kimi_content.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        kimi_content.append({
                            "type": "image_url",
                            "image_url": {"url": part.get("image_url", {}).get("url", "")}
                        })
                full_messages.append({"role": "user", "content": kimi_content})
            else:
                full_messages.append(msg)
        
        # 构建工具列表
        tools = None
        if self.use_web_search:
            tools = [{
                "type": "builtin_function",
                "function": {"name": "$web_search"}
            }]
            logger.info(f"[Kimi K2.5] Web search enabled, tools={tools}")
        
        # HTTP 请求头
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        try:
            # 循环处理工具调用（联网搜索需要多轮交互）
            max_tool_rounds = 5
            current_round = 0
            has_reasoning_content = False  # 跟踪是否收到过 reasoning_content
            
            while current_round < max_tool_rounds:
                current_round += 1
                
                # 构建请求体
                request_body = {
                    "model": self.model,
                    "messages": full_messages,
                    "stream": True
                }
                if tools:
                    request_body["tools"] = tools
                    request_body["tool_choice"] = "auto"
                    # 如果之前没有收到 reasoning_content，禁用 thinking 模式
                    # 根据官方文档：使用工具时如果模型没有产生 reasoning_content，后续请求需要禁用 thinking
                    if current_round > 1 and not has_reasoning_content:
                        request_body["thinking"] = {"type": "disabled"}
                        logger.info(f"[Kimi K2.5] Thinking disabled for round {current_round} (no reasoning_content received)")
                
                logger.info(f"[Kimi K2.5] Round {current_round}, messages count={len(full_messages)}")
                
                full_content = ""
                tool_calls_data = {}
                finish_reason = None
                reasoning_content = ""
                
                # 使用 httpx 发送流式请求
                with httpx.Client(timeout=120.0) as client:
                    with client.stream("POST", api_endpoint, headers=headers, json=request_body) as response:
                        if response.status_code != 200:
                            error_text = response.read().decode('utf-8')
                            raise Exception(f"HTTP {response.status_code}: {error_text}")
                        
                        # 处理 SSE 流式响应
                        for line in response.iter_lines():
                            if not line or not line.startswith("data: "):
                                continue
                            
                            data_str = line[6:]  # 去掉 "data: " 前缀
                            if data_str == "[DONE]":
                                break
                            
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            
                            if not chunk.get("choices"):
                                continue
                            
                            choice = chunk["choices"][0]
                            delta = choice.get("delta", {})
                            finish_reason = choice.get("finish_reason")
                            
                            # 处理正常内容
                            if delta.get("content"):
                                content = delta["content"]
                                full_content += content
                                self.message_received.emit(content, False)
                            
                            # 收集 reasoning_content (thinking 模式)
                            if delta.get("reasoning_content"):
                                reasoning_content += delta["reasoning_content"]
                                has_reasoning_content = True  # 标记收到了 reasoning_content
                            
                            # 收集工具调用信息
                            if delta.get("tool_calls"):
                                for tool_call in delta["tool_calls"]:
                                    idx = tool_call.get("index", 0)
                                    if idx not in tool_calls_data:
                                        tool_calls_data[idx] = {
                                            "id": "",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""}
                                        }
                                    if tool_call.get("id"):
                                        tool_calls_data[idx]["id"] = tool_call["id"]
                                    if tool_call.get("type"):
                                        tool_calls_data[idx]["type"] = tool_call["type"]
                                    if tool_call.get("function"):
                                        func = tool_call["function"]
                                        if func.get("name"):
                                            tool_calls_data[idx]["function"]["name"] = func["name"]
                                        if func.get("arguments"):
                                            tool_calls_data[idx]["function"]["arguments"] += func["arguments"]
                
                logger.info(f"[Kimi K2.5] Round {current_round} done, finish_reason={finish_reason}, "
                           f"tool_calls={bool(tool_calls_data)}, reasoning_content_len={len(reasoning_content)}")
                
                # 检查是否有工具调用需要处理
                if finish_reason == "tool_calls" and tool_calls_data:
                    logger.info(f"[Kimi K2.5] Processing tool_calls...")
                    
                    # 构建 assistant 消息（包含 tool_calls）
                    tool_calls_list = [tool_calls_data[i] for i in sorted(tool_calls_data.keys())]
                    assistant_msg = {
                        "role": "assistant",
                        "content": full_content if full_content else "",
                        "tool_calls": tool_calls_list
                    }
                    # 只有当收到 reasoning_content 时才包含该字段
                    # 如果模型没有产生 reasoning_content，不应该包含空字符串（会导致 API 报错）
                    if reasoning_content:
                        assistant_msg["reasoning_content"] = reasoning_content
                    full_messages.append(assistant_msg)
                    
                    # 为每个工具调用添加 tool 消息
                    for tool_call in tool_calls_list:
                        tool_call_name = tool_call["function"]["name"]
                        tool_call_arguments = tool_call["function"]["arguments"]
                        
                        try:
                            arguments_dict = json.loads(tool_call_arguments) if tool_call_arguments else {}
                        except json.JSONDecodeError:
                            arguments_dict = {}
                        
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_call_name,
                            "content": json.dumps(arguments_dict)
                        }
                        full_messages.append(tool_msg)
                        logger.info(f"[Kimi K2.5] Tool call: {tool_call_name}")
                    
                    continue
                else:
                    break
            
            logger.info(f"[Kimi K2.5] Response finished. Length: {len(full_content)}")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Kimi K2.5 API Error: {error_msg}")
            self.message_received.emit(f"\nKimi K2.5 API 调用错误: {error_msg}", True)

    def run_openai_compatible(self):
        """传统的 OpenAI 兼容模式调用"""
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
        
        logger.info(f"OpenAI Response finished. Length: {len(full_content)}")

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
    screenshotRequested = pyqtSignal()
    klineDataRequested = pyqtSignal()  # Request current stock K-line data
    stockAnalysisRequested = pyqtSignal(int)  # Request stock analysis with current K-line data, param: max_days (0 means all)
    
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
            "claude-3-5-sonnet",
            "kimi-k2.5"
        ])
        self.model_combo.setFixedWidth(180)
        self.model_combo.currentTextChanged.connect(self.on_model_selection_changed)
        self.header_layout.addWidget(self.model_combo)
        
        self.header_layout.addStretch()
        
        # 联网搜索开关
        self.web_search_cb = QCheckBox("🌐 联网搜索")
        self.web_search_cb.setObjectName("HeaderCheckbox")
        self.web_search_cb.setToolTip("开启联网搜索增强 (Gemini/Kimi)")
        self.web_search_cb.setVisible(False) # 默认隐藏，仅在选中 gemini 或 kimi 模型时显示
        self.header_layout.addWidget(self.web_search_cb)
        
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
        self.attach_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        
        # Create attachment menu
        attach_menu = QMenu(self)
        select_file_action = attach_menu.addAction("📁 选择文件...")
        select_file_action.triggered.connect(self.on_select_file_clicked)
        attach_kline_action = attach_menu.addAction("📊 发送当前股票K线数据")
        attach_kline_action.triggered.connect(self.on_attach_kline_data)
        self.attach_btn.setMenu(attach_menu)
        
        tool_layout.addWidget(self.attach_btn)
        
        # 截屏按钮
        self.screenshot_btn = QToolButton()
        self.screenshot_btn.setObjectName("AttachBtn") # 复用样式
        self.screenshot_btn.setText("📸 截屏分析")
        self.screenshot_btn.setToolTip("截取当前K线图并分析")
        self.screenshot_btn.clicked.connect(self.screenshotRequested.emit)
        tool_layout.addWidget(self.screenshot_btn)
        
        # 股票分析按钮（带下拉菜单）
        self.analysis_btn = QToolButton()
        self.analysis_btn.setObjectName("AttachBtn")
        self.analysis_btn.setText("📈 股票分析")
        self.analysis_btn.setToolTip("基于AI对当前股票进行技术分析")
        self.analysis_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        
        # Default analysis days (3 years ~ 750 trading days)
        self.analysis_max_days = 750
        
        analysis_menu = QMenu(self)
        
        # Analysis time range submenu
        range_menu = analysis_menu.addMenu("📅 分析时间范围")
        self.range_action_group = []
        range_options = [
            ("最近3个月 (~60天)", 60),
            ("最近半年 (~120天)", 120),
            ("最近1年 (~250天)", 250),
            ("最近2年 (~500天)", 500),
            ("最近3年 (~750天)", 750),
            ("最近5年 (~1250天)", 1250),
            ("全部数据", 0),
        ]
        for label, days in range_options:
            action = range_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(days == self.analysis_max_days)
            action.setData(days)
            action.triggered.connect(lambda checked, d=days, a=action: self.on_analysis_range_changed(d, a))
            self.range_action_group.append(action)
        
        analysis_menu.addSeparator()
        start_analysis_action = analysis_menu.addAction("🔍 开始分析当前股票")
        start_analysis_action.triggered.connect(self.on_start_stock_analysis)
        analysis_menu.addSeparator()
        view_history_action = analysis_menu.addAction("📋 查看历史分析")
        view_history_action.triggered.connect(self.on_view_analysis_history)
        edit_guide_action = analysis_menu.addAction("📝 编辑分析指导文件")
        edit_guide_action.triggered.connect(self.on_edit_analysis_guide)
        self.analysis_btn.setMenu(analysis_menu)
        tool_layout.addWidget(self.analysis_btn)
        
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
        self.message_input.setPlaceholderText("输入消息，或者直接拖入/粘贴图片或文件... (Enter 发送)")
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
        """点击附件按钮（保留兼容性）"""
        self.on_select_file_clicked()

    def on_select_file_clicked(self):
        """选择文件附件"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择附件", "", "图片和文件 (*.png *.jpg *.jpeg *.txt *.csv *.py);;所有文件 (*.*)"
        )
        if file_paths:
            self.add_attachments(file_paths)

    def on_attach_kline_data(self):
        """请求附加当前股票K线数据"""
        self.klineDataRequested.emit()
    
    # ==================== Stock Analysis Methods ====================
    
    def on_analysis_range_changed(self, days: int, action):
        """Handle analysis time range change"""
        self.analysis_max_days = days
        # Update checkmarks
        for a in self.range_action_group:
            a.setChecked(a.data() == days)
        range_text = "全部数据" if days == 0 else f"{days}天"
        logger.info(f"Analysis range changed to: {range_text}")
    
    def on_start_stock_analysis(self):
        """Request to start stock analysis"""
        self.stockAnalysisRequested.emit(self.analysis_max_days)
    
    def start_stock_analysis(self, df: pd.DataFrame, stock_code: str, stock_name: str, max_days: int = 750):
        """
        Start AI stock analysis with provided K-line data.
        Called by main_window after receiving stockAnalysisRequested signal.
        
        Args:
            df: K-line DataFrame
            stock_code: Stock code
            stock_name: Stock name
            max_days: Maximum days of data to analyze (0 means all data)
        """
        if df is None or df.empty:
            QMessageBox.warning(self, "警告", "没有可用的K线数据")
            return
        
        # Get current model config
        model = self.model_combo.currentText()
        config = self.model_configs.get(model, {})
        api_key = config.get("api_key", "")
        base_url = config.get("base_url", "")
        
        if not api_key:
            QMessageBox.warning(self, "警告", f"请先在设置中配置模型 {model} 的 API Key")
            self.open_settings()
            return
        
        # Get analyzer and build prompt
        analyzer = get_analyzer()
        kline_text = analyzer.format_kline_data(df, stock_code, stock_name, max_days=max_days)
        prompt = analyzer.build_analysis_prompt(kline_text, stock_code, stock_name)
        
        # Display user message (brief)
        if max_days == 0:
            user_display = f"请对 {stock_name}({stock_code}) 进行技术分析 (基于全部{len(df)}个交易日数据)"
        else:
            actual_days = min(len(df), max_days)
            user_display = f"请对 {stock_name}({stock_code}) 进行技术分析 (基于最近{actual_days}个交易日数据)"
        self.append_to_display("user", user_display)
        
        # Add to chat history (full prompt)
        self.chat_history.append({"role": "user", "content": prompt})
        
        # Prepare AI response widget
        self.append_to_display("assistant", "", is_new=True)
        
        # Disable input
        self.message_input.setEnabled(False)
        self.send_btn.setEnabled(False)
        
        # Use a default system prompt for stock analysis if system_prompt is empty
        system_prompt = self.system_prompt if self.system_prompt and self.system_prompt.strip() else \
            "你是一位专业的股票技术分析师，擅长K线形态分析、量价关系分析、技术指标解读。请用专业但易于理解的语言进行分析。"
        
        # Start analysis thread
        self.stock_analysis_thread = StockAnalysisThread(
            api_key, base_url, model, system_prompt, self.chat_history,
            stock_code, stock_name
        )
        self.stock_analysis_thread.message_received.connect(self.on_message_received)
        self.stock_analysis_thread.analysis_finished.connect(self.on_stock_analysis_finished)
        self.stock_analysis_thread.start()
    
    def on_stock_analysis_finished(self, result: str, stock_code: str, stock_name: str, success: bool):
        """Handle stock analysis completion"""
        self.message_input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.message_input.setFocus()
        
        if success and result:
            # Save analysis result
            try:
                analyzer = get_analyzer()
                filepath = analyzer.save_analysis_result(stock_code, stock_name, result)
                logger.info(f"Analysis result saved to: {filepath}")
            except Exception as e:
                logger.error(f"Failed to save analysis result: {e}")
        
        self.save_config()
    
    def on_view_analysis_history(self):
        """Show analysis history dialog"""
        dialog = AnalysisHistoryDialog(self)
        dialog.exec()
    
    def on_edit_analysis_guide(self):
        """Open analysis guide editor dialog"""
        dialog = GuideEditorDialog(self)
        dialog.exec()

    def on_model_selection_changed(self, model_name):
        """当模型选择变化时，显示/隐藏联网搜索开关"""
        model_lower = model_name.lower()
        is_gemini = "gemini" in model_lower
        is_kimi = "kimi" in model_lower
        supports_web_search = is_gemini or is_kimi
        self.web_search_cb.setVisible(supports_web_search)
        if not supports_web_search:
            self.web_search_cb.setChecked(False)

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
            QCheckBox#HeaderCheckbox {{
                color: {text_dim};
                font-size: 12px;
                margin-right: 10px;
                background: transparent;
            }}
            QCheckBox#HeaderCheckbox:hover {{
                color: {text_main};
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
                        self.on_model_selection_changed(model)
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
        
        # 获取联网搜索设置
        use_web_search = self.web_search_cb.isChecked()
        
        # 启动后台线程
        self.chat_thread = ChatThread(
            api_key, base_url, model, self.system_prompt, self.chat_history,
            use_web_search=use_web_search
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
        """处理输入框的 Enter 发送快捷键"""
        if obj is self.message_input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                self.send_message()
                return True
        return super().eventFilter(obj, event)


class StockAnalysisThread(QThread):
    """Background thread for stock analysis API calls"""
    message_received = pyqtSignal(str, bool)  # content, is_error
    analysis_finished = pyqtSignal(str, str, str, bool)  # result, stock_code, stock_name, success

    def __init__(self, api_key, base_url, model, system_prompt, messages, stock_code, stock_name):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.system_prompt = system_prompt
        self.messages = messages
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.full_result = ""

    def run(self):
        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            
            # Build messages
            full_messages = [{"role": "system", "content": self.system_prompt}]
            full_messages.extend(self.messages)
            
            response = client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                stream=True
            )
            
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    self.full_result += content
                    self.message_received.emit(content, False)
            
            logger.info(f"Stock analysis completed for {self.stock_code}. Length: {len(self.full_result)}")
            self.analysis_finished.emit(self.full_result, self.stock_code, self.stock_name, True)
            
        except Exception as e:
            logger.error(f"Stock analysis error: {e}")
            self.message_received.emit(f"\n错误: {str(e)}", True)
            self.analysis_finished.emit("", self.stock_code, self.stock_name, False)


class AnalysisHistoryDialog(QDialog):
    """Dialog to view and manage analysis history"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史分析记录")
        self.setMinimumSize(600, 500)
        self.setup_ui()
        self.load_history()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Filter row
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("筛选股票代码:"))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("输入股票代码筛选（留空显示全部）")
        self.filter_input.textChanged.connect(self.load_history)
        filter_layout.addWidget(self.filter_input)
        layout.addLayout(filter_layout)
        
        # History list
        self.history_list = QListWidget()
        self.history_list.setAlternatingRowColors(True)
        self.history_list.itemDoubleClicked.connect(self.view_detail)
        layout.addWidget(self.history_list)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        view_btn = QPushButton("查看详情")
        view_btn.clicked.connect(self.view_detail)
        btn_layout.addWidget(view_btn)
        
        copy_btn = QPushButton("复制内容")
        copy_btn.clicked.connect(self.copy_content)
        btn_layout.addWidget(copy_btn)
        
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self.delete_record)
        btn_layout.addWidget(delete_btn)
        
        btn_layout.addStretch()
        
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
    
    def load_history(self):
        """Load analysis history from files"""
        self.history_list.clear()
        
        filter_code = self.filter_input.text().strip() if hasattr(self, 'filter_input') else ""
        
        analyzer = get_analyzer()
        records = analyzer.get_analysis_history(stock_code=filter_code if filter_code else None)
        
        for record in records:
            item_text = f"{record['datetime_str']} - {record['stock_name']} ({record['stock_code']})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.history_list.addItem(item)
        
        if not records:
            item = QListWidgetItem("暂无历史记录")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.history_list.addItem(item)
    
    def get_selected_record(self):
        """Get currently selected record"""
        current = self.history_list.currentItem()
        if current:
            return current.data(Qt.ItemDataRole.UserRole)
        return None
    
    def view_detail(self):
        """View selected analysis detail"""
        record = self.get_selected_record()
        if not record:
            QMessageBox.information(self, "提示", "请先选择一条记录")
            return
        
        analyzer = get_analyzer()
        content = analyzer.read_analysis_result(record['filepath'])
        
        if content:
            dialog = QDialog(self)
            dialog.setWindowTitle(f"分析详情 - {record['stock_name']} ({record['stock_code']})")
            dialog.setMinimumSize(700, 600)
            
            layout = QVBoxLayout(dialog)
            
            text_edit = QPlainTextEdit()
            text_edit.setPlainText(content)
            text_edit.setReadOnly(True)
            layout.addWidget(text_edit)
            
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dialog.accept)
            layout.addWidget(close_btn)
            
            dialog.exec()
        else:
            QMessageBox.warning(self, "错误", "无法读取分析文件")
    
    def copy_content(self):
        """Copy selected analysis content to clipboard"""
        record = self.get_selected_record()
        if not record:
            QMessageBox.information(self, "提示", "请先选择一条记录")
            return
        
        analyzer = get_analyzer()
        content = analyzer.read_analysis_result(record['filepath'])
        
        if content:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(content)
            QMessageBox.information(self, "成功", "分析内容已复制到剪贴板")
        else:
            QMessageBox.warning(self, "错误", "无法读取分析文件")
    
    def delete_record(self):
        """Delete selected analysis record"""
        record = self.get_selected_record()
        if not record:
            QMessageBox.information(self, "提示", "请先选择一条记录")
            return
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {record['stock_name']} ({record['stock_code']}) 的分析记录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            analyzer = get_analyzer()
            if analyzer.delete_analysis_result(record['filepath']):
                self.load_history()
                QMessageBox.information(self, "成功", "记录已删除")
            else:
                QMessageBox.warning(self, "错误", "删除失败")


class GuideEditorDialog(QDialog):
    """Dialog to edit the analysis guide file"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑股票分析指导文件")
        self.setMinimumSize(800, 600)
        self.setup_ui()
        self.load_guide()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Info label
        info_label = QLabel("编辑分析指导文件，AI将根据此文件的方法论对股票进行分析：")
        layout.addWidget(info_label)
        
        # Text editor
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet("""
            QPlainTextEdit {
                font-family: 'Consolas', 'Monaco', 'Source Code Pro', monospace;
                font-size: 13px;
                line-height: 1.5;
            }
        """)
        layout.addWidget(self.editor)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.save_guide)
        btn_layout.addWidget(save_btn)
        
        reload_btn = QPushButton("重新加载")
        reload_btn.clicked.connect(self.load_guide)
        btn_layout.addWidget(reload_btn)
        
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
    
    def load_guide(self):
        """Load guide content from file"""
        analyzer = get_analyzer()
        content = analyzer.reload_guide()
        self.editor.setPlainText(content)
    
    def save_guide(self):
        """Save guide content to file"""
        content = self.editor.toPlainText()
        
        analyzer = get_analyzer()
        guide_path = analyzer.guide_path
        
        try:
            # Ensure directory exists
            guide_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(guide_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Reload the guide in analyzer
            analyzer.reload_guide()
            
            QMessageBox.information(self, "成功", "指导文件已保存")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败: {str(e)}")

