"""
Client lifecycle helpers for miniQMT.
"""
from __future__ import annotations

import os
import random
import shutil
import stat
import subprocess
import threading
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
    use_linkmini_launch: bool = True
    linkmini_source_name: str = "linkMini_copy"
    linkmini_runtime_name: str = "linkMini"
    window_title_hint: str = ""
    process_name: str = ""
    login_button_rel_x: float = 0.38
    login_button_rel_y: float = 0.855
    login_initial_delay_seconds: float = 5.0
    login_retry_interval_seconds: float = 1.2
    login_max_attempts: int = 15
    post_launch_wait_seconds: float = 15.0
    xtquant_probe_timeout_seconds: float = 8.0
    linkmini_ready_timeout_seconds: float = 45.0

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
            use_linkmini_launch=bool(source.get("use_linkmini_launch", True)),
            linkmini_source_name=str(source.get("linkmini_source_name", "linkMini_copy") or "linkMini_copy").strip() or "linkMini_copy",
            linkmini_runtime_name=str(source.get("linkmini_runtime_name", "linkMini") or "linkMini").strip() or "linkMini",
            window_title_hint=str(source.get("window_title_hint", "") or "").strip(),
            process_name=str(source.get("process_name", "") or "").strip(),
            login_button_rel_x=float(source.get("login_button_rel_x", 0.38) or 0.38),
            login_button_rel_y=float(source.get("login_button_rel_y", 0.855) or 0.855),
            login_initial_delay_seconds=float(source.get("login_initial_delay_seconds", 5.0) or 5.0),
            login_retry_interval_seconds=float(source.get("login_retry_interval_seconds", 1.2) or 1.2),
            login_max_attempts=int(source.get("login_max_attempts", 15) or 15),
            post_launch_wait_seconds=float(source.get("post_launch_wait_seconds", 15.0) or 15.0),
            xtquant_probe_timeout_seconds=float(source.get("xtquant_probe_timeout_seconds", 8.0) or 8.0),
            linkmini_ready_timeout_seconds=float(source.get("linkmini_ready_timeout_seconds", 45.0) or 45.0),
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
        self._last_launch_mode = "unknown"

    def get_status(self) -> QmtClientStatus:
        return self._get_status(force_refresh=False)

    def launch(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        self._last_launch_mode = "unknown"
        existing = self.get_status()
        if existing.running:
            self._last_launch_mode = "already_running"
            return True, "miniQMT 已在运行"

        exe_path = self.resolve_exe_path()
        if not exe_path:
            return False, "未配置 miniQMT 可执行文件路径"
        if not os.path.exists(exe_path):
            return False, f"miniQMT 可执行文件不存在: {exe_path}"

        if self.config.use_linkmini_launch:
            launched, launch_message = self._launch_with_linkmini(exe_path, status_callback=status_callback)
            if launched:
                ok, message = self._wait_for_launch_result(timeout_seconds=8.0)
                if ok:
                    self._last_launch_mode = "linkmini"
                    return True, message
                self._emit(status_callback, f"linkMini 启动后未就绪，回退常规启动: {message}")
            else:
                self._emit(status_callback, f"linkMini 启动不可用，回退常规启动: {launch_message}")

        self._emit(status_callback, f"正在启动 miniQMT: {exe_path}")
        ok, message = self._launch_executable(exe_path)
        if not ok:
            return False, message
        self._last_launch_mode = "normal"
        return self._wait_for_launch_result()

    def login(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        self._emit(status_callback, "正在尝试自动登录 miniQMT...")
        desktop_state = self.automation.get_desktop_interaction_state()
        if not desktop_state.interactive:
            self._emit(status_callback, desktop_state.message)
            return False, desktop_state.message
        initial_delay = max(float(self.config.login_initial_delay_seconds), 0.0)
        if initial_delay > 0:
            status = self.get_status()
            if status.login_window_visible:
                self._emit(status_callback, f"检测到登录界面，等待更新检查结束 {initial_delay:.1f} 秒...")
            else:
                self._emit(status_callback, f"等待 QMT 界面就绪 {initial_delay:.1f} 秒...")
            time.sleep(initial_delay)

        ok = False
        msg = "未执行登录点击"
        max_attempts = max(int(self.config.login_max_attempts), 1)
        retry_interval = max(float(self.config.login_retry_interval_seconds), 0.2)
        for attempt in range(1, max_attempts + 1):
            desktop_state = self.automation.get_desktop_interaction_state()
            if not desktop_state.interactive:
                self._emit(status_callback, desktop_state.message)
                return False, desktop_state.message
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
        if self._is_login_completed(status):
            return True, status.message

        if status.running:
            self._emit(status_callback, "检测到未完成登录的 QMT 窗口或残留进程，先关闭后重新启动...")
            close_ok, close_msg = self.close(status_callback=status_callback)
            if not close_ok:
                return False, close_msg

        ok, msg = self.launch(status_callback=status_callback)
        if not ok:
            return False, msg

        if self._last_launch_mode == "linkmini":
            self._emit(status_callback, "miniQMT 已通过 linkMini 发起启动，正在使用 xtquant.connect() 直接确认是否已就绪...")
            verified, verify_message = self._wait_for_xtquant_ready(
                timeout_seconds=max(float(self.config.linkmini_ready_timeout_seconds), float(self.config.post_launch_wait_seconds), 12.0),
                status_callback=status_callback,
            )
            if verified:
                return True, "miniQMT 已通过 linkMini 免登录启动"
            status = self._wait_for_post_launch_state(
                status_callback=status_callback,
                allow_quick_exit=True,
                post_launch_wait_seconds=8.0,
                force_refresh=True,
            )
            if self._is_login_completed(status):
                extra_timeout = max(20.0, min(float(self.config.linkmini_ready_timeout_seconds), 60.0))
                self._emit(
                    status_callback,
                    f"已识别到 QMT 主界面，但 xtquant.connect() 尚未成功，继续等待 {extra_timeout:.0f} 秒确认后台就绪...",
                )
                verified, verify_message = self._wait_for_xtquant_ready(
                    timeout_seconds=extra_timeout,
                    status_callback=status_callback,
                )
                if verified:
                    return True, "miniQMT 已通过 linkMini 免登录启动"
            desktop_state = self.automation.get_desktop_interaction_state()
            if desktop_state.interactive:
                if self._is_login_completed(status):
                    return False, f"linkMini 启动后已显示主界面，但 xtquant.connect() 仍失败: {verify_message}"
                self._emit(status_callback, f"linkMini 启动后 xtquant.connect() 仍失败，当前桌面可交互，回退自动登录流程: {verify_message}")
                return self.login(status_callback=status_callback)
            if self._is_login_completed(status):
                return False, f"linkMini 启动后已显示主界面，但 xtquant.connect() 仍失败: {verify_message}"
            return False, f"linkMini 启动后 xtquant.connect() 仍失败，且当前无交互桌面: {verify_message}"

        status = self._wait_for_post_launch_state(status_callback=status_callback)
        if self._is_login_completed(status):
            verified, verify_message = self._verify_ready_state(
                status,
                status_callback=status_callback,
                success_message=status.message,
            )
            if verified:
                return True, verify_message
            self._emit(status_callback, f"OCR 已识别主界面，但 xtquant.connect() 复核失败: {verify_message}")

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
        post_launch_wait_seconds: float | None = None,
        force_refresh: bool = False,
    ) -> QmtClientStatus:
        base_wait = self.config.post_launch_wait_seconds if post_launch_wait_seconds is None else post_launch_wait_seconds
        wait_seconds = max(float(base_wait), 0.5)
        if not allow_quick_exit:
            self._emit(status_callback, f"等待 QMT 登录界面稳定出现，最长 {wait_seconds:.1f} 秒...")
        deadline = time.time() + wait_seconds
        last_status = self._get_status(force_refresh=force_refresh)
        saw_main_window = bool(last_status.main_window_visible)
        main_ready_hits = 1 if self._is_login_completed(last_status) else 0
        while time.time() < deadline:
            last_status = self._get_status(force_refresh=force_refresh)
            if last_status.login_window_visible:
                self._emit(status_callback, "已检测到 QMT 登录界面")
                return last_status
            if last_status.main_window_visible:
                saw_main_window = True
                if self._is_login_completed(last_status):
                    main_ready_hits += 1
                else:
                    main_ready_hits = 0
                if allow_quick_exit and self._is_login_completed(last_status):
                    self._emit(status_callback, "miniQMT 主界面已就绪，无需登录")
                    return last_status
                if main_ready_hits >= 2:
                    self._emit(status_callback, "已检测到 QMT 主界面")
                    return last_status
            else:
                main_ready_hits = 0
            time.sleep(0.6)
        if self._is_login_completed(last_status):
            self._emit(status_callback, "已检测到 QMT 主界面")
            return last_status
        if saw_main_window:
            self._emit(status_callback, "已检测到 QMT 窗口，但主界面状态仍未稳定")
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

    def _get_status(self, *, force_refresh: bool = False) -> QmtClientStatus:
        process_ids = self._find_process_ids()
        probe = self.automation.probe_windows(force_refresh=force_refresh)
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

    def _verify_ready_state(
        self,
        status: QmtClientStatus,
        *,
        status_callback: Optional[StatusCallback] = None,
        success_message: str,
    ) -> tuple[bool, str]:
        if not self._is_login_completed(status):
            return False, status.message or "OCR 未识别到 miniQMT 主界面"
        self._emit(status_callback, "已通过 OCR 识别到 QMT 主界面，正在使用 xtquant.connect() 复核...")
        ok, probe_message = self._probe_xtquant_connect()
        if ok:
            self._emit(status_callback, "xtquant.connect() 复核成功")
            return True, success_message
        return False, probe_message

    def _wait_for_xtquant_ready(
        self,
        *,
        timeout_seconds: float,
        status_callback: Optional[StatusCallback] = None,
        probe_interval_seconds: float = 1.0,
    ) -> tuple[bool, str]:
        deadline = time.time() + max(timeout_seconds, 1.0)
        last_message = "xtquant.connect() 尚未成功"
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            ok, probe_message = self._probe_xtquant_connect()
            if ok:
                self._emit(status_callback, f"xtquant.connect() 复核成功（第 {attempt} 次）")
                return True, probe_message
            last_message = probe_message
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self._emit(status_callback, f"xtquant.connect() 复核未通过（第 {attempt} 次）: {probe_message}")
            time.sleep(min(max(probe_interval_seconds, 0.2), remaining))
        return False, last_message

    def _probe_xtquant_connect(self) -> tuple[bool, str]:
        qmt_path = str(self.config.qmt_path or "").strip()
        if not qmt_path:
            return False, "未配置 qmt_path，无法执行 xtquant.connect() 复核"

        result_box: dict[str, object] = {}
        done = threading.Event()
        abandon = threading.Event()

        def runner():
            xt_trader = None
            try:
                from xtquant import xttrader

                session_id = int(random.randint(100000, 999999))
                xt_trader = xttrader.XtQuantTrader(qmt_path, session_id)
                xt_trader.start()
                result = xt_trader.connect()
                if abandon.is_set():
                    try:
                        xt_trader.stop()
                    except Exception:
                        pass
                    return
                if result != 0:
                    result_box["error"] = "xtquant.connect() 返回非 0，QMT 可能尚未真正登录完成"
                    return
                result_box["ok"] = True
            except ImportError:
                result_box["error"] = "未找到 xtquant 库，无法执行 xtquant.connect() 复核"
            except Exception as exc:
                result_box["error"] = f"xtquant.connect() 复核异常: {exc}"
            finally:
                if xt_trader is not None:
                    try:
                        xt_trader.stop()
                    except Exception:
                        pass
                done.set()

        threading.Thread(target=runner, daemon=True).start()
        timeout_seconds = max(float(self.config.xtquant_probe_timeout_seconds), 2.0)
        if not done.wait(timeout_seconds):
            abandon.set()
            return False, f"xtquant.connect() 复核超时（>{timeout_seconds:.0f} 秒）"
        if result_box.get("ok"):
            return True, "xtquant.connect() 复核成功"
        return False, str(result_box.get("error") or "xtquant.connect() 复核失败")

    @staticmethod
    def _is_login_completed_state(*, running: bool, login_window_visible: bool, main_window_visible: bool) -> bool:
        return bool(running) and bool(main_window_visible) and not bool(login_window_visible)

    def close(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        self._emit(status_callback, "正在关闭 miniQMT...")
        self.automation.close_windows()
        if self._wait_until_stopped(timeout_seconds=2.5):
            return True, "miniQMT 已关闭"

        process_ids = self._find_process_ids()
        if not process_ids:
            return True, "miniQMT 已关闭"

        self._emit(status_callback, f"检测到 {len(process_ids)} 个 QMT 进程残留，尝试结束进程...")
        terminated = self._terminate_processes(process_ids, force=False)
        if self._wait_until_stopped(timeout_seconds=6.0):
            return True, f"miniQMT 已关闭（结束 {terminated} 个进程）"

        remaining_ids = self._find_process_ids()
        if remaining_ids:
            self._emit(status_callback, f"仍有 {len(remaining_ids)} 个 QMT 进程残留，尝试强制结束...")
        killed = self._terminate_processes(remaining_ids or process_ids, force=True)
        self._taskkill_candidates()

        if self._wait_until_stopped(timeout_seconds=6.0):
            total = terminated + killed
            if total > 0:
                return True, f"miniQMT 已关闭（结束 {total} 个进程）"
            return True, "miniQMT 已关闭"

        remaining = self._find_process_ids()
        if remaining:
            return False, f"未能完全关闭 miniQMT，残留进程 PID: {', '.join(str(pid) for pid in remaining)}"
        return False, "未能完全关闭 miniQMT，请手动确认"

    def ensure_ready(self, status_callback: Optional[StatusCallback] = None) -> tuple[bool, str]:
        process_ids = self._find_process_ids()
        if not process_ids:
            if not self.config.auto_launch:
                return False, "miniQMT 未启动，且当前未启用自动启动"
            ok, msg = self.launch(status_callback=status_callback)
            if not ok:
                return False, msg
            process_ids = self._find_process_ids()

        if process_ids:
            ok, probe_message = self._probe_xtquant_connect()
            if ok:
                return True, "miniQMT 已运行，xtquant.connect() 复核成功"

        status = self.get_status()

        if status.login_window_visible:
            if not self.config.auto_login:
                return False, "miniQMT 已启动但仍未登录，请手动登录或启用自动登录"
            ok, msg = self.login(status_callback=status_callback)
            if not ok:
                return False, msg
            status = self.get_status()

        if status.ready:
            verified, verify_message = self._verify_ready_state(
                status,
                status_callback=status_callback,
                success_message=status.message,
            )
            if verified:
                return True, verify_message
            return False, verify_message
        return True, "miniQMT 已运行，请继续建立 xtquant 连接"

    def resolve_exe_path(self) -> str:
        if self.config.qmt_exe_path:
            return self.config.qmt_exe_path

        qmt_path = Path(self.config.qmt_path) if self.config.qmt_path else None
        if qmt_path:
            candidates = [
                qmt_path.parent / "miniqmt.exe",
                qmt_path.parent / "MiniQmt.exe",
                qmt_path.parent / "xtMiniQmt.exe",
                qmt_path.parent / "XtMiniQmt.exe",
                qmt_path.parent / "XtItClient.exe",
                qmt_path.parent / "qmt.exe",
                qmt_path.parent / "bin.x64" / "miniqmt.exe",
                qmt_path.parent / "bin.x64" / "MiniQmt.exe",
                qmt_path.parent / "bin.x64" / "xtMiniQmt.exe",
                qmt_path.parent / "bin.x64" / "XtMiniQmt.exe",
                qmt_path.parent / "bin.x64" / "XtItClient.exe",
                qmt_path.parent.parent / "miniqmt.exe",
                qmt_path.parent.parent / "bin.x64" / "xtMiniQmt.exe",
                qmt_path.parent.parent / "bin.x64" / "XtMiniQmt.exe",
                qmt_path.parent.parent / "bin.x64" / "XtItClient.exe",
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

    def _wait_until_stopped(self, timeout_seconds: float = 5.0) -> bool:
        deadline = time.time() + max(timeout_seconds, 0.5)
        while time.time() < deadline:
            if not self.get_status().running and not self._find_process_ids():
                return True
            time.sleep(0.3)
        return not self.get_status().running and not self._find_process_ids()

    def _terminate_processes(self, process_ids: Iterable[int], *, force: bool) -> int:
        unique_ids = [pid for pid in dict.fromkeys(int(pid) for pid in process_ids if int(pid) > 0)]
        if not unique_ids:
            return 0

        handled = 0
        try:
            import psutil

            for pid in unique_ids:
                try:
                    process = psutil.Process(pid)
                except Exception:
                    continue
                try:
                    if force:
                        process.kill()
                    else:
                        process.terminate()
                    process.wait(timeout=5 if not force else 3)
                    handled += 1
                except Exception:
                    if not force:
                        try:
                            process.kill()
                            process.wait(timeout=3)
                            handled += 1
                        except Exception:
                            pass
        except Exception:
            for pid in unique_ids:
                try:
                    os.kill(pid, 9 if force else 15)
                    handled += 1
                except Exception:
                    pass
        return handled

    def _taskkill_candidates(self) -> None:
        for name in dict.fromkeys(str(n).strip() for n in self._candidate_process_names() if str(n).strip()):
            image_name = Path(name).name
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/IM", image_name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                continue

    def _launch_with_linkmini(
        self,
        exe_path: str,
        *,
        status_callback: Optional[StatusCallback] = None,
    ) -> tuple[bool, str]:
        linkmini_exe_path = self.resolve_linkmini_exe_path() or exe_path
        exe_dir = Path(linkmini_exe_path).parent
        source_path = exe_dir / self.config.linkmini_source_name
        runtime_path = exe_dir / self.config.linkmini_runtime_name
        if not source_path.exists():
            return False, f"未找到 {self.config.linkmini_source_name}"

        self._emit(status_callback, f"检测到 {source_path.name}，优先使用 linkMini 方式启动 miniQMT...")
        ok, message = self._prepare_linkmini_runtime(source_path, runtime_path)
        if not ok:
            return False, message

        self._emit(status_callback, f"正在通过 linkMini 启动 miniQMT: {linkmini_exe_path}")
        ok, message = self._launch_executable(linkmini_exe_path, self.config.linkmini_runtime_name)
        if not ok:
            return False, message
        return True, "已发起 linkMini 启动"

    def resolve_linkmini_exe_path(self) -> str:
        configured = Path(self.config.qmt_exe_path) if self.config.qmt_exe_path else None
        qmt_path = Path(self.config.qmt_path) if self.config.qmt_path else None

        candidates: list[Path] = []
        if configured:
            configured_parent = configured.parent
            candidates.extend(
                [
                    configured_parent / "xtMiniQmt.exe",
                    configured_parent / "XtMiniQmt.exe",
                    configured_parent / "miniqmt.exe",
                    configured_parent / "MiniQmt.exe",
                ]
            )
        if qmt_path:
            candidates.extend(
                [
                    qmt_path.parent / "bin.x64" / "xtMiniQmt.exe",
                    qmt_path.parent / "bin.x64" / "XtMiniQmt.exe",
                    qmt_path.parent / "bin.x64" / "miniqmt.exe",
                    qmt_path.parent / "bin.x64" / "MiniQmt.exe",
                    qmt_path.parent / "xtMiniQmt.exe",
                    qmt_path.parent / "XtMiniQmt.exe",
                    qmt_path.parent / "miniqmt.exe",
                    qmt_path.parent / "MiniQmt.exe",
                ]
            )

        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return ""

    def _prepare_linkmini_runtime(self, source_path: Path, runtime_path: Path) -> tuple[bool, str]:
        try:
            if runtime_path.exists():
                self._make_file_writable(runtime_path)
                runtime_path.unlink()
            shutil.copy2(source_path, runtime_path)
            runtime_path.chmod(stat.S_IREAD)
            return True, f"已准备 {runtime_path.name}"
        except Exception as exc:
            return False, f"准备 {runtime_path.name} 失败: {exc}"

    @staticmethod
    def _make_file_writable(path: Path) -> None:
        try:
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
        except Exception:
            pass

    def _launch_executable(self, exe_path: str, argument: str | None = None) -> tuple[bool, str]:
        if not argument:
            try:
                os.startfile(exe_path)
                return True, "已调用 os.startfile"
            except Exception as exc:
                try:
                    subprocess.Popen(
                        [exe_path],
                        cwd=str(Path(exe_path).parent),
                        shell=False,
                    )
                    return True, "已回退 subprocess 启动"
                except Exception as sub_exc:
                    return False, f"启动 miniQMT 失败: {sub_exc}（os.startfile 错误: {exc}）"

        try:
            subprocess.Popen(
                [exe_path, argument],
                cwd=str(Path(exe_path).parent),
                shell=False,
            )
            return True, f"已通过参数 {argument} 启动"
        except Exception as exc:
            return False, f"通过参数 {argument} 启动 miniQMT 失败: {exc}"

    def _wait_for_launch_result(self, timeout_seconds: float = 20.0) -> tuple[bool, str]:
        deadline = time.time() + max(timeout_seconds, 1.0)
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
