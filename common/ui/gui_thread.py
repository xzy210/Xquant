# -*- coding: utf-8 -*-
"""把调用送回 Qt 主线程再执行。"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThread

T = TypeVar("T")

_invoker: "_GuiInvoker | None" = None
_invoker_lock = threading.Lock()


class _CallbackEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, func: Callable[[], object], done: threading.Event, box: dict[str, object]) -> None:
        super().__init__(self.EVENT_TYPE)
        self.func = func
        self.done = done
        self.box = box


class _GuiInvoker(QObject):
    """常驻主线程，接收工作线程投递的调用。"""

    def customEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() != _CallbackEvent.EVENT_TYPE or not isinstance(event, _CallbackEvent):
            super().customEvent(event)
            return
        try:
            event.box["value"] = event.func()
        except Exception as exc:
            event.box["error"] = exc
        finally:
            event.done.set()


def ensure_gui_invoker() -> _GuiInvoker:
    """在主线程创建调用器。窗口初始化时调用一次即可。"""
    global _invoker
    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("Qt 应用尚未启动，无法创建主线程调用器")
    if QThread.currentThread() is not app.thread():
        raise RuntimeError("主线程调用器只能在主线程创建")
    with _invoker_lock:
        if _invoker is None:
            _invoker = _GuiInvoker(app)
        return _invoker


def call_on_qobject_thread(owner: QObject, func: Callable[[], T]) -> T:
    """在 owner 所在线程执行 func，并返回结果。

    引擎和面板都建在主线程上时，Qt 自动连接会在工作线程里直接调用界面槽。
    日终暂停调度必须在主线程里停 QTimer、刷新控件；这里阻塞等到主线程执行完，
    调用方仍能拿到原来的返回值。
    """
    if owner.thread() is QThread.currentThread():
        app = QCoreApplication.instance()
        if app is not None and owner.thread() is app.thread():
            ensure_gui_invoker()
        return func()

    app = QCoreApplication.instance()
    if app is None or owner.thread() is not app.thread():
        raise RuntimeError("只能把调用送回 Qt 主线程上的对象")

    invoker = _invoker
    if invoker is None:
        raise RuntimeError("主线程调用器尚未初始化")

    done = threading.Event()
    box: dict[str, object] = {}
    QCoreApplication.postEvent(invoker, _CallbackEvent(func, done, box))
    done.wait()
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")  # type: ignore[return-value]
