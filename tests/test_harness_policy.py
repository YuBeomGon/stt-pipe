"""
tests/test_harness_policy.py
Unit tests for Phase 3 harness keep/reject/success policy.
"""

from __future__ import annotations

import json
import math

import pytest

from harness.policy import PolicyConfig, decide_candidate, improvement_threshold
from harness.state import HarnessState


def test_provisional_sigma_uses_keep_eps() -> None:
    # 2026-06-04 R-A/F1: provisional σ(결정적 eval) 의 keep 임계는 banking floor 가
    # 아니라 keep_delta_eps(monotone). banking floor(absolute_delta_fallback)와 분리.
    threshold = improvement_threshold(
        sigma=0.0,
        is_provisional=True,
        config=PolicyConfig(absolute_delta_fallback=0.01, keep_delta_eps=0.0001),
    )
    assert threshold == 0.0001


def test_valid_sigma_uses_two_sigma() -> None:
    threshold = improvement_threshold(
        sigma=0.02,
        is_provisional=False,
        config=PolicyConfig(absolute_delta_fallback=0.01),
    )
    assert threshold == 0.04


def test_decide_success_when_target_and_runtime_met() -> None:
    decision = decide_candidate(
        report={"corpus_cer": 0.09, "total_inference_time_s": 90.0},
        baseline={"target_cer": 0.10, "total_inference_time_s": 100.0},
        best_cer=0.20,
        sigma=0.02,
    )
    assert decision.status == "success"


def test_decide_reject_only_below_keep_eps() -> None:
    # 2026-06-04 R-A/F1: keep 임계가 keep_delta_eps(0.0001)로 내려가, float-noise
    # 수준 Δ(0.00005)만 reject 된다.
    decision = decide_candidate(
        report={"corpus_cer": 0.39995, "total_inference_time_s": 90.0},
        baseline={"target_cer": 0.10, "total_inference_time_s": 100.0},
        best_cer=0.40000,
        sigma=0.0,
        sigma_is_provisional=True,
    )
    assert decision.status == "reject"
    assert decision.threshold == 0.0001


def test_monotone_best_keeps_sub_banking_improvement() -> None:
    """R-A/F1 핵심: banking floor(0.002) 미만이지만 *진짜* 개선(0.17746→0.17705,
    Δ0.00042)은 이제 KEEP 돼 best 가 전진한다. 예전엔 0.002 에 묶여 버려졌고(정체가
    일부 측정 artifact), 이 케이스가 phase3_012 정체의 원인이었다."""
    decision = decide_candidate(
        report={"corpus_cer": 0.17705, "total_inference_time_s": 90.0},
        baseline={"target_cer": 0.10, "total_inference_time_s": 100.0},
        best_cer=0.17746,
        sigma=0.0,
        sigma_is_provisional=True,
    )
    assert decision.status == "keep"
    assert decision.threshold == 0.0001


def test_keep_eps_separated_from_banking_floor() -> None:
    """keep 임계(keep_delta_eps)와 banking floor(absolute_delta_fallback)는 별개 knob.
    keep 는 작게(monotone), banking 은 0.002 유지(micro_bank 경계, is_micro_bank 사용)."""
    cfg = PolicyConfig()
    assert cfg.keep_delta_eps < cfg.absolute_delta_fallback
    assert cfg.absolute_delta_fallback == 0.002


def test_decide_rejects_non_finite_corpus_cer() -> None:
    decision = decide_candidate(
        report={"corpus_cer": math.nan, "total_inference_time_s": 90.0},
        baseline={"target_cer": 0.10, "total_inference_time_s": 100.0},
        best_cer=None,
        sigma=0.0,
        sigma_is_provisional=True,
    )
    assert decision.status == "reject"
    assert "non-finite" in decision.reason


def test_state_round_trip(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = HarnessState(job_id="job", iteration=2, best_cer=0.39, best_hyp_id="iter_2")
    state.save(path)

    loaded = HarnessState.load(path)
    assert loaded == state


def test_state_save_replaces_without_leaving_temp_file(tmp_path) -> None:
    path = tmp_path / "state.json"
    HarnessState(job_id="job", iteration=1, best_cer=0.42).save(path)
    HarnessState(job_id="job", iteration=2, best_cer=0.39).save(path)

    loaded = HarnessState.load(path)
    assert loaded.iteration == 2
    assert loaded.best_cer == 0.39
    assert not path.with_name("state.json.tmp").exists()


def test_state_load_raises_on_corrupt_json(tmp_path) -> None:
    """Corrupt state must fail loud rather than silently reset — otherwise a
    25-iter job resumes from iteration 0 and loses best_cer."""
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        HarnessState.load(path)


def test_state_load_raises_on_missing_required_field(tmp_path) -> None:
    """A JSON-valid state file without job_id is corrupt at the schema layer
    and must raise rather than silently fabricate an empty job."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"iteration": 5}), encoding="utf-8")
    with pytest.raises(TypeError):
        HarnessState.load(path)
