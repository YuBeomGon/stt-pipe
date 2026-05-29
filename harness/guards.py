"""
harness/guards.py
Phase 3 numeric guard checks for candidate STT runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

EMPTY_OUTPUT_RATE_MAX = 0.50
LENGTH_RATIO_P05_MIN = 0.10
LENGTH_RATIO_P95_MAX = 5.0
ARITHMETIC_TOL = 1e-6

QB_HALLUC_DELTA = 0.20
QB_REPEAT_DELTA = 0.20
QB_EMPTY_DELTA = 0.20
QB_LENGTH_MEAN_DELTA = 0.30
QB_COVERAGE_DROP = 0.20


def read_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"verify_check: score_report 누락 — {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"verify_check: score_report JSON 파싱 실패 — {path}: {exc}")


def read_per_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"verify_check: per_file JSON 파싱 실패 — {path}:{line_no}: {exc}"
                )
    return out


def read_baseline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"verify_check: baseline 누락 — {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def check_arithmetic(
    report: dict[str, Any], per_file: list[dict[str, Any]]
) -> str | None:
    """Verify ``sum(edits) / sum(ref_chars) == corpus_cer``."""
    if not per_file:
        return "per_file.jsonl 누락 — 산술 검증 불가"
    total_edits = sum(int(record.get("edits", 0)) for record in per_file)
    total_ref = sum(int(record.get("ref_chars", 0)) for record in per_file)
    if total_ref == 0:
        return "Σ ref_chars == 0 — 평가 가능한 라벨이 없음"
    derived = total_edits / total_ref
    reported = float(report.get("corpus_cer", -1))
    if abs(derived - reported) > ARITHMETIC_TOL:
        return (
            f"산술 무결성 위반: Σedits/Σref = {derived:.6f}, "
            f"report.corpus_cer = {reported:.6f}, Δ = {abs(derived-reported):.2e}"
        )
    return None


def check_catastrophic(report: dict[str, Any]) -> str | None:
    empty = float(report.get("empty_output_rate", 0.0) or 0.0)
    if empty > EMPTY_OUTPUT_RATE_MAX:
        return f"empty_output_rate {empty:.3f} > {EMPTY_OUTPUT_RATE_MAX}"
    length_ratio = report.get("length_ratio") or {}
    p05 = float(length_ratio.get("p05", 1.0) or 1.0)
    p95 = float(length_ratio.get("p95", 1.0) or 1.0)
    if p05 < LENGTH_RATIO_P05_MIN:
        return f"length_ratio.p05 {p05:.3f} < {LENGTH_RATIO_P05_MIN}"
    if p95 > LENGTH_RATIO_P95_MAX:
        return f"length_ratio.p95 {p95:.3f} > {LENGTH_RATIO_P95_MAX}"
    return None


def check_runtime(
    report: dict[str, Any], baseline: dict[str, Any], multiplier: float
) -> str | None:
    baseline_t = float(baseline.get("total_inference_time_s", 0.0) or 0.0)
    if baseline_t <= 0:
        return None
    cap = baseline_t * multiplier
    run_t = float(report.get("total_inference_time_s", 0.0) or 0.0)
    if run_t > cap:
        return (
            f"runtime hard cap 초과: {run_t:.1f}s > baseline {baseline_t:.1f}s × "
            f"{multiplier} = {cap:.1f}s"
        )
    return None


def check_quality_budget(
    report: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    """Return quality-budget warnings or failures."""
    issues: list[str] = []
    guard_baseline = baseline.get("guard_baseline") or {}

    baseline_halluc = float(
        guard_baseline.get("hallucination_hit_rate", 0.0) or 0.0
    )
    run_halluc = float(report.get("hallucination_hit_rate", 0.0) or 0.0)
    if run_halluc > baseline_halluc + QB_HALLUC_DELTA:
        issues.append(
            f"hallucination_hit_rate {run_halluc:.3f} > baseline "
            f"{baseline_halluc:.3f} + {QB_HALLUC_DELTA}"
        )

    baseline_repeat = float(guard_baseline.get("repeated_text_rate", 0.0) or 0.0)
    run_repeat = float(report.get("repeated_text_rate", 0.0) or 0.0)
    if run_repeat > baseline_repeat + QB_REPEAT_DELTA:
        issues.append(
            f"repeated_text_rate {run_repeat:.3f} > baseline "
            f"{baseline_repeat:.3f} + {QB_REPEAT_DELTA}"
        )

    baseline_empty = float(guard_baseline.get("empty_output_rate", 0.0) or 0.0)
    run_empty = float(report.get("empty_output_rate", 0.0) or 0.0)
    if run_empty > baseline_empty + QB_EMPTY_DELTA:
        issues.append(
            f"empty_output_rate {run_empty:.3f} > baseline "
            f"{baseline_empty:.3f} + {QB_EMPTY_DELTA}"
        )

    baseline_lr = guard_baseline.get("length_ratio") or {}
    baseline_mean = float(baseline_lr.get("mean", 1.0) or 1.0)
    run_lr = report.get("length_ratio") or {}
    run_mean = float(run_lr.get("mean", 1.0) or 1.0)
    if abs(run_mean - baseline_mean) > QB_LENGTH_MEAN_DELTA:
        issues.append(
            f"|length_ratio.mean - baseline| {abs(run_mean - baseline_mean):.3f} "
            f"> {QB_LENGTH_MEAN_DELTA} (run {run_mean:.3f}, "
            f"baseline {baseline_mean:.3f})"
        )

    baseline_coverage = guard_baseline.get("audio_coverage_rate")
    run_coverage = report.get("audio_coverage_rate")
    if baseline_coverage is not None and run_coverage is not None:
        coverage_drop = float(baseline_coverage) - float(run_coverage)
        if coverage_drop > QB_COVERAGE_DROP:
            issues.append(
                f"audio_coverage_rate drop {coverage_drop:.3f} > {QB_COVERAGE_DROP} "
                f"(baseline {float(baseline_coverage):.3f}, "
                f"run {float(run_coverage):.3f})"
            )

    return issues


def run_checks(
    report: dict[str, Any],
    per_file: list[dict[str, Any]],
    baseline: dict[str, Any],
    runtime_hard_multiplier: float = 3.0,
    quality_budget_hard: bool = False,
) -> int:
    hard_checks = (
        ("산술 무결성", lambda: check_arithmetic(report, per_file)),
        ("catastrophic output", lambda: check_catastrophic(report)),
        (
            "runtime hard cap",
            lambda: check_runtime(report, baseline, runtime_hard_multiplier),
        ),
    )
    for label, check in hard_checks:
        message = check()
        if message:
            print(f"verify_check FAIL [{label}]: {message}", file=sys.stderr)
            return 1

    quality_issues = check_quality_budget(report, baseline)
    if quality_issues:
        prefix = "FAIL" if quality_budget_hard else "WARN"
        for issue in quality_issues:
            print(f"verify_check {prefix} [quality budget]: {issue}", file=sys.stderr)
        if quality_budget_hard:
            return 1

    cer = report.get("corpus_cer", "?")
    runtime = report.get("total_inference_time_s", "?")
    print(
        f"verify_check OK — corpus_cer={cer}, total_inference_time_s={runtime}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 3 verify numeric guard.")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--per-file", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--runtime-hard-multiplier", type=float, default=3.0)
    args = parser.parse_args(argv)

    return run_checks(
        report=read_report(args.report),
        per_file=read_per_file(args.per_file),
        baseline=read_baseline(args.baseline),
        runtime_hard_multiplier=args.runtime_hard_multiplier,
        quality_budget_hard=os.environ.get("QUALITY_BUDGET_HARD", "0") == "1",
    )


if __name__ == "__main__":
    raise SystemExit(main())

