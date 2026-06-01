"""``harness.scheduler`` — 5+1 모드 deterministic scheduler (proposal §4).

기존 explore/exploit 2모드를 explore/refine/combine/ablate(+repair/plateau)로 확장.
base schedule 은 evaluated index 의 순수함수(resume-safe), override 는 precedence
표(codex fix)대로 적용. keep/reject 정책은 건드리지 않는다.
"""

from __future__ import annotations

from harness import scheduler as sch
from harness.scheduler import SchedulerContext as Ctx


def _ctx(**over):
    base = dict(
        has_best=True,
        feasible_refine=True,
        feasible_combine=True,
        feasible_ablate=True,
        iters_since_best=0,
        recent_new_family_count=3,
        repair_event=False,
    )
    base.update(over)
    return Ctx(**base)


# ── base schedule ────────────────────────────────────────────────────
def test_base_mode_is_deterministic() -> None:
    a = [sch.base_mode(i, 50) for i in range(1, 11)]
    b = [sch.base_mode(i, 50) for i in range(1, 11)]
    assert a == b


def test_base_mode_only_emits_scheduled_modes() -> None:
    seq = [sch.base_mode(i, 50) for i in range(1, 51)]
    assert set(seq) <= {"explore", "refine", "combine", "ablate"}


def test_early_phase_is_explore_heavy() -> None:
    early = [sch.base_mode(i, 50) for i in range(1, 11)]  # first 20%
    assert early.count("explore") >= 5  # ~60% target


def test_combine_capped_low_overall() -> None:
    seq = [sch.base_mode(i, 50) for i in range(1, 51)]
    # 첫 run combine ≤ ~15% (proposal §4.2)
    assert seq.count("combine") <= 9


# ── override precedence ──────────────────────────────────────────────
def test_no_best_forces_explore() -> None:
    d = sch.decide_mode(1, 50, _ctx(has_best=False))
    assert d.chosen_mode == "explore"
    assert d.override == "no_best"


def test_repair_event_highest_priority() -> None:
    d = sch.decide_mode(5, 50, _ctx(repair_event=True, iters_since_best=20))
    assert d.chosen_mode == "repair"
    assert d.override == "repair_event"


def test_diversity_stall_forces_explore() -> None:
    d = sch.decide_mode(5, 50, _ctx(recent_new_family_count=0))
    assert d.chosen_mode == "explore"
    assert d.override == "diversity_stall"


def test_plateau_after_k_no_improvement() -> None:
    d = sch.decide_mode(20, 50, _ctx(iters_since_best=sch.PLATEAU_K))
    assert d.chosen_mode == "plateau"
    assert d.override == "plateau"


def test_plateau_uses_evaluated_not_attempt_count() -> None:
    # iters_since_best 는 evaluated 기준 값이 들어와야 한다(#2). 7 < K → plateau 아님.
    d = sch.decide_mode(20, 50, _ctx(iters_since_best=sch.PLATEAU_K - 1))
    assert d.override != "plateau"


def test_stall_beats_plateau() -> None:
    # 둘 다 참이면 precedence 상 diversity_stall(3) > plateau(4)
    d = sch.decide_mode(20, 50, _ctx(recent_new_family_count=0, iters_since_best=99))
    assert d.chosen_mode == "explore"
    assert d.override == "diversity_stall"


def test_feasible_base_passes_through() -> None:
    # 평범한 상태: scheduled == chosen
    d = sch.decide_mode(25, 50, _ctx())
    assert d.chosen_mode == d.scheduled_mode
    assert d.override in (None, "scheduled")


def test_infeasible_combine_falls_back() -> None:
    # base 가 combine 인 index 를 찾아, compatible pair 없으면 fallback.
    idx = next(i for i in range(1, 51) if sch.base_mode(i, 50) == "combine")
    d = sch.decide_mode(
        idx, 50, _ctx(feasible_combine=False, recent_new_family_count=3, iters_since_best=0)
    )
    assert d.chosen_mode != "combine"
    assert d.override and d.override.startswith("infeasible")


def test_infeasible_falls_back_to_refine_then_explore() -> None:
    idx = next(i for i in range(1, 51) if sch.base_mode(i, 50) == "ablate")
    # refine 가능 → refine 로
    d1 = sch.decide_mode(idx, 50, _ctx(feasible_ablate=False, feasible_refine=True))
    assert d1.chosen_mode == "refine"
    # refine 도 불가 → explore
    d2 = sch.decide_mode(idx, 50, _ctx(feasible_ablate=False, feasible_refine=False))
    assert d2.chosen_mode == "explore"
