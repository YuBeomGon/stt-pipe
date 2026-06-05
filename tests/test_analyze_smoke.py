"""Synthetic smoke for ``scripts.analyze_run`` — `PHASE2-PLAN.md` §6.

Generates fake ``runs/<hyp_id>/`` artefacts that follow the score_report /
per_file / diagnosis schemas, then runs the analyzer against them and checks:

  * every ``{{var}}`` in the template gets substituted (no leftovers),
  * the category breakdown is non-empty and its accepted counts sum to the
    accepted iteration count,
  * monotonic-decrease / plateau / regression trajectories are all handled
    without crashing.

The smoke does *not* touch the real Whisper backend or audio — only the JSON
schema is exercised.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _per_file_record(stem: str, cer: float, audio_s: float = 100.0) -> dict[str, Any]:
    ref_chars = 1000
    edits = int(ref_chars * cer)
    return {
        "wav": f"/tmp/fake/wav/{stem}.wav",
        "label": f"/tmp/fake/label/{stem}.txt",
        "ref_chars": ref_chars,
        "hyp_chars": int(ref_chars * (1 - cer * 0.5)),
        "length_ratio": max(0.01, 1 - cer * 0.5),
        "empty_output": False,
        "audio_s": audio_s,
        "decode_s": audio_s * 0.05,
        "cer": cer,
        "rtf": 0.05,
        "sub": edits // 3,
        "del": edits // 3,
        "ins": edits - 2 * (edits // 3),
        "edits": edits,
        "audio_coverage_s": None,
        "hallucination_hits": 1 if cer > 0.6 else 0,
        "hallucinated_spans": [],
        "repeated_text": False,
    }


def _score_report(
    batch: str,
    corpus_cer: float,
    produced_at: datetime,
    halluc_rate: float = 0.0,
    repeated_rate: float = 0.0,
) -> dict[str, Any]:
    return {
        "batch": batch,
        "produced_at": produced_at.isoformat(),
        "transcribe_spec": "workspace.transcribe:transcribe",
        "num_files": 3,
        "num_files_scored": 3,
        "corpus_cer": corpus_cer,
        "macro_cer": corpus_cer + 0.01,
        "error_breakdown": {
            "sub_ratio": 0.4,
            "del_ratio": 0.4,
            "ins_ratio": 0.2,
        },
        "empty_output_rate": 0.0,
        "length_ratio": {"mean": 0.9, "p05": 0.7, "p95": 1.1},
        "repeated_text_rate": repeated_rate,
        "audio_coverage_rate": None,
        "hallucination_hit_rate": halluc_rate,
        "hallucination_hits_total": int(halluc_rate * 3),
        "total_audio_s": 300.0,
        "total_inference_time_s": 6.0,
        "runtime_s_per_audio_min": 1.2,
        "avg_rtf": 0.02,
    }


def _diagnosis(focus_wav: str) -> dict[str, Any]:
    return {
        "batch": "AIG_녹취반출_20250715",
        "per_file_diagnosis": [
            {
                "wav": f"/tmp/fake/wav/{focus_wav}.wav",
                "metrics": {
                    "cer": 0.5,
                    "length_ratio": 0.9,
                    "hallucination_hits": 0,
                    "empty_output": False,
                    "repeated_text": False,
                },
                "audio_profile_summary": None,
                "flags": [],
            }
        ],
        "focus_files": [
            {"wav": f"/tmp/fake/wav/{focus_wav}.wav", "why_selected": ["worst_cer"]}
        ],
    }


def _write_iter(
    runs_dir: Path,
    hyp_id: str,
    corpus_cer: float,
    produced_at: datetime,
    halluc_rate: float = 0.0,
    repeated_rate: float = 0.0,
    focus_wav: str = "file_a",
) -> None:
    d = runs_dir / hyp_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "score_report.json").write_text(
        json.dumps(_score_report("AIG_녹취반출_20250715", corpus_cer, produced_at,
                                  halluc_rate, repeated_rate), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    per_file = [
        _per_file_record("file_a", corpus_cer * 0.9),
        _per_file_record("file_b", corpus_cer * 1.1),
        _per_file_record("file_c", corpus_cer),
    ]
    with (d / "per_file.jsonl").open("w", encoding="utf-8") as fh:
        for r in per_file:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (d / "diagnosis_report.json").write_text(
        json.dumps(_diagnosis(focus_wav), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_baselines(baseline_dir: Path) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "target_cer.json").write_text(
        json.dumps(
            {
                "target_cer": 0.10,
                "baseline_cer": 0.4320,
                "guard_baseline": {
                    "empty_output_rate": 0.0,
                    "length_ratio": {"mean": 0.6, "p05": 0.36, "p95": 0.80},
                    "repeated_text_rate": 0.09,
                    "hallucination_hit_rate": 0.36,
                    "audio_coverage_rate": None,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (baseline_dir / "noise_floor.json").write_text(
        json.dumps(
            {
                "sigma": 0.02,
                "samples": [0.5, 0.52, 0.50],
                "is_provisional": False,
                "applies_to": "corpus_cer Δ threshold",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def synthetic_run(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    baseline_dir = tmp_path / "baseline"
    out_path = tmp_path / "runs/_summary/REPORT.md"

    # Trajectory: monotonic improvement → plateau → regression.
    base_time = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
    sequence = [
        ("smoke_iter_00", 0.90),
        ("smoke_iter_01", 0.85),
        ("smoke_iter_02", 0.83),
        ("smoke_iter_03", 0.80),
        ("smoke_iter_04", 0.80),  # plateau
        ("smoke_iter_05", 0.85),  # regression
        ("smoke_iter_06", 0.82),
    ]
    for i, (hyp_id, cer) in enumerate(sequence):
        _write_iter(
            runs_dir, hyp_id, cer,
            base_time + timedelta(minutes=10 * i),
            halluc_rate=max(0.0, 0.3 - i * 0.05),
        )
    _write_baselines(baseline_dir)

    # Copy real template so the smoke matches production behavior.
    template_src = ROOT / "docs" / "templates" / "REPORT.md"
    template_dst = tmp_path / "REPORT_TEMPLATE.md"
    shutil.copy(template_src, template_dst)

    return {
        "runs_dir": runs_dir,
        "baseline_dir": baseline_dir,
        "template": template_dst,
        "out": out_path,
    }


def test_analyze_run_renders_template(synthetic_run, monkeypatch):
    monkeypatch.chdir(ROOT)  # so git invocations / relative paths behave
    from scripts.analyze_run import main

    rc = main([
        "--runs-dir", str(synthetic_run["runs_dir"]),
        "--baseline", str(synthetic_run["baseline_dir"]),
        "--template", str(synthetic_run["template"]),
        "--out", str(synthetic_run["out"]),
        "--job-id", "smoke",
    ])
    assert rc == 0
    assert synthetic_run["out"].is_file()
    text = synthetic_run["out"].read_text(encoding="utf-8")

    # No unsubstituted placeholders.
    leftovers = re.findall(r"{{\s*[a-zA-Z_]+\s*}}", text)
    assert leftovers == [], f"unsubstituted vars: {leftovers}"

    # Sanity: report mentions all 7 iter hyp_ids.
    for hyp in (f"iter_0{i}" for i in range(7)):
        assert hyp in text

    # Best corpus_cer is smoke_iter_03's value (0.80) — the headline number
    # tracks the running-best accepted iter, not the last-produced iter
    # (smoke_iter_06 @ 0.82 is a regression that must NOT surface as "Best").
    assert "0.8000" in text


def test_category_distribution_sum_matches_accepted(synthetic_run, monkeypatch):
    monkeypatch.chdir(ROOT)
    from scripts.analyze_run import (
        classify_iterations,
        discover_iterations,
        _category_distribution,
    )

    iters = discover_iterations(synthetic_run["runs_dir"])
    assert len(iters) == 7
    baseline = json.loads(
        (synthetic_run["baseline_dir"] / "target_cer.json").read_text(encoding="utf-8")
    )
    noise = json.loads(
        (synthetic_run["baseline_dir"] / "noise_floor.json").read_text(encoding="utf-8")
    )
    classify_iterations(iters, baseline.get("guard_baseline", {}), noise)

    # Without commit metadata everything classifies as `unclassified`.
    n_accepted = sum(1 for it in iters if it.accepted)
    dist = _category_distribution(iters)
    # Sum of every category in the distribution should equal n_accepted because
    # each accepted iter contributes exactly once.
    assert sum(dist.values()) == n_accepted

    # Trajectory expectation: iters 0,1,2,3 accept (descending below 2σ=0.04 each).
    # smoke_iter_04 plateau (Δ=0) → revert. smoke_iter_05 regression (Δ=+0.05) → revert.
    # smoke_iter_06 (0.82 vs best 0.80, Δ=+0.02) → revert.
    accepted_ids = [it.hyp_id for it in iters if it.accepted]
    assert "smoke_iter_00" in accepted_ids
    assert "smoke_iter_03" in accepted_ids
    # plateau / regression must NOT be in accepted
    assert "smoke_iter_04" not in accepted_ids
    assert "smoke_iter_05" not in accepted_ids


def test_handles_no_iterations(tmp_path, monkeypatch):
    """analyze_run shouldn't crash when runs/ is empty (very first jobs)."""
    monkeypatch.chdir(ROOT)
    runs_dir = tmp_path / "runs_empty"
    runs_dir.mkdir()
    baseline_dir = tmp_path / "baseline"
    _write_baselines(baseline_dir)
    template = ROOT / "docs" / "templates" / "REPORT.md"
    out = tmp_path / "REPORT.md"

    from scripts.analyze_run import main

    rc = main([
        "--runs-dir", str(runs_dir),
        "--baseline", str(baseline_dir),
        "--template", str(template),
        "--out", str(out),
        "--job-id", "empty",
    ])
    assert rc == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    # Empty job → no leftover {{vars}}.
    assert re.findall(r"{{\s*[a-zA-Z_]+\s*}}", text) == []


