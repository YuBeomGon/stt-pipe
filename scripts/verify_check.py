"""Phase 3 verify — numeric guard helper (`PHASE3-PLAN.md §1.2`).

Reads ``score_report.json`` and ``per_file.jsonl`` produced by
``judge.evaluate`` and applies the hard / soft guards in the §1.2 table:

  * 산술 무결성 — Σ edits / Σ ref_chars == corpus_cer
  * catastrophic output — empty_output_rate > 0.50 or length_ratio.p05 < 0.10
    or length_ratio.p95 > 5.0
  * runtime hard cap — total_inference_time_s > baseline * RUNTIME_HARD_MULTIPLIER
  * quality budget — hallucination/repetition/coverage/length 가 baseline guard
    분포 대비 크게 악화. 기본 ``warning`` (exit 0 + stderr). 환경변수
    ``QUALITY_BUDGET_HARD=1`` 일 때만 exit 1.

Hard-fail 은 stderr 에 한 줄 사유를 찍고 exit 1. 정상 통과는 stderr 에 한 줄
요약을 찍고 exit 0. 산출물 (score_report.json) 자체는 호출 측 (``verify.sh``)
이 마지막에 ``corpus_cer`` 을 stdout 에 찍는다 — 본 스크립트는 stdout 을 비워둔다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# --- hard 임계 (catastrophic output) -----------------------------------------
_EMPTY_OUTPUT_RATE_MAX = 0.50
_LENGTH_RATIO_P05_MIN = 0.10
_LENGTH_RATIO_P95_MAX = 5.0
_ARITHMETIC_TOL = 1e-6

# --- quality budget 허용폭 (baseline guard 대비 가산) ------------------------
# baseline 측정 시점의 guard 분포 + 다음 값 만큼은 봐준다. 그 이상이면
# 기본 warn, ``QUALITY_BUDGET_HARD=1`` 일 때 hard.
_QB_HALLUC_DELTA = 0.20         # hallucination_hit_rate
_QB_REPEAT_DELTA = 0.20         # repeated_text_rate
_QB_EMPTY_DELTA = 0.20          # empty_output_rate (catastrophic 임계 안에서 추가)
_QB_LENGTH_MEAN_DELTA = 0.30    # |length_ratio.mean - baseline.mean|
_QB_COVERAGE_DROP = 0.20        # baseline.coverage - run.coverage (baseline 있을 때만)


def _read_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"verify_check: score_report 누락 — {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"verify_check: score_report JSON 파싱 실패 — {path}: {e}")


def _read_per_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(
                    f"verify_check: per_file JSON 파싱 실패 — {path}:{ln}: {e}"
                )
    return out


def _read_baseline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"verify_check: baseline 누락 — {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check_arithmetic(
    report: dict[str, Any], per_file: list[dict[str, Any]]
) -> str | None:
    """Σedits / Σref_chars == corpus_cer? 1e-6 허용."""
    if not per_file:
        return "per_file.jsonl 누락 — 산술 검증 불가"
    total_edits = sum(int(r.get("edits", 0)) for r in per_file)
    total_ref = sum(int(r.get("ref_chars", 0)) for r in per_file)
    if total_ref == 0:
        return "Σ ref_chars == 0 — 평가 가능한 라벨이 없음"
    derived = total_edits / total_ref
    reported = float(report.get("corpus_cer", -1))
    if abs(derived - reported) > _ARITHMETIC_TOL:
        return (
            f"산술 무결성 위반: Σedits/Σref = {derived:.6f}, "
            f"report.corpus_cer = {reported:.6f}, Δ = {abs(derived-reported):.2e}"
        )
    return None


def _check_catastrophic(report: dict[str, Any]) -> str | None:
    empty = float(report.get("empty_output_rate", 0.0) or 0.0)
    if empty > _EMPTY_OUTPUT_RATE_MAX:
        return f"empty_output_rate {empty:.3f} > {_EMPTY_OUTPUT_RATE_MAX}"
    lr = report.get("length_ratio") or {}
    p05 = float(lr.get("p05", 1.0) or 1.0)
    p95 = float(lr.get("p95", 1.0) or 1.0)
    if p05 < _LENGTH_RATIO_P05_MIN:
        return f"length_ratio.p05 {p05:.3f} < {_LENGTH_RATIO_P05_MIN}"
    if p95 > _LENGTH_RATIO_P95_MAX:
        return f"length_ratio.p95 {p95:.3f} > {_LENGTH_RATIO_P95_MAX}"
    return None


def _check_runtime(
    report: dict[str, Any], baseline: dict[str, Any], multiplier: float
) -> str | None:
    baseline_t = float(baseline.get("total_inference_time_s", 0.0) or 0.0)
    if baseline_t <= 0:
        return None  # baseline time 미측정 — 가드 보류
    cap = baseline_t * multiplier
    run_t = float(report.get("total_inference_time_s", 0.0) or 0.0)
    if run_t > cap:
        return (
            f"runtime hard cap 초과: {run_t:.1f}s > baseline {baseline_t:.1f}s × "
            f"{multiplier} = {cap:.1f}s"
        )
    return None


def _check_quality_budget(
    report: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    """Returns list of 위반 사유 (각각 한 줄). 비어 있으면 통과."""
    issues: list[str] = []
    gb = baseline.get("guard_baseline") or {}

    # hallucination
    b_h = float(gb.get("hallucination_hit_rate", 0.0) or 0.0)
    r_h = float(report.get("hallucination_hit_rate", 0.0) or 0.0)
    if r_h > b_h + _QB_HALLUC_DELTA:
        issues.append(
            f"hallucination_hit_rate {r_h:.3f} > baseline {b_h:.3f} + {_QB_HALLUC_DELTA}"
        )

    # repeated
    b_r = float(gb.get("repeated_text_rate", 0.0) or 0.0)
    r_r = float(report.get("repeated_text_rate", 0.0) or 0.0)
    if r_r > b_r + _QB_REPEAT_DELTA:
        issues.append(
            f"repeated_text_rate {r_r:.3f} > baseline {b_r:.3f} + {_QB_REPEAT_DELTA}"
        )

    # empty (catastrophic 임계 미만이지만 quality budget 위반 가능)
    b_e = float(gb.get("empty_output_rate", 0.0) or 0.0)
    r_e = float(report.get("empty_output_rate", 0.0) or 0.0)
    if r_e > b_e + _QB_EMPTY_DELTA:
        issues.append(
            f"empty_output_rate {r_e:.3f} > baseline {b_e:.3f} + {_QB_EMPTY_DELTA}"
        )

    # length_ratio.mean 의 절대 편차
    b_lr = gb.get("length_ratio") or {}
    b_mean = float(b_lr.get("mean", 1.0) or 1.0)
    r_lr = report.get("length_ratio") or {}
    r_mean = float(r_lr.get("mean", 1.0) or 1.0)
    if abs(r_mean - b_mean) > _QB_LENGTH_MEAN_DELTA:
        issues.append(
            f"|length_ratio.mean - baseline| {abs(r_mean - b_mean):.3f} > {_QB_LENGTH_MEAN_DELTA} "
            f"(run {r_mean:.3f}, baseline {b_mean:.3f})"
        )

    # coverage (baseline 에 측정값 있을 때만)
    b_c = gb.get("audio_coverage_rate")
    r_c = report.get("audio_coverage_rate")
    if b_c is not None and r_c is not None:
        if float(b_c) - float(r_c) > _QB_COVERAGE_DROP:
            issues.append(
                f"audio_coverage_rate drop {float(b_c) - float(r_c):.3f} > {_QB_COVERAGE_DROP} "
                f"(baseline {float(b_c):.3f}, run {float(r_c):.3f})"
            )

    return issues


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase 3 verify numeric guard.")
    p.add_argument("--report", required=True, type=Path,
                   help="runs/<hyp>/score_report.json")
    p.add_argument("--per-file", required=True, type=Path,
                   help="runs/<hyp>/per_file.jsonl")
    p.add_argument("--baseline", required=True, type=Path,
                   help="baseline/target_cer.json")
    p.add_argument("--runtime-hard-multiplier", type=float, default=3.0,
                   help="total_inference_time_s 의 baseline 대비 허용 배수")
    args = p.parse_args(argv)

    report = _read_report(args.report)
    per_file = _read_per_file(args.per_file)
    baseline = _read_baseline(args.baseline)

    # --- hard 가드 ---
    for label, fn in (
        ("산술 무결성", lambda: _check_arithmetic(report, per_file)),
        ("catastrophic output", lambda: _check_catastrophic(report)),
        ("runtime hard cap",
         lambda: _check_runtime(report, baseline, args.runtime_hard_multiplier)),
    ):
        msg = fn()
        if msg:
            print(f"verify_check FAIL [{label}]: {msg}", file=sys.stderr)
            return 1

    # --- quality budget (기본 warn) ---
    qb_issues = _check_quality_budget(report, baseline)
    if qb_issues:
        hard = os.environ.get("QUALITY_BUDGET_HARD", "0") == "1"
        prefix = "FAIL" if hard else "WARN"
        for issue in qb_issues:
            print(f"verify_check {prefix} [quality budget]: {issue}", file=sys.stderr)
        if hard:
            return 1

    # 정상 요약
    cer = report.get("corpus_cer", "?")
    rt = report.get("total_inference_time_s", "?")
    print(
        f"verify_check OK — corpus_cer={cer}, total_inference_time_s={rt}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
