"""harness/archive.py — flat, never-pruned candidate archive.

Layout (the entire state of the loop):
  runs/<job>/
    archive.jsonl   # append-only, 1 row/candidate, NEVER pruned
    best.txt        # 1-line cache of best id (derivable from min cer)
    <id>/transcribe.py, candidate.diff, score_report.json, prompt.md,
         claude_stdout.txt
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArchiveRecord:
    id: str
    parents: list[str]
    cer: float | None
    status: str  # "scored" | "rejected" | "format_reject" | "scope_reject" | "command_failed"
    hypothesis: str
    what_i_learned: str
    fingerprint: list[str]
    score_report: str | None
    ts: str
    mode: str | None = None
    lane: str | None = None
    capability_investigated: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def record_to_row(rec: ArchiveRecord) -> str:
    return json.dumps(asdict(rec), ensure_ascii=False)


def row_from_dict(d: dict[str, Any]) -> ArchiveRecord:
    known = {f for f in ArchiveRecord.__dataclass_fields__}
    base = {k: v for k, v in d.items() if k in known}
    extra = {k: v for k, v in d.items() if k not in known}
    base.setdefault("extra", {})
    base["extra"].update(extra)
    return ArchiveRecord(**base)


def _archive_path(job_dir: Path) -> Path:
    return Path(job_dir) / "archive.jsonl"


def load_archive(job_dir: Path) -> list[ArchiveRecord]:
    path = _archive_path(job_dir)
    if not path.is_file():
        return []
    out: list[ArchiveRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(row_from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue  # never crash the loop on a malformed row
    return out


def append_record(job_dir: Path, rec: ArchiveRecord) -> None:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    with _archive_path(job_dir).open("a", encoding="utf-8") as fh:
        fh.write(record_to_row(rec) + "\n")


def next_id(archive: list[ArchiveRecord]) -> str:
    return f"{len(archive):04d}"


def _scored(archive: list[ArchiveRecord]) -> list[ArchiveRecord]:
    return [r for r in archive if r.status == "scored" and r.cer is not None]


def best_record(archive: list[ArchiveRecord]) -> ArchiveRecord | None:
    scored = _scored(archive)
    return min(scored, key=lambda r: r.cer) if scored else None


def write_best(job_dir: Path, hyp_id: str) -> None:
    Path(job_dir).mkdir(parents=True, exist_ok=True)
    (Path(job_dir) / "best.txt").write_text(hyp_id + "\n", encoding="utf-8")


def read_best(job_dir: Path) -> str | None:
    path = Path(job_dir) / "best.txt"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def children_count(archive: list[ArchiveRecord], hyp_id: str) -> int:
    return sum(1 for r in archive if hyp_id in r.parents)


def materialize_parent(job_dir: Path, parent_id: str, workspace_file: Path) -> None:
    """Copy the parent's evaluated transcribe.py into the workspace so the LLM
    edits the real parent code (not a prose reconstruction)."""
    src = Path(job_dir) / parent_id / "transcribe.py"
    if not src.is_file():
        raise FileNotFoundError(f"parent transcribe missing: {src}")
    Path(workspace_file).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, workspace_file)


def snapshot_candidate(job_dir: Path, hyp_id: str, workspace_file: Path) -> None:
    """Persist the evaluated workspace into runs/<job>/<id>/transcribe.py so it
    can later be materialized as a parent. Always called for scored iters."""
    dst = Path(job_dir) / hyp_id / "transcribe.py"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(workspace_file, dst)
