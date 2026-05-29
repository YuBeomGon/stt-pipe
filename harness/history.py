"""
harness/history.py
Append iteration narratives to runs/_summary/HISTORY.md.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def _git_output(args: list[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def append_history(
    iter_id: str,
    commit: str,
    metric: str,
    delta: str,
    status: str,
    history_path: Path = Path("runs/_summary/HISTORY.md"),
    repo_root: Path = Path("."),
) -> Path:
    short_hash = _git_output(["rev-parse", "--short", commit], repo_root)
    body = _git_output(["log", "--format=%b", "-n", "1", short_hash], repo_root)
    return append_event(
        iter_id=iter_id,
        candidate_id=short_hash,
        metric=metric,
        delta=delta,
        status=status,
        body=body,
        history_path=history_path,
        repo_root=repo_root,
    )


def append_event(
    iter_id: str,
    candidate_id: str,
    metric: str,
    delta: str,
    status: str,
    body: str,
    history_path: Path = Path("runs/_summary/HISTORY.md"),
    repo_root: Path = Path("."),
) -> Path:
    output_path = repo_root / history_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n## iter {iter_id} · {candidate_id} · "
            f"cer={metric} (Δ{delta}) · {status}\n\n"
        )
        if body:
            fh.write(body)
            if not body.endswith("\n"):
                fh.write("\n")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append an evolution history entry.")
    parser.add_argument("iter")
    parser.add_argument("commit")
    parser.add_argument("metric")
    parser.add_argument("delta")
    parser.add_argument("status")
    parser.add_argument("--history", type=Path, default=Path("runs/_summary/HISTORY.md"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    append_history(
        iter_id=args.iter,
        commit=args.commit,
        metric=args.metric,
        delta=args.delta,
        status=args.status,
        history_path=args.history,
        repo_root=args.repo_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
