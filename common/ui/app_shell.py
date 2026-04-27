# -*- coding: utf-8 -*-
"""Business-free Qt application shell primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .themes import DARK_THEME_QSS


@dataclass(frozen=True)
class Command:
    """A command entry that can be shown in the command palette."""

    id: str
    title: str
    callback: Callable[[], None] | None = None
    description: str = ""
    shortcut: str | None = None


@dataclass(frozen=True)
class Perspective:
    """A named layout/profile hook for the application shell."""

    id: str
    title: str
    activate: Callable[["BaseMainWindow"], None] | None = None
    description: str = ""


class TabWorkspace(QTabWidget):
    """Central tab workspace used by future perspectives."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDocumentMode(True)
        self.setTabsClosable(True)
        self.setMovable(True)
        self.tabCloseRequested.connect(self.close_tab)

    def add_workspace_tab(self, widget: QWidget, title: str, *, closable: bool = True) -> int:
        index = self.addTab(widget, title)
        self.setCurrentIndex(index)
        if not closable:
            self.tabBar().setTabButton(index, self.tabBar().ButtonPosition.RightSide, None)
        return index

    def close_tab(self, index: int) -> None:
        widget = self.widget(index)
        self.removeTab(index)
        if widget is not None:
            widget.deleteLater()


class CommandPalette(QDialog):
    """Minimal searchable command palette."""

    commandTriggered = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setModal(True)
        self.resize(520, 420)

        self._commands: dict[str, Command] = {}

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Type a command...")
        self._search.textChanged.connect(self._refresh)
        self._search.returnPressed.connect(self.execute_current)

        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(lambda _item: self.execute_current())

        self._hint = QLabel("Enter: run  ·  Esc: close", self)
        self._hint.setProperty("class", "description")

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.reject)

        footer = QHBoxLayout()
        footer.addWidget(self._hint)
        footer.addStretch(1)
        footer.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._search)
        layout.addWidget(self._list, 1)
        layout.addLayout(footer)

    def set_commands(self, commands: list[Command]) -> None:
        self._commands = {command.id: command for command in commands}
        self._refresh()

    def open(self) -> None:
        self._search.clear()
        self._refresh()
        super().open()
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)

    def execute_current(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        command_id = item.data(Qt.ItemDataRole.UserRole)
        command = self._commands.get(command_id)
        if command is None:
            return
        self.commandTriggered.emit(command.id)
        self.accept()
        if command.callback is not None:
            command.callback()

    def _refresh(self) -> None:
        term = self._search.text().strip().lower()
        self._list.clear()
        for command in sorted(self._commands.values(), key=lambda item: item.title.lower()):
            haystack = f"{command.title} {command.description} {command.shortcut or ''}".lower()
            if term and term not in haystack:
                continue
            item = QListWidgetItem(self._format_command(command), self._list)
            item.setData(Qt.ItemDataRole.UserRole, command.id)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    @staticmethod
    def _format_command(command: Command) -> str:
        suffix = f"    {command.shortcut}" if command.shortcut else ""
        if command.description:
            return f"{command.title}{suffix}\n{command.description}"
        return f"{command.title}{suffix}"


class BaseMainWindow(QMainWindow):
    """Reusable main window shell with dock, perspective, and command hooks."""

    def __init__(self, title: str = "Xquant", parent: QWidget | None = None, *, theme_qss: str | None = DARK_THEME_QSS) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)

        self.workspace = TabWorkspace(self)
        self.setCentralWidget(self.workspace)

        self.command_palette = CommandPalette(self)
        self._docks: dict[str, QDockWidget] = {}
        self._perspectives: dict[str, Perspective] = {}
        self._commands: dict[str, Command] = {}
        self._command_actions: dict[str, QAction] = {}
        self.current_perspective_id: str | None = None

        if theme_qss:
            self.setStyleSheet(theme_qss)

        self.register_command(
            Command(
                id="shell.command_palette",
                title="Open Command Palette",
                callback=self.open_command_palette,
                description="Search and run registered shell commands.",
                shortcut="Ctrl+Shift+P",
            )
        )

    def register_dock(
        self,
        dock_id: str,
        title: str,
        widget: QWidget,
        *,
        area: Qt.DockWidgetArea = Qt.DockWidgetArea.LeftDockWidgetArea,
        allowed_areas: Qt.DockWidgetArea | None = None,
        visible: bool = True,
    ) -> QDockWidget:
        if dock_id in self._docks:
            raise ValueError(f"Dock already registered: {dock_id}")
        dock = QDockWidget(title, self)
        dock.setObjectName(dock_id)
        dock.setWidget(widget)
        if allowed_areas is not None:
            dock.setAllowedAreas(allowed_areas)
        self.addDockWidget(area, dock)
        dock.setVisible(visible)
        self._docks[dock_id] = dock
        return dock

    def dock(self, dock_id: str) -> QDockWidget | None:
        return self._docks.get(dock_id)

    def set_dock_visible(self, dock_id: str, visible: bool) -> None:
        dock = self._require_dock(dock_id)
        dock.setVisible(visible)

    def register_perspective(self, perspective: Perspective) -> None:
        if perspective.id in self._perspectives:
            raise ValueError(f"Perspective already registered: {perspective.id}")
        self._perspectives[perspective.id] = perspective
        self.register_command(
            Command(
                id=f"shell.perspective.{perspective.id}",
                title=f"Switch Perspective: {perspective.title}",
                callback=lambda perspective_id=perspective.id: self.activate_perspective(perspective_id),
                description=perspective.description,
            )
        )

    def activate_perspective(self, perspective_id: str) -> None:
        perspective = self._perspectives.get(perspective_id)
        if perspective is None:
            raise KeyError(f"Unknown perspective: {perspective_id}")
        self.current_perspective_id = perspective.id
        if perspective.activate is not None:
            perspective.activate(self)

    def register_command(self, command: Command) -> None:
        self._commands[command.id] = command
        if command.shortcut:
            old_action = self._command_actions.pop(command.id, None)
            if old_action is not None:
                self.removeAction(old_action)
            action = QAction(command.title, self)
            action.setShortcut(QKeySequence(command.shortcut))
            action.triggered.connect(lambda _checked=False, command_id=command.id: self.run_command(command_id))
            self.addAction(action)
            self._command_actions[command.id] = action

    def unregister_command(self, command_id: str) -> None:
        self._commands.pop(command_id, None)
        action = self._command_actions.pop(command_id, None)
        if action is not None:
            self.removeAction(action)

    def run_command(self, command_id: str) -> None:
        command = self._commands.get(command_id)
        if command is None:
            raise KeyError(f"Unknown command: {command_id}")
        if command.callback is not None:
            command.callback()

    def open_command_palette(self) -> None:
        self.command_palette.set_commands(list(self._commands.values()))
        self.command_palette.open()

    def _require_dock(self, dock_id: str) -> QDockWidget:
        dock = self._docks.get(dock_id)
        if dock is None:
            raise KeyError(f"Unknown dock: {dock_id}")
        return dock


__all__ = [
    "BaseMainWindow",
    "Command",
    "CommandPalette",
    "Perspective",
    "TabWorkspace",
]
