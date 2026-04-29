# -*- coding: utf-8 -*-
"""Run the minimal research -> backtest -> live contract regression set."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REGRESSION_SCRIPTS = (
    "scripts/etf_rotation_regression.py",
    "scripts/cross_sectional_regression.py",
    "scripts/etf_grid_regression.py",
    "scripts/backtest_live_contract_smoketest.py",
)


def main() -> int:
    for script in REGRESSION_SCRIPTS:
        subprocess.run([sys.executable, str(PROJECT_ROOT / script)], cwd=PROJECT_ROOT, check=True)
    print("Strategy contract regression set passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
