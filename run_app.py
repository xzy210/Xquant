#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launch the new Xquant dual-track application shell."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.logging_facade import configure_logging

configure_logging("research", project_root=PROJECT_ROOT)

from app.main import main


if __name__ == "__main__":
    raise SystemExit(main())
