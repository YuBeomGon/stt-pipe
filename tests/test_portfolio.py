"""``harness.portfolio`` — 진화 재료 적재 (proposal §3/§8).

keep/reject 판정은 미러링만 하고, micro_bank/축개선 보존 로직을 고정한다.
"""

from __future__ import annotations

from harness import portfolio as pf
from harness.portfolio import Portfolio


def _report(cer, sub=0.3, dele=0.3, hall=0.1, rt=400.0):
    return {
        "corpus_cer": cer,
        "error_breakdown": {"sub_ratio": sub, "del_ratio": dele, "ins_ratio": 0.1},
        "hallucination_hit_rate": hall,
        "total_inference_time_s": rt,
    }


# ── axis_value ───────────────────────────────────────────────────────
def test_axis_value_dotted_and_missing() -> None:
    r = _report(0.2)
    assert pf.axis_value(r, "corpus_cer") == 0.2
    assert pf.axis_value(r, "error_breakdown.sub_ratio") == 0.3
    assert pf.axis_value(r, "nope.nope") is None


# ── is_micro_bank ────────────────────────────────────────────────────
def test_micro_bank_strict_cer_below_threshold() -> None:
    best = _report(0.2000)
    cand = _report(0.1997)  # Δ0.0003, keep threshold 0.002 미만
    ok, _ = pf.is_micro_bank(cand, best, keep_threshold=0.002)
    assert ok


def test_micro_bank_axis_improvement_even_if_cer_worse() -> None:
    best = _report(0.20, sub=0.30)
    cand = _report(0.21, sub=0.25)  # CER 나쁘지만 sub 개선
    ok, reason = pf.is_micro_bank(cand, best, keep_threshold=0.002)
    assert ok
    assert "sub_ratio" in reason


def test_not_micro_bank_when_nothing_improves() -> None:
    best = _report(0.20, sub=0.30, dele=0.30, hall=0.1, rt=400)
    cand = _report(0.25, sub=0.40, dele=0.40, hall=0.2, rt=500)
    ok, _ = pf.is_micro_bank(cand, best, keep_threshold=0.002)
    assert not ok


def test_no_best_yet_is_not_micro_bank() -> None:
    ok, _ = pf.is_micro_bank(_report(0.2), None, keep_threshold=0.002)
    assert not ok


# ── Portfolio.update ─────────────────────────────────────────────────
def _kw(**over):
    base = dict(
        hyp_id="phase3_006_iter_001",
        iteration=1,
        decision_status="keep",
        report=_report(0.18),
        best_report=None,
        harness_signature="sig_a",
        harness_family_id="family_001",
    )
    base.update(over)
    return base


def test_update_keep_sets_global_and_family_and_metric() -> None:
    p = Portfolio(job_id="phase3_006")
    updated = p.update(**_kw())
    assert p.global_best == "phase3_006_iter_001"
    assert "family_001" in p.family_best
    assert "global_best" in updated
    assert any(s.startswith("metric_best:") for s in updated)


def test_update_micro_bank_appends_material_not_global() -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw())  # keep → global
    updated = p.update(
        **_kw(
            hyp_id="phase3_006_iter_002",
            iteration=2,
            decision_status="micro_bank",
            report=_report(0.181),
            harness_family_id="family_002",
        )
    )
    assert "micro_bank" in updated
    assert len(p.micro_bank) == 1
    assert p.global_best == "phase3_006_iter_001"  # 안 바뀜


def test_update_reject_with_axis_gain_goes_to_promising() -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw(report=_report(0.18, sub=0.30)))
    best = _report(0.18, sub=0.30)
    updated = p.update(
        **_kw(
            hyp_id="phase3_006_iter_003",
            iteration=3,
            decision_status="reject",
            report=_report(0.20, sub=0.20),  # CER 나쁘지만 sub 개선
            best_report=best,
        )
    )
    assert "rejected_promising" in updated
    assert p.rejected_promising[0]["improved_axes"] == ["best_substitution"]


def test_update_reject_plain_changes_nothing() -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw())
    best = _report(0.18)
    updated = p.update(
        **_kw(
            hyp_id="phase3_006_iter_004",
            iteration=4,
            decision_status="reject",
            report=_report(0.30, sub=0.5, dele=0.5, hall=0.3, rt=900),
            best_report=best,
        )
    )
    assert updated == []


# ── persistence ──────────────────────────────────────────────────────
def test_save_load_roundtrip(tmp_path) -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw())
    path = tmp_path / "phase3_006_portfolio.json"
    p.save(path)
    q = Portfolio.load(path)
    q.job_id = "phase3_006"
    assert q.global_best == p.global_best
    assert q.family_best.keys() == p.family_best.keys()


def test_load_missing_file() -> None:
    from pathlib import Path

    q = Portfolio.load(Path("/nonexistent/phase3_006_portfolio.json"))
    assert q.global_best is None


