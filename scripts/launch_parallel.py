#!/usr/bin/env python3
"""scripts/launch_parallel.py — run N evolution jobs in parallel, each in its
own git worktree cut from the protected champion (HARNESS-REDESIGN §50/§78).

Concurrency cap defaults to 2: CT2 turbo is ~1.5GB/job (24GB GPU fits many) but
GPU-compute contention is the real limit. Promotion to champion is serialized by
the lock in harness.promotion, so concurrent jobs are safe.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import gitops  # noqa: E402


def build_job_cmds(repo_root: Path, worktree_root: Path, job_ids: list[str],
                   iters: int, candidate_cmd: str, set_budget: int,
                   champion_ref: str = "champion") -> list[tuple[str, Path, list[str]]]:
    """Pure-ish: prepare worktrees and return (job_id, worktree, argv) tuples.
    Split out so it is unit-testable without launching processes."""
    jobs: list[tuple[str, Path, list[str]]] = []
    for jid in job_ids:
        wt = worktree_root / f"wt-{jid}"
        gitops.prepare_job_worktree(repo_root, wt, f"job/{jid}", champion_ref)
        argv = [sys.executable, "scripts/evolve.py", "--job-id", jid,
                "--iters", str(iters), "--candidate-cmd", candidate_cmd,
                "--commit-results", "--set-budget", str(set_budget)]
        jobs.append((jid, wt, argv))
    return jobs


def run_pool(jobs: list[tuple[str, Path, list[str]]], cap: int,
             poll_s: float = 2.0) -> dict[str, int]:
    """Run jobs with at most `cap` concurrent Popen, each cwd=its worktree.
    Returns {job_id: returncode}."""
    pending = list(jobs)
    running: dict[str, subprocess.Popen] = {}
    rc: dict[str, int] = {}
    while pending or running:
        while pending and len(running) < cap:
            jid, wt, argv = pending.pop(0)
            running[jid] = subprocess.Popen(argv, cwd=wt)
        for jid, proc in list(running.items()):
            if proc.poll() is not None:
                rc[jid] = proc.returncode
                del running[jid]
        if running:
            time.sleep(poll_s)
    return rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run N evolution jobs in parallel worktrees.")
    p.add_argument("--job-ids", required=True, help="comma-separated job ids")
    p.add_argument("--iters", type=int, required=True)
    p.add_argument("--candidate-cmd", required=True)
    p.add_argument("--set-budget", type=int, default=4)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--repo-root", type=Path, default=ROOT)
    p.add_argument("--worktree-root", type=Path, default=ROOT.parent)
    p.add_argument("--champion-ref", default="champion")
    args = p.parse_args(argv)
    gitops.ensure_champion_ref(args.repo_root.resolve(), args.champion_ref)
    jobs = build_job_cmds(args.repo_root.resolve(), args.worktree_root.resolve(),
                          [j.strip() for j in args.job_ids.split(",") if j.strip()],
                          args.iters, args.candidate_cmd, args.set_budget,
                          args.champion_ref)
    rc = run_pool(jobs, cap=args.concurrency)
    print({jid: code for jid, code in rc.items()})
    return 0 if all(c == 0 for c in rc.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
