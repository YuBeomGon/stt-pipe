"""
tests/test_harness_policy.py
Unit tests for Phase 3 harness keep/reject/success policy.
"""

from __future__ import annotations

from harness.policy import PolicyConfig, decide_candidate, improvement_threshold
from harness.state import HarnessState


def test_provisional_sigma_uses_absolute_fallback() -> None:
    threshold = improvement_threshold(
        sigma=0.0,
        is_provisional=True,
        config=PolicyConfig(absolute_delta_fallback=0.01),
    )
    assert threshold == 0.01


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


def test_decide_reject_when_improvement_below_threshold() -> None:
    decision = decide_candidate(
        report={"corpus_cer": 0.395, "total_inference_time_s": 90.0},
        baseline={"target_cer": 0.10, "total_inference_time_s": 100.0},
        best_cer=0.400,
        sigma=0.0,
        sigma_is_provisional=True,
    )
    assert decision.status == "reject"
    assert decision.threshold == 0.01


def test_state_round_trip(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = HarnessState(job_id="job", iteration=2, best_cer=0.39, best_hyp_id="iter_2")
    state.save(path)

    loaded = HarnessState.load(path)
    assert loaded == state

