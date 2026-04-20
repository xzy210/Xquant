from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from common.io_utils import atomic_write_json

from .strategy_constants import (
    AI_STOCK_STRATEGY_ID,
    AI_STOCK_STRATEGY_NAME,
    AI_STOCK_VIRTUAL_ACCOUNT_ID,
    OWNER_TYPE_AI,
    OWNER_TYPE_ETF_ROTATION,
    OWNER_TYPE_OTHER,
    load_default_etf_rotation_profile,
    normalize_symbol_code,
)

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


class StrategyRegistryService:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or _OWNERSHIP_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ownerships: Dict[str, SymbolOwnership] = {}
        self._load()

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
        if AI_STOCK_STRATEGY_ID not in {record.strategy_id for record in self._ownerships.values()}:
            # 仅写入策略元信息，不抢占任何股票。
            changed = changed or False
        etf_strategy_id, etf_strategy_name, etf_virtual_account_id, etf_symbols, _ = load_default_etf_rotation_profile()
        for code in etf_symbols:
            owner = self._ownerships.get(code)
            if owner is not None:
                continue
            self._ownerships[code] = SymbolOwnership(
                symbol_code=code,
                strategy_id=etf_strategy_id,
                strategy_name=etf_strategy_name,
                virtual_account_id=etf_virtual_account_id,
                owner_type=OWNER_TYPE_ETF_ROTATION,
                enabled=True,
            )
            changed = True
        return changed

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
        if not record.strategy_name and strategy_id == AI_STOCK_STRATEGY_ID:
            record.strategy_name = AI_STOCK_STRATEGY_NAME
        if not record.virtual_account_id and strategy_id == AI_STOCK_STRATEGY_ID:
            record.virtual_account_id = AI_STOCK_VIRTUAL_ACCOUNT_ID
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
