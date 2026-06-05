"""tests/test_gitops.py — champion ref helpers (single-lane, phase1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness import gitops


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.py").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "f.py")
    _git(root, "commit", "-qm", "c1")
    return root


def test_ensure_champion_ref_creates_at_head(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    head = _git(repo, "rev-parse", "HEAD")
    champ = _git(repo, "rev-parse", "champion")
    assert head == champ


def test_ensure_champion_ref_idempotent(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    first = _git(repo, "rev-parse", "champion")
    # advance HEAD; ensure must NOT move an existing champion.
    (repo / "f.py").write_text("v2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "c2")
    gitops.ensure_champion_ref(repo, "champion")
    assert _git(repo, "rev-parse", "champion") == first


def test_restore_file_from_ref(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    (repo / "f.py").write_text("dirty\n", encoding="utf-8")
    gitops.restore_file_from_ref(repo, "champion", Path("f.py"))
    assert (repo / "f.py").read_text(encoding="utf-8") == "v1\n"


def test_advance_champion_ref_moves_to_commit(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    (repo / "f.py").write_text("v2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "c2")
    new = _git(repo, "rev-parse", "HEAD")
    gitops.advance_champion_ref(repo, "champion", new)
    assert _git(repo, "rev-parse", "champion") == new
