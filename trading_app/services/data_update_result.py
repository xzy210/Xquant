from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class DataUpdateResult:
    ok: bool
    updated_stocks: int = 0
    updated_etfs: int = 0
    updated_indices: int = 0
    updated_rotation_etfs: int = 0
    stale_codes: List[str] = field(default_factory=list)
    failed_codes: List[str] = field(default_factory=list)
    cache_refreshed: bool = False
    cache_refreshed_stocks: int = 0
    cache_refreshed_etfs: int = 0
    message: str = ""
    details: Dict[str, str] = field(default_factory=dict)

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_codes or self.stale_codes)

    @property
    def summary(self) -> str:
        if self.message:
            return self.message
        parts: List[str] = []
        if self.updated_stocks:
            parts.append(f"股票{self.updated_stocks}只")
        if self.updated_etfs:
            parts.append(f"ETF{self.updated_etfs}只")
        if self.updated_indices:
            parts.append(f"指数{self.updated_indices}个")
        if self.updated_rotation_etfs:
            parts.append(f"轮动ETF{self.updated_rotation_etfs}只")
        if self.cache_refreshed:
            parts.append(f"缓存 股票{self.cache_refreshed_stocks} ETF{self.cache_refreshed_etfs}")
        if self.stale_codes:
            parts.append(f"未就绪{len(self.stale_codes)}个")
        if self.failed_codes:
            parts.append(f"失败{len(self.failed_codes)}个")
        if parts:
            return "；".join(parts)
        return "数据更新完成" if self.ok else "数据更新失败"

    def to_ui_message(self) -> str:
        parts: List[str] = [self.summary]
        counters: List[str] = []
        if self.updated_stocks:
            counters.append(f"股票 {self.updated_stocks} 只")
        if self.updated_etfs:
            counters.append(f"ETF {self.updated_etfs} 只")
        if self.updated_indices:
            counters.append(f"指数 {self.updated_indices} 个")
        if self.updated_rotation_etfs:
            counters.append(f"轮动ETF {self.updated_rotation_etfs} 只")
        if counters:
            parts.append("更新: " + "，".join(counters))
        if self.cache_refreshed:
            parts.append(f"缓存已刷新: 股票 {self.cache_refreshed_stocks} 只，ETF {self.cache_refreshed_etfs} 只")
        if self.stale_codes:
            preview = "、".join(self.stale_codes[:8])
            suffix = f" 等 {len(self.stale_codes)} 个" if len(self.stale_codes) > 8 else ""
            parts.append(f"未就绪: {preview}{suffix}")
        if self.failed_codes:
            preview = "、".join(self.failed_codes[:8])
            suffix = f" 等 {len(self.failed_codes)} 个" if len(self.failed_codes) > 8 else ""
            parts.append(f"失败: {preview}{suffix}")
        if self.details:
            detail_preview = "；".join(f"{key}: {value}" for key, value in list(self.details.items())[:4])
            if detail_preview:
                parts.append(detail_preview)
        return "；".join(part for part in parts if part)

    def to_legacy_tuple(self) -> Tuple[bool, str]:
        return self.ok, self.to_ui_message()
