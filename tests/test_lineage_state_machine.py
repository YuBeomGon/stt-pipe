"""tests/test_lineage_state_machine.py
Pure set state machine (HARNESS-REDESIGN §94/§117). No audio data needed —
deterministic transitions only.
"""

from __future__ import annotations

from harness.lineage import SetBudget, SetState, Outcome, step_set


BUDGET = SetBudget(max_repairs=2, max_refines=3)


def _ok(cer, lineage_status, beats_champion=False, hyp_id="h"):
    return Outcome(verify_ok=True, lineage_status=lineage_status, cer=cer,
                   beats_champion=beats_champion, hyp_id=hyp_id)


def _fail(hyp_id="h"):
    return Outcome(verify_ok=False, lineage_status=None, cer=None,
                   beats_champion=False, hyp_id=hyp_id)


def test_new_set_starts_in_explore() -> None:
    s = SetState.new(set_id=1)
    assert s.phase == "explore"


def test_explore_advance_goes_to_refine_and_keeps_code() -> None:
    s = SetState.new(set_id=1)
    t = step_set(s, _ok(0.190, "advance", hyp_id="h1"), BUDGET)
    assert t.action == "advance"
    assert t.state.phase == "refine"
    assert t.state.best_cer == 0.190
    assert t.state.best_hyp_id == "h1"


def test_explore_fail_goes_to_repair() -> None:
    s = SetState.new(set_id=1)
    t = step_set(s, _fail(hyp_id="h1"), BUDGET)
    assert t.action == "repair"
    assert t.state.phase == "repair"
    assert t.state.last_failure_hyp_id == "h1"


def test_explore_dead_end_closes_and_resets() -> None:
    s = SetState.new(set_id=1)
    t = step_set(s, _ok(0.40, "dead_end"), BUDGET)
    assert t.action == "reset"
    assert t.state.phase == "closed"
    assert t.state.close_reason == "dead_end"


def test_refine_improves_lineage_and_keeps_climbing() -> None:
    s = SetState(set_id=1, phase="refine", best_cer=0.190, best_hyp_id="h1")
    t = step_set(s, _ok(0.176, "advance", hyp_id="h2"), BUDGET)
    assert t.action == "advance"
    assert t.state.phase == "refine"
    assert t.state.best_cer == 0.176
    # F1: a strictly-advancing refine does NOT consume the refine budget, so a
    # monotonically-improving lineage is never cut mid-climb.
    assert t.state.refines_used == 0


def test_refine_budget_exhausted_closes() -> None:
    # F1: budget is now spent only on non-improving (hold) refines, so the
    # budget-exhaustion close is driven by a hold at the budget edge — NOT by an
    # advance (an advance never closes on budget).
    s = SetState(set_id=1, phase="refine", best_cer=0.176, refines_used=2)
    t = step_set(s, _ok(0.176, "hold", hyp_id="h5"), BUDGET)
    assert t.action == "reset"
    assert t.state.close_reason == "refine_budget"


def test_refine_no_gain_holds_until_budget() -> None:
    s = SetState(set_id=1, phase="refine", best_cer=0.176, refines_used=0)
    t = step_set(s, _ok(0.1761, "hold", hyp_id="h3"), BUDGET)
    assert t.action == "repair"  # hold → roll back candidate, retry refine
    assert t.state.phase == "refine"
    assert t.state.refines_used == 1
    assert t.state.best_cer == 0.176  # unchanged


def test_beats_champion_promotes_and_closes() -> None:
    s = SetState(set_id=1, phase="refine", best_cer=0.176, best_hyp_id="h2")
    t = step_set(s, _ok(0.150, "advance", beats_champion=True, hyp_id="h4"), BUDGET)
    assert t.action == "promote"
    assert t.state.phase == "closed"
    assert t.state.close_reason == "promoted"


def test_repair_fixes_then_refines() -> None:
    s = SetState(set_id=1, phase="repair", best_cer=None, repairs_used=0,
                 last_failure_hyp_id="h1")
    t = step_set(s, _ok(0.21, "advance", hyp_id="h2"), BUDGET)
    assert t.action == "advance"
    assert t.state.phase == "refine"
    assert t.state.best_cer == 0.21


def test_repair_exhausted_closes() -> None:
    s = SetState(set_id=1, phase="repair", repairs_used=1)  # this is the 2nd fail
    t = step_set(s, _fail(hyp_id="h3"), BUDGET)
    assert t.action == "reset"
    assert t.state.close_reason == "repair_exhausted"


def test_transition_is_deterministic() -> None:
    s = SetState.new(set_id=1)
    o = _ok(0.190, "advance", hyp_id="h1")
    assert step_set(s, o, BUDGET).state == step_set(s, o, BUDGET).state


def test_refine_strict_advance_does_not_consume_budget():
    from harness.lineage import SetState, Outcome, step_set, SetBudget
    budget = SetBudget(max_repairs=2, max_refines=3)
    s = SetState(set_id=1, phase="refine", best_cer=0.20,
                 best_hyp_id="seed", refines_used=2)   # one refine left under old rule
    # a strict lineage advance (lower cer) must NOT consume the refine budget and
    # must keep the set open to refine again.
    out = Outcome(verify_ok=True, lineage_status="advance", cer=0.18,
                  beats_champion=False, hyp_id="r3")
    t = step_set(s, out, budget)
    assert t.action == "advance"
    assert t.state.phase == "refine"          # set still open (not closed)
    assert t.state.refines_used == 2          # advance did NOT spend budget
    assert t.state.best_cer == 0.18


def test_refine_hold_still_consumes_budget_and_closes():
    from harness.lineage import SetState, Outcome, step_set, SetBudget
    budget = SetBudget(max_repairs=2, max_refines=3)
    s = SetState(set_id=1, phase="refine", best_cer=0.20,
                 best_hyp_id="seed", refines_used=2)
    # a hold (no local gain) spends the last budget unit → set closes.
    out = Outcome(verify_ok=True, lineage_status="hold", cer=0.20,
                  beats_champion=False, hyp_id="r3")
    t = step_set(s, out, budget)
    assert t.action == "reset"
    assert t.state.phase == "closed"
    assert t.state.refines_used == 3


def test_refine_monotonic_advances_run_past_old_budget_cap():
    """F1 termination/freedom guard: a stream of strictly-advancing refines runs
    well past the old max_refines cap without ever closing on budget, AND a
    lineage that stops improving (hold) still terminates (reset)."""
    from harness.lineage import SetState, Outcome, step_set, SetBudget
    budget = SetBudget(max_repairs=2, max_refines=3)
    s = SetState(set_id=1, phase="refine", best_cer=0.30, best_hyp_id="seed")
    cer = 0.30
    for i in range(10):                       # >> max_refines (3)
        cer -= 0.01
        out = Outcome(verify_ok=True, lineage_status="advance", cer=cer,
                      beats_champion=False, hyp_id=f"r{i}")
        t = step_set(s, out, budget)
        assert t.action == "advance"          # never cut while improving
        assert t.state.phase == "refine"
        assert t.state.refines_used == 0      # advances are free
        s = t.state
    # improvement stops: holds now spend budget and the set MUST terminate.
    for _ in range(budget.max_refines):
        out = Outcome(verify_ok=True, lineage_status="hold", cer=cer,
                      beats_champion=False, hyp_id="stall")
        t = step_set(s, out, budget)
        s = t.state
    assert t.action == "reset"
    assert t.state.phase == "closed"
    assert t.state.close_reason == "refine_budget"
