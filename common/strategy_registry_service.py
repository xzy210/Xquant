from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .strategy_spec import StrategySpec
from .strategy_spec_service import get_strategy_spec_service


@dataclass(frozen=True)
class StrategyRegistration:
    spec: StrategySpec
    factory: Optional[Callable[[], Any]] = None
    strategy_class: Optional[type] = None

    @property
    def strategy_id(self) -> str:
        return self.spec.strategy_id

    @property
    def strategy_name(self) -> str:
        return self.spec.strategy_name

    def create(self, params: Optional[dict] = None) -> Any:
        if self.factory is None:
            raise ValueError(f"策略 {self.strategy_id} 未注册可创建工厂")
        strategy = self.factory()
        if params and hasattr(strategy, "set_params"):
            strategy.set_params(params)
        return strategy


class StrategyRegistryService:
    """Research/backtest-safe strategy registration center."""

    def __init__(self) -> None:
        self._strategy_registrations: Dict[str, StrategyRegistration] = {}
        self._strategy_aliases: Dict[str, str] = {}
        self._ensure_builtin_strategy_registrations()

    def _ensure_builtin_strategy_registrations(self) -> None:
        for spec in get_strategy_spec_service().list_specs(include_hidden=True, include_test=True):
            self.register_strategy_spec(spec, override=False)

    def normalize_strategy_id(self, strategy_id: str) -> str:
        normalized = str(strategy_id or "").strip()
        return self._strategy_aliases.get(normalized, normalized)

    def register_strategy_alias(self, alias: str, strategy_id: str) -> str:
        normalized_alias = str(alias or "").strip()
        normalized_id = self.normalize_strategy_id(strategy_id)
        if not normalized_alias or not normalized_id:
            raise ValueError("strategy alias and strategy_id are required")
        self._strategy_aliases[normalized_alias] = normalized_id
        return normalized_id

    def register_strategy_spec(self, spec: StrategySpec, *, override: bool = True) -> StrategyRegistration:
        if spec is None or not spec.strategy_id:
            raise ValueError("strategy spec with strategy_id is required")
        existing = self._strategy_registrations.get(spec.strategy_id)
        if existing and not override:
            return existing
        get_strategy_spec_service().register(spec, override=True)
        registration = StrategyRegistration(
            spec=spec,
            factory=existing.factory if existing else None,
            strategy_class=existing.strategy_class if existing else None,
        )
        self._strategy_registrations[spec.strategy_id] = registration
        return registration

    def register_strategy_class(
        self,
        strategy_class: type,
        *,
        override: bool = True,
        aliases: Optional[List[str]] = None,
    ) -> StrategyRegistration:
        spec = getattr(strategy_class, "spec", None)
        if not isinstance(spec, StrategySpec) or not spec.strategy_id:
            raise ValueError("strategy_class must expose common StrategySpec as .spec")
        existing = self._strategy_registrations.get(spec.strategy_id)
        if existing and existing.strategy_class is not None and not override:
            return existing
        get_strategy_spec_service().register(spec, override=True)
        registration = StrategyRegistration(spec=spec, factory=strategy_class, strategy_class=strategy_class)
        self._strategy_registrations[spec.strategy_id] = registration
        for alias in aliases or []:
            self.register_strategy_alias(alias, spec.strategy_id)
        return registration

    def get_strategy_registration(self, strategy_id: str) -> Optional[StrategyRegistration]:
        return self._strategy_registrations.get(self.normalize_strategy_id(strategy_id))

    def list_strategy_registrations(
        self,
        *,
        include_hidden: bool = False,
        include_test: bool = True,
    ) -> List[StrategyRegistration]:
        registrations = list(self._strategy_registrations.values())
        if not include_hidden:
            registrations = [item for item in registrations if not item.spec.hidden]
        if not include_test:
            registrations = [item for item in registrations if not item.spec.is_test]
        return sorted(registrations, key=lambda item: (item.spec.plugin_name or item.spec.strategy_name, item.spec.strategy_id))

    def list_strategy_specs(self, *, include_hidden: bool = False, include_test: bool = True) -> List[StrategySpec]:
        return [item.spec for item in self.list_strategy_registrations(include_hidden=include_hidden, include_test=include_test)]

    def get_strategy_labels(self, *, include_hidden: bool = False, include_test: bool = True) -> Dict[str, str]:
        return {
            item.strategy_id: item.strategy_name
            for item in self.list_strategy_registrations(include_hidden=include_hidden, include_test=include_test)
            if item.factory is not None or item.strategy_class is not None
        }

    def get_strategy_class(self, strategy_id: str) -> Optional[type]:
        registration = self.get_strategy_registration(strategy_id)
        return registration.strategy_class if registration else None

    def create_strategy(self, strategy_id: str, params: Optional[dict] = None) -> Any:
        normalized_id = self.normalize_strategy_id(strategy_id)
        registration = self.get_strategy_registration(normalized_id)
        if registration is None or registration.factory is None:
            raise ValueError(f"未知策略: {strategy_id}")
        return registration.create(params)


_strategy_registry_service: Optional[StrategyRegistryService] = None


def get_strategy_registry_service() -> StrategyRegistryService:
    global _strategy_registry_service
    if _strategy_registry_service is None:
        _strategy_registry_service = StrategyRegistryService()
    return _strategy_registry_service
