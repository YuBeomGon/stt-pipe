"""``harness.runner._persist_decision`` — Step 1 decision trace 배선.

candidate.diff + score_report.json 이 있는 iter dir 에서 decisions.jsonl 과
portfolio.json 이 만들어지고, reject 라도 축 개선이면 micro_bank 로 보존되는지 고정.
"""

from __future__ import annotations

import json
from pathlib import Path

from types import SimpleNamespace

from harness.runner import (
    RunnerConfig,
    _format_parents_block,
    _last_failure_parent,
    _persist_decision,
    _persist_verify_failure,
)


def _iter_dir(repo: Path, hyp_id: str, *, diff: str, report: dict) -> None:
    d = repo / "runs" / hyp_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.diff").write_text(diff, encoding="utf-8")
    (d / "score_report.json").write_text(json.dumps(report), encoding="utf-8")


_DIFF_DECODE = (
    "diff --git a/workspace/transcribe.py b/workspace/transcribe.py\n"
    "@@ -1,1 +1,2 @@ def transcribe(audio, sr):\n"
    "+    results = generate(features, beam_size=5, patience=2.0)\n"
)
_DIFF_AUDIO = (
    "diff --git a/workspace/transcribe.py b/workspace/transcribe.py\n"
    "@@ -1,1 +1,2 @@ def transcribe(audio, sr):\n"
    "+    audio = preemphasis(channel_eq(audio))\n"
)


def _report(cer, sub=0.3, dele=0.3, hall=0.1, rt=400.0):
    return {
        "corpus_cer": cer,
        "error_breakdown": {"sub_ratio": sub, "del_ratio": dele, "ins_ratio": 0.1},
        "hallucination_hit_rate": hall,
        "total_inference_time_s": rt,
    }


def test_decision_trace_and_portfolio_written(tmp_path: Path) -> None:
    cfg = RunnerConfig(job_id="phase3_006", repo_root=tmp_path)
    _iter_dir(tmp_path, "phase3_006_iter_001", diff=_DIFF_DECODE, report=_report(0.18))
    staged = _persist_decision(cfg, "phase3_006_iter_001", 1, "keep")

    dec = tmp_path / "runs/_summary/phase3_006_decisions.jsonl"
    port = tmp_path / "runs/_summary/phase3_006_portfolio.json"
    assert dec.is_file() and port.is_file()
    assert any("decisions.jsonl" in p for p in staged)

    line = json.loads(dec.read_text(encoding="utf-8").splitlines()[0])
    assert line["harness_family_id"].startswith("family_")
    assert line["harness_signature"].startswith("sig_")
    assert line["final_decision"] == "keep"
    assert line["cer"] == 0.18
    assert "global_best" in line["portfolio_slots_updated"]

    p = json.loads(port.read_text(encoding="utf-8"))
    assert p["global_best"] == "phase3_006_iter_001"


def test_family_numbering_stable_across_calls(tmp_path: Path) -> None:
    cfg = RunnerConfig(job_id="phase3_006", repo_root=tmp_path)
    # decode 계열 두 번(값만 다름) → 같은 family. audio 계열 → 새 family.
    _iter_dir(tmp_path, "phase3_006_iter_001", diff=_DIFF_DECODE, report=_report(0.18))
    _persist_decision(cfg, "phase3_006_iter_001", 1, "keep")
    _iter_dir(
        tmp_path,
        "phase3_006_iter_002",
        diff=_DIFF_DECODE.replace("beam_size=5", "beam_size=8"),
        report=_report(0.19),
    )
    _persist_decision(cfg, "phase3_006_iter_002", 2, "reject")
    _iter_dir(tmp_path, "phase3_006_iter_003", diff=_DIFF_AUDIO, report=_report(0.20))
    _persist_decision(cfg, "phase3_006_iter_003", 3, "reject")

    dec = tmp_path / "runs/_summary/phase3_006_decisions.jsonl"
    fams = [
        json.loads(l)["harness_family_id"]
        for l in dec.read_text(encoding="utf-8").splitlines()
    ]
    assert fams[0] == fams[1]            # decode 계열 동일 family
    assert fams[2] != fams[0]            # audio 는 새 family
    # 다양성 상한 추정치: distinct family 2개
    assert len(set(fams)) == 2


def test_reject_with_axis_gain_becomes_micro_bank(tmp_path: Path) -> None:
    cfg = RunnerConfig(job_id="phase3_006", repo_root=tmp_path)
    _iter_dir(
        tmp_path, "phase3_006_iter_001", diff=_DIFF_DECODE, report=_report(0.18, sub=0.30)
    )
    _persist_decision(cfg, "phase3_006_iter_001", 1, "keep")  # global best
    # CER 더 나쁘지만 sub 개선 → policy 는 reject, harness 는 micro_bank 보존.
    _iter_dir(
        tmp_path, "phase3_006_iter_002", diff=_DIFF_AUDIO, report=_report(0.20, sub=0.20)
    )
    _persist_decision(cfg, "phase3_006_iter_002", 2, "reject")

    dec = tmp_path / "runs/_summary/phase3_006_decisions.jsonl"
    line2 = json.loads(dec.read_text(encoding="utf-8").splitlines()[1])
    assert line2["final_decision"] == "micro_bank"

    port = json.loads(
        (tmp_path / "runs/_summary/phase3_006_portfolio.json").read_text(encoding="utf-8")
    )
    assert len(port["micro_bank"]) == 1
    assert port["global_best"] == "phase3_006_iter_001"  # keep 불변


