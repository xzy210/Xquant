"""
Windows UI automation helpers for miniQMT.
"""
from __future__ import annotations

import logging
import time
import ctypes
from dataclasses import dataclass
from typing import Iterable, List

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_HINTS = ("miniqmt", "qmt", "迅投", "交易端", "中金财富", "极速策略")
DEFAULT_LOGIN_BUTTON_REL_X = 0.38
DEFAULT_LOGIN_BUTTON_REL_Y = 0.855


@dataclass
class WindowProbeResult:
    login_window_found: bool = False
    main_window_found: bool = False
    matched_titles: List[str] | None = None


class QmtWindowAutomation:
    """Best-effort UI automation for miniQMT login windows."""

    def __init__(
        self,
        title_hint: str = "",
        *,
        login_button_rel_x: float = DEFAULT_LOGIN_BUTTON_REL_X,
        login_button_rel_y: float = DEFAULT_LOGIN_BUTTON_REL_Y,
    ) -> None:
        self.title_hint = (title_hint or "").strip()
        self.login_button_rel_x = float(login_button_rel_x or DEFAULT_LOGIN_BUTTON_REL_X)
        self.login_button_rel_y = float(login_button_rel_y or DEFAULT_LOGIN_BUTTON_REL_Y)
        self._ensure_dpi_awareness()

    def is_available(self) -> bool:
        try:
            import win32gui  # noqa: F401
            import win32process  # noqa: F401
        except Exception:
            return False
        return True

    def probe_windows(self) -> WindowProbeResult:
        result = WindowProbeResult(matched_titles=[])
        for handle in self._iter_windows():
            title = self._safe_window_text(handle)
            if title:
                result.matched_titles.append(title)
            if self._looks_like_login_window(handle, title.lower()):
                result.login_window_found = True
            else:
                result.main_window_found = True
        return result

    def wait_for_any_window(self, timeout: float = 20.0) -> WindowProbeResult:
        deadline = time.time() + max(timeout, 1.0)
        last = WindowProbeResult(matched_titles=[])
        while time.time() < deadline:
            last = self.probe_windows()
            if last.login_window_found or last.main_window_found:
                return last
            time.sleep(0.5)
        return last

    def fill_login_form(
        self,
        username: str,
        password: str,
        *,
        timeout: float = 10.0,
    ) -> tuple[bool, str]:
        if not username or not password:
            return False, "缺少 miniQMT 登录账号或密码"

        deadline = time.time() + max(timeout, 1.0)
        last_error = "未找到 miniQMT 登录窗口"
        while time.time() < deadline:
            login_handles = self._get_login_handles()
            for handle in login_handles:
                try:
                    return self._fill_login_window(handle, username, password)
                except Exception as exc:
                    last_error = f"登录窗口自动化失败: {exc}"
                    logger.warning(last_error)
            time.sleep(0.5)
        return False, last_error

    def click_login_button(self, *, timeout: float = 10.0) -> tuple[bool, str]:
        if not self.is_available():
            return False, "未安装 win32gui/pywin32，无法自动点击 miniQMT 登录按钮"

        deadline = time.time() + max(timeout, 1.0)
        last_error = "未找到 miniQMT 登录窗口"
        while time.time() < deadline:
            login_handles = self._get_login_handles()
            for handle in login_handles:
                try:
                    return self._click_login_by_relative_position(handle)
                except Exception as exc:
                    last_error = f"点击登录按钮失败: {exc}"
                    logger.warning(last_error)
            time.sleep(0.5)
        return False, last_error

    def close_windows(self) -> tuple[bool, str]:
        handles = list(self._iter_windows())
        if not handles:
            return True, "未检测到 miniQMT 窗口"

        closed_count = 0
        for handle in handles:
            try:
                import win32con
                import win32gui

                win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
                closed_count += 1
            except Exception as exc:
                logger.debug("关闭 QMT 窗口失败: %s", exc)
        return True, f"已尝试关闭 {closed_count} 个 miniQMT 窗口"

    def _iter_windows(self) -> Iterable[int]:
        try:
            import win32gui
        except Exception:
            return []

        handles: list[int] = []

        def callback(hwnd, _extra):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = self._safe_window_text(hwnd).strip()
                class_name = self._safe_class_name(hwnd).strip().lower()
                process_name = self._safe_process_name(hwnd).strip().lower()
                if self._is_candidate_window(hwnd, title, class_name, process_name):
                    handles.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        return handles

    def _fill_login_window(self, handle: int, username: str, password: str) -> tuple[bool, str]:
        window = self._connect_window(handle)
        try:
            window.set_focus()
        except Exception:
            pass

        edits = []
        for control in window.descendants(control_type="Edit"):
            try:
                if control.is_visible() and control.is_enabled():
                    edits.append(control)
            except Exception:
                continue

        if len(edits) < 2:
            raise RuntimeError("登录窗口中未找到足够的输入框")

        self._set_edit_text(edits[0], username)
        self._set_edit_text(edits[1], password)
        return self._click_login_by_relative_position(handle)

    @staticmethod
    def _set_edit_text(control: object, value: str) -> None:
        try:
            control.set_edit_text(value)
            return
        except Exception:
            pass
        control.click_input()
        try:
            control.type_keys("^a{BACKSPACE}", set_foreground=True)
            control.type_keys(value, with_spaces=True, set_foreground=True)
        except Exception as exc:
            raise RuntimeError(f"无法写入输入框: {exc}") from exc

    def _matches_title(self, title: str) -> bool:
        title_lower = title.lower()
        hints = [hint.lower() for hint in DEFAULT_WINDOW_HINTS]
        if self.title_hint:
            hints.insert(0, self.title_hint.lower())
        return any(hint in title_lower for hint in hints)

    def _is_candidate_window(
        self,
        handle: int,
        title: str,
        class_name: str,
        process_name: str,
    ) -> bool:
        if title and self._matches_title(title):
            return True
        if process_name in ("xtitclient.exe", "xtitclient"):
            return True
        if class_name == "qt5qwindowicon":
            width, height = self._safe_window_size(handle)
            return width >= 300 and height >= 200
        return False

    def _looks_like_login_window(self, handle: int, lower_title: str) -> bool:
        if "登录" in lower_title or "login" in lower_title:
            return True
        if self._matches_title(lower_title):
            width, height = self._safe_window_size(handle)
            if 420 <= width <= 1500 and 300 <= height <= 950:
                return True
        process_name = self._safe_process_name(handle).lower()
        class_name = self._safe_class_name(handle).lower()
        width, height = self._safe_window_size(handle)
        if process_name in ("xtitclient.exe", "xtitclient") and class_name == "qt5qwindowicon":
            if 900 <= width <= 1500 and 500 <= height <= 950:
                return True
        return False

    def _get_login_handles(self) -> List[int]:
        handles = list(self._iter_windows())
        login_handles = [
            handle
            for handle in handles
            if self._looks_like_login_window(handle, self._safe_window_text(handle).lower())
        ]
        if login_handles:
            return login_handles

        xt_handles = [
            handle
            for handle in handles
            if self._safe_process_name(handle).lower() in ("xtitclient.exe", "xtitclient")
        ]
        if len(xt_handles) == 1:
            logger.info("未识别到明确登录窗，回退使用唯一 XtItClient 窗口: %s", xt_handles[0])
            return xt_handles
        return []

    def _click_login_by_relative_position(self, handle: int) -> tuple[bool, str]:
        try:
            import win32gui
            from pywinauto import mouse
        except Exception as exc:
            raise RuntimeError(f"无法加载点击能力: {exc}") from exc

        try:
            win32gui.ShowWindow(handle, 5)
            win32gui.SetForegroundWindow(handle)
        except Exception:
            pass

        target_x, target_y, width, height, origin_x, origin_y = self._get_click_target(handle)

        logger.info(
            "使用相对坐标点击 QMT 登录按钮: origin_x=%s origin_y=%s width=%s height=%s x=%s y=%s",
            origin_x,
            origin_y,
            width,
            height,
            target_x,
            target_y,
        )
        mouse.move(coords=(target_x, target_y))
        time.sleep(0.15)
        mouse.click(button="left", coords=(target_x, target_y))
        return True, f"已按相对坐标点击登录按钮 ({target_x}, {target_y})"

    def _get_click_target(self, handle: int) -> tuple[int, int, int, int, int, int]:
        import win32gui

        try:
            client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(handle)
            client_origin_x, client_origin_y = win32gui.ClientToScreen(handle, (client_left, client_top))
            width = max(int(client_right - client_left), 1)
            height = max(int(client_bottom - client_top), 1)
            target_x = int(client_origin_x + width * self.login_button_rel_x)
            target_y = int(client_origin_y + height * self.login_button_rel_y)
            return target_x, target_y, width, height, client_origin_x, client_origin_y
        except Exception:
            left, top, right, bottom = win32gui.GetWindowRect(handle)
            width = max(int(right - left), 1)
            height = max(int(bottom - top), 1)
            target_x = int(left + width * self.login_button_rel_x)
            target_y = int(top + height * self.login_button_rel_y)
            return target_x, target_y, width, height, left, top

    @staticmethod
    def _safe_window_text(handle: int) -> str:
        try:
            import win32gui

            return win32gui.GetWindowText(handle) or ""
        except Exception:
            return ""

    @staticmethod
    def _safe_class_name(handle: int) -> str:
        try:
            import win32gui

            return win32gui.GetClassName(handle) or ""
        except Exception:
            return ""

    @staticmethod
    def _safe_process_name(handle: int) -> str:
        try:
            import psutil
            import win32process

            _thread_id, pid = win32process.GetWindowThreadProcessId(handle)
            if not pid:
                return ""
            return psutil.Process(pid).name() or ""
        except Exception:
            return ""

    @staticmethod
    def _safe_window_size(handle: int) -> tuple[int, int]:
        try:
            import win32gui

            left, top, right, bottom = win32gui.GetWindowRect(handle)
            return int(right - left), int(bottom - top)
        except Exception:
            return 0, 0

    @staticmethod
    def _connect_window(handle: int):
        from pywinauto import Application

        try:
            return Application(backend="win32").connect(handle=handle).window(handle=handle)
        except Exception:
            return Application(backend="uia").connect(handle=handle).window(handle=handle)

    @staticmethod
    def _ensure_dpi_awareness() -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
