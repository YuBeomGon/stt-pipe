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