# ── parent selection (Step 2) ────────────────────────────────────────
def _populate(p: Portfolio, hyp, it, fam, cer):
    p.update(
        hyp_id=hyp, iteration=it, decision_status="keep",
        report=_report(cer), best_report=None,
        harness_signature=f"sig_{fam}", harness_family_id=fam,
    )


def test_feasibility_empty_portfolio() -> None:
    p = Portfolio(job_id="j")
    f = pf.feasibility(p)
    assert f == {"refine": False, "combine": False, "ablate": False}


def test_feasibility_one_family_no_combine() -> None:
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.18)
    f = pf.feasibility(p)
    assert f["refine"] and f["ablate"] and not f["combine"]


def test_feasibility_two_families_enables_combine() -> None:
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.18)
    _populate(p, "j_iter_002", 2, "family_002", 0.19)
    assert pf.feasibility(p)["combine"]


def test_parents_refine_returns_global_best() -> None:
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.18)
    parents = pf.parents_for_mode(p, "refine")
    assert len(parents) == 1 and parents[0]["hyp_id"] == "j_iter_001"


def test_parents_combine_two_distinct_families() -> None:
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.18)
    _populate(p, "j_iter_002", 2, "family_002", 0.19)
    parents = pf.parents_for_mode(p, "combine")
    assert len(parents) == 2
    assert {e["harness_family_id"] for e in parents} == {"family_001", "family_002"}


def test_parents_explore_and_repair_empty() -> None:
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.18)
    assert pf.parents_for_mode(p, "explore") == []
    assert pf.parents_for_mode(p, "repair") == []


# ── near-best pool (global best × factor 근방 보존) ───────────────────
def _reject(p, hyp, it, fam, cer, best):
    """best 를 못 깨는 reject 후보를 portfolio 에 흘려보낸다."""
    return p.update(
        hyp_id=hyp, iteration=it, decision_status="reject",
        report=_report(cer), best_report=best,
        harness_signature=f"sig_{fam}", harness_family_id=fam,
    )


def test_near_best_retains_reject_within_factor() -> None:
    # best 0.10, factor 1.20 → 0.12 까지 보존. 0.115 reject 은 best 를 못 깼지만 근방.
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.10)
    updated = _reject(p, "j_iter_002", 2, "family_002", 0.115, _report(0.10))
    assert "near_best" in updated
    assert any(e["hyp_id"] == "j_iter_002" for e in p.near_best)


def test_near_best_excludes_reject_outside_factor() -> None:
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.10)
    updated = _reject(p, "j_iter_002", 2, "family_002", 0.13, _report(0.10))  # >0.12
    assert "near_best" not in updated
    assert not any(e["hyp_id"] == "j_iter_002" for e in p.near_best)


def test_near_best_prunes_when_global_best_improves() -> None:
    # 0.115 는 best 0.10 근방이라 보존됐다가, best 가 0.09(→0.108)로 내려가면 탈락.
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.10)
    _reject(p, "j_iter_002", 2, "family_002", 0.115, _report(0.10))
    assert any(e["hyp_id"] == "j_iter_002" for e in p.near_best)
    _populate(p, "j_iter_003", 3, "family_003", 0.09)  # 새 best → threshold 0.108
    assert not any(e["hyp_id"] == "j_iter_002" for e in p.near_best)


def test_parents_refine_rotates_over_pool() -> None:
    # 풀에 서로 다른 family 근방 후보가 여럿이면 refine 은 evaluated_index 로 회전한다
    # (항상 global_best 만 고르지 않음 — 다양성).
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.10)  # global best
    _reject(p, "j_iter_002", 2, "family_002", 0.11, _report(0.10))
    _reject(p, "j_iter_003", 3, "family_003", 0.115, _report(0.10))
    picks = {
        pf.parents_for_mode(p, "refine", evaluated_index=i)[0]["hyp_id"]
        for i in range(3)
    }
    assert len(picks) >= 2  # 회전으로 두 개 이상의 서로 다른 parent 가 선택됨


def test_parents_combine_draws_from_near_best_pool() -> None:
    # combine 은 근방 풀의 서로 다른 family 2개를 재료로 쓴다(reject 도 포함).
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.10)
    _reject(p, "j_iter_002", 2, "family_002", 0.11, _report(0.10))
    parents = pf.parents_for_mode(p, "combine")
    assert {e["harness_family_id"] for e in parents} == {"family_001", "family_002"}


def test_parents_refine_single_entry_is_global_best() -> None:
    # 풀이 1개뿐이면 회전해도 global_best.
    p = Portfolio(job_id="j")
    _populate(p, "j_iter_001", 1, "family_001", 0.18)
    for i in range(3):
        got = pf.parents_for_mode(p, "refine", evaluated_index=i)
        assert len(got) == 1 and got[0]["hyp_id"] == "j_iter_001"