def test_portfolio_section_aggregates_modes(tmp_path) -> None:
    """portfolio_evolution_section 이 decisions.jsonl 의 chosen_mode/final_decision/
    parent_shortlist 을 집계한다 (#3 Step 3 scheduler 결과 반영)."""
    from scripts.analyze_run import portfolio_evolution_section

    dec = tmp_path / "job_decisions.jsonl"
    rows = [
        {"iter": 1, "harness_family_id": "family_001", "chosen_mode": "explore",
         "final_decision": "keep", "evaluated": True, "cer": 0.19, "parent_shortlist": []},
        {"iter": 2, "harness_family_id": "family_002", "chosen_mode": "refine",
         "final_decision": "reject", "evaluated": False, "cer": None,  # format reject
         "parent_shortlist": [{"hyp_id": "job_iter_001"}]},
        {"iter": 3, "harness_family_id": "family_001", "chosen_mode": "combine",
         "final_decision": "micro_bank", "evaluated": True, "cer": 0.188,
         "parent_shortlist": [{"hyp_id": "a"}, {"hyp_id": "b"}]},
    ]
    dec.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    section = portfolio_evolution_section(dec, tmp_path / "job_portfolio.json")

    assert "mode_attempt_distribution" in section
    assert "explore=1" in section and "refine=1" in section and "combine=1" in section
    # 성공률은 evaluated 만 분모 — refine(format reject, evaluated=False)은 제외.
    assert "mode_evaluated_success_rate" in section
    assert "combine_success_rate" in section  # evaluated combine 1개 micro_bank → 1/1
    assert "portfolio_usage" in section
