"""
tests/test_harness_history.py
Unit tests for Phase 3 HISTORY append helpers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness.history import append_history


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=root,
        check=True,
    )
    (root / "workspace").mkdir()
    (root / "workspace/transcribe.py").write_text(
        "def transcribe(audio, sr):\n    return ''\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "workspace/transcribe.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "candidate subject", "-m", "candidate body"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_append_history_uses_full_commit_message(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    history_path = append_history(
        iter_id="1",
        commit="HEAD",
        metric="0.400000",
        delta="NA",
        status="keep",
        repo_root=tmp_path,
    )

    text = history_path.read_text(encoding="utf-8")
    assert "candidate subject" in text
    assert "candidate body" in text
