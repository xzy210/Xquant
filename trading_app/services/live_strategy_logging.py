from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_live_strategy_log_dir(project_root: str | Path | None = None) -> Path:
    root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent.parent
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_live_strategy_log_path(project_root: str | Path | None = None) -> Path:
    return get_live_strategy_log_dir(project_root) / "live_strategy_center.log"


def configure_live_strategy_logging(project_root: str | Path | None = None) -> Path:
    """Attach a rotating file handler for the live strategy center."""
    log_path = get_live_strategy_log_path(project_root)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    existing_file_handler = None
    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(getattr(handler, "baseFilename", "")) == log_path:
            existing_file_handler = handler
            break

    if existing_file_handler is None:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root_logger.addHandler(file_handler)

    has_stream_handler = any(type(handler) is logging.StreamHandler for handler in root_logger.handlers)
    if not has_stream_handler:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root_logger.addHandler(stream_handler)

        logging.getLogger(__name__).info("实盘策略中枢统一日志已接入: %s", log_path)
    return log_path
