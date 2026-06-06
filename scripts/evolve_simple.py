#!/usr/bin/env python3
"""scripts/evolve_simple.py — thin CLI for the simple self-evolve loop."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.evolve_simple import SimpleConfig, run_job  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Simple self-evolve loop")
    p.add_argument("--job-id", required=True)
    p.add_argument("--iters", type=int, default=1)
    p.add_argument("--directive", default="")
    p.add_argument("--explore", type=float, default=0.5)
    p.add_argument("--ban", action="append", default=[])
    p.add_argument("--pin", default=None)
    p.add_argument("--parent-policy", choices=["llm", "random", "best"], default="llm")
    p.add_argument("--candidate-cmd", default="claude -p")
    p.add_argument("--repo-root", type=Path, default=Path("."))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    cfg = SimpleConfig(
        job_id=ns.job_id, repo_root=ns.repo_root, candidate_cmd=ns.candidate_cmd,
        iters=ns.iters, explore=ns.explore, parent_policy=ns.parent_policy,
        directive=ns.directive, bans=ns.ban, pinned=ns.pin,
    )
    best = run_job(cfg)
    print(f"job {ns.job_id} done — best={best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
