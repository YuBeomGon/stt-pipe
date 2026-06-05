"""tests/test_gitops_worktree.py — git worktree helpers (phase2).

Mirrors tests/test_gitops.py's temp-repo fixture pattern. No audio data.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness import gitops

pytestmark = pytest.mark.worktree


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "workspace").mkdir()
    (root / "workspace" / "transcribe.py").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c1")
    gitops.ensure_champion_ref(root, "champion")
    return root


def test_prepare_job_worktree_creates_branch_from_champion(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    # worktree exists, on its own branch, file content == champion's.
    assert (wt / "workspace" / "transcribe.py").read_text() == "v1\n"
    branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == "job/job1"
    # job branch HEAD == champion HEAD at creation.
    assert _git(wt, "rev-parse", "HEAD") == _git(repo, "rev-parse", "champion")


def test_prepare_job_worktree_idempotent(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    head1 = _git(wt, "rev-parse", "HEAD")
    # the job advances its lineage; a second prepare must NOT reset it to champion.
    (wt / "workspace" / "transcribe.py").write_text("v2\n", encoding="utf-8")
    _git(wt, "commit", "-qam", "lineage advance")
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    assert _git(wt, "rev-parse", "HEAD") != head1  # not reset to champion


def test_restore_lineage_head_reverts_to_worktree_head_not_champion(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    # advance the lineage head in the worktree (a kept in-set checkpoint).
    (wt / "workspace" / "transcribe.py").write_text("LINEAGE\n", encoding="utf-8")
    _git(wt, "commit", "-qam", "lineage")
    # candidate then dirties the file (a refine attempt being rolled back).
    (wt / "workspace" / "transcribe.py").write_text("DIRTY\n", encoding="utf-8")
    gitops.restore_lineage_head(wt, Path("workspace/transcribe.py"))
    # restored to the worktree's OWN HEAD (lineage head), NOT champion's v1.
    assert (wt / "workspace" / "transcribe.py").read_text() == "LINEAGE\n"


def test_list_worktrees_includes_job(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    paths = gitops.list_worktrees(repo)
    assert any(Path(p).resolve() == wt.resolve() for p in paths)


def test_cleanup_worktree_removes_it(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    gitops.cleanup_worktree(repo, wt)
    assert not wt.exists()
    paths = gitops.list_worktrees(repo)
    assert all(Path(p).resolve() != wt.resolve() for p in paths)


def _git_rc(root: Path, *args: str) -> int:
    return subprocess.run(["git", *args], cwd=root,
                          capture_output=True, text=True).returncode


def test_promote_to_champion_splices_only_file_and_cas(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt"
    gitops.prepare_job_worktree(repo, wt, "job/j", "champion")
    (wt / "workspace" / "transcribe.py").write_text("WINNER\n", encoding="utf-8")
    (wt / "noise.txt").write_text("metadata junk\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "lineage + junk")
    src = _git(wt, "rev-parse", "HEAD")
    old = gitops.read_ref(repo, "refs/heads/champion")
    new = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=old, message="promote j")
    assert new is not None and new != old
    assert gitops.read_ref(repo, "refs/heads/champion") == new
    # champion got the file but NOT the junk → linear, metadata-free.
    assert _git(repo, "show", "champion:workspace/transcribe.py") == "WINNER"
    assert _git_rc(repo, "cat-file", "-e", "champion:noise.txt") != 0


def test_promote_to_champion_cas_loses_when_champion_moved(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt"
    gitops.prepare_job_worktree(repo, wt, "job/j", "champion")
    (wt / "workspace" / "transcribe.py").write_text("A\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "a")
    src = _git(wt, "rev-parse", "HEAD")
    stale_old = gitops.read_ref(repo, "refs/heads/champion")
    # someone else promotes first (advances champion off stale_old).
    won = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=stale_old, message="b1")
    assert won is not None
    # our promote with the now-stale expected_old must LOSE (return None).
    lost = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=stale_old, message="b2")
    assert lost is None
