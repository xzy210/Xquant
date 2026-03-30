"""
Credential storage helpers for desktop automation secrets.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "StockTradebyZ.miniQMT"


def save_password(
    username: str,
    password: str,
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> bool:
    """Persist a password via the system credential backend if available."""
    try:
        import keyring

        keyring.set_password(service_name, username, password)
        return True
    except Exception as exc:
        logger.warning("保存系统凭据失败: %s", exc)
        return False


def load_password(
    username: str,
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> Optional[str]:
    """Load a password from the system credential backend."""
    try:
        import keyring

        return keyring.get_password(service_name, username)
    except Exception as exc:
        logger.warning("读取系统凭据失败: %s", exc)
        return None


def delete_password(
    username: str,
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> bool:
    """Delete a stored password from the system credential backend."""
    try:
        import keyring

        keyring.delete_password(service_name, username)
        return True
    except Exception as exc:
        logger.warning("删除系统凭据失败: %s", exc)
        return False
