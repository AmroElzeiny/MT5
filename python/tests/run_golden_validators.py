#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    golden = root / "tests" / "golden"
    validator = root / "po3_fvg_replay_validator.py"
    cmd = [
        sys.executable,
        str(validator),
        "--golden-dir",
        str(golden),
        "--lookback",
        "3",
        "--swing-span",
        "1",
        "--disp-atr-min",
        "0.65",
        "--disp-body-frac-min",
        "0.45",
        "--max-fvg-age",
        "8",
    ]
    return subprocess.call(cmd, cwd=str(root))


if __name__ == "__main__":
    raise SystemExit(main())
