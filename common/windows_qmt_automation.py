"""
Windows UI automation helpers for miniQMT.
"""
from __future__ import annotations

import logging
import threading
import time
import ctypes
from dataclasses import dataclass
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_HINTS = ("miniqmt", "qmt", "迅投", "交易端", "中金财富", "极速策略")
DEFAULT_LOGIN_BUTTON_REL_X = 0.38
DEFAULT_LOGIN_BUTTON_REL_Y = 0.855

_LOGIN_OCR_KEYWORDS = ("登录", "密码", "记住密码", "自动登录", "脱机", "独立交易")
_MAIN_OCR_KEYWORDS = ("委托", "持仓", "成交", "撤单", "资金")


@dataclass
class WindowProbeResult:
    login_window_found: bool = False
    main_window_found: bool = False
    matched_titles: List[str] | None = None


@dataclass
class DesktopInteractionState:
    interactive: bool
    desktop_name: str | None = None
    message: str = ""


class QmtWindowAutomation:
    """Best-effort UI automation for miniQMT login windows."""

    _ocr_engine: object = None
    _ocr_available: Optional[bool] = None
    _ocr_init_lock = threading.Lock()
    _ocr_classify_cache: dict[int, tuple[float, Optional[str]]] = {}
    _OCR_CACHE_TTL = 10.0

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
            window_type = self._classify_window(handle, title)
            if window_type == "login":
                result.login_window_found = True
            elif window_type == "main":
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

        desktop_state = self.get_desktop_interaction_state()
        if not desktop_state.interactive:
            return False, desktop_state.message

        deadline = time.time() + max(timeout, 1.0)
        last_error = "未找到 miniQMT 登录窗口"
        while time.time() < deadline:
            login_handles = self._get_login_handles()
            for handle in login_handles:
                try:
                    return self._fill_login_window(handle, username, password)
                except Exception as exc:
                    blocked_message = self._normalize_desktop_interaction_error(exc)
                    if blocked_message:
                        logger.warning(blocked_message)
                        return False, blocked_message
                    last_error = f"登录窗口自动化失败: {exc}"
                    logger.warning(last_error)
            time.sleep(0.5)
        return False, last_error

    def click_login_button(self, *, timeout: float = 10.0) -> tuple[bool, str]:
        if not self.is_available():
            return False, "未安装 win32gui/pywin32，无法自动点击 miniQMT 登录按钮"

        desktop_state = self.get_desktop_interaction_state()
        if not desktop_state.interactive:
            return False, desktop_state.message

        deadline = time.time() + max(timeout, 1.0)
        last_error = "未找到 miniQMT 登录窗口"
        while time.time() < deadline:
            login_handles = self._get_login_handles()
            for handle in login_handles:
                try:
                    return self._click_login_by_relative_position(handle)
                except Exception as exc:
                    blocked_message = self._normalize_desktop_interaction_error(exc)
                    if blocked_message:
                        logger.warning(blocked_message)
                        return False, blocked_message
                    last_error = f"点击登录按钮失败: {exc}"
                    logger.warning(last_error)
            time.sleep(0.5)
        return False, last_error

    def get_desktop_interaction_state(self) -> DesktopInteractionState:
        desktop_name, error_message = self._get_input_desktop_name()
        if error_message:
            return DesktopInteractionState(
                interactive=False,
                desktop_name=None,
                message=(
                    "当前无法访问 Windows 输入桌面，可能处于锁屏、远程桌面断开后的无活动桌面，"
                    f"或非交互会话中: {error_message}"
                ),
            )
        if not desktop_name:
            return DesktopInteractionState(
                interactive=False,
                desktop_name=None,
                message="当前未获取到 Windows 输入桌面，可能处于锁屏或无活动桌面状态",
            )
        if desktop_name.strip().lower() != "default":
            return DesktopInteractionState(
                interactive=False,
                desktop_name=desktop_name,
                message=f"当前 Windows 输入桌面为 {desktop_name}，不是可交互桌面，无法自动登录 miniQMT",
            )
        return DesktopInteractionState(
            interactive=True,
            desktop_name=desktop_name,
            message=f"当前输入桌面可交互: {desktop_name}",
        )

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

    # ------------------------------------------------------------------
    # Window classification: title keyword fast-path → OCR (with cache)
    # ------------------------------------------------------------------

    def _classify_window(self, handle: int, title: str) -> Optional[str]:
        """Classify a candidate window as 'login', 'main', or None (unknown).

        Fast path: title contains explicit keyword.
        Slow path: screenshot + OCR with class-level cache (TTL based).
        """
        lower_title = title.lower()
        if "登录" in lower_title or "login" in lower_title:
            return "login"

        cache = QmtWindowAutomation._ocr_classify_cache
        entry = cache.get(handle)
        if entry is not None:
            ts, cached_result = entry
            if time.time() - ts <= self._OCR_CACHE_TTL:
                return cached_result

        ocr_result = self._classify_window_by_ocr(handle)
        cache[handle] = (time.time(), ocr_result)

        now = time.time()
        stale = [h for h, (ts, _) in cache.items() if now - ts > self._OCR_CACHE_TTL * 3]
        for h in stale:
            del cache[h]

        return ocr_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    def _get_login_handles(self) -> List[int]:
        handles = list(self._iter_windows())
        login_handles = [
            handle
            for handle in handles
            if self._classify_window(handle, self._safe_window_text(handle)) == "login"
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

    @staticmethod
    def _normalize_desktop_interaction_error(exc: Exception) -> str | None:
        message = str(exc) or exc.__class__.__name__
        lowered = message.lower()
        if "there is no active desktop required for moving mouse cursor" in lowered:
            return "当前无活动桌面，无法移动鼠标点击 miniQMT 登录按钮"
        if "screen has been locked" in lowered:
            return "当前桌面已锁定，无法执行 miniQMT 自动登录"
        return None

    @staticmethod
    def _get_input_desktop_name() -> tuple[str | None, str | None]:
        user32 = ctypes.windll.user32
        UOI_NAME = 2
        DESKTOP_READOBJECTS = 0x0001
        DESKTOP_SWITCHDESKTOP = 0x0100

        ctypes.set_last_error(0)
        desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS | DESKTOP_SWITCHDESKTOP)
        if not desktop:
            err = ctypes.get_last_error()
            return None, ctypes.FormatError(err).strip() if err else "OpenInputDesktop 返回空句柄"

        try:
            needed = ctypes.c_uint(0)
            user32.GetUserObjectInformationW(desktop, UOI_NAME, None, 0, ctypes.byref(needed))
            if needed.value <= 0:
                err = ctypes.get_last_error()
                return None, ctypes.FormatError(err).strip() if err else "GetUserObjectInformationW 未返回桌面名称长度"

            buf = ctypes.create_unicode_buffer(max(needed.value // ctypes.sizeof(ctypes.c_wchar), 1) + 1)
            ok = user32.GetUserObjectInformationW(
                desktop,
                UOI_NAME,
                buf,
                ctypes.sizeof(buf),
                ctypes.byref(needed),
            )
            if not ok:
                err = ctypes.get_last_error()
                return None, ctypes.FormatError(err).strip() if err else "GetUserObjectInformationW 获取桌面名称失败"
            return buf.value, None
        finally:
            user32.CloseDesktop(desktop)

    # ------------------------------------------------------------------
    # OCR-based window classification
    # ------------------------------------------------------------------

    @classmethod
    def _get_ocr_engine(cls) -> object | None:
        if cls._ocr_available is False:
            return None
        if cls._ocr_engine is not None:
            return cls._ocr_engine
        with cls._ocr_init_lock:
            if cls._ocr_engine is not None:
                return cls._ocr_engine
            try:
                logging.getLogger("rapidocr").setLevel(logging.WARNING)
                from rapidocr import RapidOCR
                cls._ocr_engine = RapidOCR()
                cls._ocr_available = True
                logger.info("OCR 引擎已初始化 (rapidocr)")
                return cls._ocr_engine
            except Exception as exc:
                cls._ocr_available = False
                logger.debug("rapidocr 不可用，OCR 回退已禁用: %s", exc)
                return None

    @staticmethod
    def _capture_window_image(handle: int):
        """截取窗口为 numpy BGR 数组（支持被遮挡的窗口）。"""
        try:
            import numpy as np
            import win32gui
            import win32ui
        except Exception:
            return None

        try:
            left, top, right, bottom = win32gui.GetWindowRect(handle)
            w, h = right - left, bottom - top
            if w <= 0 or h <= 0:
                return None

            hwnd_dc = win32gui.GetWindowDC(handle)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)

            ctypes.windll.user32.PrintWindow(handle, save_dc.GetSafeHdc(), 3)

            bmp_bits = bitmap.GetBitmapBits(True)
            img = np.frombuffer(bmp_bits, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()

            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(handle, hwnd_dc)
            win32gui.DeleteObject(bitmap.GetHandle())
            return img
        except Exception as exc:
            logger.debug("窗口截图失败: %s", exc)
            return None

    def _classify_window_by_ocr(self, handle: int) -> Optional[str]:
        """截图 + OCR 判定窗口类型，返回 'login' / 'main' / None。"""
        engine = self._get_ocr_engine()
        if engine is None:
            return None

        img = self._capture_window_image(handle)
        if img is None:
            return None

        try:
            result = engine(img)
            if not result or result.txts is None or len(result.txts) == 0:
                # 登录对话框是静态 Qt 窗口，PrintWindow 始终能捕获到文字。
                # 检测不到任何文字，说明是硬件渲染的主交易界面。
                logger.info("OCR 未检测到文字，推断为主界面（硬件渲染窗口）")
                return "main"

            all_text = " ".join(result.txts)
            login_hits = sum(1 for kw in _LOGIN_OCR_KEYWORDS if kw in all_text)
            main_hits = sum(1 for kw in _MAIN_OCR_KEYWORDS if kw in all_text)

            if login_hits >= 2:
                logger.info("OCR 判定为登录窗口 (命中 %d 个关键词: %s)",
                            login_hits, [kw for kw in _LOGIN_OCR_KEYWORDS if kw in all_text])
                return "login"
            if main_hits >= 2:
                logger.info("OCR 判定为主界面 (命中 %d 个关键词: %s)",
                            main_hits, [kw for kw in _MAIN_OCR_KEYWORDS if kw in all_text])
                return "main"
            logger.debug("OCR 未能明确判定窗口类型，识别文本: %s", all_text[:200])
            return None
        except Exception as exc:
            logger.debug("OCR 识别异常: %s", exc)
            return None
