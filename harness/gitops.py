"""harness/gitops.py
Git ref helpers for the lineage-set refactor (HARNESS-REDESIGN §195).

Phase 1 (single lane, no worktree): HEAD on the job branch is the *lineage head*;
a separate ``champion`` ref holds the last promoted code. On set reset we restore
the workspace file from champion; on promotion we advance champion to the verified
lineage commit. Phase 2 will add ``git worktree`` on top of these same refs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo_root: Path, args: list[str], check: bool = True
         ) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, check=check,
                          capture_output=True, text=True)


def _ref_exists(repo_root: Path, ref: str) -> bool:
    return _git(repo_root, ["show-ref", "--verify", "--quiet",
                            f"refs/heads/{ref}"], check=False).returncode == 0


def ensure_champion_ref(repo_root: Path, champion_ref: str = "champion") -> None:
    """Create ``champion_ref`` at current HEAD if it does not exist. Never moves
    an existing champion (promotion does that, explicitly)."""
    if not _ref_exists(repo_root, champion_ref):
        _git(repo_root, ["branch", champion_ref, "HEAD"])


def restore_file_from_ref(repo_root: Path, ref: str, rel_path: Path) -> None:
    """Restore one file's working-tree content from ``ref`` (set reset /
    champion rollback). Equivalent to ``git restore --source=<ref> -- <path>``."""
    _git(repo_root, ["restore", "--source", ref, "--", rel_path.as_posix()])


def advance_champion_ref(repo_root: Path, champion_ref: str, commit: str) -> None:
    """Move ``champion_ref`` to ``commit`` (promotion). ``commit`` must already
    contain the verified promoted code."""
    _git(repo_root, ["branch", "-f", champion_ref, commit])


def prepare_job_worktree(
    repo_root: Path, worktree_path: Path, job_ref: str, champion_ref: str = "champion"
) -> None:
    """Create a linked worktree at ``worktree_path`` on branch ``job_ref`` cut
    from ``champion_ref`` (HARNESS-REDESIGN §78). Idempotent: if the worktree
    already exists it is left untouched (a resumed job keeps its advanced lineage
    head — we must NOT reset it to champion). The job worktree is where the
    lineage head lives; ``champion`` is never checked out by a runner."""
    if worktree_path.exists():
        return
    branch = job_ref.removeprefix("refs/heads/")
    args = ["worktree", "add", worktree_path.as_posix()]
    if _ref_exists(repo_root, branch):
        # branch already exists (prior run) but its worktree was pruned → re-link
        # at the existing branch tip (the prior lineage head), not at champion.
        args += [branch]
    else:
        args += ["-b", branch, champion_ref]
    _git(repo_root, args)


def restore_lineage_head(repo_root: Path, rel_path: Path) -> None:
    """Restore one file to the worktree's OWN HEAD (the lineage head), NOT to
    champion (HARNESS-REDESIGN §78: repair/refine rollback targets the job-local
    lineage head). ``repo_root`` here is the JOB WORKTREE path."""
    _git(repo_root, ["restore", "--source", "HEAD", "--", rel_path.as_posix()])


def list_worktrees(repo_root: Path) -> list[str]:
    """Return the filesystem paths of all linked worktrees (porcelain parse)."""
    out = _git(repo_root, ["worktree", "list", "--porcelain"]).stdout
    return [line[len("worktree "):] for line in out.splitlines()
            if line.startswith("worktree ")]


def cleanup_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Remove a job worktree (HARNESS-REDESIGN §78 cleanup). ``--force`` because a
    job may leave the workspace file dirty (a half-applied candidate); the branch
    is preserved so a future run can re-link and resume the lineage."""
    if not worktree_path.exists():
        return
    _git(repo_root, ["worktree", "remove", "--force", worktree_path.as_posix()],
         check=False)
    _git(repo_root, ["worktree", "prune"], check=False)
