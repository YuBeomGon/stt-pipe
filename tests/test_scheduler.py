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


def test_crash_before_first_best_routes_to_repair_not_explore() -> None:
    """stub 에서 시작한 첫 후보가 평가기를 crash(verify_fail) 시키면, best 가 없어도
    repair 로 가야 한다. 예전엔 no_best 가 먼저 매칭돼 explore 로 빠졌고, parent/피드백
    없는 explore 가 같은 crash 를 매 iter 재발명했다(회귀 방지)."""
    d = sch.decide_mode(1, 50, _ctx(has_best=False, repair_event=True))
    assert d.chosen_mode == "repair"
    assert d.override == "repair_event"


def test_repair_feasible_without_best_when_repair_event() -> None:
    assert sch._feasible("repair", _ctx(has_best=False, repair_event=True)) is True
    # 고칠 실패도 best 도 없으면 repair 불가 (그냥 explore 로).
    assert sch._feasible("repair", _ctx(has_best=False, repair_event=False)) is False


def test_diversity_stall_forces_explore() -> None:
    d = sch.decide_mode(5, 50, _ctx(recent_new_family_count=0))
    assert d.chosen_mode == "explore"
    assert d.override == "diversity_stall"


def test_plateau_after_k_no_improvement() -> None:
    d = sch.decide_mode(20, 50, _ctx(iters_since_best=sch.PLATEAU_K))
    assert d.chosen_mode == "plateau"
    assert d.override == "plateau"


def test_plateau_is_periodic_burst_not_permanent() -> None:
    # 임계를 넘어도 매 iter plateau 가 아니라 PLATEAU_EVERY 주기로만 plateau.
    # 사이 iter 는 base schedule(scheduled mode)이 통과해야 한다(phase3_008 회귀 방지).
    modes = [
        sch.decide_mode(20, 50, _ctx(iters_since_best=k)).chosen_mode
        for k in range(sch.PLATEAU_K, sch.PLATEAU_K + sch.PLATEAU_EVERY * 2)
    ]
    assert "plateau" in modes              # 여전히 발동은 함
    assert any(m != "plateau" for m in modes)  # 영구 독점은 아님
    assert modes.count("plateau") == 2     # 2주기 동안 정확히 2번


def test_exploit_modes_survive_after_plateau_onset() -> None:
    """phase3_008 회귀 가드: best 가 고정된 채 evaluated_index 와 iters_since_best 가
    함께 진행해도, plateau 시작 이후 refine/combine/ablate 가 다시 나타나야 한다
    (영구 plateau 면 후반이 전부 plateau 로 붕괴했었다)."""
    modes = []
    for ev_idx in range(7, 40):       # best=6 고정 시나리오
        isb = ev_idx - 6
        modes.append(
            sch.decide_mode(ev_idx, 100, _ctx(iters_since_best=isb)).chosen_mode
        )
    late = modes[sch.PLATEAU_K:]      # plateau 가 발동하기 시작한 구간
    assert "plateau" in late
    # exploit/discovery 모드가 plateau 에 독점당하지 않고 살아남아야 한다.
    assert {"refine", "explore"} & set(late)
    assert any(m != "plateau" for m in late)


def test_mode_directives_match_scheduler_modes() -> None:
    """drift 가드: candidate 가 받는 mode 블록(_MODE_DIRECTIVES)이 scheduler 가 낼
    수 있는 mode 집합과 정확히 일치해야 한다. 한쪽만 늘면 런타임 프롬프트가 짜깁기
    된다(2026-06-01 회귀: harness 6-mode 인데 프롬프트 2-mode)."""
    from harness.runner import _MODE_DIRECTIVES

    emittable = set(sch._SCHEDULED_MODES) | {"repair", "plateau"}
    assert set(_MODE_DIRECTIVES) == emittable


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
