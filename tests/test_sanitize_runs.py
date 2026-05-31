"""``scripts.sanitize_runs`` — PII 스캐너/redaction 안전망.

스킬(`archive-phase-run`)이 이 코드에 의존하므로, 콜ID·마스킹폰·batch·홈경로를
잡고 CER float 소수부는 오탐하지 않음을 고정한다.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from scripts import sanitize_runs as s


def test_scan_catches_pii() -> None:
    assert s._scan_pii("00003004871752551193")        # 20자리 콜ID
    assert s._scan_pii("010XXXX0120")                  # 마스킹폰
    assert s._scan_pii("0106613XXXX")
    assert s._scan_pii("AIG_녹취반출_20250715")        # batch
    assert s._scan_pii("/home/someone/x/stt-pipe/d")   # 홈경로
    assert s._scan_pii("010-4349-8048")                # 실제 전화 표기


def test_scan_ignores_float_false_positives() -> None:
    # CER/length_ratio/rtf 소수부는 콜ID/전화로 오탐하면 안 됨
    assert not s._scan_pii("corpus_cer 0.16737159161112")
    assert not s._scan_pii("0.1835525536")
    assert not s._scan_pii("rtf 0.026075197846154814")
    assert not s._scan_pii("file00.wav file10.txt")
    assert not s._scan_pii("[REDACTED]")
    assert not s._scan_pii("beam_size=5 patience=2")


def test_redact_maps_callid_and_strips_path() -> None:
    m = {"00003004871752551193_l": "file00", "00003004871752551193": "file00"}
    out = s._redact_text(
        "/home/beomgon/side/agent/stt-pipe/data/raw/wav/"
        "AIG_녹취반출_20250715/00003004871752551193_l.wav",
        m,
    )
    assert "00003004871752551193" not in out
    assert "녹취반출" not in out
    assert "/home/beomgon" not in out
    assert "file00.wav" in out


def test_redact_hallucination_text() -> None:
    line = json.dumps(
        {"wav": "x.wav", "hallucinated_spans": [{"text": "시청해주셔서 감사합니다", "pattern": "p", "start": 0, "end": 5}]},
        ensure_ascii=False,
    )
    out = s._redact_jsonl_hallucinations(line)
    assert "시청해주셔서" not in out
    assert "[REDACTED]" in out


def test_end_to_end_clean_archive(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    d = runs / "jobX_iter_001"
    d.mkdir(parents=True)
    (d / "per_file.jsonl").write_text(
        json.dumps({
            "wav": "/home/u/stt-pipe/data/raw/wav/AIG_녹취반출_20250715/01_6036_010XXXX0120_2025_07_15_l.wav",
            "label": "/home/u/stt-pipe/data/raw/label/AIG_녹취반출_20250715/01_6036_010XXXX0120_2025_07_15_l.txt",
            "cer": 0.1835525536, "ref_chars": 1000, "hyp_chars": 990,
            "hallucinated_spans": [{"text": "감사합니다", "pattern": "p"}],
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (d / "candidate.diff").write_text("def transcribe(): pass\n", encoding="utf-8")

    out = tmp_path / "out.tar.gz"
    rc = s.sanitize("jobX", runs, scan_only=False, out=out)
    assert rc == 0
    assert out.is_file()

    # 추출해 독립 검증
    ex = tmp_path / "ex"
    with tarfile.open(out) as t:
        t.extractall(ex)
    blob = "".join(
        p.read_text(encoding="utf-8") for p in ex.rglob("*") if p.is_file()
    )
    for leak in ("010XXXX0120", "녹취반출", "/home/u", "감사합니다"):
        assert leak not in blob, f"PII 누출: {leak}"
    assert "file00" in blob
