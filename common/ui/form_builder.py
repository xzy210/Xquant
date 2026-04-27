# -*- coding: utf-8 -*-
"""Generate lightweight Qt forms from pydantic models."""

from __future__ import annotations

import json
import types
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


_INVALID_FIELD_STYLE = """
QLineEdit[validationState="invalid"],
QComboBox[validationState="invalid"] {
    border: 1px solid #d9534f;
}
"""


@dataclass(frozen=True)
class _RenderedField:
    name: str
    annotation: Any
    widget: QWidget
    label: QLabel
    error_label: QLabel


class FormBuilder(QWidget):
    """Build and validate a QWidget form from a pydantic BaseModel class."""

    validityChanged = pyqtSignal(bool)
    valueChanged = pyqtSignal(object)

    def __init__(
        self,
        model_cls: type[BaseModel],
        initial: BaseModel | dict[str, Any] | None = None,
        parent: QWidget | None = None,
        *,
        run_button_text: str = "Run",
    ) -> None:
        super().__init__(parent)
        if not isinstance(model_cls, type) or not issubclass(model_cls, BaseModel):
            raise TypeError("model_cls must be a pydantic BaseModel class")

        self.model_cls = model_cls
        self.run_button = QPushButton(run_button_text, self)
        self.run_button.setEnabled(False)

        self._rendered_fields: dict[str, _RenderedField] = {}
        self._current_model: BaseModel | None = None
        self._current_error: ValidationError | None = None
        self._is_valid = False
        self._style_installed = False

        self._build_layout(self._initial_values(initial))
        self.validate()

    @property
    def is_valid(self) -> bool:
        return self._is_valid

    @property
    def error(self) -> ValidationError | None:
        return self._current_error

    def model(self) -> BaseModel:
        """Return the current model or raise the current validation error."""
        self.validate()
        if self._current_model is not None:
            return self._current_model
        if self._current_error is not None:
            raise self._current_error
        raise ValueError("form is invalid")

    def model_or_none(self) -> BaseModel | None:
        self.validate()
        return self._current_model

    def values(self) -> dict[str, Any]:
        return {name: self._read_field(rendered) for name, rendered in self._rendered_fields.items()}

    def set_values(self, values: BaseModel | dict[str, Any]) -> None:
        payload = self._model_to_dict(values) if isinstance(values, BaseModel) else dict(values)
        for name, value in payload.items():
            rendered = self._rendered_fields.get(name)
            if rendered is None:
                continue
            self._write_widget_value(rendered.widget, rendered.annotation, value)
        self.validate()

    def field_widget(self, field_name: str) -> QWidget:
        rendered = self._rendered_fields.get(field_name)
        if rendered is None:
            raise KeyError(f"Unknown form field: {field_name}")
        return rendered.widget

    def field_error(self, field_name: str) -> str:
        rendered = self._rendered_fields.get(field_name)
        if rendered is None:
            raise KeyError(f"Unknown form field: {field_name}")
        return rendered.error_label.text()

    def validate(self) -> bool:
        raw_values = self.values()
        errors_by_field: dict[str, str] = {}
        previous_valid = self._is_valid

        try:
            self._current_model = self.model_cls(**raw_values)
            self._current_error = None
            self._is_valid = True
        except ValidationError as exc:
            self._current_model = None
            self._current_error = exc
            self._is_valid = False
            for error in exc.errors():
                loc = error.get("loc", ())
                if not loc:
                    continue
                field_name = str(loc[0])
                errors_by_field.setdefault(field_name, str(error.get("msg", "Invalid value")))

        for name, rendered in self._rendered_fields.items():
            message = errors_by_field.get(name, "")
            rendered.error_label.setText(message)
            self._set_invalid(rendered.widget, bool(message))

        self.run_button.setEnabled(self._is_valid)
        if previous_valid != self._is_valid:
            self.validityChanged.emit(self._is_valid)
        self.valueChanged.emit(self._current_model)
        return self._is_valid

    def _build_layout(self, initial_values: dict[str, Any]) -> None:
        self._install_validation_style()

        form_layout = QFormLayout()
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        for name, field in self._model_fields().items():
            annotation = self._field_annotation(field)
            description = self._field_description(field)
            value = initial_values.get(name, self._field_default(field))
            widget = self._create_widget(annotation, value)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            label = QLabel(self._field_label(name, field), self)
            error_label = QLabel("", self)
            error_label.setObjectName(f"{name}_error")
            error_label.setProperty("class", "error")
            error_label.setStyleSheet("color: #d9534f; font-size: 11px;")
            error_label.setWordWrap(True)

            if description:
                label.setToolTip(description)
                widget.setToolTip(description)

            field_container = QWidget(self)
            field_layout = QVBoxLayout(field_container)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(2)
            field_layout.addWidget(widget)
            field_layout.addWidget(error_label)

            rendered = _RenderedField(name=name, annotation=annotation, widget=widget, label=label, error_label=error_label)
            self._rendered_fields[name] = rendered
            self._connect_widget(widget)
            form_layout.addRow(label, field_container)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.run_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addLayout(button_row)
        layout.addStretch(1)

    def _install_validation_style(self) -> None:
        if self._style_installed:
            return
        self.setStyleSheet(self.styleSheet() + _INVALID_FIELD_STYLE)
        self._style_installed = True

    def _model_fields(self) -> dict[str, Any]:
        return dict(getattr(self.model_cls, "model_fields", None) or getattr(self.model_cls, "__fields__", {}))

    def _initial_values(self, initial: BaseModel | dict[str, Any] | None) -> dict[str, Any]:
        if initial is None:
            return {}
        if isinstance(initial, BaseModel):
            return self._model_to_dict(initial)
        return dict(initial)

    @staticmethod
    def _model_to_dict(model: BaseModel) -> dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump()
        return model.dict()

    @staticmethod
    def _field_annotation(field: Any) -> Any:
        return getattr(field, "annotation", None) or getattr(field, "outer_type_", None) or getattr(field, "type_", str)

    @staticmethod
    def _field_description(field: Any) -> str:
        description = getattr(field, "description", None)
        if description:
            return str(description)
        field_info = getattr(field, "field_info", None)
        return str(getattr(field_info, "description", "") or "")

    @staticmethod
    def _field_label(name: str, field: Any) -> str:
        title = getattr(field, "title", None)
        if not title:
            field_info = getattr(field, "field_info", None)
            title = getattr(field_info, "title", None)
        return str(title or name.replace("_", " ").title())

    @staticmethod
    def _field_default(field: Any) -> Any:
        if hasattr(field, "is_required") and field.is_required():
            return ""
        if getattr(field, "required", False):
            return ""
        if hasattr(field, "get_default"):
            try:
                return field.get_default(call_default_factory=True)
            except TypeError:
                return field.get_default()
        default_factory = getattr(field, "default_factory", None)
        if default_factory is not None:
            return default_factory()
        default = getattr(field, "default", "")
        if str(default).endswith("Undefined"):
            return ""
        return default

    def _create_widget(self, annotation: Any, value: Any) -> QWidget:
        enum_cls = self._enum_class(annotation)
        if enum_cls is not None:
            combo = QComboBox(self)
            for item in enum_cls:
                combo.addItem(str(item.value), item.value)
            self._write_widget_value(combo, annotation, value)
            return combo

        literal_values = self._literal_values(annotation)
        if literal_values:
            combo = QComboBox(self)
            for item in literal_values:
                combo.addItem(str(item), item)
            self._write_widget_value(combo, annotation, value)
            return combo

        if self._base_type(annotation) is bool:
            checkbox = QCheckBox(self)
            self._write_widget_value(checkbox, annotation, value)
            return checkbox

        line_edit = QLineEdit(self)
        self._write_widget_value(line_edit, annotation, value)
        return line_edit

    def _connect_widget(self, widget: QWidget) -> None:
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(lambda _text: self.validate())
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(lambda _checked: self.validate())
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(lambda _index: self.validate())

    def _read_field(self, rendered: _RenderedField) -> Any:
        widget = rendered.widget
        annotation = rendered.annotation
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QLineEdit):
            text = widget.text()
            if text == "" and self._allows_none(annotation):
                return None
            if self._expects_json(annotation):
                if not text.strip():
                    return [] if self._base_type(annotation) in (list, tuple, set) else {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
            return text
        return None

    def _write_widget_value(self, widget: QWidget, annotation: Any, value: Any) -> None:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
            return
        if isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:
                index = widget.findText(str(value))
            widget.setCurrentIndex(max(index, 0))
            return
        if isinstance(widget, QLineEdit):
            if value is None:
                widget.setText("")
            elif self._expects_json(annotation):
                widget.setText(json.dumps(value, ensure_ascii=False))
            else:
                widget.setText(str(value))

    @staticmethod
    def _set_invalid(widget: QWidget, invalid: bool) -> None:
        widget.setProperty("validationState", "invalid" if invalid else "valid")
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    @staticmethod
    def _base_type(annotation: Any) -> Any:
        origin = get_origin(annotation)
        if origin in (Union, types.UnionType):
            args = [item for item in get_args(annotation) if item is not type(None)]
            return FormBuilder._base_type(args[0]) if len(args) == 1 else annotation
        if origin is not None and origin is not Literal:
            return origin
        return annotation

    @staticmethod
    def _allows_none(annotation: Any) -> bool:
        return type(None) in get_args(annotation)

    @staticmethod
    def _expects_json(annotation: Any) -> bool:
        base_type = FormBuilder._base_type(annotation)
        return base_type in (list, tuple, set, dict)

    @staticmethod
    def _enum_class(annotation: Any) -> type[Enum] | None:
        base_type = FormBuilder._base_type(annotation)
        if isinstance(base_type, type) and issubclass(base_type, Enum):
            return base_type
        return None

    @staticmethod
    def _literal_values(annotation: Any) -> tuple[Any, ...]:
        if get_origin(annotation) is Literal:
            return get_args(annotation)
        if get_origin(annotation) in (Union, types.UnionType):
            for item in get_args(annotation):
                if get_origin(item) is Literal:
                    return get_args(item)
        return ()


__all__ = ["FormBuilder"]
