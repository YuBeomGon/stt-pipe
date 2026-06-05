#!/usr/bin/env python3
"""scripts/archive_summary.py — flat-archive leaderboard (in-loop + holdout)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import archive as arch  # noqa: E402


def _load_holdout(summary_dir: Path, job_id: str) -> dict[str, float]:
    """Map hyp_id -> holdout cer from any <job>_holdout.jsonl sidecar (Task 8)."""
    path = summary_dir / f"{job_id}_holdout.jsonl"
    out: dict[str, float] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("hyp_id") and rec.get("holdout_cer") is not None:
                out[rec["hyp_id"]] = float(rec["holdout_cer"])
    return out


def render_leaderboard(job_dir: Path, holdout: dict[str, float]) -> str:
    archive = arch.load_archive(job_dir)
    best_id = arch.read_best(job_dir)
    scored = sorted(
        [r for r in archive if r.status == "scored" and r.cer is not None],
        key=lambda r: r.cer,
    )
    lines = [
        f"# Leaderboard — {job_dir.name}  ({len(archive)} candidates, "
        f"{len(scored)} scored)",
        "| rank | id | cer | holdout | fingerprint | hypothesis |",
        "|------|----|-----|---------|-------------|------------|",
    ]
    for i, r in enumerate(scored, 1):
        mark = " *" if r.id == best_id else ""
        hold = holdout.get(r.id)
        hold_str = f"{hold:.4f}" if hold is not None else "—"
        fp = ",".join(r.fingerprint)
        hyp = (r.hypothesis or "").replace("\n", " ")[:60]
        lines.append(f"| {i} | {r.id}{mark} | {r.cer:.4f} | {hold_str} | {fp} | {hyp} |")
    non_scored = [r for r in archive if r.status != "scored"]
    if non_scored:
        lines.append("")
        lines.append(f"({len(non_scored)} non-scored: "
                     + ", ".join(sorted({r.status for r in non_scored})) + ")")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Flat-archive leaderboard")
    p.add_argument("--job", required=True)
    p.add_argument("--repo-root", type=Path, default=Path("."))
    ns = p.parse_args(argv)
    job_dir = ns.repo_root / "runs" / ns.job
    summary_dir = ns.repo_root / "runs" / "_summary"
    print(render_leaderboard(job_dir, _load_holdout(summary_dir, ns.job)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
