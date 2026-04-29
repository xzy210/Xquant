from __future__ import annotations

import logging
from pathlib import Path

from common.logging_facade import configure_logging, get_role_log_path


def get_live_strategy_log_dir(project_root: str | Path | None = None) -> Path:
    log_dir = get_live_strategy_log_path(project_root).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_live_strategy_log_path(project_root: str | Path | None = None) -> Path:
    return get_role_log_path("live", project_root=project_root)


def configure_live_strategy_logging(project_root: str | Path | None = None) -> Path:
    """Attach a rotating file handler for the live strategy center."""
    log_path = configure_logging("live", project_root=project_root, level=logging.INFO)
    logging.getLogger(__name__).info("实盘策略中枢统一日志已接入: %s", log_path)
    return log_path
