from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal


LoggingRole = Literal["live", "research", "data", "fetch"]

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_project_root() -> Path:
    """Return the repository root inferred from the common package."""
    return Path(__file__).resolve().parents[1]


def get_role_log_path(
    role: LoggingRole,
    *,
    project_root: str | Path | None = None,
    log_file: str | Path | None = None,
) -> Path:
    """Resolve the canonical log path for a runtime role.

    Existing file names are intentionally preserved. Fetch scripts default to
    the current working directory because historical logs were written there.
    """
    if log_file is not None:
        path = Path(log_file)
        return path if path.is_absolute() else Path.cwd() / path

    root = Path(project_root) if project_root is not None else get_project_root()
    if role == "live":
        return root / "logs" / "live_strategy_center.log"
    if role == "research":
        return root / "logs" / "research.log"
    if role == "data":
        return root / "logs" / "data.log"
    if role == "fetch":
        return Path.cwd() / "fetch.log"
    raise ValueError(f"Unsupported logging role: {role}")


def configure_logging(
    role: LoggingRole,
    *,
    project_root: str | Path | None = None,
    log_file: str | Path | None = None,
    logger_name: str | None = None,
    level: int = logging.INFO,
    console: bool = True,
    rotate: bool | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> Path:
    """Configure a role-based logger and return the selected log path.

    By default the root logger is configured, matching the existing entrypoint
    behavior. Passing ``logger_name`` scopes handlers to a named logger.
    """
    log_path = get_role_log_path(role, project_root=project_root, log_file=log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    target_logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
    target_logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    use_rotating_file = role == "live" if rotate is None else rotate

    if not _has_file_handler(target_logger, log_path):
        if use_rotating_file:
            file_handler: logging.Handler = RotatingFileHandler(
                log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        else:
            file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        target_logger.addHandler(file_handler)

    if console and not _has_plain_stream_handler(target_logger):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        target_logger.addHandler(stream_handler)

    return log_path


def _has_file_handler(logger: logging.Logger, log_path: Path) -> bool:
    expected = log_path.resolve()
    for handler in logger.handlers:
        base_filename = getattr(handler, "baseFilename", None)
        if base_filename and Path(base_filename).resolve() == expected:
            return True
    return False


def _has_plain_stream_handler(logger: logging.Logger) -> bool:
    return any(type(handler) is logging.StreamHandler for handler in logger.handlers)
