# scheduler.py - 定时任务管理器
"""
负责管理定时执行的数据更新、选股和通知任务
"""
import json
import logging
from pathlib import Path
from datetime import datetime, time, timedelta
from typing import Optional, Dict, List

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QThread

try:
    from data_updater import DataUpdateThread
    from widgets.stock_screener_widget import ScreenerThread
    from strategies import get_strategy, get_all_strategies
    from notifier import get_notification_manager
    from data_loader import load_stock_name_map
except ImportError:
    from .data_updater import DataUpdateThread
    from .widgets.stock_screener_widget import ScreenerThread
    from .strategies import get_strategy, get_all_strategies
    from .notifier import get_notification_manager
    from .data_loader import load_stock_name_map

class ScheduledTaskWorker(QObject):
    """
    定时任务执行器，负责按顺序运行更新、选股和通知
    """
    finished = pyqtSignal(bool, str)
    log_message = pyqtSignal(str)
    
    def __init__(self, config: dict, data_dir: str, stocklist_path: str):
        super().__init__()
        self.config = config
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        self.update_thread = None
        self.screener_thread = None
        self.results = []

    def stop(self):
        """停止当前执行的流水线"""
        if self.update_thread and self.update_thread.isRunning():
            self.update_thread.stop()
        if self.screener_thread and self.screener_thread.isRunning():
            self.screener_thread.stop()

    def wait_all(self, timeout_ms: int = 5000):
        """
        等待所有内部线程完全退出
        
        Args:
            timeout_ms: 每个线程的最大等待时间（毫秒）
        """
        if self.update_thread:
            if self.update_thread.isRunning():
                self.update_thread.wait(timeout_ms)
        if self.screener_thread:
            if self.screener_thread.isRunning():
                self.screener_thread.wait(timeout_ms)

    def start(self):
        """开始执行任务流水线"""
        task_name = self.config.get("name", "未命名任务")
        self.log_message.emit(f"🚀 开始执行定时任务 [{task_name}]: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if self.config.get("step_update", True):
            self._step1_update_data()
        elif self.config.get("step_screen", True):
            self._step2_run_screener()
        elif self.config.get("step_notify", True):
            self._step3_send_notification()
        else:
            self.log_message.emit("ℹ️ 未勾选任何执行步骤，任务提前结束")
            self.finished.emit(True, "任务结束")

    def _step1_update_data(self):
        """步骤1: 更新数据"""
        is_full = self.config.get("full_update", False)
        start_date = self.config.get("start_date", None)
        
        mode_str = f"全量更新 (从 {start_date})" if is_full else "增量更新"
        self.log_message.emit(f"🔄 步骤1: 正在进行数据{mode_str}...")
        
        data_source = self.config.get("data_source", "xtquant")
        token = self.config.get("tushare_token", "")
        
        self.update_thread = DataUpdateThread(
            data_dir=self.data_dir,
            stocklist_path=self.stocklist_path,
            tushare_token=token,
            full_update=is_full,
            start_date=start_date,
            data_source=data_source,
            period="1d"
        )
        
        self.update_thread.finished_signal.connect(self._on_update_finished)
        self.update_thread.start()

    def _on_update_finished(self, success, message):
        if not success:
            self.log_message.emit(f"❌ 数据更新失败: {message}")
            self.finished.emit(False, f"数据更新失败: {message}")
            return
            
        self.log_message.emit("✅ 数据更新完成")
        
        if self.config.get("step_screen", True):
            self._step2_run_screener()
        elif self.config.get("step_notify", True):
            self._step3_send_notification()
        else:
            self.finished.emit(True, "全部任务已完成")

    def _step2_run_screener(self):
        """步骤2: 运行选股策略"""
        strategy_name = self.config.get("strategy_id")
        if not strategy_name:
            self.log_message.emit("⚠️ 未配置选股策略，跳过选股步骤")
            if self.config.get("step_notify", True):
                self._step3_send_notification()
            else:
                self.finished.emit(True, "任务完成（未运行选股）")
            return
            
        self.log_message.emit(f"🔍 步骤2: 正在运行策略 [{strategy_name}]...")
        self.results = []
        
        self.screener_thread = ScreenerThread(strategy_name, self.data_dir, self.stocklist_path)
        self.screener_thread.stock_found.connect(self._on_stock_found)
        self.screener_thread.finished_signal.connect(self._on_screener_finished)
        self.screener_thread.start()

    def _on_stock_found(self, result):
        self.results.append(result)

    def _on_screener_finished(self, message):
        self.log_message.emit(f"✅ 选股完成，共找到 {len(self.results)} 只股票")
        
        if self.config.get("step_notify", True):
            self._step3_send_notification()
        else:
            self.finished.emit(True, "全部任务已完成")

    def _step3_send_notification(self):
        """步骤3: 发送企微通知"""
        if not self.results and self.config.get("step_screen", True):
            self.log_message.emit("ℹ️ 没有选中的股票，跳过通知发送")
            self.finished.emit(True, "全部任务已完成 (无选中股票)")
            return
            
        self.log_message.emit("📤 步骤3: 正在发送企微通知...")
        nm = get_notification_manager()
        
        if not nm.is_enabled():
            self.log_message.emit("⚠️ 企微通知未启用，请在通知设置中配置 Webhook")
            self.finished.emit(True, "任务完成（未发送通知）")
            return
            
        strategy_id = self.config.get("strategy_id")
        strategies = get_all_strategies()
        strategy_display_name = strategies.get(strategy_id, strategy_id)
        
        title = f"定时选股结果 - {strategy_display_name}"
        
        stocks_to_send = []
        for r in self.results:
            stocks_to_send.append({
                "code": r.get("code"),
                "name": r.get("name")
            })
            
        success, msg = nm.send_stock_alert(title, stocks_to_send)
        
        if success:
            self.log_message.emit("✅ 通知发送成功！")
            self.finished.emit(True, "全部任务已完成")
        else:
            self.log_message.emit(f"❌ 通知发送失败: {msg}")
            self.finished.emit(False, f"通知发送失败: {msg}")


class ScheduledTaskManager(QObject):
    """
    管理定时任务的调度
    """
    task_started = pyqtSignal()
    task_finished = pyqtSignal(bool, str)
    task_log = pyqtSignal(str)
    
    CONFIG_FILE = "scheduler_config.json"
    
    def __init__(self, data_dir: str, stocklist_path: str):
        super().__init__()
        self.data_dir = data_dir
        self.stocklist_path = stocklist_path
        
        config_dir = Path(__file__).parent / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = config_dir / self.CONFIG_FILE
        
        self.config = self._load_config()
        
        self.timer = QTimer()
        self.timer.timeout.connect(self._check_time)
        self.timer.start(30000) # 每30秒检查一次
        
        self.last_run_times = self.config.get("last_run_times", {}) # 从配置中恢复上次运行时间
        self.current_worker = None
        self.is_running = False

    def stop(self):
        """停止所有正在运行的任务和定时器"""
        self.timer.stop()
        if self.current_worker:
            self.current_worker.stop()
            # 等待内部线程完全退出，防止 "QThread: Destroyed while thread is still running"
            self.current_worker.wait_all()
        self.is_running = False

    def _load_config(self) -> dict:
        defaults = {
            "screener_enabled": False,
            "screener_time": "14:30",
            "screener_strategy_id": "continuous_drop_rebound",
            "screener_step_update": True,
            "screener_step_screen": True,
            "screener_step_notify": True,
            
            "maint_enabled": False,
            "maint_time": "18:00",
            "maint_start_date": "20080101",
            
            "data_source": "xtquant",
            "tushare_token": ""
        }
        
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                # 简单迁移逻辑：如果发现旧 key，迁移到新 key
                if "enabled" in config:
                    config["screener_enabled"] = config.pop("enabled")
                if "time" in config:
                    config["screener_time"] = config.pop("time")
                if "strategy_id" in config:
                    config["screener_strategy_id"] = config.pop("strategy_id")
                if "step_update" in config:
                    config["screener_step_update"] = config.pop("step_update")
                if "step_screen" in config:
                    config["screener_step_screen"] = config.pop("step_screen")
                if "step_notify" in config:
                    config["screener_step_notify"] = config.pop("step_notify")
                
                # 合并默认值
                for k, v in defaults.items():
                    if k not in config:
                        config[k] = v
                return config
            except Exception as e:
                logging.error(f"加载定时任务配置失败: {e}")
        
        return defaults

    def save_config(self, config: dict):
        # 确保持久化的运行记录被包含在内
        if "last_run_times" not in config:
            config["last_run_times"] = self.last_run_times
        else:
            # 如果传入的 config 已经包含了这个 key，也要确保它反映了最新的内存状态
            config["last_run_times"].update(self.last_run_times)
            
        self.config = config
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"保存定时任务配置失败: {e}")

    def _check_time(self):
        if self.is_running:
            return
            
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        # 1. 检查选股任务
        if self.config.get("screener_enabled", False):
            if self._should_run("screener", now, today_str):
                task_config = {
                    "name": "每日选股推送",
                    "step_update": self.config.get("screener_step_update", True),
                    "step_screen": self.config.get("screener_step_screen", True),
                    "step_notify": self.config.get("screener_step_notify", True),
                    "strategy_id": self.config.get("screener_strategy_id"),
                    "data_source": self.config.get("data_source"),
                    "tushare_token": self.config.get("tushare_token")
                }
                self._run_task(task_config, "screener", today_str)
                return # 同一时刻只运行一个任务

        # 2. 检查维护任务
        if self.config.get("maint_enabled", False):
            if self._should_run("maintenance", now, today_str):
                task_config = {
                    "name": "全量数据更新",
                    "step_update": True,
                    "full_update": True,
                    "start_date": self.config.get("maint_start_date", "20080101"),
                    "step_screen": False,
                    "step_notify": False,
                    "data_source": self.config.get("data_source"),
                    "tushare_token": self.config.get("tushare_token")
                }
                self._run_task(task_config, "maintenance", today_str)
                return

    def _should_run(self, task_id, now, today_str):
        if self.last_run_times.get(task_id) == today_str:
            return False
            
        time_key = "screener_time" if task_id == "screener" else "maint_time"
        scheduled_time_str = self.config.get(time_key, "14:30" if task_id == "screener" else "18:00")
        
        try:
            hour, minute = map(int, scheduled_time_str.split(':'))
            scheduled_time = time(hour, minute)
            
            # 方案 C：取消过期补跑逻辑，仅严格匹配时间窗口（2分钟内触发）
            # 计算当天的目标触发时间点
            target_dt = datetime.combine(now.date(), scheduled_time)
            # 定义一个 2 分钟的触发窗口，过期不补
            window_end = target_dt + timedelta(minutes=2)
            
            return target_dt <= now <= window_end
        except:
            return False

    def _run_task(self, task_config, task_id, today_str):
        self.is_running = True
        self.last_run_times[task_id] = today_str
        # 立即保存一次运行状态到磁盘，防止重启后重复执行
        self.save_config(self.config)
        
        self.task_started.emit()
        
        self.current_worker = ScheduledTaskWorker(
            task_config, self.data_dir, self.stocklist_path
        )
        self.current_worker.log_message.connect(self.task_log.emit)
        self.current_worker.finished.connect(self._on_task_finished)
        self.current_worker.start()

    def _on_task_finished(self, success, message):
        self.is_running = False
        self.task_finished.emit(success, message)
        
        # 等待所有内部线程完全退出后再释放 worker
        # 防止 "QThread: Destroyed while thread is still running" 错误
        if self.current_worker:
            self.current_worker.wait_all()
        self.current_worker = None

    def run_now(self, task_id="screener"):
        """手动立即执行任务"""
        if self.is_running:
            return False, "任务正在运行中"
            
        if task_id == "screener":
            task_config = {
                "name": "每日选股推送 (手动)",
                "step_update": self.config.get("screener_step_update", True),
                "step_screen": self.config.get("screener_step_screen", True),
                "step_notify": self.config.get("screener_step_notify", True),
                "strategy_id": self.config.get("screener_strategy_id"),
                "data_source": self.config.get("data_source"),
                "tushare_token": self.config.get("tushare_token")
            }
        else:
            task_config = {
                "name": "全量数据更新 (手动)",
                "step_update": True,
                "full_update": True,
                "start_date": self.config.get("maint_start_date", "20080101"),
                "step_screen": False,
                "step_notify": False,
                "data_source": self.config.get("data_source"),
                "tushare_token": self.config.get("tushare_token")
            }
            
        self._run_task(task_config, task_id, datetime.now().strftime("%Y-%m-%d"))
        return True, "任务已启动"
