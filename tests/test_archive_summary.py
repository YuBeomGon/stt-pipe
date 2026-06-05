from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import archive as arch
import scripts.archive_summary as summ


def test_leaderboard_sorts_by_cer_and_marks_best(tmp_path):
    job_dir = tmp_path / "runs" / "job"
    for cid, cer, st in [("0000", 0.20, "scored"), ("0001", 0.15, "scored"),
                         ("0002", None, "rejected"), ("0003", 0.18, "scored")]:
        arch.append_record(job_dir, arch.ArchiveRecord(
            id=cid, parents=[], cer=cer, status=st, hypothesis="h",
            what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
    arch.write_best(job_dir, "0001")
    text = summ.render_leaderboard(job_dir, holdout={})
    lines = text.splitlines()
    # best row carries a marker and appears before worse scored rows
    assert "0001" in text and "*" in text
    assert lines.index([l for l in lines if "0001" in l][0]) < \
           lines.index([l for l in lines if "0003" in l][0])


def test_holdout_column_rendered_when_present(tmp_path):
    job_dir = tmp_path / "runs" / "job"
    arch.append_record(job_dir, arch.ArchiveRecord(
        id="0000", parents=[], cer=0.15, status="scored", hypothesis="h",
        what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
    text = summ.render_leaderboard(job_dir, holdout={"0000": 0.16})
    assert "0.1600" in text
