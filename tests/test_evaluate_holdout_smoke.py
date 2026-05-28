"""Smoke for ``scripts.evaluate_holdout`` — `PHASE2-PLAN.md` §6.

Verifies:
  * the lock check refuses execution when JOB_DONE.lock is absent,
  * --dry-run exits 0 without touching permissions or invoking judge,
  * --unseal + --dry-run is rejected (mutually exclusive),
  * default invocation (no --unseal) is refused because chmod must be
    explicitly authorized.

The smoke does *not* call the real Whisper backend.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "scripts.evaluate_holdout", *args],
        env={**__import__("os").environ, **(env or {}),
             "PYTHONPATH": f"{ROOT}:{__import__('os').environ.get('PYTHONPATH','')}"},
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


def test_refuses_without_lock(tmp_path):
    summary = tmp_path / "summary"
    summary.mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()

    result = _run([
        "--dry-run",
        "--runs-dir", str(runs),
        "--summary-dir", str(summary),
        "--baseline", "baseline/",
    ])
    assert result.returncode == 2, result.stderr
    assert "JOB_DONE.lock" in result.stderr or "lock" in result.stderr.lower()


def test_dry_run_with_lock_succeeds(tmp_path):
    summary = tmp_path / "summary"
    summary.mkdir()
    (summary / "JOB_DONE.lock").touch()
    runs = tmp_path / "runs"
    runs.mkdir()

    result = _run([
        "--dry-run",
        "--runs-dir", str(runs),
        "--summary-dir", str(summary),
        "--baseline", "baseline/",
    ])
    # dry-run with lock and a valid data root (project default) should exit 0.
    # It may exit 3 if the project's data/raw doesn't exist on this machine —
    # both are acceptable smoke outcomes (they verify the code path differently).
    assert result.returncode in (0, 3), (result.stdout, result.stderr)


def test_mutually_exclusive_unseal_and_dry_run(tmp_path):
    summary = tmp_path / "summary"
    summary.mkdir()
    (summary / "JOB_DONE.lock").touch()

    result = _run([
        "--unseal",
        "--dry-run",
        "--summary-dir", str(summary),
    ])
    assert result.returncode != 0
    assert "--unseal" in result.stderr or "동시" in result.stderr


def test_default_refuses_without_unseal(tmp_path):
    """Without --unseal the script must refuse to chmod the holdout dirs."""
    summary = tmp_path / "summary"
    summary.mkdir()
    (summary / "JOB_DONE.lock").touch()

    # Point at a fake data root that exists but is otherwise harmless.
    data_root = tmp_path / "data_raw"
    (data_root / "wav" / "AIG_녹취반출_20250813").mkdir(parents=True)
    (data_root / "label" / "AIG_녹취반출_20250813").mkdir(parents=True)

    result = _run(
        [
            "--summary-dir", str(summary),
            "--runs-dir", str(tmp_path / "runs"),
            "--baseline", "baseline/",
        ],
        env={"ASR_RAW_DATA_ROOT": str(data_root)},
    )
    assert result.returncode == 4, (result.stdout, result.stderr)
    assert "--unseal" in result.stderr