def test_early_reject_preserves_reason_and_attempt_status(tmp_path: Path) -> None:
    """리뷰 #4: early reject 가 전부 'reject' 로 뭉개지지 않고 실제 원인을 보존.
    score_report 없는(평가 전) reject 는 reason 으로 attempt_status 가 분류된다."""
    cfg = RunnerConfig(job_id="phase3_006", repo_root=tmp_path)
    # iter dir 은 있으나 score_report 없음 (format reject 상황)
    d = tmp_path / "runs" / "phase3_006_iter_001"
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.diff").write_text("@@ @@\n+    x = 1\n", encoding="utf-8")
    _persist_decision(
        cfg, "phase3_006_iter_001", 1, "reject",
        reason="format reject: missing YAML block",
    )
    line = json.loads(
        (tmp_path / "runs/_summary/phase3_006_decisions.jsonl")
        .read_text(encoding="utf-8").splitlines()[0]
    )
    assert line["evaluated"] is False
    assert line["attempt_status"] == "format_reject"
    assert "missing YAML block" in line["decision_reason"]


def test_judge_crash_classified_as_verify_fail(tmp_path: Path) -> None:
    """후보 transcribe 가 첫 파일에서 크래시해 judge.evaluate 가 non-zero
    종료하면 reason='judge.evaluate 종료 코드 비정상' → verify_fail 로 분류.
    (이전엔 키워드 미스로 unknown 으로 떨어져 repair_event 가 안 잡혔다.)"""
    cfg = RunnerConfig(job_id="phase3_006", repo_root=tmp_path)
    d = tmp_path / "runs" / "phase3_006_iter_001"
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.diff").write_text("@@ @@\n+    x = 1\n", encoding="utf-8")
    _persist_decision(
        cfg, "phase3_006_iter_001", 1, "reject",
        reason="judge.evaluate 종료 코드 비정상",
    )
    line = json.loads(
        (tmp_path / "runs/_summary/phase3_006_decisions.jsonl")
        .read_text(encoding="utf-8").splitlines()[0]
    )
    assert line["evaluated"] is False
    assert line["attempt_status"] == "verify_fail"


def test_verify_failure_persisted_and_sourced_as_repair_parent(tmp_path: Path) -> None:
    """crash 한 iter 의 stderr 가 저장되고, 다음 repair 의 합성 parent 로 잡혀
    prompt 에 'REPAIR TARGET' + 실패 출력이 노출되는지 고정(stub crash 루프 탈출)."""
    cfg = RunnerConfig(job_id="phase3_007", repo_root=tmp_path)
    hyp = "phase3_007_iter_001"
    d = tmp_path / "runs" / hyp
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.diff").write_text(
        _DIFF_DECODE.replace("beam_size=5", "windows=batch(audio)"), encoding="utf-8"
    )
    vr = SimpleNamespace(
        stderr="Traceback (most recent call last):\nRuntimeError: shape mismatch\n",
        error="judge.evaluate 종료 코드 비정상",
    )
    _persist_verify_failure(tmp_path, cfg, hyp, vr)
    assert (d / "verify_stderr.txt").is_file()

    _persist_decision(cfg, hyp, 1, "reject", reason="judge.evaluate 종료 코드 비정상")

    parent = _last_failure_parent(cfg)
    assert parent is not None
    assert parent["is_repair_target"] is True
    assert parent["cer"] is None
    assert "shape mismatch" in parent["failure_stderr"]
    assert "windows=batch" in parent["diff"]

    block = _format_parents_block([parent])
    assert "REPAIR TARGET" in block
    assert "shape mismatch" in block
    assert "did NOT" in block  # "did NOT score" — explore 가 아니라 fix 지시


def test_last_failure_parent_none_when_last_evaluated(tmp_path: Path) -> None:
    cfg = RunnerConfig(job_id="phase3_007", repo_root=tmp_path)
    _iter_dir(tmp_path, "phase3_007_iter_001", diff=_DIFF_DECODE, report=_report(0.18))
    _persist_decision(cfg, "phase3_007_iter_001", 1, "keep")
    assert _last_failure_parent(cfg) is None


def test_attempt_status_evaluated_when_report_present(tmp_path: Path) -> None:
    cfg = RunnerConfig(job_id="phase3_006", repo_root=tmp_path)
    _iter_dir(tmp_path, "phase3_006_iter_001", diff=_DIFF_DECODE, report=_report(0.18))
    _persist_decision(cfg, "phase3_006_iter_001", 1, "keep")
    line = json.loads(
        (tmp_path / "runs/_summary/phase3_006_decisions.jsonl")
        .read_text(encoding="utf-8").splitlines()[0]
    )
    assert line["evaluated"] is True
    assert line["attempt_status"] == "evaluated"


def test_synthetic_abort_hyp_id_skipped(tmp_path: Path) -> None:
    cfg = RunnerConfig(job_id="phase3_006", repo_root=tmp_path)
    # 실제 iter dir 없음 (abort 합성 hyp) → 아무 것도 안 만든다.
    staged = _persist_decision(cfg, "command_failure", 5, "abort")
    assert staged == []
    assert not (tmp_path / "runs/_summary/phase3_006_decisions.jsonl").exists()
