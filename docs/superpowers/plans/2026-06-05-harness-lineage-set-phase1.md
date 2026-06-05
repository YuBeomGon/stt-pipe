# Harness Lineage-Set Refactor — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an `explore` candidate that is *worse than champion* survive and be cultivated by a bounded `repair`/`refine` set, instead of being rolled back and lost after one shot (root cause C2).

**Architecture:** Split the single champion-relative comparison into **two** comparisons — `decide_lineage_progress` (advance the in-set lineage) vs `decide_promotion` (beat the global champion) — and drive an `explore → repair/refine` **bounded set** with a pure state machine. The on-disk `transcribe.py` becomes the **lineage head** during a set (HEAD on the job branch), while a separate `champion` git ref holds the last promoted code. No git worktree yet (Phase 2); single lane only. Run-metadata moves out of git into append-only files.

**Tech Stack:** Python 3.11 (conda env `evolve`), pytest, git (cli via `subprocess`), existing `harness/` package.

**Scope boundary (what Phase 1 is NOT):** no `git worktree`, no parallel/multi-job lanes, no branch protection / CI markers / `git archive` packaging, no archive/islands. Those are Phase 2–4 (see `docs/HARNESS-REDESIGN.md` §172 migration timeline). Phase 1 = state separation + two comparison functions + bounded-set state machine + metadata-out-of-git, all behind an opt-in flag so the legacy single-shot path is preserved until the set path is proven.

---

## Review-driven revisions (2026-06-05)

This plan was reviewed against the real code (`docs/reviews/2026-06-05-lineage-set-phase1-plan-review.md`). Three decisions resolve the review's Critical/Important findings and are now baked into the tasks below:

1. **In-set advances must NOT pollute the portfolio's `global_best`** (review C-2/I-3). `commit_iteration(status="keep")` flows into `Portfolio.update`, which sets `global_best=hyp_id` for any keep/success (`portfolio.py:289-291`). A worse-than-champion explore (0.190) kept as a lineage head would thus hijack `global_best`, corrupting refine-parent and near-best selection — defeating the entire C2 fix. **Decision:** introduce a distinct status **`lineage_advance`** for in-set code checkpoints. It commits the code (lineage head moves) but `Portfolio.update` treats it as non-valid → it touches **no** portfolio pool. Only a *promotion* (beats champion) uses `status="keep"` and legitimately updates `global_best`. This is the *minimal* form of the §209 `job_best`/`global_best` split — we do **not** pull the full portfolio split forward (that stays Phase 2). (Task 4B + Task 8.)

2. **When a set is active, the set phase drives the prompt mode** (review I-1, Open Q1 → option A). Today `_decide_iteration` lets the scheduler pick the mode independently, so a set in `refine` could be handed an `explore` prompt (scheduler `diversity_stall`/`discovery_phase` override), and a set in `repair` would not get `_last_failure_parent` injected (that helper is `chosen_mode=="repair"`-only). **Decision:** when `set_budget>1` and `state.set_phase ∈ {explore,repair,refine}`, `_decide_iteration` **overrides** the scheduler's `chosen_mode` with the set phase and selects parents accordingly (repair → `_last_failure_parent`; refine → none needed, the on-disk file *is* the lineage head; explore → none). Scheduler overrides are ignored while a set is active. (Task 8.)

3. **`metadata-off-git` must fix the one test it breaks** (review C-1). `test_step1_decision_trace_committed_and_survives_next_iter` (`tests/test_harness_runner.py:431-432`) asserts the decisions/portfolio files are *git-tracked*; after this change they are durable-on-disk but untracked. Convert those asserts to disk-existence, and fix `_init_repo`'s own `.gitignore` (line 76-79) to match production (`runs/`). (Task 7.)

Minor review notes (M-1…M-6) are folded into the relevant task steps.

**Source-of-truth docs (cite these, NOT `docs/archive/`):**
- `docs/HARNESS-REDESIGN.md` — the blueprint (§50 lanes, §74 two comparison fns, §84–90 set scenario, §117 simulator, §195 change-points table).
- `docs/HARNESS-MECHANICS.md` — how the code works today.
- `docs/STT-PIPELINE-SPEC.md` — problem/metric/prohibitions (unchanged by this refactor).

---

## Background: the current single-shot path (what we are changing)

Read these before starting (line numbers as of base `fc3474e`):

- `harness/runner.py:1820` `run_iteration` — the loop body. On `keep`/`success` the candidate code stays on disk and is committed; on `reject` it calls `rollback_paths(... candidate_owned_statuses ...)` (line 2097) which `git restore`s `workspace/transcribe.py` back to HEAD = champion. **This rollback is exactly what kills a worse-than-champion explore.**
- `harness/runner.py:2083` `decide_candidate(...)` — the single comparison, `best_cer=state.best_cer`. keep iff `Δ = best_cer − cand >= keep_delta_eps`.
- `harness/policy.py:56` `decide_candidate` — returns `Decision(status, ...)`.
- `harness/state.py:14` `HarnessState` — `best_cer`, `best_hyp_id`, `evaluated_*` counters. No set/lineage fields.
- `harness/runner.py:1774` `commit_iteration` — `git add`s `transcribe.py` + `HISTORY.md` + decisions/meta/state and commits every iter. **Source of the 720-commit churn + races we are removing.**
- `harness/scheduler.py:126` `decide_mode` — picks the mode (explore/refine/…). Stays as-is in Phase 1; the **set** layer sits *above* it (the set decides phase; within a phase the existing mode/parent selection is reused).

---

## File Structure

| File | Responsibility | Phase 1 change |
|------|----------------|----------------|
| `harness/config.py` | tunable constants (SSOT) | **add** set-budget + lineage constants |
| `harness/policy.py` | keep/reject decisions | **add** `LineageDecision`, `decide_lineage_progress`, `decide_promotion` (alias), `PolicyConfig.lineage_dead_end_factor` |
| `harness/lineage.py` | **NEW** — pure set state machine | new file |
| `harness/portfolio.py` | evolution-material pools | **add** `lineage_advance` status → pool-inert (C-2) |
| `harness/state.py` | serializable harness state | **add** set/lineage fields (+ champion ref) |
| `harness/gitops.py` | **NEW** — git ref helpers (champion ref, restore-from-ref) | new file |
| `harness/runner.py` | iteration loop | wire set path behind flag; split commit (code vs metadata) |
| `scripts/evolve.py` → `harness/runner.main` | CLI | **add** `--set-budget`, `--max-repairs`, `--max-refines` |
| `.gitignore` | tracked-path policy | stop tracking metadata files |
| `tests/test_lineage_state_machine.py` | **NEW** | pure state-machine tests |
| `tests/test_lineage_policy.py` | **NEW** | two-comparison tests |
| `tests/test_gitops.py` | **NEW** | champion-ref helper tests |
| `tests/test_harness_runner.py` | existing | add set-path integration test |

The pure modules (`lineage.py`, the two policy functions, the state fields) are built and fully unit-tested **first** (Tasks 1–5), with **no behavior change** to live runs. The runner is wired **last** (Tasks 6–8) behind `--set-budget`, default `1` = legacy single-shot. This keeps each task self-contained and reversible.

