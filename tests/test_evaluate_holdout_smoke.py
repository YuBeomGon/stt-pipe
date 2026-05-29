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


def test_best_eval_run_prefers_explicit_job_id(tmp_path):
    """F3 regression: when ≥ 2 state files exist (e.g. phase3_001 +
    phase3_002), passing job_id explicitly must select that job's state
    rather than falling back to last-produced. The pre-fix behavior
    silently mixed jobs."""
    import json
    from scripts.evaluate_holdout import _best_eval_run

    runs = tmp_path / "runs"
    summary = runs / "_summary"
    summary.mkdir(parents=True)

    # Two state files side by side.
    (summary / "job_a_state.json").write_text(
        json.dumps({"job_id": "job_a", "best_hyp_id": "job_a_iter_005"}),
        encoding="utf-8",
    )
    (summary / "job_b_state.json").write_text(
        json.dumps({"job_id": "job_b", "best_hyp_id": "job_b_iter_010"}),
        encoding="utf-8",
    )

    # Backing run dirs with score_report.
    for hyp in ("job_a_iter_005", "job_b_iter_010"):
        d = runs / hyp
        d.mkdir()
        (d / "score_report.json").write_text(
            json.dumps({"batch": "AIG_녹취반출_20250715", "corpus_cer": 0.3}),
            encoding="utf-8",
        )

    # Without job_id: ambiguous (2 state files) → falls back to last-produced
    # (mtime-based), unstable so just assert it's NOT None.
    fallback = _best_eval_run(runs, summary, job_id=None)
    assert fallback is not None

    # With explicit job_id: must pick that job's best.
    a = _best_eval_run(runs, summary, job_id="job_a")
    assert a is not None and a.name == "job_a_iter_005"

    b = _best_eval_run(runs, summary, job_id="job_b")
    assert b is not None and b.name == "job_b_iter_010"

    # Unknown job_id: state lookup misses, falls through to last-produced
    # (still returns one of the two real dirs).
    unknown = _best_eval_run(runs, summary, job_id="job_missing")
    assert unknown is not None
