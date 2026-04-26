from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from common.io_utils import atomic_write_json
from common.strategy_spec import StrategySpec

from .strategy_constants import (
    OWNER_TYPE_OTHER,
    normalize_symbol_code,
)
from .strategy_spec_service import get_strategy_spec_service

logger = logging.getLogger(__name__)

_OWNERSHIP_PATH = Path(__file__).resolve().parent.parent / "config" / "strategy_symbol_ownership.json"


@dataclass
class SymbolOwnership:
    symbol_code: str
    strategy_id: str
    strategy_name: str = ""
    virtual_account_id: str = ""
    owner_type: str = OWNER_TYPE_OTHER
    enabled: bool = True
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.symbol_code = normalize_symbol_code(self.symbol_code)
        self.strategy_id = (self.strategy_id or "").strip()
        self.strategy_name = (self.strategy_name or "").strip()
        self.virtual_account_id = (self.virtual_account_id or "").strip()
        self.owner_type = (self.owner_type or OWNER_TYPE_OTHER).strip() or OWNER_TYPE_OTHER
        if not self.updated_at:
            self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SymbolOwnership":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


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
    def __init__(self, path: Optional[Path] = None):
        self.path = path or _OWNERSHIP_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ownerships: Dict[str, SymbolOwnership] = {}
        self._strategy_registrations: Dict[str, StrategyRegistration] = {}
        self._strategy_aliases: Dict[str, str] = {}
        self._load()
        self._ensure_builtin_strategy_registrations()

    def _load(self) -> None:
        raw = {}
        if self.path.exists():
            try:
                import json

                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f) or {}
            except Exception as exc:
                logger.warning("读取股票归属注册表失败，使用空表重建: %s", exc)
        ownerships = raw.get("ownerships", {}) if isinstance(raw, dict) else {}
        self._ownerships = {}
        if isinstance(ownerships, list):
            iterable = ownerships
        else:
            iterable = ownerships.values()
        for item in iterable:
            try:
                record = SymbolOwnership.from_dict(item or {})
            except Exception:
                continue
            if record.symbol_code and record.strategy_id:
                self._ownerships[record.symbol_code] = record
        changed = self._ensure_default_ownerships()
        if changed or not self.path.exists():
            self._save()

    def _save(self) -> None:
        payload = {
            "ownerships": {
                code: record.to_dict()
                for code, record in sorted(self._ownerships.items(), key=lambda item: item[0])
            }
        }
        atomic_write_json(self.path, payload)

    def _ensure_default_ownerships(self) -> bool:
        changed = False
        ai_spec = get_strategy_spec_service().ai_stock()
        if ai_spec.strategy_id not in {record.strategy_id for record in self._ownerships.values()}:
            # 仅写入策略元信息，不抢占任何股票。
            changed = changed or False
        etf_spec = get_strategy_spec_service().etf_rotation()
        legacy_etf_strategy_ids = {"etf_three_factor_momentum"}
        for code in etf_spec.universe:
            owner = self._ownerships.get(code)
            if owner is not None:
                if owner.strategy_id in legacy_etf_strategy_ids:
                    owner.strategy_id = etf_spec.strategy_id
                    owner.strategy_name = etf_spec.strategy_name
                    owner.virtual_account_id = etf_spec.virtual_account_id
                    owner.owner_type = etf_spec.owner_type
                    owner.enabled = True
                    owner.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
                continue
            self._ownerships[code] = SymbolOwnership(
                symbol_code=code,
                strategy_id=etf_spec.strategy_id,
                strategy_name=etf_spec.strategy_name,
                virtual_account_id=etf_spec.virtual_account_id,
                owner_type=etf_spec.owner_type,
                enabled=True,
            )
            changed = True
        return changed

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
        registration = StrategyRegistration(spec=spec, factory=existing.factory if existing else None, strategy_class=existing.strategy_class if existing else None)
        self._strategy_registrations[spec.strategy_id] = registration
        return registration

    def register_strategy_class(self, strategy_class: type, *, override: bool = True, aliases: Optional[List[str]] = None) -> StrategyRegistration:
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

    def list_strategy_registrations(self, *, include_hidden: bool = False, include_test: bool = True) -> List[StrategyRegistration]:
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

    def get_owner(self, symbol_code: str) -> Optional[SymbolOwnership]:
        code = normalize_symbol_code(symbol_code)
        if not code:
            return None
        return self._ownerships.get(code)

    def list_symbols(self, strategy_id: str = "", enabled_only: bool = True) -> List[SymbolOwnership]:
        items = list(self._ownerships.values())
        if strategy_id:
            items = [item for item in items if item.strategy_id == strategy_id]
        if enabled_only:
            items = [item for item in items if item.enabled]
        return sorted(items, key=lambda item: item.symbol_code)

    def get_conflicts(self, strategy_id: str, symbols: List[str]) -> List[dict]:
        conflicts = []
        for raw_code in symbols or []:
            code = normalize_symbol_code(raw_code)
            if not code:
                continue
            owner = self.get_owner(code)
            if owner and owner.enabled and owner.strategy_id != strategy_id:
                conflicts.append(
                    {
                        "symbol_code": code,
                        "strategy_id": owner.strategy_id,
                        "strategy_name": owner.strategy_name,
                        "owner_type": owner.owner_type,
                    }
                )
        return conflicts

    def claim_symbol(
        self,
        symbol_code: str,
        *,
        strategy_id: str,
        strategy_name: str = "",
        virtual_account_id: str = "",
        owner_type: str = OWNER_TYPE_OTHER,
        allow_reassign: bool = False,
    ) -> Tuple[bool, str, Optional[SymbolOwnership]]:
        code = normalize_symbol_code(symbol_code)
        strategy_id = (strategy_id or "").strip()
        if not code or not strategy_id:
            return False, "股票归属登记缺少 symbol_code 或 strategy_id", None

        existing = self._ownerships.get(code)
        if existing and existing.enabled and existing.strategy_id != strategy_id and not allow_reassign:
            return (
                False,
                f"{code} 已归属于 {existing.strategy_name or existing.strategy_id}，不能重复分配",
                existing,
            )

        record = SymbolOwnership(
            symbol_code=code,
            strategy_id=strategy_id,
            strategy_name=strategy_name or existing.strategy_name if existing else strategy_name,
            virtual_account_id=virtual_account_id or existing.virtual_account_id if existing else virtual_account_id,
            owner_type=owner_type or (existing.owner_type if existing else OWNER_TYPE_OTHER),
            enabled=True,
        )
        ai_spec = get_strategy_spec_service().ai_stock()
        if not record.strategy_name and strategy_id == ai_spec.strategy_id:
            record.strategy_name = ai_spec.strategy_name
        if not record.virtual_account_id and strategy_id == ai_spec.strategy_id:
            record.virtual_account_id = ai_spec.virtual_account_id
        self._ownerships[code] = record
        self._save()
        return True, "", record

    def validate_or_claim(
        self,
        symbol_code: str,
        *,
        strategy_id: str,
        strategy_name: str = "",
        virtual_account_id: str = "",
        owner_type: str = OWNER_TYPE_OTHER,
        auto_claim: bool = True,
    ) -> Tuple[bool, str, Optional[SymbolOwnership]]:
        code = normalize_symbol_code(symbol_code)
        strategy_id = (strategy_id or "").strip()
        if not code or not strategy_id:
            return False, "策略归属校验缺少 symbol_code 或 strategy_id", None

        owner = self.get_owner(code)
        if owner and owner.enabled:
            if owner.strategy_id == strategy_id:
                return True, "", owner
            return (
                False,
                f"{code} 已归属于 {owner.strategy_name or owner.strategy_id}，当前策略无权操作",
                owner,
            )

        if not auto_claim:
            return False, f"{code} 尚未分配策略归属", None

        return self.claim_symbol(
            code,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            virtual_account_id=virtual_account_id,
            owner_type=owner_type,
        )

    def ensure_strategy_symbols(
        self,
        *,
        strategy_id: str,
        symbols: List[str],
        strategy_name: str = "",
        virtual_account_id: str = "",
        owner_type: str = OWNER_TYPE_OTHER,
    ) -> Tuple[bool, str]:
        conflicts = self.get_conflicts(strategy_id, symbols)
        if conflicts:
            detail = ", ".join(
                f"{item['symbol_code']}->{item['strategy_name'] or item['strategy_id']}" for item in conflicts
            )
            return False, f"发现跨策略股票冲突: {detail}"
        changed = False
        for code in symbols or []:
            ok, message, _ = self.claim_symbol(
                code,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                virtual_account_id=virtual_account_id,
                owner_type=owner_type,
            )
            if not ok:
                return False, message
            changed = True
        if changed:
            self._save()
        return True, ""

    def release_symbol(
        self,
        symbol_code: str,
        *,
        strategy_id: str = "",
    ) -> bool:
        code = normalize_symbol_code(symbol_code)
        if not code:
            return False
        existing = self._ownerships.get(code)
        if existing is None:
            return False
        target_strategy_id = (strategy_id or "").strip()
        if target_strategy_id and existing.strategy_id != target_strategy_id:
            return False
        self._ownerships.pop(code, None)
        self._save()
        return True


_strategy_registry_service: Optional[StrategyRegistryService] = None


def get_strategy_registry_service() -> StrategyRegistryService:
    global _strategy_registry_service
    if _strategy_registry_service is None:
        _strategy_registry_service = StrategyRegistryService()
    return _strategy_registry_service