---

## Task 1: Config constants for the set

**Files:**
- Modify: `harness/config.py` (append a new section near line 51, after the explore/exploit block)
- Test: `tests/test_lineage_policy.py` (created in Task 3 — no test here, constants are data)

- [ ] **Step 1: Add the constants**

Append to `harness/config.py`:

```python
# ── Lineage set (HARNESS-REDESIGN §84–90, Phase 1) ──────────────────
# A "set" cultivates one explore seed through a bounded repair/refine chain
# instead of discarding a worse-than-champion explore after one shot (C2).
# Budget keeps cost bounded (redesign §90: explore 1 + repair/refine 2~3).
SET_MAX_REPAIRS: int = 2          # verify_fail fixes allowed per set
SET_MAX_REFINES: int = 3          # local-refine steps allowed per set
# Within-set "is this worth cultivating" gate. A candidate whose CER is worse
# than the current lineage best by more than this factor is a dead end (close
# the set) rather than something refine could rescue. Looser than promotion so
# a 0.190 explore (champion 0.154) is NOT a dead end and gets refined.
LINEAGE_DEAD_END_FACTOR: float = 1.50
```

- [ ] **Step 2: Verify import**

Run: `python -c "from harness import config as c; print(c.SET_MAX_REPAIRS, c.SET_MAX_REFINES, c.LINEAGE_DEAD_END_FACTOR)"`
Expected: `2 3 1.5`

- [ ] **Step 3: Commit**

```bash
git add harness/config.py
git commit -m "feat(config): add lineage-set budget + dead-end constants (phase1)"
```

---

## Task 2: `decide_promotion` alias (rename clarity, zero behavior change)

`decide_promotion` is **semantically identical** to today's `decide_candidate` (does this beat the global champion?). We add it as a named alias so the runner and tests read clearly, and keep `decide_candidate` so existing tests/imports keep working.

**Files:**
- Modify: `harness/policy.py` (after `decide_candidate`, ~line 123)
- Test: `tests/test_lineage_policy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lineage_policy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lineage_policy.py::test_decide_promotion_matches_decide_candidate -v`
Expected: FAIL with `ImportError: cannot import name 'decide_promotion'`

- [ ] **Step 3: Add the alias**

Append to `harness/policy.py`:

```python
def decide_promotion(
    report: dict[str, Any],
    baseline: dict[str, Any],
    champion_cer: float | None,
    sigma: float | None,
    sigma_is_provisional: bool = False,
    config: PolicyConfig | None = None,
) -> Decision:
    """Promotion gate: does this candidate beat the global champion?

    Identical semantics to the legacy ``decide_candidate`` — the global champion
    *is* the historical "best_cer". Named separately (HARNESS-REDESIGN §74) so the
    runner can pair it with ``decide_lineage_progress`` (the in-set comparison)
    and the two roles read distinctly.
    """
    return decide_candidate(
        report=report,
        baseline=baseline,
        best_cer=champion_cer,
        sigma=sigma,
        sigma_is_provisional=sigma_is_provisional,
        config=config,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_lineage_policy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/policy.py tests/test_lineage_policy.py
git commit -m "feat(policy): add decide_promotion alias for champion gate (phase1)"
```

---

## Task 3: `decide_lineage_progress` (the in-set comparison)

This is the new comparison that makes a set possible: it compares the candidate to the **lineage best** (not the champion), with a looser gate, returning `advance` / `hold` / `dead_end`.

**Files:**
- Modify: `harness/policy.py`
- Test: `tests/test_lineage_policy.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lineage_policy.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_lineage_policy.py -v`
Expected: FAIL with `ImportError: cannot import name 'LineageDecision'`

- [ ] **Step 3: Implement**

First add a `lineage_dead_end_factor` field to `PolicyConfig` (so the factor lives in config alongside `keep_delta_eps`, and tests can override it via the dataclass instead of an import-time-bound default — review M-6). In `harness/policy.py` `PolicyConfig` (after `keep_delta_eps`, ~line 29):

```python
    # In-set "worth cultivating" gate: a candidate worse than the lineage best
    # by more than this factor is a dead end (close the set). SSOT: config.py.
    lineage_dead_end_factor: float = cfg.LINEAGE_DEAD_END_FACTOR
```

Then add the dataclass + function near `Decision`:

```python
LineageStatus = Literal["advance", "hold", "dead_end"]


@dataclass(frozen=True)
class LineageDecision:
    status: LineageStatus
    candidate_cer: float
    lineage_best_cer: float | None
    delta: float | None
    reason: str


def decide_lineage_progress(
    report: dict[str, Any],
    lineage_best_cer: float | None,
    config: PolicyConfig | None = None,
) -> LineageDecision:
    """In-set comparison (HARNESS-REDESIGN §86, §136): is this candidate worth
    keeping as / advancing the lineage head?

    - No lineage best yet (first scored candidate in the set) → ``advance`` and
      seed the lineage, *even if it is worse than the global champion*. This is
      the whole point: a 0.190 explore (champion 0.154) survives to be refined.
    - Catastrophic (non-finite, or worse than lineage best by
      ``config.lineage_dead_end_factor``) → ``dead_end``; close the set.
    - Strict improvement over lineage best (Δ ≥ keep_delta_eps) → ``advance``.
    - Otherwise → ``hold`` (no local gain; refine may try again until budget).
    """
    cfg_ = config or PolicyConfig()
    cer = float(report["corpus_cer"])
    if not math.isfinite(cer):
        return LineageDecision("dead_end", cer, lineage_best_cer, None,
                               f"non-finite corpus_cer: {cer!r}")
    if lineage_best_cer is None:
        return LineageDecision("advance", cer, None, None,
                               "set seed (first lineage candidate)")
    delta = lineage_best_cer - cer
    factor = cfg_.lineage_dead_end_factor
    if cer > lineage_best_cer * factor:
        return LineageDecision(
            "dead_end", cer, lineage_best_cer, delta,
            f"catastrophic: {cer:.6f} > {lineage_best_cer:.6f}×{factor}",
        )
    if delta >= cfg_.keep_delta_eps:
        return LineageDecision("advance", cer, lineage_best_cer, delta,
                               f"lineage improvement Δ{delta:.6f}")
    return LineageDecision("hold", cer, lineage_best_cer, delta,
                           f"no lineage gain Δ{delta:.6f}")
```

