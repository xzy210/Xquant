"""
Client lifecycle helpers for miniQMT.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .credential_store import DEFAULT_SERVICE_NAME, load_password
from .windows_qmt_automation import QmtWindowAutomation

StatusCallback = Callable[[str], None]


@dataclass
class QmtClientConfig:
    qmt_path: str = ""
    account: str = ""
    qmt_exe_path: str = ""
    login_username: str = ""
    credential_service: str = DEFAULT_SERVICE_NAME
    login_password: str = ""
    auto_launch: bool = True
    auto_login: bool = False
    window_title_hint: str = ""
    process_name: str = ""
    login_button_rel_x: float = 0.38
    login_button_rel_y: float = 0.855
    login_initial_delay_seconds: float = 5.0
    login_retry_interval_seconds: float = 1.2
    login_max_attempts: int = 15
    post_launch_wait_seconds: float = 8.0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, object]]) -> "QmtClientConfig":
        source = dict(data or {})
        return cls(
            qmt_path=str(source.get("qmt_path", "") or "").strip(),
            account=str(source.get("account", "") or "").strip(),
            qmt_exe_path=str(source.get("qmt_exe_path", "") or "").strip(),
            login_username=str(source.get("login_username", "") or "").strip(),
            credential_service=str(source.get("credential_service", "") or DEFAULT_SERVICE_NAME).strip() or DEFAULT_SERVICE_NAME,
            login_password=str(source.get("login_password", "") or "").strip(),
            auto_launch=bool(source.get("auto_launch", True)),
            auto_login=bool(source.get("auto_login", False)),
            window_title_hint=str(source.get("window_title_hint", "") or "").strip(),
            process_name=str(source.get("process_name", "") or "").strip(),
            login_button_rel_x=float(source.get("login_button_rel_x", 0.38) or 0.38),
            login_button_rel_y=float(source.get("login_button_rel_y", 0.855) or 0.855),
            login_initial_delay_seconds=float(source.get("login_initial_delay_seconds", 5.0) or 5.0),
            login_retry_interval_seconds=float(source.get("login_retry_interval_seconds", 1.2) or 1.2),
            login_max_attempts=int(source.get("login_max_attempts", 15) or 15),
            post_launch_wait_seconds=float(source.get("post_launch_wait_seconds", 8.0) or 8.0),
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class QmtClientStatus:
    running: bool = False
    login_window_visible: bool = False
    main_window_visible: bool = False
    ready: bool = False
    process_ids: List[int] | None = None
    matched_titles: List[str] | None = None
    message: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class QmtClientService:
    """Best-effort miniQMT process and login controller."""

    DEFAULT_PROCESS_NAMES = (
        "miniqmt.exe",
        "xtminiqmt.exe",
        "qmt.exe",
        "thinktrader.exe",
        "xtitclient.exe",
    )

    def __init__(self, config: Optional[Dict[str, object]] = None) -> None:
        self.config = QmtClientConfig.from_dict(config)
        self.automation = QmtWindowAutomation(
            self.config.window_title_hint,
            login_button_rel_x=self.config.login_button_rel_x,
            login_button_rel_y=self.config.login_button_rel_y,
        )

    def get_status(self) -> QmtClientStatus:
        process_ids = self._find_process_ids()
        probe = self.automation.probe_windows()
        running = bool(process_ids) or probe.login_window_found or probe.main_window_found
        ready = self._is_login_completed_state(
            running=running,
            login_window_visible=probe.login_window_found,
            main_window_visible=probe.main_window_found,
        )
        message = self._build_status_message(running, probe.login_window_found, probe.main_window_found)
        return QmtClientStatus(
            running=running,
            login_window_visible=probe.login_window_found,
            main_window_visible=probe.main_window_found,
            ready=ready,
            process_ids=process_ids,
            matched_titles=probe.matched_titles or [],
            message=message,
        )

    def launch(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        existing = self.get_status()
        if existing.running:
            return True, "miniQMT 已在运行"

        exe_path = self.resolve_exe_path()
        if not exe_path:
            return False, "未配置 miniQMT 可执行文件路径"
        if not os.path.exists(exe_path):
            return False, f"miniQMT 可执行文件不存在: {exe_path}"

        self._emit(status_callback, f"正在启动 miniQMT: {exe_path}")
        try:
            os.startfile(exe_path)
        except Exception as exc:
            self._emit(status_callback, f"os.startfile 启动失败，回退 subprocess: {exc}")
            try:
                subprocess.Popen(
                    [exe_path],
                    cwd=str(Path(exe_path).parent),
                    shell=False,
                )
            except Exception as sub_exc:
                return False, f"启动 miniQMT 失败: {sub_exc}"

        deadline = time.time() + 20.0
        last_probe = None
        while time.time() < deadline:
            process_ids = self._find_process_ids()
            if process_ids:
                probe = self.automation.probe_windows()
                if probe.login_window_found or probe.main_window_found:
                    return True, "miniQMT 已启动"
                last_probe = probe
                return True, "miniQMT 进程已启动"
            time.sleep(0.6)

        probe = self.automation.wait_for_any_window(timeout=3.0)
        if probe.login_window_found or probe.main_window_found:
            return True, "miniQMT 已启动"
        if last_probe and (last_probe.login_window_found or last_probe.main_window_found):
            return True, "miniQMT 已启动"
        return False, "miniQMT 启动后未检测到窗口或进程"

    def login(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        self._emit(status_callback, "正在尝试自动登录 miniQMT...")
        initial_delay = max(float(self.config.login_initial_delay_seconds), 0.0)
        if initial_delay > 0:
            self._emit(status_callback, f"检测到登录界面，等待更新检查结束 {initial_delay:.1f} 秒...")
            time.sleep(initial_delay)

        ok = False
        msg = "未执行登录点击"
        max_attempts = max(int(self.config.login_max_attempts), 1)
        retry_interval = max(float(self.config.login_retry_interval_seconds), 0.2)
        for attempt in range(1, max_attempts + 1):
            status = self.get_status()
            if self._is_login_completed(status):
                return True, "miniQMT 登录完成"

            self._emit(status_callback, f"正在执行登录点击，第 {attempt}/{max_attempts} 次...")
            clicked, msg = self.automation.click_login_button(timeout=2.0)
            if not clicked:
                username = self.config.login_username.strip()
                password = self.resolve_password()
                if username and password:
                    clicked, msg = self.automation.fill_login_form(username, password, timeout=2.0)
            if not clicked:
                if attempt < max_attempts:
                    time.sleep(retry_interval)
                continue

            if self._wait_for_login_completion(timeout_seconds=max(retry_interval * 2, 2.0)):
                return True, "miniQMT 登录完成"

            self._emit(status_callback, "登录界面仍存在，按钮可能暂不可用，继续重试...")
            if attempt < max_attempts:
                time.sleep(retry_interval)
        return False, "已多次尝试点击登录，但登录界面仍未关闭，请继续调整等待时间或点击坐标"

    def launch_and_login(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        status = self.get_status()
        if not status.running:
            ok, msg = self.launch(status_callback=status_callback)
            if not ok:
                return False, msg
            status = self._wait_for_post_launch_state(status_callback=status_callback)
        else:
            self._emit(status_callback, "miniQMT 已在运行，继续检查登录状态...")
            status = self._wait_for_post_launch_state(status_callback=status_callback, allow_quick_exit=True)

        if status.login_window_visible:
            return self.login(status_callback=status_callback)

        self._emit(status_callback, "未明确检测到登录界面，继续尝试自动登录...")
        ok, msg = self.login(status_callback=status_callback)
        if ok:
            return True, msg

        status = self.get_status()
        if self._is_login_completed(status):
            return True, "miniQMT 已启动，但未检测到可操作的登录界面"
        return False, msg

    def _wait_for_post_launch_state(
        self,
        status_callback: Optional[StatusCallback] = None,
        *,
        allow_quick_exit: bool = False,
    ) -> QmtClientStatus:
        wait_seconds = max(float(self.config.post_launch_wait_seconds), 0.5)
        if not allow_quick_exit:
            self._emit(status_callback, f"等待 QMT 登录界面稳定出现，最长 {wait_seconds:.1f} 秒...")
        deadline = time.time() + wait_seconds
        last_status = self.get_status()
        saw_main_window = bool(last_status.main_window_visible)
        while time.time() < deadline:
            last_status = self.get_status()
            if last_status.login_window_visible:
                self._emit(status_callback, "已检测到 QMT 登录界面")
                return last_status
            if last_status.main_window_visible:
                saw_main_window = True
            time.sleep(0.6)
        if saw_main_window:
            self._emit(status_callback, "已检测到 QMT 窗口，但未明确识别为登录界面")
        return last_status

    def _wait_for_login_completion(self, timeout_seconds: float = 2.5) -> bool:
        deadline = time.time() + max(timeout_seconds, 0.5)
        while time.time() < deadline:
            status = self.get_status()
            if self._is_login_completed(status):
                return True
            time.sleep(0.35)
        return False

    @staticmethod
    def _is_login_completed(status: QmtClientStatus) -> bool:
        return QmtClientService._is_login_completed_state(
            running=bool(status.running),
            login_window_visible=bool(status.login_window_visible),
            main_window_visible=bool(status.main_window_visible),
        )

    @staticmethod
    def _is_login_completed_state(*, running: bool, login_window_visible: bool, main_window_visible: bool) -> bool:
        return bool(running) and bool(main_window_visible) and not bool(login_window_visible)

    def close(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        self._emit(status_callback, "正在关闭 miniQMT...")
        self.automation.close_windows()
        time.sleep(1.0)

        process_ids = self._find_process_ids()
        if not process_ids:
            return True, "miniQMT 已关闭"

        killed = 0
        try:
            import psutil

            for pid in process_ids:
                try:
                    process = psutil.Process(pid)
                    process.terminate()
                    process.wait(timeout=5)
                    killed += 1
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=3)
                        killed += 1
                    except Exception:
                        pass
        except Exception:
            for pid in process_ids:
                try:
                    os.kill(pid, 9)
                    killed += 1
                except Exception:
                    pass

        still_running = self.get_status().running
        if still_running:
            return False, "未能完全关闭 miniQMT，请手动确认"
        if killed:
            return True, f"miniQMT 已关闭（结束 {killed} 个进程）"
        return True, "miniQMT 已关闭"

    def ensure_ready(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        status = self.get_status()
        if status.ready:
            return True, status.message

        if not status.running:
            if not self.config.auto_launch:
                return False, "miniQMT 未启动，且当前未启用自动启动"
            ok, msg = self.launch(status_callback=status_callback)
            if not ok:
                return False, msg
            status = self.get_status()

        if status.login_window_visible:
            if not self.config.auto_login:
                return False, "miniQMT 已启动但仍未登录，请手动登录或启用自动登录"
            ok, msg = self.login(status_callback=status_callback)
            if not ok:
                return False, msg
            status = self.get_status()

        if status.ready:
            return True, status.message
        return True, "miniQMT 已运行，请继续建立 xtquant 连接"

    def resolve_exe_path(self) -> str:
        if self.config.qmt_exe_path:
            return self.config.qmt_exe_path

        qmt_path = Path(self.config.qmt_path) if self.config.qmt_path else None
        if qmt_path:
            candidates = [
                qmt_path.parent / "miniqmt.exe",
                qmt_path.parent / "MiniQmt.exe",
                qmt_path.parent / "qmt.exe",
                qmt_path.parent / "bin.x64" / "miniqmt.exe",
                qmt_path.parent.parent / "miniqmt.exe",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        return ""

    def resolve_password(self) -> str:
        if self.config.login_password:
            return self.config.login_password
        username = self.config.login_username.strip()
        if not username:
            return ""
        return load_password(username, service_name=self.config.credential_service) or ""

    def _find_process_ids(self) -> List[int]:
        names = {name.lower() for name in self._candidate_process_names()}
        process_ids: List[int] = []
        try:
            import psutil

            for process in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    name = str(process.info.get("name") or "").lower()
                    exe_name = Path(str(process.info.get("exe") or "")).name.lower()
                    if name in names or exe_name in names:
                        process_ids.append(int(process.info["pid"]))
                except Exception:
                    continue
            return process_ids
        except Exception:
            pass

        try:
            output = subprocess.check_output(["tasklist", "/fo", "csv", "/nh"], text=True, encoding="utf-8", errors="ignore")
        except Exception:
            return process_ids

        for raw_line in output.splitlines():
            line = raw_line.strip().strip('"')
            if not line:
                continue
            parts = [part.strip('"') for part in raw_line.split('","')]
            if len(parts) < 2:
                continue
            image_name = parts[0].strip('"').lower()
            pid_text = parts[1].strip('"')
            if image_name in names:
                try:
                    process_ids.append(int(pid_text))
                except ValueError:
                    pass
        return process_ids

    def _candidate_process_names(self) -> Iterable[str]:
        if self.config.process_name:
            yield self.config.process_name
        exe_path = self.resolve_exe_path()
        if exe_path:
            yield Path(exe_path).name
        for name in self.DEFAULT_PROCESS_NAMES:
            yield name

    @staticmethod
    def _emit(status_callback: Optional[StatusCallback], message: str) -> None:
        if callable(status_callback):
            status_callback(message)

    @staticmethod
    def _build_status_message(running: bool, login_visible: bool, main_visible: bool) -> str:
        if login_visible:
            return "miniQMT 已启动，等待登录"
        if running and main_visible:
            return "miniQMT 已启动并进入主界面"
        if running:
            return "miniQMT 进程运行中"
        return "miniQMT 未启动"
