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


from harness.policy import LineageDecision, decide_lineage_progress


def _rep(cer: float) -> dict:
    return {"corpus_cer": cer, "total_inference_time_s": 90.0}


def test_first_lineage_candidate_seeds_even_if_worse_than_champion() -> None:
    # champion is 0.154; explore opens at 0.190. No lineage best yet → seed it.
    d = decide_lineage_progress(_rep(0.190), lineage_best_cer=None)
    assert d.status == "advance"
    assert d.lineage_best_cer is None


def test_lineage_improvement_advances() -> None:
    d = decide_lineage_progress(_rep(0.176), lineage_best_cer=0.190)
    assert d.status == "advance"
    assert d.delta is not None and d.delta > 0


def test_no_local_gain_holds() -> None:
    d = decide_lineage_progress(_rep(0.1901), lineage_best_cer=0.190)
    assert d.status == "hold"


def test_catastrophic_regression_is_dead_end() -> None:
    # 0.190 best, candidate 0.40 > 0.190 * 1.5 = 0.285 → dead end.
    d = decide_lineage_progress(_rep(0.40), lineage_best_cer=0.190)
    assert d.status == "dead_end"


def test_non_finite_is_dead_end() -> None:
    d = decide_lineage_progress(_rep(float("inf")), lineage_best_cer=0.190)
    assert d.status == "dead_end"
