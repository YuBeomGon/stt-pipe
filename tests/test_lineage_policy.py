"""tests/test_lineage_policy.py
Two-comparison policy (HARNESS-REDESIGN §74): promotion (vs champion) and
lineage progress (within a set, vs lineage best).
"""

from __future__ import annotations

from harness.policy import decide_candidate, decide_promotion


def test_decide_promotion_matches_decide_candidate() -> None:
    args = dict(
        report={"corpus_cer": 0.15, "total_inference_time_s": 90.0},
        baseline={"target_cer": 0.05, "total_inference_time_s": 100.0},
        champion_cer=0.16,
        sigma=0.0,
        sigma_is_provisional=True,
    )
    promo = decide_promotion(**args)
    legacy = decide_candidate(
        report=args["report"], baseline=args["baseline"],
        best_cer=args["champion_cer"], sigma=args["sigma"],
        sigma_is_provisional=args["sigma_is_provisional"],
    )
    assert promo.status == legacy.status == "keep"
    assert promo.candidate_cer == legacy.candidate_cer
