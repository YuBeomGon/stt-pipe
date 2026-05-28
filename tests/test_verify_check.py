"""Smoke for ``scripts.verify_check`` — Phase 3 numeric guard.

가드별 fixture 를 만들어 exit code 와 stderr 메시지를 확인. judge 의 실제 출력은
호출하지 않고 schema 만 합성한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _run(report: Path, per_file: Path, baseline: Path,
         runtime_mult: float = 3.0,
         quality_hard: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{os.environ.get('PYTHONPATH','')}"}
    if quality_hard:
        env["QUALITY_BUDGET_HARD"] = "1"
    return subprocess.run(
        [
            sys.executable, "-m", "scripts.verify_check",
            "--report", str(report),
            "--per-file", str(per_file),
            "--baseline", str(baseline),
            "--runtime-hard-multiplier", str(runtime_mult),
        ],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


def _write_report(path: Path, *, corpus_cer: float = 0.30,
                  total_inference_time_s: float = 100.0,
                  empty_output_rate: float = 0.0,
                  lr_mean: float = 0.6, lr_p05: float = 0.4, lr_p95: float = 0.9,
                  repeated_text_rate: float = 0.0,
                  hallucination_hit_rate: float = 0.0,
                  audio_coverage_rate: float | None = None) -> None:
    path.write_text(json.dumps({
        "batch": "AIG_녹취반출_20250715",
        "corpus_cer": corpus_cer,
        "macro_cer": corpus_cer + 0.01,
        "total_inference_time_s": total_inference_time_s,
        "empty_output_rate": empty_output_rate,
        "length_ratio": {"mean": lr_mean, "p05": lr_p05, "p95": lr_p95},
        "repeated_text_rate": repeated_text_rate,
        "hallucination_hit_rate": hallucination_hit_rate,
        "audio_coverage_rate": audio_coverage_rate,
    }, ensure_ascii=False), encoding="utf-8")


def _write_per_file_matching(path: Path, corpus_cer: float, *,
                             total_ref: int = 1000) -> None:
    """3 개 row 합산이 corpus_cer 와 일치하도록 합성."""
    total_edits = round(corpus_cer * total_ref)
    rows = [
        {"ref_chars": 400, "edits": round(total_edits * 0.4),
         "cer": 0.0, "sub": 0, "del": 0, "ins": 0},
        {"ref_chars": 300, "edits": round(total_edits * 0.3),
         "cer": 0.0, "sub": 0, "del": 0, "ins": 0},
        {"ref_chars": 300, "edits": total_edits - round(total_edits * 0.4) - round(total_edits * 0.3),
         "cer": 0.0, "sub": 0, "del": 0, "ins": 0},
    ]
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _write_baseline(path: Path, *,
                    total_inference_time_s: float = 100.0,
                    hallucination_hit_rate: float = 0.1,
                    repeated_text_rate: float = 0.1,
                    empty_output_rate: float = 0.0,
                    lr_mean: float = 0.6,
                    audio_coverage_rate: float | None = None) -> None:
    path.write_text(json.dumps({
        "target_cer": 0.10,
        "baseline_cer": 0.4320,
        "total_inference_time_s": total_inference_time_s,
        "guard_baseline": {
            "empty_output_rate": empty_output_rate,
            "length_ratio": {"mean": lr_mean, "p05": 0.4, "p95": 0.9},
            "repeated_text_rate": repeated_text_rate,
            "hallucination_hit_rate": hallucination_hit_rate,
            "audio_coverage_rate": audio_coverage_rate,
        },
    }, ensure_ascii=False), encoding="utf-8")


def test_pass_normal(tmp_path: Path) -> None:
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    _write_report(report, corpus_cer=0.30)
    _write_per_file_matching(per_file, corpus_cer=0.30)
    _write_baseline(baseline)

    r = _run(report, per_file, baseline)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "verify_check OK" in r.stderr


def test_fail_arithmetic_mismatch(tmp_path: Path) -> None:
    """Σedits/Σref 가 corpus_cer 와 크게 다르면 exit 1."""
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    _write_report(report, corpus_cer=0.30)
    # per_file 은 corpus_cer=0.5 에 해당하도록 작성 → 불일치 유도
    _write_per_file_matching(per_file, corpus_cer=0.50)
    _write_baseline(baseline)

    r = _run(report, per_file, baseline)
    assert r.returncode == 1
    assert "산술 무결성" in r.stderr


def test_fail_empty_output_rate(tmp_path: Path) -> None:
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    _write_report(report, corpus_cer=0.30, empty_output_rate=0.80)
    _write_per_file_matching(per_file, corpus_cer=0.30)
    _write_baseline(baseline)

    r = _run(report, per_file, baseline)
    assert r.returncode == 1
    assert "empty_output_rate" in r.stderr


def test_fail_length_ratio_p05(tmp_path: Path) -> None:
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    _write_report(report, corpus_cer=0.30, lr_p05=0.05)
    _write_per_file_matching(per_file, corpus_cer=0.30)
    _write_baseline(baseline)

    r = _run(report, per_file, baseline)
    assert r.returncode == 1
    assert "length_ratio.p05" in r.stderr


def test_fail_length_ratio_p95(tmp_path: Path) -> None:
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    _write_report(report, corpus_cer=0.30, lr_p95=6.0)
    _write_per_file_matching(per_file, corpus_cer=0.30)
    _write_baseline(baseline)

    r = _run(report, per_file, baseline)
    assert r.returncode == 1
    assert "length_ratio.p95" in r.stderr


def test_fail_runtime_hard_cap(tmp_path: Path) -> None:
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    # baseline 100s × 3.0 = 300s 캡. 500s 면 위반.
    _write_report(report, corpus_cer=0.30, total_inference_time_s=500.0)
    _write_per_file_matching(per_file, corpus_cer=0.30)
    _write_baseline(baseline, total_inference_time_s=100.0)

    r = _run(report, per_file, baseline)
    assert r.returncode == 1
    assert "runtime hard cap" in r.stderr


def test_quality_budget_warn_by_default(tmp_path: Path) -> None:
    """quality budget 위반은 기본 warn (exit 0) — QUALITY_BUDGET_HARD=1 일 때만 fail."""
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    # baseline hallucination 0.1, run 0.5 (Δ=0.4 > 허용 0.2).
    _write_report(report, corpus_cer=0.30, hallucination_hit_rate=0.5)
    _write_per_file_matching(per_file, corpus_cer=0.30)
    _write_baseline(baseline, hallucination_hit_rate=0.1)

    r = _run(report, per_file, baseline)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "WARN" in r.stderr
    assert "hallucination_hit_rate" in r.stderr


def test_quality_budget_hard_mode(tmp_path: Path) -> None:
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file.jsonl"
    baseline = tmp_path / "baseline.json"

    _write_report(report, corpus_cer=0.30, hallucination_hit_rate=0.5)
    _write_per_file_matching(per_file, corpus_cer=0.30)
    _write_baseline(baseline, hallucination_hit_rate=0.1)

    r = _run(report, per_file, baseline, quality_hard=True)
    assert r.returncode == 1
    assert "FAIL" in r.stderr
    assert "hallucination_hit_rate" in r.stderr


def test_missing_per_file_arithmetic_skipped(tmp_path: Path) -> None:
    """per_file.jsonl 누락이면 산술 무결성 못 검증 → exit 1."""
    report = tmp_path / "score_report.json"
    per_file = tmp_path / "per_file_missing.jsonl"  # not created
    baseline = tmp_path / "baseline.json"

    _write_report(report, corpus_cer=0.30)
    _write_baseline(baseline)

    r = _run(report, per_file, baseline)
    assert r.returncode == 1
    assert "산술 무결성" in r.stderr or "per_file" in r.stderr