Notes:
- `cfg` (`from harness import config as cfg`, line 12), `Any` + `Literal` (`from typing import Any, Literal`, line 10), and `math` (line 8) are **already imported** in `policy.py` — no import edits needed (review M-1).
- **I-5 (known limitation, leave as-is in Phase 1):** the factor is relative to `lineage_best`, so late in a set with a very low `lineage_best` (e.g. 0.05) a near-target 0.08 candidate trips `0.08 > 0.05×1.5` and is marked `dead_end`. This is benign in Phase 1 (a champion-beating candidate is caught first by `decide_promotion`/`beats_champion`, which always wins in `step_set`), but record it as an Open Question for a later absolute-margin floor (`max(best×factor, best+abs_margin)`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_lineage_policy.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/policy.py tests/test_lineage_policy.py
git commit -m "feat(policy): add decide_lineage_progress in-set comparison (phase1)"
```

---

## Task 4: Pure set state machine (`harness/lineage.py`)

This is the heart of Phase 1 and the part the redesign says is **fully testable without audio data** (§113, §117). It is a pure transition function: given the current set state and an iteration outcome, return the next state + the action the runner must take.

**Files:**
- Create: `harness/lineage.py`
- Test: `tests/test_lineage_state_machine.py`

**Design (mirrors HARNESS-REDESIGN §94 state diagram + §117 simulator):**

Phases: `explore → (repair | refine) → … → closed`. Actions the runner executes:
- `advance` — candidate verified and advances the lineage: **keep it on disk** as the new lineage head (code checkpoint), continue the set.
- `repair` — candidate failed verify: **roll back to lineage head**, next iter is `repair` (failure artifact fed via existing prompt path).
- `promote` — candidate beats the global champion: **promote to champion**, close the set, reseed next set from the new champion.
- `reset` — set is closed (dead end / budget exhausted): **roll back to champion**, start a fresh set.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lineage_state_machine.py`:

```python
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
    assert t.state.refines_used == 1


def test_refine_budget_exhausted_closes() -> None:
    s = SetState(set_id=1, phase="refine", best_cer=0.176, refines_used=3)
    t = step_set(s, _ok(0.175, "advance", hyp_id="h5"), BUDGET)
    assert t.action == "reset"
    assert t.state.close_reason == "refine_budget"


def test_refine_no_gain_holds_until_budget() -> None:
    s = SetState(set_id=1, phase="refine", best_cer=0.176, refines_used=0)
    t = step_set(s, _ok(0.1761, "hold", hyp_id="h3"), BUDGET)
    assert t.action == "advance" or t.action == "hold"  # code rolled back, set continues
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_lineage_state_machine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.lineage'`

- [ ] **Step 3: Implement `harness/lineage.py`**

```python
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
```

Note the test `test_refine_no_gain_holds_until_budget` asserts `action in {advance, hold}` — the implementation returns `"repair"` for hold (rollback to lineage head). Update that test's assertion to `assert t.action == "repair"` to match the final design before running.

- [ ] **Step 4: Fix the one hold-assertion in the test**

Edit `tests/test_lineage_state_machine.py`, `test_refine_no_gain_holds_until_budget`:

```python
    assert t.action == "repair"  # hold → roll back candidate, retry refine
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_lineage_state_machine.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add harness/lineage.py tests/test_lineage_state_machine.py
git commit -m "feat(lineage): pure bounded-set state machine (phase1)"
```

---

## Task 4B: Portfolio — `lineage_advance` status does not pollute `global_best`

**Why (review C-2, the most important fix):** an in-set advance keeps the explore code on disk as the lineage head and must be committed as a code checkpoint. But if we commit it with `status="keep"`, `commit_iteration → _persist_decision → Portfolio.update` sets `global_best=hyp_id` for any keep/success (`portfolio.py:289-291`), so a worse-than-champion explore (0.190) would hijack `global_best` and corrupt refine-parent + near-best selection. We introduce a distinct status `lineage_advance` that commits code but updates **no** portfolio pool. Only a real promotion (beats champion) uses `keep`/`success`.

**Files:**
- Modify: `harness/portfolio.py` (`Portfolio.update`, line 282 `valid = …` and the docstring)
- Test: `tests/test_portfolio.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_portfolio.py`:

```python
def test_lineage_advance_does_not_touch_any_pool() -> None:
    from harness.portfolio import Portfolio

    p = Portfolio(job_id="j")
    # establish a real champion first.
    p.update(
        hyp_id="champ", iteration=1, decision_status="keep",
        report={"corpus_cer": 0.154}, best_report=None,
        harness_signature="s0", harness_family_id="F0",
    )
    assert p.global_best == "champ"
    # an in-set advance, WORSE than champion, must not move global_best nor
    # enter family_best/metric_best/near_best.
    before_family = dict(p.family_best)
    before_near = list(p.near_best)
    updated = p.update(
        hyp_id="explore1", iteration=2, decision_status="lineage_advance",
        report={"corpus_cer": 0.190}, best_report={"corpus_cer": 0.154},
        harness_signature="s1", harness_family_id="F1",
    )
    assert p.global_best == "champ"          # unchanged
    assert p.family_best == before_family    # F1 not added
    assert p.near_best == before_near        # 0.190 not banked
    assert updated == []                     # no slot updated
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_portfolio.py::test_lineage_advance_does_not_touch_any_pool -v`
Expected: FAIL (currently `lineage_advance` is unknown → `valid` is False so most pools skip, but `near_best` is updated unconditionally for any finite cer via `_retain_near_best` at `portfolio.py:329` → `updated` contains `"near_best"`, and the assert fails).

- [ ] **Step 3: Exclude `lineage_advance` from every pool**

In `Portfolio.update` (`portfolio.py:282`), `valid` already excludes unknown statuses from family/metric pools, and `global_best` is gated on `("keep","success")` — both already skip `lineage_advance`. The only leak is `near_best`, which is updated for any finite cer (line 329, regardless of status). Guard it:

```python
        # near_best — best 를 못 깬 후보라도 global best × factor 근방이면 보존.
        # NOTE(phase1): lineage_advance(=in-set code checkpoint)는 어떤 풀에도
        # 넣지 않는다 — set 내부 lineage head 는 state 가 추적하고, portfolio 는
        # 승격된 champion 계열만 담아야 refine/combine parent 가 안 오염된다(C-2).
        if decision_status != "lineage_advance" and cer is not None and self._retain_near_best(entry):
            updated.append("near_best")
```

Also update the `update` docstring's `decision_status` line to list `lineage_advance` and note it is portfolio-inert.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_portfolio.py -q`
Expected: PASS (existing portfolio tests unaffected; new test green).

- [ ] **Step 5: Commit**

```bash
git add harness/portfolio.py tests/test_portfolio.py
git commit -m "feat(portfolio): lineage_advance status is pool-inert (C-2, phase1)"
```

---

## Task 5: State fields for the set + champion ref

Persist the set across resume. Add fields to `HarnessState` (backward-compatible: defaults + `load` already ignores unknown keys, see `state.py:44`).

**Files:**
- Modify: `harness/state.py`
- Test: `tests/test_lineage_policy.py` (add a state round-trip test) or new `tests/test_harness_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_harness_state.py`:

```python
"""tests/test_harness_state.py — set/lineage state persistence."""

from __future__ import annotations

from harness.state import HarnessState


def test_set_fields_default_and_roundtrip(tmp_path) -> None:
    s = HarnessState(job_id="phase3_015")
    assert s.set_id == 0
    assert s.set_phase == "idle"
    assert s.set_best_cer is None
    assert s.champion_ref == "champion"
    s.set_id = 2
    s.set_phase = "refine"
    s.set_best_cer = 0.176
    p = tmp_path / "state.json"
    s.save(p)
    back = HarnessState.load(p)
    assert back.set_id == 2
    assert back.set_phase == "refine"
    assert back.set_best_cer == 0.176


def test_old_state_file_without_set_fields_loads(tmp_path) -> None:
    p = tmp_path / "old.json"
    p.write_text('{"job_id": "old", "iteration": 5, "best_cer": 0.2}\n',
                 encoding="utf-8")
    s = HarnessState.load(p)
    assert s.set_phase == "idle"   # default applied
    assert s.iteration == 5
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_harness_state.py -v`
Expected: FAIL (`AttributeError: 'HarnessState' object has no attribute 'set_id'`)

- [ ] **Step 3: Add fields**

In `harness/state.py`, add to the `HarnessState` dataclass (after `evaluated_since_best_update`, before `load`):

```python
    # ── Lineage set (HARNESS-REDESIGN §195, Phase 1) ──────────────────
    # Global champion ref: a git branch/tag holding the last promoted code.
    # The on-disk transcribe.py is the *lineage head* during an active set; on
    # set reset we restore from this ref. Default "champion" — gitops ensures it.
    champion_ref: str = "champion"
    # Active set bookkeeping (mirrors harness.lineage.SetState so resume can
    # rebuild it). set_phase "idle" = no active set (legacy single-shot path).
    set_id: int = 0
    set_phase: str = "idle"          # idle|explore|repair|refine|closed
    set_best_cer: float | None = None
    set_best_hyp_id: str | None = None
    set_repairs_used: int = 0
    set_refines_used: int = 0
    last_failure_hyp_id: str | None = None
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_harness_state.py -v`
Expected: PASS

- [ ] **Step 5: Run the full existing suite (no regressions)**

Run: `pytest tests/test_harness_policy.py tests/test_scheduler.py tests/test_portfolio.py -q`
Expected: PASS (existing behavior untouched)

- [ ] **Step 6: Commit**

```bash
git add harness/state.py tests/test_harness_state.py
git commit -m "feat(state): add set/lineage + champion_ref fields (phase1)"
```

---

## Task 6: `harness/gitops.py` — champion ref helpers

Single-lane Phase 1 still needs a stable champion pointer distinct from HEAD. These helpers manage a `champion` ref and restore the workspace file from it.

**Files:**
- Create: `harness/gitops.py`
- Test: `tests/test_gitops.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gitops.py`:

```python
"""tests/test_gitops.py — champion ref helpers (single-lane, phase1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness import gitops


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.py").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "f.py")
    _git(root, "commit", "-qm", "c1")
    return root


def test_ensure_champion_ref_creates_at_head(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    head = _git(repo, "rev-parse", "HEAD")
    champ = _git(repo, "rev-parse", "champion")
    assert head == champ


def test_ensure_champion_ref_idempotent(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    first = _git(repo, "rev-parse", "champion")
    # advance HEAD; ensure must NOT move an existing champion.
    (repo / "f.py").write_text("v2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "c2")
    gitops.ensure_champion_ref(repo, "champion")
    assert _git(repo, "rev-parse", "champion") == first


def test_restore_file_from_ref(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    (repo / "f.py").write_text("dirty\n", encoding="utf-8")
    gitops.restore_file_from_ref(repo, "champion", Path("f.py"))
    assert (repo / "f.py").read_text(encoding="utf-8") == "v1\n"


def test_advance_champion_ref_moves_to_commit(repo: Path) -> None:
    gitops.ensure_champion_ref(repo, "champion")
    (repo / "f.py").write_text("v2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "c2")
    new = _git(repo, "rev-parse", "HEAD")
    gitops.advance_champion_ref(repo, "champion", new)
    assert _git(repo, "rev-parse", "champion") == new
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_gitops.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'harness.gitops'`)

