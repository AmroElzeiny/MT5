#!/usr/bin/env python3
"""
Replay historical bars and validate production-shaped PO3/FVG labels.

This validator intentionally mirrors the EA's sequence contract rather than
trying to predict profitability:
- closed liquidity sweep with reclaim
- displacement back in the intended direction
- BOS/MSS after displacement
- FVG formed after sweep/displacement
- entry retrace into a still-tradable FVG/retrace zone
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplaySetup:
    direction: str = ""
    sweep_side: str = "none"
    sweep_candle: str = "none"
    sweep_idx: int = -1
    displacement_candle: str = "none"
    displacement_idx: int = -1
    bos_mss_candle: str = "none"
    bos_idx: int = -1
    structure_type: str = "none"
    fvg_idx: int = -1
    fvg_lower: float = 0.0
    fvg_upper: float = 0.0
    fvg_state: str = "none"
    entry_zone: str = "none"
    target_source: str = "none"
    target_price: float = 0.0
    po3_state: str = "PO3_IDLE"
    invalidation_reason: str = "ok"
    final_setup_class: str = "none"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _load_bars(path: Path) -> List[Bar]:
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    bars: List[Bar] = []
    for row in rows:
        extra = dict(row)
        bars.append(
            Bar(
                time=str(row.get("time") or row.get("datetime") or row.get("timestamp") or ""),
                open=_safe_float(row.get("open")),
                high=_safe_float(row.get("high")),
                low=_safe_float(row.get("low")),
                close=_safe_float(row.get("close")),
                volume=_safe_float(row.get("volume"), _safe_float(row.get("tick_volume"))),
                extra=extra,
            )
        )
    return bars


def _load_expected(path: Path) -> Dict[str, Dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if text.startswith("["):
        rows = json.loads(text)
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        time_key = str(row.get("time") or row.get("datetime") or row.get("timestamp") or "")
        expected = row.get("expected") if isinstance(row.get("expected"), dict) else row
        if time_key:
            out[time_key] = expected
    return out


def _atr(bars: List[Bar], idx: int, period: int = 14) -> float:
    if idx <= 0:
        return 0.0
    start = max(1, idx - period + 1)
    tr_values: List[float] = []
    for pos in range(start, idx + 1):
        prev_close = bars[pos - 1].close
        bar = bars[pos]
        tr_values.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
    return sum(tr_values) / len(tr_values) if tr_values else 0.0


def _body_frac(bar: Bar) -> float:
    return abs(bar.close - bar.open) / max(bar.high - bar.low, 1e-9)


def _range_high_low(bars: List[Bar], end_idx: int, lookback: int) -> Tuple[float, float]:
    start = max(0, end_idx - lookback)
    window = bars[start:end_idx]
    return (
        max((bar.high for bar in window), default=0.0),
        min((bar.low for bar in window), default=0.0),
    )


def _is_displacement(bar: Bar, prev_bar: Bar, direction: str, atr: float, range_atr_min: float, body_frac_min: float) -> bool:
    if atr <= 0.0:
        atr = max(prev_bar.high - prev_bar.low, 1e-9)
    body_ok = _body_frac(bar) >= body_frac_min
    range_ok = (bar.high - bar.low) / max(atr, 1e-9) >= range_atr_min
    if direction == "bull":
        direction_ok = bar.close > bar.open
        close_ok = bar.close >= prev_bar.close
    else:
        direction_ok = bar.close < bar.open
        close_ok = bar.close <= prev_bar.close
    return bool(direction_ok and body_ok and range_ok and close_ok)


def _structure_break(
    bars: List[Bar],
    idx: int,
    setup: ReplaySetup,
    swing_span: int,
) -> Tuple[bool, str, float]:
    start = max(0, setup.displacement_idx - max(3, swing_span * 4))
    prior = bars[start:idx]
    if not prior:
        return False, "none", 0.0
    if setup.direction == "bull":
        level = max(bar.high for bar in prior)
        if bars[idx].close <= level:
            return False, "none", level
        pretrend = "down" if bars[setup.sweep_idx].close < bars[start].close else "up"
        structure_type = "reversal_mss" if pretrend == "down" else "continuation_bos"
        return True, structure_type, level
    level = min(bar.low for bar in prior)
    if bars[idx].close >= level:
        return False, "none", level
    pretrend = "up" if bars[setup.sweep_idx].close > bars[start].close else "down"
    structure_type = "reversal_mss" if pretrend == "up" else "continuation_bos"
    return True, structure_type, level


def _detect_fvg(bars: List[Bar], idx: int, direction: str) -> Optional[Tuple[float, float]]:
    if idx < 2:
        return None
    older = bars[idx - 2]
    newer = bars[idx]
    if direction == "bull" and older.high < newer.low:
        return older.high, newer.low
    if direction == "bear" and older.low > newer.high:
        return newer.high, older.low
    return None


def _update_fvg_state(setup: ReplaySetup, bar: Bar) -> None:
    if setup.fvg_idx < 0 or setup.fvg_state in {"structure_invalidated", "entry_invalid"}:
        return
    width = max(setup.fvg_upper - setup.fvg_lower, 1e-9)
    mid = (setup.fvg_lower + setup.fvg_upper) * 0.5
    eps = max(width * 0.04, 1e-9)
    if setup.direction == "bull":
        if bar.low < setup.fvg_lower - eps:
            setup.fvg_state = "structure_invalidated"
            setup.invalidation_reason = "fvg_structure_invalidated"
        elif bar.low <= setup.fvg_lower + eps:
            setup.fvg_state = "fully_mitigated"
            if setup.entry_zone == "none":
                setup.invalidation_reason = "fvg_fully_mitigated_before_entry"
        elif bar.low <= mid:
            setup.fvg_state = "mid_mitigated"
            setup.entry_zone = "fvg_mid"
        elif bar.low <= setup.fvg_upper:
            setup.fvg_state = "touched"
            setup.entry_zone = "fvg_edge"
    else:
        if bar.high > setup.fvg_upper + eps:
            setup.fvg_state = "structure_invalidated"
            setup.invalidation_reason = "fvg_structure_invalidated"
        elif bar.high >= setup.fvg_upper - eps:
            setup.fvg_state = "fully_mitigated"
            if setup.entry_zone == "none":
                setup.invalidation_reason = "fvg_fully_mitigated_before_entry"
        elif bar.high >= mid:
            setup.fvg_state = "mid_mitigated"
            setup.entry_zone = "fvg_mid"
        elif bar.high >= setup.fvg_lower:
            setup.fvg_state = "touched"
            setup.entry_zone = "fvg_edge"


def _machine_state(setup: ReplaySetup) -> str:
    if setup.invalidation_reason != "ok":
        if setup.invalidation_reason == "stale_fvg":
            return "PO3_EXPIRED"
        return "PO3_INVALIDATED"
    if setup.fvg_idx >= 0 and setup.entry_zone != "none" and setup.fvg_state not in {
        "fully_mitigated",
        "entry_invalid",
        "structure_invalidated",
        "stale",
    }:
        return "PO3_CONFIRMED"
    if setup.fvg_idx >= 0:
        return "PO3_FVG_CONFIRMED"
    if setup.sweep_idx >= 0 and setup.displacement_idx >= 0 and setup.bos_idx >= 0:
        return "PO3_STRUCTURE_CONFIRMED"
    if setup.sweep_idx >= 0 and setup.displacement_idx >= 0:
        return "PO3_DISPLACEMENT_CONFIRMED"
    if setup.sweep_idx >= 0:
        return "PO3_SWEEP_CONFIRMED"
    return "PO3_IDLE"


def _final_class(setup: ReplaySetup) -> str:
    if setup.invalidation_reason != "ok":
        return "rejected_" + setup.invalidation_reason
    if setup.po3_state == "PO3_CONFIRMED":
        return ("bullish" if setup.direction == "bull" else "bearish") + "_confirmed"
    if setup.po3_state == "PO3_ENTRY_WAITING":
        return ("bullish" if setup.direction == "bull" else "bearish") + "_entry_waiting"
    if setup.po3_state in {"PO3_FVG_CONFIRMED", "PO3_STRUCTURE_CONFIRMED", "PO3_DISPLACEMENT_CONFIRMED", "PO3_SWEEP_CONFIRMED"}:
        return ("bullish" if setup.direction == "bull" else "bearish") + "_watchlist"
    if setup.po3_state == "PO3_DEVELOPING":
        return ("bullish" if setup.direction == "bull" else "bearish") + "_developing"
    return "none"


def _snapshot(setup: ReplaySetup, bar_time: str) -> Dict[str, Any]:
    setup.po3_state = _machine_state(setup)
    setup.final_setup_class = _final_class(setup)
    bounds: Any = "none"
    if setup.fvg_idx >= 0:
        bounds = [round(setup.fvg_lower, 6), round(setup.fvg_upper, 6)]
    return {
        "time": bar_time,
        "po3_state": setup.po3_state,
        "sweep_side": setup.sweep_side,
        "sweep_candle": setup.sweep_candle,
        "displacement_candle": setup.displacement_candle,
        "bos_mss_candle": setup.bos_mss_candle,
        "structure_type": setup.structure_type,
        "fvg_bounds": bounds,
        "fvg_state": setup.fvg_state,
        "entry_zone": setup.entry_zone,
        "invalidation_reason": setup.invalidation_reason,
        "target_source": setup.target_source,
        "final_setup_class": setup.final_setup_class,
    }


def _replay_labels(
    bars: List[Bar],
    lookback: int,
    swing_span: int,
    disp_atr_min: float,
    disp_body_frac_min: float,
    max_fvg_age: int,
    max_spread_r: float,
) -> List[Dict[str, Any]]:
    labels: List[Dict[str, Any]] = []
    setup = ReplaySetup()

    for idx, bar in enumerate(bars):
        atr = _atr(bars, idx)
        recent_high, recent_low = _range_high_low(bars, idx, lookback)

        if setup.fvg_idx >= 0 and idx > setup.fvg_idx:
            _update_fvg_state(setup, bar)

        if setup.invalidation_reason == "ok":
            if _safe_float(bar.extra.get("news_risk")) >= 1.0:
                setup.invalidation_reason = "news_risk_high"
            elif _safe_float(bar.extra.get("spread_r")) > max_spread_r:
                setup.invalidation_reason = "spread_cost_too_high"
            elif _safe_float(bar.extra.get("opposing_obstruction_r"), 999.0) < 0.8:
                setup.invalidation_reason = "opposing_htf_imbalance_too_close"

        if setup.fvg_idx >= 0 and setup.entry_zone == "none" and idx - setup.fvg_idx > max_fvg_age:
            setup.fvg_state = "stale"
            setup.invalidation_reason = "stale_fvg"

        if setup.invalidation_reason == "ok" and setup.target_price > 0.0 and setup.entry_zone == "none":
            if setup.direction == "bull" and bar.high >= setup.target_price:
                setup.invalidation_reason = "target_already_reached"
            elif setup.direction == "bear" and bar.low <= setup.target_price:
                setup.invalidation_reason = "target_already_reached"

        can_start = setup.sweep_idx < 0
        if idx >= lookback and can_start and recent_high > 0 and recent_low > 0:
            buy_side_reclaim = bar.high > recent_high and bar.close < recent_high
            sell_side_reclaim = bar.low < recent_low and bar.close > recent_low
            breakout_no_reclaim = (bar.high > recent_high and bar.close >= recent_high) or (
                bar.low < recent_low and bar.close <= recent_low
            )
            if buy_side_reclaim or sell_side_reclaim:
                dr_width = max(recent_high - recent_low, 1e-9)
                setup = ReplaySetup(
                    direction="bear" if buy_side_reclaim else "bull",
                    sweep_side="buy_side" if buy_side_reclaim else "sell_side",
                    sweep_candle=bar.time,
                    sweep_idx=idx,
                    fvg_state="none",
                    target_source="prior_low" if buy_side_reclaim else "prior_high",
                    target_price=(recent_low - dr_width) if buy_side_reclaim else (recent_high + dr_width),
                    po3_state="PO3_SWEEP_CONFIRMED",
                )
            elif breakout_no_reclaim:
                setup = ReplaySetup(
                    sweep_side="breakout",
                    sweep_candle=bar.time,
                    sweep_idx=idx,
                    po3_state="PO3_INVALIDATED",
                    invalidation_reason="sweep_breakout_no_reclaim",
                    final_setup_class="rejected_sweep_breakout_no_reclaim",
                )

        if setup.sweep_idx >= 0 and setup.invalidation_reason == "ok":
            if setup.displacement_idx < 0 and idx > setup.sweep_idx:
                if _is_displacement(bar, bars[idx - 1], setup.direction, atr, disp_atr_min, disp_body_frac_min):
                    setup.displacement_idx = idx
                    setup.displacement_candle = bar.time

            if setup.displacement_idx >= 0 and setup.bos_idx < 0 and idx > setup.displacement_idx:
                has_break, structure_type, _ = _structure_break(bars, idx, setup, swing_span)
                if has_break:
                    setup.bos_idx = idx
                    setup.bos_mss_candle = bar.time
                    setup.structure_type = structure_type

            if (
                setup.displacement_idx >= 0
                and setup.bos_idx >= 0
                and setup.fvg_idx < 0
                and idx > max(setup.displacement_idx, setup.bos_idx)
            ):
                fvg = _detect_fvg(bars, idx, setup.direction)
                if fvg:
                    setup.fvg_idx = idx
                    setup.fvg_lower, setup.fvg_upper = fvg
                    setup.fvg_state = "virgin"

        labels.append(_snapshot(setup, bar.time))

    if labels:
        final = labels[-1]
        if final["po3_state"] in {"PO3_DISPLACEMENT_CONFIRMED", "PO3_DEVELOPING"} and setup.displacement_idx >= 0 and setup.bos_idx < 0:
            setup.invalidation_reason = "bos_missing"
            labels[-1] = _snapshot(setup, bars[-1].time)
    return labels


def _compare(expected: Dict[str, Dict[str, Any]], actual: List[Dict[str, Any]]) -> Dict[str, Any]:
    mismatches: List[Dict[str, Any]] = []
    actual_by_time = {row["time"]: row for row in actual}
    for time_key, expected_row in expected.items():
        actual_row = actual_by_time.get(time_key)
        if actual_row is None:
            mismatches.append({"time": time_key, "field": "_row", "expected": expected_row, "actual": "missing"})
            continue
        for field, expected_value in expected_row.items():
            if field in {"time", "datetime", "timestamp", "expected"}:
                continue
            actual_value = actual_row.get(field)
            if actual_value != expected_value:
                mismatches.append(
                    {
                        "time": time_key,
                        "field": field,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
    return {
        "expected_rows": len(expected),
        "actual_rows": len(actual),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def _validate_one(
    bars_path: Path,
    expected_path: Path,
    lookback: int,
    swing_span: int,
    disp_atr_min: float,
    disp_body_frac_min: float,
    max_fvg_age: int,
    max_spread_r: float,
) -> Dict[str, Any]:
    bars = _load_bars(bars_path)
    expected = _load_expected(expected_path)
    actual = _replay_labels(
        bars,
        lookback=max(2, lookback),
        swing_span=max(1, swing_span),
        disp_atr_min=max(0.1, disp_atr_min),
        disp_body_frac_min=max(0.1, min(0.95, disp_body_frac_min)),
        max_fvg_age=max(1, max_fvg_age),
        max_spread_r=max(0.01, max_spread_r),
    )
    report = _compare(expected, actual)
    report["bars"] = str(bars_path)
    report["expected"] = str(expected_path)
    report["actual_labels"] = actual
    return report


def _iter_golden_pairs(golden_dir: Path) -> Iterable[Tuple[Path, Path]]:
    for bars_path in sorted(golden_dir.glob("*.csv")):
        expected_path = bars_path.with_suffix(".expected.json")
        if expected_path.exists():
            yield bars_path, expected_path
    for bars_path in sorted(golden_dir.glob("*.bars.json")):
        expected_path = Path(str(bars_path).replace(".bars.json", ".expected.json"))
        if expected_path.exists():
            yield bars_path, expected_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", default="", help="CSV or JSON file with OHLCV bars.")
    parser.add_argument("--expected", default="", help="JSON list or NDJSON expected labels.")
    parser.add_argument("--golden-dir", default="", help="Run every *.csv/*.expected.json pair in this folder.")
    parser.add_argument("--output", default="", help="Optional output path for mismatch report JSON.")
    parser.add_argument("--lookback", type=int, default=3, help="Lookback used for sweep/dealing-range detection.")
    parser.add_argument("--swing-span", type=int, default=1, help="Swing span used for BOS/MSS detection.")
    parser.add_argument("--disp-atr-min", type=float, default=0.65, help="Minimum range/ATR for displacement labeling.")
    parser.add_argument("--disp-body-frac-min", type=float, default=0.45, help="Minimum candle body fraction for displacement.")
    parser.add_argument("--max-fvg-age", type=int, default=8, help="Bars after formation before a no-entry FVG is expired.")
    parser.add_argument("--max-spread-r", type=float, default=0.25, help="Maximum modeled spread cost in R.")
    args = parser.parse_args()

    reports: List[Dict[str, Any]] = []
    if args.golden_dir:
        for bars_path, expected_path in _iter_golden_pairs(Path(args.golden_dir)):
            reports.append(
                _validate_one(
                    bars_path,
                    expected_path,
                    args.lookback,
                    args.swing_span,
                    args.disp_atr_min,
                    args.disp_body_frac_min,
                    args.max_fvg_age,
                    args.max_spread_r,
                )
            )
        report: Dict[str, Any] = {
            "case_count": len(reports),
            "mismatch_count": sum(row["mismatch_count"] for row in reports),
            "cases": reports,
        }
    else:
        if not args.bars or not args.expected:
            raise SystemExit("Provide --bars and --expected, or --golden-dir.")
        report = _validate_one(
            Path(args.bars),
            Path(args.expected),
            args.lookback,
            args.swing_span,
            args.disp_atr_min,
            args.disp_body_frac_min,
            args.max_fvg_age,
            args.max_spread_r,
        )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    if report.get("mismatch_count", 0) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
