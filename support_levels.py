from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Literal, Optional

import pandas as pd


@dataclass
class LevelLine:
    label: str
    price: float
    kind: Literal["support", "resistance", "pivot"]
    method: str


SUPPORT_METHOD_CHOICES: Dict[str, str] = {
    "Pivot 经典": "pivot",
    "波段高低点": "swing",
    "斐波那契回撤": "fibonacci",
    "布林带通道": "bollinger",
}

METHOD_KEY_TO_LABEL = {key: label for label, key in SUPPORT_METHOD_CHOICES.items()}


def _ensure_odd_window(window: int) -> int:
    window = max(3, window)
    return window if window % 2 == 1 else window + 1


def _extract_pivots(series: pd.Series, window: int, mode: Literal["min", "max"]) -> pd.Series:
    if len(series) < window:
        return series.iloc[0:0]
    window = _ensure_odd_window(window)
    rolled = series.rolling(window, center=True)
    target = rolled.min() if mode == "min" else rolled.max()
    mask = series == target
    return series[mask].dropna()


def _calc_pivot_levels(df: pd.DataFrame, method_label: str) -> List[LevelLine]:
    last = df.iloc[-1]
    high = float(last["High"])
    low = float(last["Low"])
    close = float(last["Close"])
    pp = (high + low + close) / 3
    diff = high - low
    levels = [
        LevelLine("Pivot", pp, "pivot", method_label),
        LevelLine("R1", 2 * pp - low, "resistance", method_label),
        LevelLine("R2", pp + diff, "resistance", method_label),
        LevelLine("R3", high + 2 * (pp - low), "resistance", method_label),
        LevelLine("S1", 2 * pp - high, "support", method_label),
        LevelLine("S2", pp - diff, "support", method_label),
        LevelLine("S3", low - 2 * (high - pp), "support", method_label),
    ]
    return levels


def _calc_swing_levels(
    df: pd.DataFrame,
    method_label: str,
    lookback: int = 120,
    window: int = 5,
    max_levels: int = 2,
) -> List[LevelLine]:
    recent = df.tail(max(lookback, window * 3))
    if recent.empty:
        return []

    window = _ensure_odd_window(window)
    pivot_lows = _extract_pivots(recent["Low"], window, mode="min")
    pivot_highs = _extract_pivots(recent["High"], window, mode="max")

    if pivot_lows.empty and not recent.empty:
        pivot_lows = pd.Series([recent["Low"].min()], index=[recent["Low"].idxmin()])
    if pivot_highs.empty and not recent.empty:
        pivot_highs = pd.Series([recent["High"].max()], index=[recent["High"].idxmax()])

    lines: List[LevelLine] = []

    for idx, (ts, price) in enumerate(pivot_highs.tail(max_levels).items(), start=1):
        lines.append(
            LevelLine(
                label=f"波段压力#{idx}({ts.strftime('%m-%d')})",
                price=float(price),
                kind="resistance",
                method=method_label,
            )
        )

    for idx, (ts, price) in enumerate(pivot_lows.tail(max_levels).items(), start=1):
        lines.append(
            LevelLine(
                label=f"波段支撑#{idx}({ts.strftime('%m-%d')})",
                price=float(price),
                kind="support",
                method=method_label,
            )
        )

    return lines


def _calc_fibonacci_levels(
    df: pd.DataFrame,
    method_label: str,
    lookback: int = 180,
) -> List[LevelLine]:
    recent = df.tail(max(lookback, 30))
    if recent.empty:
        return []

    high = float(recent["High"].max())
    low = float(recent["Low"].min())
    diff = high - low
    if diff <= 0:
        return []

    latest_close = float(recent["Close"].iloc[-1])
    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    lines: List[LevelLine] = []

    for ratio in ratios:
        price = high - diff * ratio
        label_pct = f"{int(round(ratio * 100))}%"
        if ratio == 0.5:
            kind: Literal["support", "resistance", "pivot"] = "pivot"
        else:
            kind = "resistance" if price >= latest_close else "support"
        lines.append(
            LevelLine(
                label=f"Fib {label_pct}",
                price=float(price),
                kind=kind,
                method=method_label,
            )
        )

    return lines