- [ ] **Step 3: Implement `harness/gitops.py`**

```python
"""harness/gitops.py
Git ref helpers for the lineage-set refactor (HARNESS-REDESIGN §195).

Phase 1 (single lane, no worktree): HEAD on the job branch is the *lineage head*;
a separate ``champion`` ref holds the last promoted code. On set reset we restore
the workspace file from champion; on promotion we advance champion to the verified
lineage commit. Phase 2 will add ``git worktree`` on top of these same refs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo_root: Path, args: list[str], check: bool = True
         ) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, check=check,
                          capture_output=True, text=True)


def _ref_exists(repo_root: Path, ref: str) -> bool:
    return _git(repo_root, ["show-ref", "--verify", "--quiet",
                            f"refs/heads/{ref}"], check=False).returncode == 0


def ensure_champion_ref(repo_root: Path, champion_ref: str = "champion") -> None:
    """Create ``champion_ref`` at current HEAD if it does not exist. Never moves
    an existing champion (promotion does that, explicitly)."""
    if not _ref_exists(repo_root, champion_ref):
        _git(repo_root, ["branch", champion_ref, "HEAD"])


def restore_file_from_ref(repo_root: Path, ref: str, rel_path: Path) -> None:
    """Restore one file's working-tree content from ``ref`` (set reset /
    champion rollback). Equivalent to ``git restore --source=<ref> -- <path>``."""
    _git(repo_root, ["restore", "--source", ref, "--", rel_path.as_posix()])


def advance_champion_ref(repo_root: Path, champion_ref: str, commit: str) -> None:
    """Move ``champion_ref`` to ``commit`` (promotion). ``commit`` must already
    contain the verified promoted code."""
    _git(repo_root, ["branch", "-f", champion_ref, commit])
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_gitops.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/gitops.py tests/test_gitops.py
git commit -m "feat(gitops): champion ref helpers for single-lane sets (phase1)"
```

---

## Task 7: Metadata out of git — **DEFERRED out of this increment (2026-06-05)**

> **Status: DEFERRED.** Implementation was attempted and **blocked** by a design collision the plan + review both missed: fully gitignoring `runs/` silently disables the **candidate scope-violation guard and rollback**, which are built on git tracking of the `runs/_summary/` surface:
> - **Detection** — `git_status` uses `git status --porcelain --untracked-files=all`, which does **not** report ignored paths. With `runs/` ignored, a candidate writing into `runs/_summary/<job>_state.json` (or baseline/docs) during `transcribe` is **no longer caught** by `disallowed_candidate_paths`/`disallowed_post_verify_paths` (`runner.py:295-328`, `1958-1968`, `2008-2018`) — a real safety regression, not a test artifact.
> - **Rollback** — `rollback_paths` (`runner.py:345-353`) uses `git restore` (can't restore an untracked/ignored file) + `git clean -fd` **without `-x`** (won't remove ignored files). Poison in an ignored `runs/` can't be rolled back.
> - **F2 abort-commit** — `run_job` commits aborted state via `commit_iteration("abort", …)` (`runner.py:2180-2191`); a status-gated commit-only-code rule makes that a no-op, breaking `test_run_job_commits_aborted_state_when_commit_results`.
>
> **Decision:** metadata-off-git is **independent of the C2 fix** (Tasks 1–6, 8 deliver the lineage set with the *existing* `commit_iteration` unchanged — it commits code+metadata together every iter, keeping the tree clean, which is correct just churnier). Doing it properly requires its own design for the scope-guard/rollback on an ignored tree (filesystem-snapshot guard, or `git status --ignored` + `git clean -fdx` for the runs surface, plus removing the now-obsolete F2 abort-commit). That is a separate sub-project — pulling it into the C2 increment violates "small, reversible" and over-reaches. **Tracked as a Phase-1.5 follow-up; the original spec below is retained for that work.**
>
> Task 8 does **not** depend on this task — it uses the existing `commit_iteration` (passing `lineage_advance`/`keep`/`reject` statuses; the original function commits whenever there is a staged diff, so lineage checkpoints and promotions commit correctly and the tree stays clean).

