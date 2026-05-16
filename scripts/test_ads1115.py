#!/usr/bin/env python3
"""Run ADS1115 bench test with repo root on PYTHONPATH (works from scripts/)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from nina.app.ads1115_bench_test import main

if __name__ == "__main__":
    raise SystemExit(main())
