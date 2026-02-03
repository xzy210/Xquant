# notifier.py - 消息通知模块
"""
支持企业微信机器人消息推送
"""
import json
import requests
from pathlib import Path
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class NotificationConfig:
    """通知配置"""
    enabled: bool = False
    webhook_url: str = ""
    

class WeChatWorkNotifier:
    """企业微信机器人通知"""
    
    def __init__(self, webhook_url: str):
        """
        初始化企业微信通知器
        
        Args:
            webhook_url: 企业微信机器人的 webhook 地址
        """
        self.webhook_url = webhook_url
    
    def send_text(self, content: str, mentioned_list: List[str] = None) -> Tuple[bool, str]:
        """
        发送文本消息
        
        Args:
            content: 消息内容，最长不超过2048字节
            mentioned_list: @的成员列表，如 ["userid1", "userid2"]，@all 表示所有人
            
        Returns:
            (是否成功, 消息)
        """
        data = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }
        
        if mentioned_list:
            data["text"]["mentioned_list"] = mentioned_list
            
        return self._send(data)
    
    def send_markdown(self, content: str) -> Tuple[bool, str]:
        """
        发送 Markdown 消息
        
        支持的语法：
        - 标题：# 一级标题 到 ###### 六级标题
        - 加粗：**bold**
        - 链接：[链接文字](URL)
        - 引用：> 引用内容
        - 字体颜色：<font color="info">绿色</font>
                   <font color="comment">灰色</font>
                   <font color="warning">橙红色</font>
        
        Args:
            content: Markdown 格式的消息内容
            
        Returns:
            (是否成功, 消息)
        """
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": content
            }
        }
        return self._send(data)
    
    def _send(self, data: dict) -> Tuple[bool, str]:
        """发送消息到企业微信"""
        if not self.webhook_url:
            return False, "Webhook URL 未配置"
        
        try:
            resp = requests.post(
                self.webhook_url, 
                json=data, 
                timeout=10,
                headers={"Content-Type": "application/json"}
            )
            result = resp.json()
            
            if result.get("errcode") == 0:
                return True, "发送成功"
            else:
                return False, f"发送失败: {result.get('errmsg', '未知错误')}"
                
        except requests.exceptions.Timeout:
            return False, "请求超时"
        except requests.exceptions.RequestException as e:
            return False, f"网络错误: {str(e)}"
        except Exception as e:
            return False, f"发送失败: {str(e)}"


class NotificationManager:
    """
    通知管理器
    
    统一管理消息通知功能，支持配置持久化
    """
    
    CONFIG_FILE = "notification_config.json"
    
    def __init__(self, config_dir: str = None):
        """
        初始化通知管理器
        
        Args:
config_dir: 配置文件目录，默认为 trading_app/config
        """
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path(__file__).parent / "config"
        
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / self.CONFIG_FILE
        
        # 加载配置
        self.config = self._load_config()
        
        # 初始化通知器
        self._notifier: Optional[WeChatWorkNotifier] = None
        self._init_notifier()
    
    def _load_config(self) -> NotificationConfig:
        """加载配置"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return NotificationConfig(
                    enabled=data.get("enabled", False),
                    webhook_url=data.get("webhook_url", "")
                )
            except Exception:
                pass
        return NotificationConfig()
    
    def _save_config(self):
        """保存配置"""
        data = {
            "enabled": self.config.enabled,
            "webhook_url": self.config.webhook_url
        }
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _init_notifier(self):
        """初始化通知器"""
        if self.config.webhook_url:
            self._notifier = WeChatWorkNotifier(self.config.webhook_url)
        else:
            self._notifier = None
    
    def set_webhook_url(self, url: str):
        """设置 Webhook URL"""
        self.config.webhook_url = url
        self._save_config()
        self._init_notifier()
    
    def set_enabled(self, enabled: bool):
        """设置是否启用通知"""
        self.config.enabled = enabled
        self._save_config()
    
    def is_enabled(self) -> bool:
        """检查通知是否启用"""
        return self.config.enabled and self._notifier is not None
    
    def get_webhook_url(self) -> str:
        """获取当前 Webhook URL"""
        return self.config.webhook_url
    
    def send_text(self, content: str) -> Tuple[bool, str]:
        """发送文本消息"""
        if not self.is_enabled():
            return False, "通知未启用"
        return self._notifier.send_text(content)
    
    def send_markdown(self, content: str) -> Tuple[bool, str]:
        """发送 Markdown 消息"""
        if not self.is_enabled():
            return False, "通知未启用"
        return self._notifier.send_markdown(content)
    
    def send_stock_alert(self, title: str, stocks: List[Dict], 
                         extra_info: str = None) -> Tuple[bool, str]:
        """
        发送选股结果通知
        
        Args:
            title: 通知标题
            stocks: 股票列表，每个元素包含 code, name 等字段
            extra_info: 额外信息
            
        Returns:
            (是否成功, 消息)
        """
        if not self.is_enabled():
            return False, "通知未启用"
        
        if not stocks:
            return False, "股票列表为空"
        
        # 构建 Markdown 消息
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"**{title}** 🔔",
            f"> 时间：{now}",
            f"> 数量：**{len(stocks)}** 只",
            ""
        ]
        
        for i, stock in enumerate(stocks[:20], 1):  # 最多显示20只
            code = stock.get("code", "")
            name = stock.get("name", "")
            price = stock.get("price", "")
            change = stock.get("change_pct", "")
            
            line = f"{i}. **{code}** {name}"
            if price:
                line += f" | 现价: {price}"
            if change:
                try:
                    color = "warning" if float(change) >= 0 else "info"
                    line += f" | <font color=\"{color}\">{float(change):+.2f}%</font>"
                except (ValueError, TypeError):
                    pass
            lines.append(line)
        
        if len(stocks) > 20:
            lines.append(f"\n... 共 {len(stocks)} 只，仅显示前20只")
        
        if extra_info:
            lines.append(f"\n{extra_info}")
        
        content = "\n".join(lines)
        return self.send_markdown(content)
    
    def send_test_message(self) -> Tuple[bool, str]:
        """发送测试消息"""
        if not self._notifier:
            return False, "Webhook URL 未配置"
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = f"""**来财 - 测试消息** ✅
> 发送时间：{now}

🎉 恭喜！企业微信通知配置成功！

后续选股结果将通过此渠道推送。"""
        
        return self._notifier.send_markdown(content)


# 全局单例
_notification_manager: Optional[NotificationManager] = None


def get_notification_manager() -> NotificationManager:
    """获取通知管理器单例"""
    global _notification_manager
    if _notification_manager is None:
        _notification_manager = NotificationManager()
    return _notification_manager

