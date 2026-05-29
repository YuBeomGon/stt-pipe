#!/usr/bin/env python3
"""
scripts/evolve.py
Thin CLI for the Phase 3 evolution harness.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.runner import main


if __name__ == "__main__":
    raise SystemExit(main())