*(Original spec, retained for the deferred follow-up:)*

Today `commit_iteration` (`runner.py:1774`) git-adds `transcribe.py` + `HISTORY.md` + decisions/meta/state and commits **every iter** — the 720-commit churn and the race that wiped uncommitted docs. Phase 1 keeps these files as **append-only on disk** (they already use atomic writes — `state.save`, `Portfolio.save`, jsonl appends) and removes them from git tracking; git commits become **code checkpoints only**.

**Files:**
- Modify: `.gitignore`
- Modify: `harness/runner.py` (`commit_iteration`)
- Modify: `tests/test_harness_runner.py` — the **one** test that asserts metadata is git-tracked: `test_step1_decision_trace_committed_and_survives_next_iter` (lines 431-432, 462-466) **and** its `_init_repo` `.gitignore` (lines 76-79)

- [ ] **Step 1: Inventory what asserts metadata commits (confirm it's just one test)**

Run: `grep -rn "_tracked\|ls-files\|git_log_subjects\|commit_iteration\|decisions.jsonl\|portfolio.json\|_state.json" tests/`
Confirm the only **tracking/commit** assertions (vs merely *writing*) are in `tests/test_harness_runner.py:431-432` (`_tracked(... job_decisions.jsonl)`, `_tracked(... job_portfolio.json)`) and the clean-tree assert at line 462-466. `_tracked` itself is `git ls-files --error-unmatch` (line 372-378). Other tests assert *contents on disk*, which still hold.

- [ ] **Step 2: Write the unit test for the code-only stage**

Add to `tests/test_harness_runner.py`:

```python
def test_commit_iteration_stages_code_only(tmp_path, monkeypatch):
    """metadata files are written to disk but NOT git-staged (phase1, C-1)."""
    from harness import runner
    from harness.runner import RunnerConfig

    staged = {"added": None}

    def fake_git(repo_root, args, check=True):
        if args[:1] == ["add"]:
            staged["added"] = args[2:]  # paths after "add", "--"
        class R:
            returncode = 1  # pretend a staged diff exists so commit proceeds
            stdout = ""
        return R()

    monkeypatch.setattr(runner, "_run_git", fake_git)
    monkeypatch.setattr(runner, "_persist_candidate_meta", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_persist_decision", lambda *a, **k: [])
    cfg_ = RunnerConfig(job_id="j", repo_root=tmp_path)
    runner.commit_iteration(cfg_, tmp_path / "s.json", "keep", "h1", 1)
    assert staged["added"] == ["workspace/transcribe.py"]
    # a reject must not stage/commit anything
    staged["added"] = None
    runner.commit_iteration(cfg_, tmp_path / "s.json", "reject", "h2", 2)
    assert staged["added"] is None
```

- [ ] **Step 3: Rewrite `commit_iteration` to stage code only**

Replace the body of `commit_iteration` (`runner.py:1774-1809`). The `_persist_*` calls still run (they write append-only files); only the git staging changes — commit **only the code file**, and only for statuses that advance code (`keep`, `success`, and the new `lineage_advance` from Task 4B):

```python
def commit_iteration(
    config: RunnerConfig,
    state_path: Path,
    status: str,
    hyp_id: str,
    iteration: int,
    reason: str | None = None,
    attempt_status: str | None = None,
) -> None:
    # Metadata is written to disk (append-only, atomic) but NOT committed — it
    # lives under runs/ which is now fully gitignored (phase1, HARNESS-REDESIGN
    # §80). git commits are code checkpoints only, killing the per-iter churn and
    # the uncommitted-file race.
    _persist_candidate_meta(config, hyp_id, iteration, status)
    _persist_decision(
        config, hyp_id, iteration, status, reason=reason,
        attempt_status=attempt_status,
    )
    # Only a code-advancing status checkpoints the workspace: keep/success
    # (promotion) and lineage_advance (in-set lineage head). reject/abort leave
    # the tree as the rollback already set it.
    if status not in ("keep", "success", "lineage_advance"):
        return
    _run_git(config.repo_root, ["add", "--", config.allowed_path.as_posix()])
    diff = _run_git(config.repo_root, ["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        return
    _run_git(config.repo_root, ["commit", "-m", f"iter{iteration}: {status} {hyp_id}"])
```

- [ ] **Step 4: Stop tracking metadata in git (production `.gitignore`)**

Edit `.gitignore` lines 17-20 (replace the `runs/*` + `!runs/_summary/` exception with a blanket ignore):

```gitignore
# 실행 산출물 — 전부 ignored. metadata(state/portfolio/decisions/meta/HISTORY)는
# append-only 파일로 디스크에 durable 하게 남고(atomic write), git 에는 코드
# checkpoint 만 커밋한다(phase1, HARNESS-REDESIGN §80). resume 는 파일만으로 충분.
runs/
```

Untrack any already-committed summary files (no-op on this branch — `git ls-files runs/_summary` is currently empty; harmless, kept for repos where it isn't):

```bash
git rm -r --cached runs/_summary 2>/dev/null || true
```

- [ ] **Step 5: Fix the broken integration test (C-1)**

In `tests/test_harness_runner.py`:

a) `_init_repo`'s embedded `.gitignore` (lines 76-79) — change it to match production so the test repo behaves identically:

```python
    (repo / ".gitignore").write_text("runs/\n", encoding="utf-8")
```

If `_init_repo` then `git add`s a `runs/_summary/...` path (lines ~100-101) it must use `git add -f` (force, since now ignored) or drop that add — adjust so the initial commit doesn't try to track an ignored path.

b) `test_step1_decision_trace_committed_and_survives_next_iter` (lines 431-432) — convert the *tracked* asserts to *on-disk existence* (the trace is now durable-but-untracked):

```python
    assert (tmp_path / "runs/_summary/job_decisions.jsonl").is_file()
    assert (tmp_path / "runs/_summary/job_portfolio.json").is_file()
```