def _calc_bollinger_levels(
    df: pd.DataFrame,
    method_label: str,
    window: int = 20,
    num_std: float = 2.0,
) -> List[LevelLine]:
    if len(df) < window:
        return []

    window = max(5, window)
    rolling = df["Close"].rolling(window)
    mid = rolling.mean().iloc[-1]
    std = rolling.std(ddof=0).iloc[-1]

    if pd.isna(mid) or pd.isna(std):
        return []

    upper = mid + num_std * std
    lower = mid - num_std * std

    return [
        LevelLine(f"BOLL 上轨({window})", float(upper), "resistance", method_label),
        LevelLine(f"BOLL 中轨({window})", float(mid), "pivot", method_label),
        LevelLine(f"BOLL 下轨({window})", float(lower), "support", method_label),
    ]


LEVEL_CALCULATORS = {
    "pivot": _calc_pivot_levels,
    "swing": _calc_swing_levels,
    "fibonacci": _calc_fibonacci_levels,
    "bollinger": _calc_bollinger_levels,
}


PRIMARY_METHOD_PRIORITY = [
    "波段高低点",
    "Pivot 经典",
    "斐波那契回撤",
    "布林带通道",
]


def compute_support_resistance_lines(
    df: pd.DataFrame,
    selected_method_keys: List[str],
    method_params: Dict[str, Dict[str, Any]],
) -> List[LevelLine]:
    lines: List[LevelLine] = []
    for key in selected_method_keys:
        calc = LEVEL_CALCULATORS.get(key)
        if not calc:
            continue
        params = method_params.get(key, {})
        label = METHOD_KEY_TO_LABEL.get(key, key)
        try:
            lines.extend(calc(df, label, **params))
        except Exception as exc:
            logging.warning("支撑/压力计算失败 (%s): %s", key, exc)

    lines = [line for line in lines if line.price is not None and not pd.isna(line.price)]
    return sorted(lines, key=lambda x: x.price, reverse=True)


def _is_valid_for_kind(line: LevelLine, close_price: float) -> bool:
    if line.kind == "support":
        return line.price <= close_price
    if line.kind == "resistance":
        return line.price >= close_price
    return True


def _pick_best_line(
    candidates: List[LevelLine],
    close_price: float,
    kind: Literal["support", "resistance"],
) -> Optional[LevelLine]:
    directional = [
        line for line in candidates if _is_valid_for_kind(line, close_price) and line.kind == kind
    ]
    pool = directional or [line for line in candidates if line.kind == kind]
    if not pool:
        return None

    def sort_key(line: LevelLine) -> tuple[float, float]:
        distance = abs(line.price - close_price)
        secondary = -line.price if kind == "support" else line.price
        return distance, secondary

    return sorted(pool, key=sort_key)[0]


def select_primary_levels(
    df: pd.DataFrame,
    candidates: List[LevelLine],
    priority_methods: Optional[List[str]] = None,
) -> List[LevelLine]:
    if not candidates or df.empty:
        return []

    close_price = float(df["Close"].iloc[-1])
    priorities = priority_methods or PRIMARY_METHOD_PRIORITY

    def pick_for_kind(kind: Literal["support", "resistance"]) -> Optional[LevelLine]:
        for method in priorities:
            method_lines = [line for line in candidates if line.method == method]
            line = _pick_best_line(method_lines, close_price, kind)
            if line:
                return line
        return _pick_best_line(candidates, close_price, kind)

    selected: List[LevelLine] = []
    resistance_line = pick_for_kind("resistance")
    support_line = pick_for_kind("support")

    if resistance_line:
        selected.append(resistance_line)
    if support_line:
        selected.append(support_line)
    return selected

