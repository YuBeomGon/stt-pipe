"""
harness/scheduler.py
Deterministic iteration-mode scheduler for the portfolio evolution loop
(proposal §4). Extends the legacy explore/exploit two-mode picker to
explore / refine / combine / ablate (+ repair, plateau).

설계 불변:
- `base_mode` 는 evaluated index 의 **순수함수** — outcome 과 무관한 고정 mode
  시퀀스라 resume/replay 에 동일(legacy `_is_explore_iter` 와 같은 error-diffusion
  철학을 multi-class 로 일반화).
- override 는 state/portfolio 에서 파생한 `SchedulerContext` 로만 결정 — 그 context
  는 디스크(state.json/decisions.jsonl/portfolio.json)에서 재구성되므로 역시 결정적.
- scheduler 는 "무엇을 *시도*할지"만 정한다. keep/reject(채택)는 여전히
  `policy.decide_candidate` 가 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

# base schedule 이 분배하는 모드(repair=이벤트, plateau=override 전용은 제외).
_SCHEDULED_MODES: tuple[str, ...] = ("explore", "refine", "combine", "ablate")
# tie-break 고정 순서(같은 deficit 이면 앞선 것 우선) — 재현성.
_TIE_ORDER: tuple[str, ...] = ("explore", "refine", "combine", "ablate")

# 구간별 가중치(progress = evaluated_index/total 의 상한). proposal §4.2.
# 첫 MVP run 은 combine ≤15%(초반 10%). repair 는 quota 가 아니라 이벤트.
_PHASES: tuple[tuple[float, dict[str, float]], ...] = (
    (0.20, {"explore": 0.60, "refine": 0.20, "combine": 0.10, "ablate": 0.10}),
    (0.70, {"explore": 0.35, "refine": 0.35, "combine": 0.15, "ablate": 0.15}),
    (1.01, {"explore": 0.20, "refine": 0.40, "combine": 0.15, "ablate": 0.25}),
)

# no-improvement 이 이 횟수(evaluated 기준) 이상이면 plateau (proposal §4.3).
PLATEAU_K: int = 8


@dataclass(frozen=True)
class SchedulerContext:
    """override 판정에 필요한, state/portfolio 에서 파생한 결정적 입력."""
    has_best: bool
    feasible_refine: bool
    feasible_combine: bool
    feasible_ablate: bool
    iters_since_best: int
    recent_new_family_count: int  # 최근 window 의 신규 harness family 수
    repair_event: bool            # guard fail / hallucination / runtime 폭증 등


@dataclass(frozen=True)
class SchedulerDecision:
    scheduled_mode: str   # base schedule 이 정한 mode (override 전)
    chosen_mode: str      # override 적용 후 실제 실행 mode
    override: str | None  # 적용된 override 이름(없으면 None / "scheduled")


def _phase_weights(progress: float) -> dict[str, float]:
    for upper, weights in _PHASES:
        if progress < upper:
            return weights
    return _PHASES[-1][1]


def base_mode(evaluated_index: int, total: int) -> str:
    """evaluated index(1-based)에서의 base mode. multi-class error-diffusion
    accumulator 를 1..index 까지 재생(replay)해 각 mode 를 목표 밀도로 고르게
    배치한다. outcome 무관·RNG 없음 → resume 안전."""
    acc = {m: 0.0 for m in _SCHEDULED_MODES}
    chosen = _SCHEDULED_MODES[0]
    n = max(1, total)
    for i in range(1, max(1, evaluated_index) + 1):
        w = _phase_weights(i / n)
        for m in _SCHEDULED_MODES:
            acc[m] += w.get(m, 0.0)
        # deficit(누적 목표) 최대 mode 선택, 동률은 _TIE_ORDER 앞선 것.
        best, best_v = _TIE_ORDER[0], float("-inf")
        for m in _TIE_ORDER:
            if acc[m] > best_v:
                best_v = acc[m]
                best = m
        chosen = best
        acc[chosen] -= 1.0
    return chosen


def _feasible(mode: str, ctx: SchedulerContext) -> bool:
    if mode == "explore" or mode == "plateau":
        return True
    if mode == "repair":
        return ctx.has_best
    if mode == "refine":
        return ctx.feasible_refine
    if mode == "combine":
        return ctx.feasible_combine
    if mode == "ablate":
        return ctx.feasible_ablate
    return True


def _fallback(ctx: SchedulerContext) -> str:
    """infeasible base 의 대체: refine 가능하면 refine, 아니면 explore."""
    return "refine" if ctx.feasible_refine else "explore"


def decide_mode(evaluated_index: int, total: int, ctx: SchedulerContext) -> SchedulerDecision:
    """base schedule + override precedence(codex fix 표). 첫 매칭 우선:
    1 no_best → explore
    2 repair_event → repair
    3 diversity_stall(recent_new_family==0) → explore
    4 plateau(iters_since_best>=K) → plateau
    5 base feasible → base
    6 else → feasible fallback
    """
    scheduled = base_mode(evaluated_index, total)

    if not ctx.has_best:
        return SchedulerDecision(scheduled, "explore", "no_best")
    if ctx.repair_event:
        return SchedulerDecision(scheduled, "repair", "repair_event")
    if ctx.recent_new_family_count == 0:
        return SchedulerDecision(scheduled, "explore", "diversity_stall")
    if ctx.iters_since_best >= PLATEAU_K:
        return SchedulerDecision(scheduled, "plateau", "plateau")
    if _feasible(scheduled, ctx):
        return SchedulerDecision(scheduled, scheduled, "scheduled")
    fb = _fallback(ctx)
    return SchedulerDecision(scheduled, fb, f"infeasible:{scheduled}->{fb}")