The clean-tree assert (line 462-466) now holds because `runs/` is ignored (the metadata writes no longer show as untracked-dirty).

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_harness_runner.py tests/test_runner_decision_trace.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add .gitignore harness/runner.py tests/test_harness_runner.py
git commit -m "refactor(runner): commit code only, metadata append-only off-git (phase1, C-1)"
```

---

## Task 8: Wire the set into `run_iteration` (behind `--set-budget`, default off)

This is the integration task. It is gated so `--set-budget 1` (default) preserves the exact legacy single-shot path; `--set-budget N>1` activates the bounded set.

**Files:**
- Modify: `harness/runner.py` (`RunnerConfig`, `_decide_iteration` set-phase override, `run_iteration` verify-fail + decision/rollback blocks, `run_job` bootstrap, `main` argparse)
- Test: `tests/test_harness_runner.py` (set-path integration tests with injected `verify_func`)

This task has three coupled pieces, in order: **(8a)** config fields, **(8b)** set-phase → prompt-mode override in `_decide_iteration` (review I-1), **(8c)** the dual-decision set branch in `run_iteration` with `lineage_advance`/promote wiring (review C-2, I-2).

> **Depends on the EXISTING `commit_iteration`** — Task 7 (status-gated commit-code-only) is **deferred** (see Task 7 banner). The current `commit_iteration` commits whenever there is a staged diff (it always stages `transcribe.py` + metadata), so: `lineage_advance`/`keep`/`success` calls commit the lineage code checkpoint (HEAD advances — `rev-parse HEAD` after the call yields a real commit for `advance_champion_ref`), and `reject` (repair/reset) calls commit metadata so the tree stays clean for the next `ensure_worktree_ready`. **Caveat (accepted churn):** on `reset` we `restore_file_from_ref(champion)` first, so the subsequent `reject` commit records a code revert-to-champion — noisy history but correct; cleaned up when Task 7 lands. The `--set-budget>1 requires --commit-results` guard (Step 5) is what makes the reset-restore safe (without a commit the restored file would leave the tree dirty).
>
> **Carry these Task-4 review contract notes into the wiring:** (1) prompt mode is driven by `state.set_phase` (persisted from `t.state.phase`), NOT by `t.action` — e.g. a refine `hold`/fail returns `action="repair"` but keeps `phase="refine"`, so the next prompt is correctly `refine`, not `repair`. Only an explore-fail sets `phase="repair"`. (2) Repair prompt injection uses `_last_failure_parent` which reads the last `verify_fail` from `decisions.jsonl` (not `state.last_failure_hyp_id`), so the state field being stale on a refine-rollback is harmless in Phase 1.

- [ ] **Step 1: Add RunnerConfig fields (M-2: frozen → set via constructor)**

`RunnerConfig` is `@dataclass(frozen=True)` (`runner.py:214`) — these must be constructor args (Step 7 threads them in `main`), never post-init assignment. Add after `commit_results`:

```python
    # Lineage set (phase1). set_budget == 1 → legacy single-shot path (explore
    # rolled back on reject). > 1 activates the bounded explore→repair/refine set.
    set_budget: int = 1
    max_repairs: int = cfg.SET_MAX_REPAIRS
    max_refines: int = cfg.SET_MAX_REFINES
```

- [ ] **Step 2: Set-phase overrides scheduler mode when a set is active (I-1)**

Without this, the scheduler can hand a `refine`-phase set an `explore` prompt (its `diversity_stall`/`discovery_phase` overrides), and a `repair`-phase set won't get `_last_failure_parent` injected (that helper is `chosen_mode=="repair"`-only at `runner.py:1215`). Decision (review Open Q1 → A): **when `set_budget>1` and `state.set_phase ∈ {explore,repair,refine}`, force `chosen_mode = state.set_phase`** and pick parents for that mode.

In `_decide_iteration` (`runner.py:1190`), after `sched = scheduler.decide_mode(...)` (line 1198) and before the parent loop, insert:

```python
    # Set-active override (phase1, I-1): a running set owns the prompt mode. The
    # scheduler still computes scheduled_mode for the trace, but the set phase
    # decides what we actually ask the candidate to do, so "refine the lineage
    # head" / "repair the last failure" reaches the prompt (not a scheduler
    # explore override). set_phase is the phase the PREVIOUS iter left behind.
    if config.set_budget > 1 and state.set_phase in ("explore", "repair", "refine"):
        from harness.scheduler import SchedulerDecision
        sched = SchedulerDecision(
            scheduled_mode=sched.scheduled_mode,
            chosen_mode=state.set_phase,
            override=f"set:{state.set_phase}",
        )
```

The existing parent selection below (`parents_for_mode(..., sched.chosen_mode, ...)` + the `repair`→`_last_failure_parent` fallback at line 1215) then runs against the set phase automatically — no further change. For `refine` the on-disk file already *is* the lineage head, so `parents_for_mode("refine")` supplying a portfolio parent is harmless (the candidate edits the current file); for `repair` the `_last_failure_parent` fallback now fires correctly.

Write a unit test asserting the override:

```python
def test_set_phase_overrides_scheduler_mode(tmp_path, monkeypatch):
    from harness.runner import RunnerConfig, _decide_iteration
    from harness.state import HarnessState
    cfg_ = RunnerConfig(job_id="j", repo_root=tmp_path, set_budget=4)
    st = HarnessState(job_id="j", set_phase="refine", evaluated_count=20,
                      best_hyp_id="h", best_cer=0.2)
    # portfolio empty → scheduler would normally pick explore; set forces refine.
    sched, _parents = _decide_iteration(cfg_, st)
    assert sched.chosen_mode == "refine"
    assert sched.override == "set:refine"
```

- [ ] **Step 3: Write the C2 integration test (I-4 — no longer a stub)**

Reuse the fixtures already in `tests/test_harness_runner.py` — read `test_run_iteration_rolls_back_workspace_on_verify_failure` (lines ~340-369) and `_init_repo` first, then mirror their temp-repo + injected `candidate`/`verify_func` pattern. The test proves the C2 fix end-to-end: a worse-than-champion explore is **not** rolled back and the set moves to `refine`.

```python
def test_set_keeps_worse_than_champion_explore(tmp_path, monkeypatch):
    """C2 fix: with set_budget>1 a worse-than-champion explore survives into
    refine (its code stays on disk) instead of being rolled back."""
    repo = _init_repo(tmp_path)          # seeds workspace/transcribe.py + champion
    cfg_ = RunnerConfig(job_id="job", repo_root=repo, set_budget=4,
                        commit_results=True, manual=False,
                        candidate_cmd=None)
    state = HarnessState(job_id="job", best_cer=0.20, best_hyp_id="champ")
    state_path = repo / "runs/_summary/job_state.json"

    # candidate writes a new structure; verify scores it WORSE than champion.
    def candidate(prompt, out_dir):
        (repo / "workspace/transcribe.py").write_text(
            "def transcribe(a, sr):\n    return 'EXPLORE'\n", encoding="utf-8")
        return _ok_completed_process(out_dir)   # mirror existing helper
    def verifier(hyp_id):
        return _verify_ok(report={"corpus_cer": 0.30,
                                  "total_inference_time_s": 90.0})

    from harness import gitops
    gitops.ensure_champion_ref(repo, "champion")
    runner.run_iteration(cfg_, state, state_path,
                         candidate_func=candidate, verify_func=verifier)

    assert state.set_phase == "refine"            # cultivated, not discarded
    assert state.set_best_cer == 0.30
    assert state.best_cer == 0.20                  # champion NOT moved by advance
    assert "EXPLORE" in (repo / "workspace/transcribe.py").read_text()  # not rolled back
