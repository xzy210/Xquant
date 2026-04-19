"""声明式风控配置 schema。

Policy 通过 :meth:`config_schema` 返回 :class:`RiskConfigField` 列表，UI 层据此
自动渲染表单 —— 新增 / 删减字段只需改 policy，无须再为每个字段写控件。

该 schema 只描述 "字段如何渲染 + 合法值范围"，不承担校验 / 保存语义；
具体读写由 policy 的 :meth:`get_config` / :meth:`apply_config` 负责。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

FieldType = str  # 约束值：bool / int / float / time / text / choice


@dataclass(frozen=True)
class RiskConfigField:
    """单个可配置字段的声明式描述。

    Attributes:
        name:        字段名（与 policy.get_config / apply_config 的 key 对应）
        label:       UI 显示文案
        type:        类型枚举 ``bool / int / float / time / text / choice``
        default:     默认值；UI 在 "恢复默认" 时回填
        min_value:   数值下限（int / float 生效）
        max_value:   数值上限（int / float 生效）
        step:        步长（SpinBox 生效）
        decimals:    小数位数（float 生效）
        suffix:      显示后缀，如 " %" / " 秒"
        help:        字段说明（渲染为 tooltip 和副文本）
        choices:     ``choice`` 类型用的 ``[(value, label), ...]``
        display_scale: 显示值 = 存储值 * display_scale，常用于把 0.08 渲染成 "8"
        group:       分组名（用于 UI 折叠分组；同名字段会聚合渲染在一起）
        depends_on:  仅当某字段值为 True 时启用本字段（例如 ``enable_risk_check``）
    """

    name: str
    label: str
    type: FieldType
    default: Any = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None
    decimals: Optional[int] = None
    suffix: str = ""
    help: str = ""
    choices: Sequence[tuple] = field(default_factory=tuple)
    display_scale: float = 1.0
    group: str = ""
    depends_on: Optional[str] = None

    def to_display(self, value: Any) -> Any:
        if self.type in {"int", "float"} and self.display_scale and self.display_scale != 1.0:
            try:
                return float(value) * float(self.display_scale)
            except (TypeError, ValueError):
                return value
        return value

    def from_display(self, display_value: Any) -> Any:
        if self.type in {"int", "float"} and self.display_scale and self.display_scale != 1.0:
            try:
                raw = float(display_value) / float(self.display_scale)
            except (TypeError, ValueError):
                return display_value
            return int(round(raw)) if self.type == "int" else raw
        if self.type == "int":
            try:
                return int(display_value)
            except (TypeError, ValueError):
                return display_value
        if self.type == "float":
            try:
                return float(display_value)
            except (TypeError, ValueError):
                return display_value
        if self.type == "bool":
            return bool(display_value)
        return display_value


ConfigSaver = Callable[[dict], None]
