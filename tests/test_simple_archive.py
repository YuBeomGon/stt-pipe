from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import archive as arch


def _rec(cid, cer, status="scored", parents=None, fp=None):
    return arch.ArchiveRecord(
        id=cid, parents=parents or [], cer=cer, status=status,
        hypothesis="h", what_i_learned="l",
        fingerprint=fp or ["t"], score_report=f"{cid}/score_report.json",
        ts="2026-06-06T00:00:00Z",
    )


def test_next_id_starts_at_zero_and_increments(tmp_path):
    assert arch.next_id([]) == "0000"
    recs = [_rec("0000", 0.2), _rec("0001", 0.19)]
    assert arch.next_id(recs) == "0002"


def test_append_and_load_roundtrip(tmp_path):
    job_dir = tmp_path / "runs" / "job"
    arch.append_record(job_dir, _rec("0000", 0.2))
    arch.append_record(job_dir, _rec("0001", 0.18))
    loaded = arch.load_archive(job_dir)
    assert [r.id for r in loaded] == ["0000", "0001"]
    assert loaded[1].cer == 0.18


def test_load_missing_archive_is_empty(tmp_path):
    assert arch.load_archive(tmp_path / "runs" / "nope") == []


def test_best_record_ignores_unscored_and_none(tmp_path):
    recs = [
        _rec("0000", 0.2),
        _rec("0001", None, status="rejected"),
        _rec("0002", 0.15),
        _rec("0003", 0.16),
    ]
    best = arch.best_record(recs)
    assert best.id == "0002"


def test_write_and_read_best(tmp_path):
    job_dir = tmp_path / "runs" / "job"
    job_dir.mkdir(parents=True)
    arch.write_best(job_dir, "0002")
    assert arch.read_best(job_dir) == "0002"


def test_children_count(tmp_path):
    recs = [_rec("0000", 0.2), _rec("0001", 0.19, parents=["0000"]),
            _rec("0002", 0.18, parents=["0000"])]
    assert arch.children_count(recs, "0000") == 2
    assert arch.children_count(recs, "0001") == 0


def test_materialize_parent_copies_transcribe(tmp_path):
    job_dir = tmp_path / "runs" / "job"
    (job_dir / "0000").mkdir(parents=True)
    (job_dir / "0000" / "transcribe.py").write_text("def transcribe(a, s):\n    return 'X'\n")
    ws = tmp_path / "workspace" / "transcribe.py"
    ws.parent.mkdir(parents=True)
    arch.materialize_parent(job_dir, "0000", ws)
    assert "return 'X'" in ws.read_text()