```

(Adjust helper names `_ok_completed_process`/`_verify_ok` to whatever the existing tests use; if the file builds these inline, copy that form.)

- [ ] **Step 4: Implement the set branch in `run_iteration`**

Add the two helpers near `_decide_iteration`:

```python
def _set_state_from(state: HarnessState):
    from harness.lineage import SetState
    if state.set_phase in ("idle", "closed"):
        return SetState.new(set_id=state.set_id + 1)
    return SetState(
        set_id=state.set_id,
        phase=state.set_phase,
        best_cer=state.set_best_cer,
        best_hyp_id=state.set_best_hyp_id,
        repairs_used=state.set_repairs_used,
        refines_used=state.set_refines_used,
        last_failure_hyp_id=state.last_failure_hyp_id,
    )


def _persist_set_state(state: HarnessState, s) -> None:
    state.set_id = s.set_id
    state.set_phase = "idle" if s.phase == "closed" else s.phase
    state.set_best_cer = s.best_cer
    state.set_best_hyp_id = s.best_hyp_id
    state.set_repairs_used = s.repairs_used
    state.set_refines_used = s.refines_used
    state.last_failure_hyp_id = s.last_failure_hyp_id
```

**4a. Verify-fail under a set** — generalize the verify-fail block (`runner.py:2048-2077`). When `config.set_budget>1`, route through the set instead of the unconditional reject:

```python
    if not verify_result.ok or verify_result.report is None:
        _persist_verify_failure(repo_root, config, hyp_id, verify_result)
        if config.set_budget > 1:
            s = _set_state_from(state)
            from harness.lineage import SetBudget, Outcome, step_set
            outcome = Outcome(verify_ok=False, lineage_status=None, cer=None,
                              beats_champion=False, hyp_id=hyp_id)
            t = step_set(s, outcome, SetBudget(config.max_repairs, config.max_refines))
            # repair → rollback to lineage head (== HEAD single-lane); next iter's
            # _decide_iteration sees set_phase="repair" and injects the failure.
            # reset → close set, rollback workspace to champion ref.
            rollback_paths(repo_root, candidate_owned_statuses(git_status(repo_root), config))
            if t.action == "reset":
                from harness import gitops
                gitops.restore_file_from_ref(repo_root, state.champion_ref, config.allowed_path)
            _persist_set_state(state, t.state)
            result = IterationResult(hyp_id=hyp_id, status="reject", decision=None,
                                     verify_result=verify_result,
                                     reason=verify_result.error or "verify 실패 (set)")
            append_event(str(state.iteration), hyp_id, "NA", "NA", "reject",
                         _history_body(result), repo_root=repo_root)
            state.save(state_path)
            if config.commit_results:
                commit_iteration(config, state_path, "reject", hyp_id,
                                 state.iteration, reason=result.reason)
            return result
        # legacy single-shot path (unchanged) ↓
        rollback_paths(repo_root, candidate_owned_statuses(git_status(repo_root), config))
        ... (existing reject return) ...
```

**4b. Scored-decision under a set** — replace the decision block (`runner.py:2082-2121`, after `state.record_evaluated()`):

```python
    if config.set_budget <= 1:
        # ── legacy single-shot path (unchanged) ───────────────────────────
        decision = decide_candidate(
            report=verify_result.report, baseline=baseline,
            best_cer=state.best_cer, sigma=noise.get("sigma"),
            sigma_is_provisional=bool(noise.get("is_provisional")),
            config=PolicyConfig(absolute_delta_fallback=config.absolute_delta_fallback),
        )
        if decision.status in ("keep", "success"):
            state.record_best(hyp_id, decision.candidate_cer)
            if decision.status == "success":
                state.status = "success"
        else:
            rollback_paths(repo_root, candidate_owned_statuses(git_status(repo_root), config))
        result = IterationResult(hyp_id=hyp_id, status=decision.status,
                                 decision=decision, verify_result=verify_result,
                                 reason=decision.reason)
        append_event(str(state.iteration), hyp_id, f"{decision.candidate_cer:.6f}",
                     _format_delta(decision.delta_from_best), decision.status,
                     _history_body(result), repo_root=repo_root)
        state.save(state_path)
        if config.commit_results:
            commit_iteration(config, state_path, decision.status, hyp_id,
                             state.iteration, reason=result.reason)
        return result

    # ── set path (set_budget>1): two comparisons (C-2 keeps global_best clean) ─
    from harness.lineage import SetBudget, Outcome, step_set
    from harness import gitops
    cand_cer = float(verify_result.report["corpus_cer"])
    promo = decide_promotion(
        report=verify_result.report, baseline=baseline, champion_cer=state.best_cer,
        sigma=noise.get("sigma"), sigma_is_provisional=bool(noise.get("is_provisional")),
        config=PolicyConfig(absolute_delta_fallback=config.absolute_delta_fallback),
    )
    lin = decide_lineage_progress(verify_result.report, lineage_best_cer=state.set_best_cer)
    s = _set_state_from(state)
    outcome = Outcome(verify_ok=True, lineage_status=lin.status, cer=cand_cer,
                      beats_champion=promo.status in ("keep", "success"),
                      hyp_id=hyp_id)
    t = step_set(s, outcome, SetBudget(config.max_repairs, config.max_refines))

    if t.action == "promote":
        # candidate beat champion → it IS the new champion. Commit as a real keep
        # (updates global_best legitimately), then advance the champion ref to the
        # commit we just made (I-2: read HEAD AFTER the commit).
        commit_status = "success" if promo.status == "success" else "keep"
        state.record_best(hyp_id, cand_cer)
        if promo.status == "success":
            state.status = "success"
        if config.commit_results:
            commit_iteration(config, state_path, commit_status, hyp_id,
                             state.iteration, reason=promo.reason)
            head = _run_git(repo_root, ["rev-parse", "HEAD"]).stdout.strip()
            gitops.advance_champion_ref(repo_root, state.champion_ref, head)
        decision_status = commit_status
        reason = promo.reason
    elif t.action == "advance":
        # in-set lineage head: commit code as lineage_advance (pool-inert, C-2).
        decision_status = "lineage_advance"
        reason = lin.reason
        if config.commit_results:
            commit_iteration(config, state_path, "lineage_advance", hyp_id,
                             state.iteration, reason=reason)
    else:  # "repair" or "reset"
        rollback_paths(repo_root, candidate_owned_statuses(git_status(repo_root), config))
        if t.action == "reset":
            gitops.restore_file_from_ref(repo_root, state.champion_ref, config.allowed_path)
        decision_status = "reject"
        reason = lin.reason
        if config.commit_results:
            commit_iteration(config, state_path, "reject", hyp_id,
                             state.iteration, reason=reason)

    _persist_set_state(state, t.state)
    result = IterationResult(hyp_id=hyp_id, status=decision_status, decision=None,
                             verify_result=verify_result, reason=reason)
    append_event(str(state.iteration), hyp_id, f"{cand_cer:.6f}", "NA",
                 decision_status, _history_body(result), repo_root=repo_root)
    state.save(state_path)
    return result
```

Notes:
- `state.save(state_path)` is reached on **every** branch (M-4) — promote/advance/repair/reset all fall through to the single `state.save` at the end (verify-fail set branch saves explicitly).
- `commit_iteration` is called *before* `advance_champion_ref` in promote so HEAD already contains the promoted code (I-2). If the promoted code is byte-identical to the prior lineage commit, `commit_iteration` skips the commit (no diff) and `rev-parse HEAD` returns the existing lineage commit — which still contains the code, so `advance_champion_ref` is correct either way.

**4c. Bootstrap the champion ref** — in `run_job` (`runner.py:2124`), after `state, state_path = load_or_init_state(config)` (line 2126), add:

```python
    if config.set_budget > 1 and config.commit_results:
        from harness import gitops
        gitops.ensure_champion_ref(config.repo_root.resolve(), state.champion_ref)
