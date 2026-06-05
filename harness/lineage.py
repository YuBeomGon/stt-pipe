"""harness/lineage.py
Pure bounded-set state machine (HARNESS-REDESIGN §94/§117).

A "set" cultivates one explore seed through a bounded repair/refine chain so a
worse-than-champion explore is not discarded after one shot (root cause C2). This
module is *pure* — no git, no I/O, no eval — so the whole control flow is
deterministically unit-testable without audio data. The runner (Phase 1 Task 6)
calls ``step_set`` and executes the returned ``action``.

Phases: explore → (repair | refine) → … → closed.
Actions: advance (keep code, continue) | repair (rollback to lineage, repair) |
         promote (beat champion, close+reseed) | reset (close, rollback to champion).
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SetBudget:
    max_repairs: int = 2
    max_refines: int = 3


@dataclass(frozen=True)
class Outcome:
    """One iteration's result, distilled for the state machine.

    lineage_status is the ``decide_lineage_progress`` verdict
    ("advance"|"hold"|"dead_end") when verify_ok, else None. beats_champion is the
    ``decide_promotion`` verdict (status in {keep, success}).
    """
    verify_ok: bool
    lineage_status: str | None
    cer: float | None
    beats_champion: bool
    hyp_id: str


@dataclass(frozen=True)
class SetState:
    set_id: int
    phase: str = "explore"          # explore|repair|refine|closed
    best_cer: float | None = None
    best_hyp_id: str | None = None
    repairs_used: int = 0
    refines_used: int = 0
    last_failure_hyp_id: str | None = None
    close_reason: str | None = None

    @classmethod
    def new(cls, set_id: int) -> "SetState":
        return cls(set_id=set_id, phase="explore")


@dataclass(frozen=True)
class Transition:
    state: SetState
    action: str   # advance | repair | promote | reset


def _promote(state: SetState) -> Transition:
    return Transition(replace(state, phase="closed", close_reason="promoted"),
                      "promote")


def _close(state: SetState, reason: str) -> Transition:
    return Transition(replace(state, phase="closed", close_reason=reason), "reset")


def _advance_to_refine(state: SetState, outcome: Outcome) -> Transition:
    return Transition(
        replace(state, phase="refine", best_cer=outcome.cer,
                best_hyp_id=outcome.hyp_id),
        "advance",
    )


def step_set(state: SetState, outcome: Outcome, budget: SetBudget) -> Transition:
    """Pure transition. Promotion always wins: if the candidate beats the global
    champion we promote and close (the next set reseeds from the higher
    champion), regardless of phase."""
    if outcome.verify_ok and outcome.beats_champion:
        return _promote(state)

    if state.phase == "explore":
        if not outcome.verify_ok:
            if budget.max_repairs <= 0:
                return _close(state, "explore_failed")
            return Transition(
                replace(state, phase="repair",
                        last_failure_hyp_id=outcome.hyp_id), "repair")
        if outcome.lineage_status == "dead_end":
            return _close(state, "dead_end")
        # advance / hold on the first scored candidate both seed the lineage.
        return _advance_to_refine(state, outcome)

    if state.phase == "repair":
        if not outcome.verify_ok:
            used = state.repairs_used + 1
            if used >= budget.max_repairs:
                return _close(replace(state, repairs_used=used),
                              "repair_exhausted")
            return Transition(
                replace(state, repairs_used=used,
                        last_failure_hyp_id=outcome.hyp_id), "repair")
        if outcome.lineage_status == "dead_end":
            return _close(replace(state, repairs_used=state.repairs_used + 1),
                          "dead_end")
        return _advance_to_refine(
            replace(state, repairs_used=state.repairs_used + 1), outcome)

    if state.phase == "refine":
        used = state.refines_used + 1
        if not outcome.verify_ok or outcome.lineage_status == "dead_end":
            # broken/catastrophic refine: roll back to lineage head, keep set
            # alive until budget so another refine can try a different edit.
            if used >= budget.max_refines:
                return _close(replace(state, refines_used=used), "refine_budget")
            return Transition(replace(state, refines_used=used), "repair")
        if outcome.lineage_status == "advance":
            nxt = replace(state, refines_used=used, best_cer=outcome.cer,
                          best_hyp_id=outcome.hyp_id)
            if used >= budget.max_refines:
                return _close(nxt, "refine_budget")
            return Transition(nxt, "advance")
        # hold: no local gain — roll back candidate, retry refine until budget.
        if used >= budget.max_refines:
            return _close(replace(state, refines_used=used), "refine_budget")
        return Transition(replace(state, refines_used=used), "repair")

    raise AssertionError(f"step_set called on terminal phase: {state.phase!r}")
