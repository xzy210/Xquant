from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from .strategy_adapter import LiveStrategyAdapter


@dataclass(frozen=True)
class LiveStrategyTaskSpec:
    """Task-center contribution declared by a live strategy plugin."""

    task_key: str
    task_type: str
    title: str
    provider: Callable[[], dict]
    actions: Mapping[str, Callable[[], Any]] = field(default_factory=dict)
    strategy_id: str = ""
    strategy_name: str = ""
    order: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_key", str(self.task_key or "").strip())
        object.__setattr__(self, "task_type", str(self.task_type or "").strip())
        object.__setattr__(self, "title", str(self.title or self.task_key or "").strip())
        object.__setattr__(self, "strategy_id", str(self.strategy_id or "").strip())
        object.__setattr__(self, "strategy_name", str(self.strategy_name or self.strategy_id or "").strip())
        object.__setattr__(self, "actions", dict(self.actions or {}))
        if not self.task_key:
            raise ValueError("task_key is required")
        if not self.task_type:
            raise ValueError("task_type is required")


@dataclass(frozen=True)
class LiveStrategyPortfolioProvider:
    """Portfolio/performance contribution declared by a live strategy plugin."""

    strategy_id: str
    strategy_name: str = ""
    account_row_provider: Optional[Callable[[Any, list[dict] | None], dict]] = None
    position_rows_provider: Optional[Callable[[Any, list[dict] | None], list[dict]]] = None
    finalize_day_provider: Optional[Callable[[Any, str], dict]] = None
    name_resolver: Optional[Callable[[str], str]] = None
    order: int = 100
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", str(self.strategy_id or "").strip())
        object.__setattr__(self, "strategy_name", str(self.strategy_name or self.strategy_id or "").strip())
        if not self.strategy_id:
            raise ValueError("strategy_id is required")


@dataclass(frozen=True)
class LiveStrategyPlugin:
    """Metadata and integration objects for a live strategy center plugin."""

    plugin_id: str
    plugin_name: str
    adapter: Optional[LiveStrategyAdapter] = None
    widget: object | None = None
    tab_key: str = ""
    tab_title: str = ""
    task_specs: Iterable[LiveStrategyTaskSpec] = field(default_factory=tuple)
    portfolio_providers: Iterable[LiveStrategyPortfolioProvider] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    order: int = 100
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_id", str(self.plugin_id or "").strip())
        object.__setattr__(self, "plugin_name", str(self.plugin_name or self.plugin_id or "").strip())
        object.__setattr__(self, "tab_key", str(self.tab_key or "").strip())
        object.__setattr__(self, "tab_title", str(self.tab_title or "").strip())
        object.__setattr__(self, "task_specs", tuple(self.task_specs or ()))
        object.__setattr__(self, "portfolio_providers", tuple(self.portfolio_providers or ()))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not self.plugin_id:
            raise ValueError("plugin_id is required")

    @property
    def has_tab(self) -> bool:
        return self.widget is not None and bool(self.tab_key) and bool(self.tab_title)


class LiveStrategyPluginRegistry:
    """In-memory registry for live strategy center plugins."""

    def __init__(self, plugins: Optional[Iterable[LiveStrategyPlugin]] = None) -> None:
        self._plugins: dict[str, LiveStrategyPlugin] = {}
        for plugin in list(plugins or []):
            self.register(plugin)

    def register(self, plugin: LiveStrategyPlugin) -> LiveStrategyPlugin:
        if plugin.plugin_id in self._plugins:
            raise ValueError(f"duplicate live strategy plugin: {plugin.plugin_id}")
        self._plugins[plugin.plugin_id] = plugin
        return plugin

    def get(self, plugin_id: str) -> LiveStrategyPlugin | None:
        return self._plugins.get(str(plugin_id or "").strip())

    def plugins(self, *, include_disabled: bool = False) -> list[LiveStrategyPlugin]:
        result = list(self._plugins.values())
        if not include_disabled:
            result = [plugin for plugin in result if plugin.enabled]
        return sorted(result, key=lambda item: (item.order, item.plugin_name, item.plugin_id))

    def adapters(self, *, include_disabled: bool = False) -> list[LiveStrategyAdapter]:
        return [
            plugin.adapter
            for plugin in self.plugins(include_disabled=include_disabled)
            if plugin.adapter is not None
        ]

    def tab_specs(self, *, include_disabled: bool = False) -> list[tuple[str, str, object]]:
        return [
            (plugin.tab_key, plugin.tab_title, plugin.widget)
            for plugin in self.plugins(include_disabled=include_disabled)
            if plugin.has_tab
        ]

    def task_specs(self, *, include_disabled: bool = False) -> list[LiveStrategyTaskSpec]:
        result: list[tuple[int, int, LiveStrategyTaskSpec]] = []
        for plugin_index, plugin in enumerate(self.plugins(include_disabled=include_disabled)):
            for task in tuple(plugin.task_specs or ()):
                result.append((plugin.order, task.order + plugin_index, task))
        return [task for _plugin_order, _task_order, task in sorted(result, key=lambda item: (item[0], item[1], item[2].title))]

    def portfolio_providers(self, *, include_disabled: bool = False) -> list[LiveStrategyPortfolioProvider]:
        result: list[tuple[int, int, int, LiveStrategyPortfolioProvider]] = []
        for plugin_index, plugin in enumerate(self.plugins(include_disabled=include_disabled)):
            for provider in tuple(plugin.portfolio_providers or ()):
                if include_disabled or provider.enabled:
                    result.append((provider.order, plugin.order, plugin_index, provider))
        return [provider for _provider_order, _plugin_order, _plugin_index, provider in sorted(result, key=lambda item: (item[0], item[1], item[2], item[3].strategy_name))]