```

- [ ] **Step 5: Add CLI flags + guard (M-3)**

In `main` argparse (`runner.py:2216+`), add:

```python
    parser.add_argument("--set-budget", type=int, default=1,
                        help="1 = legacy single-shot; >1 activates the bounded "
                             "explore→repair/refine set (phase1).")
    parser.add_argument("--max-repairs", type=int, default=cfg.SET_MAX_REPAIRS)
    parser.add_argument("--max-refines", type=int, default=cfg.SET_MAX_REFINES)
```

Add a guard (M-3) near the existing `iters>1 requires --commit-results` check (`runner.py:2238`): the set path checkpoints code via commits, so

```python
    if args.set_budget > 1 and not args.commit_results:
        parser.error("--set-budget>1 requires --commit-results (sets checkpoint code via commits)")
```

Then thread the three fields into the `RunnerConfig(...)` constructor in `main` (M-2): `set_budget=args.set_budget, max_repairs=args.max_repairs, max_refines=args.max_refines`.

- [ ] **Step 6: Run the integration + full suite**

Run: `pytest tests/test_harness_runner.py -v && pytest -q`
Expected: new set tests PASS; full suite green. Legacy tests unaffected (default `set_budget=1`).

- [ ] **Step 7: Commit**

```bash
git add harness/runner.py tests/test_harness_runner.py
git commit -m "feat(runner): wire bounded lineage set behind --set-budget (phase1, C-2/I-1/I-2)"
```

---

## Task 9: Docs + SSOT sync

**Files:**
- Modify: `docs/HARNESS-MECHANICS.md` (add a "lineage set (phase1)" section describing the two comparisons + state machine + the `--set-budget` flag; note legacy path is default)
- Modify: `docs/SSOT.md` (§3 code map: add `lineage`, `gitops` modules; §5: mark this plan as the active Phase-1 work)
- Verify: `tests/test_doc_consistency.py` (`test_ssot_code_map_lists_every_harness_module`) now requires `lineage` and `gitops` to appear in SSOT §3

- [ ] **Step 1: Run the doc-consistency guard to see it fail**

Run: `pytest tests/test_doc_consistency.py -v`
Expected: FAIL — `lineage`, `gitops` missing from SSOT §3.

- [ ] **Step 2: Add the two modules to SSOT §3**

In `docs/SSOT.md` §3 `harness/` row, append `lineage`(bounded-set state machine), `gitops`(champion ref helpers) to the module list.

- [ ] **Step 3: Add the HARNESS-MECHANICS lineage-set section**

Document: the two comparison functions, the `explore→repair/refine` set state machine (reference the §94 diagram in HARNESS-REDESIGN), the `champion` ref vs lineage head split in single lane, and that `--set-budget 1` (default) = legacy. Keep it code-grounded (cite the new modules/functions).

- [ ] **Step 4: Run guard + full suite**

Run: `pytest tests/test_doc_consistency.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/SSOT.md docs/HARNESS-MECHANICS.md
git commit -m "docs: document lineage-set (phase1) in MECHANICS + SSOT module map"
```

---

## Self-Review checklist (run before handoff)

**Spec coverage (HARNESS-REDESIGN §195 change-points → tasks):**
- `RunnerConfig` set fields → Task 8 Step 1 ✓
- `rollback_paths` generalized to lineage head → Task 8 (single-lane: lineage head == HEAD, so existing rollback is correct; champion rollback is the new `gitops.restore_file_from_ref` on `reset`) ✓
- `_decide_iteration` set phase → Task 8 Step 2 (set phase **overrides** scheduler `chosen_mode` when active — I-1) ✓
- `run_iteration` dual decision (`decide_lineage_progress` + `decide_promotion`) → Task 8 Step 4 ✓
- `commit_iteration` code vs metadata split → Task 7 ✓
- `HarnessState` lineage fields → Task 5 ✓
- `policy.decide_candidate` → split into `decide_lineage_progress` + `decide_promotion` → Tasks 2,3 ✓
- new `harness/gitops.py` → Task 6 ✓
- `scripts/evolve.py` CLI flags → Task 8 Step 5 (flags land in `runner.main`; thin CLI unchanged) ✓
- `portfolio.py` `global_best` pollution → **partially pulled forward** (review C-2/I-3): the full §209 `job_best`/`global_best` split stays Phase 2, but Phase 1 needs the *minimal* form — Task 4B makes the `lineage_advance` status pool-inert so in-set advances never touch `global_best`/`near_best`. (Corrects this plan's earlier "deferral is NOT a gap" claim, which the review refuted.)
- `scheduler.py` `decide_mode` set-phase awareness → handled at the `_decide_iteration` override layer (Task 8 Step 2) rather than inside `decide_mode` itself; `decide_mode` stays a pure function. ✓
- `cooldown.py` `api:`/`rmapi:` normalize fix → **deferred** (independent bug, not on the C2 critical path; track separately).

**Resolved review issues:** C-1 (Task 7 Steps 1/5), C-2 (Task 4B + Task 8 Step 4 `lineage_advance`/promote split), I-1 (Task 8 Step 2 override), I-2 (Task 8 Step 4 promote ordering), I-4 (Task 8 Step 3 concrete test), I-5 (Task 3 note + Open Question), M-1…M-6 (folded into Tasks 3/4/7/8).

**Open Questions deferred past Phase 1 (operator-acknowledged):** (a) absolute-margin floor for `decide_lineage_progress` dead-end (I-5) — benign in Phase 1; (b) whether reset-reseed-from-champion re-introduces partial explore anchoring in single lane (review Open Q3) — accepted, redesign §86 permits champion seed; fully resolved only by Phase 2 worktree + pluggable seed.

**Deferred-to-later-phase (NOT Phase 1, by design — redesign §172 timeline):** worktree execution, parallel jobs, branch protection, `requires_data`/`integration` markers, `git archive` packaging, archive/islands, scheduler/portfolio `job_best` split.

**Placeholder scan:** Task 8 Step 2 leaves the integration-test body as a guided stub (`...`) because it must mirror existing `run_iteration` fixtures in `tests/test_harness_runner.py` that the implementing agent will read in-context — the *behavioral assertions* are fully specified (set_phase, set_best_cer, on-disk file not restored). Flesh out against those fixtures before running.

**Type consistency:** `Outcome.lineage_status` (str) ← `LineageDecision.status` (`"advance"|"hold"|"dead_end"`); `Outcome.beats_champion` (bool) ← `decide_promotion(...).status in {"keep","success"}`; `SetState` field names match `_set_state_from`/`_persist_set_state`; `step_set` returns `Transition(state, action)` with `action ∈ {advance, repair, promote, reset}` consumed verbatim in Task 8 Step 3.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-05-harness-lineage-set-phase1.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.
