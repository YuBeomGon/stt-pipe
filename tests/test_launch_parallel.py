"""tests/test_launch_parallel.py — launcher pool unit tests (no real jobs)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.worktree
def test_build_job_cmds_creates_one_worktree_per_job(tmp_path):
    from scripts import launch_parallel as lp
    from harness import gitops
    main = tmp_path / "main"
    main.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=main, check=True, capture_output=True, text=True)
    (main / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=main, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=main, check=True, capture_output=True, text=True)
    gitops.ensure_champion_ref(main, "champion")
    jobs = lp.build_job_cmds(main, tmp_path / "wts", ["a", "b"], 5, "claude -p", 4)
    assert len(jobs) == 2
    assert all(wt.exists() for _, wt, _ in jobs)
    assert jobs[0][2][:3] == [sys.executable, "scripts/evolve.py", "--job-id"]


def test_run_pool_respects_cap(monkeypatch):
    from scripts import launch_parallel as lp
    peak = {"n": 0}
    live = {"n": 0}

    class FakeProc:
        def __init__(self):
            self.n = 0
            self.returncode = 0
            live["n"] += 1
            peak["n"] = max(peak["n"], live["n"])

        def poll(self):
            self.n += 1
            if self.n >= 2:
                live["n"] -= 1
                return 0
            return None

    monkeypatch.setattr(lp.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(lp.time, "sleep", lambda *_: None)
    jobs = [(f"j{i}", Path("."), ["x"]) for i in range(5)]
    rc = lp.run_pool(jobs, cap=2, poll_s=0)
    assert peak["n"] <= 2 and len(rc) == 5 and all(c == 0 for c in rc.values())
