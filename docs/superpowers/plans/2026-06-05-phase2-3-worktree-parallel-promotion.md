# Harness Refactor — Phase 2+3: Worktree Execution Lane + Parallel Jobs + Gated Promotion

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run each evolution job in its own `git worktree` cut from a protected `champion` ref (never a runner `repo_root`), run **N jobs in parallel** (default cap 2), and make promotion to the single shared `champion` **serialized + gated**: under a local file lock, re-validate against the *live* champion CER, then advance the champion via a ref compare-and-swap (CAS) that splices only `workspace/transcribe.py` for linear history. Parallel **requires** gated promotion — concurrent jobs racing to advance one champion re-introduce the exact single-HEAD race this refactor exists to kill, so Phase 2 (parallel) and Phase 3 (governance) ship as **one coherent unit**.

**Architecture (2-3 sentences):** A protected `champion` branch holds the last promoted code and is *never* checked out by a runner; each job gets a linked worktree via `git worktree add <wt_root> -b job/<job_id> champion`, and the runner's existing git ops (which already `cwd` into `config.repo_root`) operate worktree-local — the on-disk `transcribe.py` is that worktree's lineage head, repair-rollback targets the worktree's own HEAD, reset targets the `champion` ref read through the worktree. Promotion is pulled out of the per-iteration `run_iteration` hot path into a serialized lane guarded by an `fcntl.flock` lock-file that auto-releases on process death; under the lock the candidate is re-scored against the *current* champion (the A-vs-B concurrent-beat race), then `git update-ref refs/heads/champion <new> <old-expected>` (CAS) advances it, recording `runs/_summary/promotion_map.jsonl`; a lost CAS keeps the candidate as the job's lineage head (not discarded), by re-deciding the set step with `beats_champion=False`.

**Tech Stack:** Python 3.11 (conda env `evolve`; the git/worktree/promotion tests in this plan use only stdlib + git CLI and run under base python too — match the existing `tests/test_gitops.py` convention), pytest, git CLI via `subprocess`, `fcntl.flock` (POSIX, Linux operator), `subprocess.Popen` pool for the launcher. Builds **on** Phase 1 modules (`harness/lineage.py`, `harness/gitops.py`, `harness/state.py`, `harness/policy.py`) — none are rewritten.

---

## Revision note (2026-06-05)

This plan was reworked after a code-grounded review. Changes vs the first draft: (1) every new test now matches the **real** `_init_repo` (returns `None`, creates no `champion`) — tests call it for side effects, then `gitops.ensure_champion_ref(tmp_path, …)`; (2) the "promotion lost" race is modeled at the **input** to `step_set` (re-decide with `beats_champion=False`), never by mutating a closed `Transition` — the `_reopen_as_refine` helper is deleted; (3) the live champion CER is **seeded** at `ensure_champion_ref` time from `baseline/target_cer.json:baseline_cer`, so a bootstrap with no promotions still re-validates against a real number; (4) Phase 1.5 is now a **prerequisite that lands first**, so the shared set-path edits reduce to adding `restore_lineage_head` + wiring the promotion lane; (5) scope is split — Tasks 1–3 are the shipped core, markers/packaging are deferred, but the `worktree`/`promotion` marker *registration* is pulled forward into Task 0.

---

## Ground truth: what Phase 1 (and Phase 1.5) already landed (READ before starting)

Phase 1 (`docs/superpowers/plans/2026-06-05-harness-lineage-set-phase1.md`) is **merged on `refactor-harness`**, and **Phase 1.5 (metadata-off-git) lands BEFORE this plan** (see the "Dependency on Phase 1.5" section — it is a hard prerequisite). Confirmed in code:

- `harness/lineage.py` — pure `SetBudget`/`Outcome`/`SetState`/`Transition`/`step_set`. **Reuse verbatim.** Actions: `advance | repair | promote | reset`. Note the key fact this plan leans on for the C2 fix: `step_set` only emits `promote` when `outcome.verify_ok and outcome.beats_champion` (`lineage.py:84`); with `beats_champion=False` an explore-first scored candidate goes through `_advance_to_refine` → action `advance` (`lineage.py:96-97`), and a refine candidate follows its `advance`/`hold` logic (`lineage.py:122-131`).
- `harness/gitops.py` — `_git(repo_root, args, check=True)` (returns `CompletedProcess`), `_ref_exists(repo_root, ref)`, `ensure_champion_ref(repo_root, champion_ref="champion")`, `restore_file_from_ref(repo_root, ref, rel_path)`, `advance_champion_ref(repo_root, champion_ref, commit)`. **Phase 2 ADDS to this file** (Task 1): `prepare_job_worktree`, `restore_lineage_head`, `cleanup_worktree`, `list_worktrees`; **Phase 3 ADDS** (Task 3): `read_ref`, `promote_to_champion`, and a seeding step inside/after `ensure_champion_ref`.
- `harness/state.py` — `HarnessState` has `champion_ref="champion"` (`state.py:44`), `best_cer`/`best_hyp_id`, `evaluated_count`, `set_phase` (`idle|explore|repair|refine|closed`), `set_best_cer`, `set_best_hyp_id`, `set_repairs_used`, `set_refines_used`, `last_failure_hyp_id`, `record_best(hyp_id, corpus_cer)`. `load` ignores unknown keys; `save` is atomic.
- `harness/policy.py` — `decide_promotion(report, baseline, champion_cer, sigma, sigma_is_provisional=False, config=None) -> Decision` (`policy.py:179`). `Decision` fields: `status` (`"keep"|"reject"|"success"`), `candidate_cer: float`, `best_cer`, `delta_from_best`, `threshold`, `reason`. **Crucial defect for I3:** `decide_promotion` delegates to `decide_candidate`, where `best_cer is None` → unconditional `status="keep"` ("first valid candidate", `policy.py:147-155`). So `champion_cer=None` means "first candidate always wins." `PolicyConfig(absolute_delta_fallback=...)` is the config knob the runner threads.
- `harness/runner.py`:
  - `RunnerConfig` (frozen dataclass, `runner.py:221`): `job_id`, `iterations`, `repo_root=Path(".")`, `candidate_cmd=None`, `manual=False`, `allowed_path=Path("workspace/transcribe.py")`, `runs_dir`, `summary_dir`, `baseline_file`, `noise_floor_file`, `absolute_delta_fallback`, `commit_results=False`, and the Phase-1 set fields `set_budget=1`, `max_repairs`, `max_refines`. **Phase 2 ADDS one field:** `main_repo_root: Path | None = None`.
  - `_run_git(repo_root, args, check=True)` (`runner.py:282`) — **all** runner git ops pass `cwd=repo_root`, so they are already worktree-correct.
  - `run_iteration` (`runner.py:1872` entry; legacy single-shot path `set_budget<=1` at `runner.py:2172`): the **set path** (`set_budget>1`, `runner.py:2214`) computes `decide_promotion` + `decide_lineage_progress`, builds an `Outcome` (`beats_champion=promo.status in ("keep","success")`, `runner.py:2227-2229`), calls `step_set`, and on `t.action`:
    - `promote` → status `keep`/`success`, `record_best`, then (after `state.save`) `commit_iteration(...)`, then **`head = rev-parse HEAD` → `advance_champion_ref(repo_root, state.champion_ref, head)`** (`runner.py:2277-2279`). **This unguarded promote is the race to replace in Phase 3.**
    - `advance` → status `lineage_advance` (committed code checkpoint, portfolio pool-inert — `portfolio.py`).
    - `repair`/`reset` → `rollback_paths`; on `reset` also `restore_file_from_ref(repo_root, champion_ref, allowed_path)` (`runner.py:2249-2254`). The verify-fail set block (`runner.py:2106-2119`) does the same.
  - `_decide_iteration` (`runner.py:1201`): set-phase override of scheduler `chosen_mode` (`runner.py:1215`); parent selection via `pf.parents_for_mode` (`runner.py:1224-1241`); `_PROMISING_DIFF_MAX_CHARS` truncation; the synthetic repair-parent fallback (`runner.py:1242-1247`).
  - `run_job` (`runner.py:2283`): `load_or_init_state`, then `if set_budget>1 and commit_results: gitops.ensure_champion_ref(config.repo_root.resolve(), state.champion_ref)` (`runner.py:2286-2288`). Evaluated-iteration budget loop.
  - `main` (`runner.py:~2377`): `--set-budget`/`--max-repairs`/`--max-refines` + the `--set-budget>1 requires --commit-results` guard.
- `harness/portfolio.py` — `lineage_advance` is pool-inert; `parents_for_mode("refine")` rotates `_ranked_pool` keyed off `global_best`/`near_best` (champion family); entries carry `harness_family_id`.
- **Phase 1.5 (prerequisite, landed first):** `commit_iteration` stages **code-only**; iteration metadata is append-only **off-git** under `runs/`; `reset` is recorded as a code checkpoint status. `runs/_summary/` is git-tracked (`.gitignore`: `runs/*` + `!runs/_summary/`).
- `tests/test_harness_runner.py` — `def _init_repo(root: Path) -> None:` (`runner test:58`). **It RETURNS `None` and creates NO `champion` ref.** It `git init`s `root`, makes `workspace/`, `baseline/target_cer.json` (with `target_cer`, `total_inference_time_s`, `guard_baseline`), `baseline/noise_floor.json` (`sigma=0.0`, `is_provisional=True`), `runs/_summary/HISTORY.md`, `.gitignore`, a seed `workspace/transcribe.py`, and an initial commit. Existing tests call it as a bare statement `_init_repo(tmp_path)` then build `RunnerConfig(..., repo_root=tmp_path)`. The injected-candidate test template is `_write_valid_meta` + `candidate_func` returning `subprocess.CompletedProcess(["fake"], 0, "", "")` + `verify_func` returning a `VerifyResult(...)`.
- `tests/test_gitops.py` — temp-repo fixture pattern (`_git` helper + `repo` fixture creating `init`/`config`/commit). Mirror for new git-touching tests.
- `pyproject.toml` — only `[tool.pytest.ini_options] testpaths=["tests"]`; **no markers registered yet.**
- `baseline/target_cer.json` — carries `baseline_cer` (the bootstrap champion's measured corpus CER, currently `0.1714…`) and `total_inference_time_s`. **This is the authoritative seed value for I3.**

**Source-of-truth docs (cite these ONLY, NOT `docs/archive/`):** `docs/HARNESS-REDESIGN.md` (§50-82 lanes/branch-strategy/rollback/commit-policy, §111-170 markers + `git archive` + branch protection, §172 timeline, §195 change-points, §217-291 script examples), `docs/HARNESS-MECHANICS.md` (§3 git lifecycle, §12 phase1 lineage-set as built), the phase1 plan, and the actual code above.

---

## Dependency on Phase 1.5 (metadata-off-git) — a prerequisite that lands FIRST

Phase 1.5 (`runs/` metadata append-only off-git; `commit_iteration` stages code-only; `reset` recorded as a code checkpoint) **is a prerequisite of this plan and lands before it.** Both touch the set `repair`/`reset` blocks (`runner.py:2106-2119` and `runner.py:2249-2254`) and the promote arm, so sequencing matters:

- **Because 1.5 has landed first**, the code-only `commit_iteration`, the metadata-off-git change, and the `reset`-as-code-checkpoint status are **already done**. This plan does **not** re-introduce metadata commits and does **not** re-do the code/metadata split.
- This plan's remaining edits to those shared blocks reduce to: **add `restore_lineage_head` for the worktree `repair` path**, keep `reset` → `restore_file_from_ref(champion)`, and **route the promote arm through the promotion lane**. Tasks 2 and 3 state this rebasing explicitly.
- Worktrees additionally give per-job `runs/` isolation (each worktree is its own working tree), so even tracked `runs/_summary/<job_id>_*` files never collide across parallel jobs.
- The promotion **splice** (Task 3) cherry-picks **only `workspace/transcribe.py`** onto `champion`, so `champion` history stays clean regardless of what the job branches carry — the splice insulates `champion` independent of 1.5.

**Decision:** build on top of Phase 1.5. The splice is what insulates `champion`; 1.5 keeps the job branches clean.

---

## Decision: the Phase-1 refine-parent wart — fix scope

**The wart (phase1 plan Open Q, confirmed in code):** in set mode, a `refine`-phase iter calls `parents_for_mode(portfolio, "refine", …)` (`runner.py:1224`) which rotates `_ranked_pool` — keyed off `global_best`/`near_best` = the **champion** family, NOT the worktree's lineage head. The *prompt parent hint* therefore shows champion-family diffs, while the on-disk file (which the candidate actually edits) IS the lineage head. So the candidate edits the right file but is shown a misleading "parent."

**Decision for Phase 2:** **fix it, minimally, in Task 2 Step 4** — once jobs run in worktrees, the lineage head is an unambiguous artifact (the worktree's HEAD commit of `transcribe.py` + the live on-disk file). When `set_phase=="refine"` and a set is active, inject the **lineage head itself** as the refine parent hint (its diff = `git diff champion..HEAD -- transcribe.py` in the job worktree), instead of a portfolio-pool rotation. This is cheap (one gitops read), is directly enabled by the worktree split (lineage head = worktree HEAD), and removes a real prompt/behavior mismatch. We do **not** pull forward the full §209 `Portfolio.job_best`/`global_best` split (that stays a later phase) — only the refine-parent hint is corrected.

---

## File Structure

| File | Responsibility | Phase 2+3 change |
|------|----------------|------------------|
| `pyproject.toml` | pytest marker registry | **add** `worktree`/`promotion` markers up front (Task 0); the broader `requires_data`/`integration` markers + default `-m "not requires_data"` are deferred |
| `harness/gitops.py` | git ref + worktree helpers | **add** `prepare_job_worktree`, `restore_lineage_head`, `cleanup_worktree`, `list_worktrees` (Task 1); **add** `read_ref`, `promote_to_champion` CAS, and champion-CER seeding (Task 3) |
| `harness/promotion.py` | **NEW** — serialized gated promotion (lock + re-validate + CAS + map) | new file (Task 3) |
| `harness/runner.py` | iteration loop / promote arm / refine parent | `repair`→lineage-head, route promote arm through `harness.promotion` under the lock, model lost-race as `beats_champion=False` re-decision (Task 3); inject lineage-head refine parent (Task 2); `main_repo_root` field + bootstrap on the main repo (Task 3) |
| `scripts/__init__.py` | **NEW (empty)** — make `scripts/` importable from tests | new file (Task 2) |
| `scripts/launch_parallel.py` | **NEW** — Popen pool launching `scripts/evolve.py` per job in its worktree | new file (Task 2) |
| `runs/_summary/promotion_map.jsonl` | append-only source-commit ↔ champion-commit ledger (+ seeded bootstrap row) | written by `harness.promotion` / seeded by `ensure_champion_ref` (Task 3) |
| `tests/test_gitops_worktree.py` | **NEW** | worktree + CAS helper tests (`worktree` marker) |
| `tests/test_promotion.py` | **NEW** | lock + re-validate + CAS + map tests (`promotion` marker) |
| `tests/test_launch_parallel.py` | **NEW** | launcher pool unit tests (no real jobs) |

**Ordering rationale (each task yields working/testable software):** register the markers the core tests use (Task 0) → worktree helpers (pure git, temp-repo testable) → single job *in a worktree* end-to-end + refine-parent fix + launcher *defined* but not yet load-bearing (Task 2) → promotion lock + re-validation + CAS + map + seeding + route the runner promote arm (Task 3); concurrent promotion is only *safe to enable* once Task 3's gate exists. Promotion gating lands **before** the launcher is used in anger.

---

## Task 0: Register the `worktree` + `promotion` pytest markers

The core tests in Tasks 1–3 tag themselves `@pytest.mark.worktree` / `@pytest.mark.promotion`. Unregistered markers raise `PytestUnknownMarkWarning`, which fails under `-W error`. Register **just these two** now; the broader `requires_data`/`integration` machinery + default skip is deferred (see the deferred section).

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the two markers to `pyproject.toml`**

`pyproject.toml` currently has only `testpaths`. Add a `markers` list:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "worktree: exercises git worktree creation/cleanup (no audio data)",
    "promotion: exercises the serialized champion promotion lock + ref CAS (no audio data)",
]
```

- [ ] **Step 2: Verify markers are registered**

Run: `python -m pytest --markers | grep -E "worktree|promotion"`
Expected: both `@pytest.mark.worktree` and `@pytest.mark.promotion` listed.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "test: register worktree/promotion pytest markers (phase2 prep)"
```

---

## Task 1: Worktree helpers in `harness/gitops.py`

Pure git operations, fully testable against a temp repo (mirror `tests/test_gitops.py`). No runner wiring yet.

**Files:**
- Modify: `harness/gitops.py` (append after `advance_champion_ref`)
- Test: `tests/test_gitops_worktree.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gitops_worktree.py`:

```python
"""tests/test_gitops_worktree.py — git worktree helpers (phase2).

Mirrors tests/test_gitops.py's temp-repo fixture pattern. No audio data.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness import gitops

pytestmark = pytest.mark.worktree


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "workspace").mkdir()
    (root / "workspace" / "transcribe.py").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c1")
    gitops.ensure_champion_ref(root, "champion")
    return root


def test_prepare_job_worktree_creates_branch_from_champion(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    # worktree exists, on its own branch, file content == champion's.
    assert (wt / "workspace" / "transcribe.py").read_text() == "v1\n"
    branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == "job/job1"
    # job branch HEAD == champion HEAD at creation.
    assert _git(wt, "rev-parse", "HEAD") == _git(repo, "rev-parse", "champion")


def test_prepare_job_worktree_idempotent(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    head1 = _git(wt, "rev-parse", "HEAD")
    # the job advances its lineage; a second prepare must NOT reset it to champion.
    (wt / "workspace" / "transcribe.py").write_text("v2\n", encoding="utf-8")
    _git(wt, "commit", "-qam", "lineage advance")
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    assert _git(wt, "rev-parse", "HEAD") != head1  # not reset to champion


def test_restore_lineage_head_reverts_to_worktree_head_not_champion(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    # advance the lineage head in the worktree (a kept in-set checkpoint).
    (wt / "workspace" / "transcribe.py").write_text("LINEAGE\n", encoding="utf-8")
    _git(wt, "commit", "-qam", "lineage")
    # candidate then dirties the file (a refine attempt being rolled back).
    (wt / "workspace" / "transcribe.py").write_text("DIRTY\n", encoding="utf-8")
    gitops.restore_lineage_head(wt, Path("workspace/transcribe.py"))
    # restored to the worktree's OWN HEAD (lineage head), NOT champion's v1.
    assert (wt / "workspace" / "transcribe.py").read_text() == "LINEAGE\n"


def test_list_worktrees_includes_job(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    paths = gitops.list_worktrees(repo)
    assert any(Path(p).resolve() == wt.resolve() for p in paths)


def test_cleanup_worktree_removes_it(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(repo, wt, "job/job1", "champion")
    gitops.cleanup_worktree(repo, wt)
    assert not wt.exists()
    paths = gitops.list_worktrees(repo)
    assert all(Path(p).resolve() != wt.resolve() for p in paths)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_gitops_worktree.py -v`
Expected: FAIL — `AttributeError: module 'harness.gitops' has no attribute 'prepare_job_worktree'`.

- [ ] **Step 3: Implement the helpers**

Append to `harness/gitops.py` (the module already imports `subprocess`, `Path`, and defines `_git`/`_ref_exists`):

```python
def prepare_job_worktree(
    repo_root: Path, worktree_path: Path, job_ref: str, champion_ref: str = "champion"
) -> None:
    """Create a linked worktree at ``worktree_path`` on branch ``job_ref`` cut
    from ``champion_ref`` (HARNESS-REDESIGN §78). Idempotent: if the worktree
    already exists it is left untouched (a resumed job keeps its advanced lineage
    head — we must NOT reset it to champion). The job worktree is where the
    lineage head lives; ``champion`` is never checked out by a runner."""
    if worktree_path.exists():
        return
    branch = job_ref.removeprefix("refs/heads/")
    args = ["worktree", "add", worktree_path.as_posix()]
    if _ref_exists(repo_root, branch):
        # branch already exists (prior run) but its worktree was pruned → re-link
        # at the existing branch tip (the prior lineage head), not at champion.
        args += [branch]
    else:
        args += ["-b", branch, champion_ref]
    _git(repo_root, args)


def restore_lineage_head(repo_root: Path, rel_path: Path) -> None:
    """Restore one file to the worktree's OWN HEAD (the lineage head), NOT to
    champion (HARNESS-REDESIGN §78: repair/refine rollback targets the job-local
    lineage head). ``repo_root`` here is the JOB WORKTREE path."""
    _git(repo_root, ["restore", "--source", "HEAD", "--", rel_path.as_posix()])


def list_worktrees(repo_root: Path) -> list[str]:
    """Return the filesystem paths of all linked worktrees (porcelain parse)."""
    out = _git(repo_root, ["worktree", "list", "--porcelain"]).stdout
    return [line[len("worktree "):] for line in out.splitlines()
            if line.startswith("worktree ")]


def cleanup_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Remove a job worktree (HARNESS-REDESIGN §78 cleanup). ``--force`` because a
    job may leave the workspace file dirty (a half-applied candidate); the branch
    is preserved so a future run can re-link and resume the lineage."""
    if not worktree_path.exists():
        return
    _git(repo_root, ["worktree", "remove", "--force", worktree_path.as_posix()],
         check=False)
    _git(repo_root, ["worktree", "prune"], check=False)
```

Note on `restore_lineage_head` vs Phase-1 `restore_file_from_ref`: Phase 1's single-lane reset restored from `champion`. In the worktree model, `repair` rollback must go to the **worktree's HEAD** (lineage head), while `reset` (set closed) still goes to `champion`. `restore_lineage_head` is the `repair` target; the existing `restore_file_from_ref(wt, champion_ref, path)` remains the `reset` target. Task 2 wires this.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_gitops_worktree.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/gitops.py tests/test_gitops_worktree.py
git commit -m "feat(gitops): worktree helpers prepare/restore-lineage/list/cleanup (phase2)"
```

---

## Task 2: Run a single job INSIDE a worktree (no parallelism yet)

Prove the runner works unchanged when `config.repo_root` is a job worktree; make `repair` (→ lineage head) explicit while keeping `reset` (→ champion); fix the refine-parent wart; define the parallel launcher (not yet load-bearing — its concurrency is only safe after Task 3's gate exists).

**Rebasing note (Phase 1.5 landed first):** the `repair`/`reset` blocks below are *already* code-only commits with `reset` recorded as a code checkpoint. This task's change to them is narrow: swap the `repair` working-tree restore from "restore to HEAD via `rollback_paths`" to the **named** `restore_lineage_head`, and keep `reset` → `restore_file_from_ref(champion)`. Do NOT re-introduce metadata commits.

**Files:**
- Modify: `harness/runner.py` (set-path verify-fail block + scored `repair`/`reset` block; `_decide_iteration` refine parent)
- Create: `scripts/__init__.py`, `scripts/launch_parallel.py`
- Test: `tests/test_harness_runner.py` (worktree integration + refine-parent unit), `tests/test_launch_parallel.py`

- [ ] **Step 1: Write the failing integration test**

Read `tests/test_harness_runner.py`'s `_init_repo` + the injected `candidate_func`/`verify_func` pattern first (the set-path tests around `runner.py:2214` are the template; `_write_valid_meta` writes `claude_stdout.txt`, `candidate_func` returns `subprocess.CompletedProcess(["fake"], 0, "", "")`, `verify_func` returns a `VerifyResult`). **`_init_repo` returns `None` and creates NO `champion`** — call it for side effects, then create `champion` on `tmp_path`. Add a module-level `_git` helper if the file lacks one.

```python
@pytest.mark.worktree
def test_job_runs_in_worktree_and_repair_targets_lineage_head(tmp_path):
    """A job in a worktree: a kept lineage advance becomes the worktree HEAD; a
    later verify-fail repair rolls back to THAT head, not champion (phase2)."""
    import subprocess
    from harness import gitops, runner
    from harness.runner import RunnerConfig
    from harness.state import HarnessState
    from harness.verify import VerifyResult

    def _git(root, *a):
        return subprocess.run(["git", *a], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    _init_repo(tmp_path)                              # side effects only (returns None)
    gitops.ensure_champion_ref(tmp_path, "champion")  # champion @ the init commit
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(tmp_path, wt, "job/job1", "champion")

    cfg_ = RunnerConfig(job_id="job1", repo_root=wt, set_budget=4, max_repairs=2,
                        max_refines=3, commit_results=True)
    state = HarnessState(job_id="job1", best_cer=0.20, best_hyp_id="champ")
    state_path = wt / "runs/_summary/job1_state.json"

    # iter1: explore worse-than-champion (0.30 > 0.20) but valid → lineage seed
    # → advance; lineage head moves to a new worktree commit.
    def cand_ok(prompt, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "claude_stdout.txt").write_text(_VALID_META_STDOUT, encoding="utf-8")
        (wt / "workspace/transcribe.py").write_text(
            "def transcribe(a, sr):\n    return 'LINEAGE'\n", encoding="utf-8")
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verify_ok(_hyp):
        return VerifyResult(ok=True, report={"corpus_cer": 0.30,
                            "total_inference_time_s": 90.0}, error=None)

    runner.run_iteration(cfg_, state, state_path, candidate_func=cand_ok,
                         verify_func=verify_ok)
    assert state.set_phase == "refine"
    assert "LINEAGE" in (wt / "workspace/transcribe.py").read_text()
    lineage_head = _git(wt, "rev-parse", "HEAD")

    # iter2: a refine that breaks verify → repair rolls back to the lineage head
    # (its committed content), NOT champion's seed.
    def cand_break(prompt, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "claude_stdout.txt").write_text(_VALID_META_STDOUT, encoding="utf-8")
        (wt / "workspace/transcribe.py").write_text("BROKEN\n", encoding="utf-8")
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verify_fail(_hyp):
        return VerifyResult(ok=False, report=None, error="boom")

    runner.run_iteration(cfg_, state, state_path, candidate_func=cand_break,
                         verify_func=verify_fail)
    assert "LINEAGE" in (wt / "workspace/transcribe.py").read_text()   # lineage head, not seed
    assert _git(wt, "rev-parse", "HEAD") == lineage_head               # head unchanged
```

(Adjust `VerifyResult(...)` kwargs to the file's actual `VerifyResult` shape — it is imported in `test_harness_runner.py` already; copy a working construction from an existing set-path test.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_harness_runner.py::test_job_runs_in_worktree_and_repair_targets_lineage_head -v`
Expected: this may PASS partially today — `rollback_paths` (`runner.py:356`) does `git restore -- <tracked>` (restores to HEAD == lineage head in a worktree) + `git clean -fd`, so `repair` already targets the worktree HEAD *by accident of HEAD==lineage-head*. The change below makes `repair` use the **named** `restore_lineage_head` for clarity + correctness, and keeps `reset` → champion explicit. If the test passes before the change, that confirms behavior; still apply Step 3 so the intent is explicit and a future metadata-off-git refactor can't silently change the repair target. Then re-run to confirm it stays green.

- [ ] **Step 3: Make repair→lineage-head explicit, keep reset→champion (set path)**

In `run_iteration`, **verify-fail set block** (`runner.py:2106-2119`), replace the unconditional `rollback_paths` + reset-only champion restore with an action-split:

```python
            t = step_set(s, outcome, SetBudget(config.max_repairs, config.max_refines))
            if t.action == "reset":
                # set closed → drop the whole lineage back to the protected champion.
                rollback_paths(repo_root, candidate_owned_statuses(git_status(repo_root), config))
                gitops.restore_file_from_ref(repo_root, state.champion_ref, config.allowed_path)
            else:  # "repair": keep the set alive, drop only the failed candidate
                gitops.restore_lineage_head(repo_root, config.allowed_path)
                # clean any stray untracked files the candidate left behind.
                rollback_paths(repo_root, [st for st in candidate_owned_statuses(
                    git_status(repo_root), config) if st.untracked])
            _persist_set_state(state, t.state)
```

And in the **scored set path** `repair`/`reset` block (`runner.py:2249-2254`):

```python
    else:  # "repair" or "reset"
        decision_status = "reject"
        reason = lin.reason
        if t.action == "reset":
            rollback_paths(repo_root, candidate_owned_statuses(git_status(repo_root), config))
            gitops.restore_file_from_ref(repo_root, state.champion_ref, config.allowed_path)
        else:  # "repair"
            gitops.restore_lineage_head(repo_root, config.allowed_path)
            rollback_paths(repo_root, [st for st in candidate_owned_statuses(
                git_status(repo_root), config) if st.untracked])
```

`gitops` is already imported in both blocks (`from harness import gitops` at `runner.py:2111` and `runner.py:2218`). `GitPathStatus.untracked` is a real property (`runner.py:277-278`).

- [ ] **Step 4: Fix the refine-parent wart (inject the lineage head)**

In `_decide_iteration` (`runner.py:1223`, after the set-phase override and where `parents` is built at `runner.py:1224`), when a set is active in `refine`, replace the portfolio-pool refine parent with the lineage head:

```python
    parents: list[dict[str, Any]] = []
    if (config.set_budget > 1 and state.set_phase == "refine"
            and state.set_best_hyp_id):
        # Refine parent = the lineage head itself (this worktree's HEAD), NOT a
        # champion-family portfolio rotation (phase1 wart: parents_for_mode
        # ["refine"] keys off global_best/near_best). The on-disk file already IS
        # the lineage head; show its champion-delta as the parent diff so the
        # prompt hint matches what the candidate actually edits.
        diff = _run_git(config.repo_root,
                        ["diff", f"{state.champion_ref}..HEAD", "--",
                         config.allowed_path.as_posix()], check=False).stdout
        parents.append({
            "hyp_id": state.set_best_hyp_id,
            "cer": state.set_best_cer,
            "mode": "refine",
            "diff": diff[:_PROMISING_DIFF_MAX_CHARS] or "(lineage head == champion)",
            "harness_family_id": "lineage",
        })
    else:
        for e in pf.parents_for_mode(
            portfolio, sched.chosen_mode, evaluated_index=state.evaluated_count + 1
        ):
            entry = dict(e)
            diff_rel = entry.get("diff_path")
            diff_text = ""
            if diff_rel:
                dp = config.repo_root / diff_rel
                if dp.is_file():
                    diff_text = dp.read_text(encoding="utf-8")[:_PROMISING_DIFF_MAX_CHARS]
            entry["diff"] = diff_text or "(diff unavailable)"
            parents.append(entry)
        # repair-with-no-portfolio-parent fallback (unchanged from phase1).
        if sched.chosen_mode == "repair" and not parents:
            fail_parent = _last_failure_parent(config)
            if fail_parent:
                parents.append(fail_parent)
```

Add a unit test (note the corrected `_init_repo` pattern):

```python
def test_refine_parent_is_lineage_head_not_portfolio(tmp_path):
    from harness import gitops
    from harness.runner import RunnerConfig, _decide_iteration
    from harness.state import HarnessState
    _init_repo(tmp_path)                              # side effects only
    gitops.ensure_champion_ref(tmp_path, "champion")
    cfg_ = RunnerConfig(job_id="j", repo_root=tmp_path, set_budget=4)
    st = HarnessState(job_id="j", set_phase="refine", set_best_hyp_id="h2",
                      set_best_cer=0.18, evaluated_count=20, best_cer=0.2,
                      best_hyp_id="champ")
    sched, parents = _decide_iteration(cfg_, st)
    assert sched.chosen_mode == "refine"
    assert len(parents) == 1 and parents[0]["hyp_id"] == "h2"
    assert parents[0]["harness_family_id"] == "lineage"
```

- [ ] **Step 5: Define the parallel launcher**

Create an empty `scripts/__init__.py` so `from scripts import launch_parallel` works under pytest (`scripts/` has no `__init__.py` today). Then create `scripts/launch_parallel.py` — a `subprocess.Popen` pool that prepares one worktree per job and runs `scripts/evolve.py` inside it, with a concurrency cap (default 2; CT2-turbo ~1.5GB/job on a 24GB GPU — the binding limit is GPU-compute contention, not memory — HARNESS-REDESIGN §80). The promotion gate (Task 3) is what makes concurrent promotion safe; this launcher is added now but only safe to *run concurrently* once Task 3 lands.

```python
#!/usr/bin/env python3
"""scripts/launch_parallel.py — run N evolution jobs in parallel, each in its
own git worktree cut from the protected champion (HARNESS-REDESIGN §50/§78).

Concurrency cap defaults to 2: CT2 turbo is ~1.5GB/job (24GB GPU fits many) but
GPU-compute contention is the real limit. Promotion to champion is serialized by
the lock in harness.promotion, so concurrent jobs are safe.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import gitops  # noqa: E402


def build_job_cmds(repo_root: Path, worktree_root: Path, job_ids: list[str],
                   iters: int, candidate_cmd: str, set_budget: int,
                   champion_ref: str = "champion") -> list[tuple[str, Path, list[str]]]:
    """Pure-ish: prepare worktrees and return (job_id, worktree, argv) tuples.
    Split out so it is unit-testable without launching processes."""
    jobs: list[tuple[str, Path, list[str]]] = []
    for jid in job_ids:
        wt = worktree_root / f"wt-{jid}"
        gitops.prepare_job_worktree(repo_root, wt, f"job/{jid}", champion_ref)
        argv = [sys.executable, "scripts/evolve.py", "--job-id", jid,
                "--iters", str(iters), "--candidate-cmd", candidate_cmd,
                "--commit-results", "--set-budget", str(set_budget)]
        jobs.append((jid, wt, argv))
    return jobs


def run_pool(jobs: list[tuple[str, Path, list[str]]], cap: int,
             poll_s: float = 2.0) -> dict[str, int]:
    """Run jobs with at most `cap` concurrent Popen, each cwd=its worktree.
    Returns {job_id: returncode}."""
    pending = list(jobs)
    running: dict[str, subprocess.Popen] = {}
    rc: dict[str, int] = {}
    while pending or running:
        while pending and len(running) < cap:
            jid, wt, argv = pending.pop(0)
            running[jid] = subprocess.Popen(argv, cwd=wt)
        for jid, proc in list(running.items()):
            if proc.poll() is not None:
                rc[jid] = proc.returncode
                del running[jid]
        if running:
            time.sleep(poll_s)
    return rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run N evolution jobs in parallel worktrees.")
    p.add_argument("--job-ids", required=True, help="comma-separated job ids")
    p.add_argument("--iters", type=int, required=True)
    p.add_argument("--candidate-cmd", required=True)
    p.add_argument("--set-budget", type=int, default=4)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--repo-root", type=Path, default=ROOT)
    p.add_argument("--worktree-root", type=Path, default=ROOT.parent)
    p.add_argument("--champion-ref", default="champion")
    args = p.parse_args(argv)
    gitops.ensure_champion_ref(args.repo_root.resolve(), args.champion_ref)
    jobs = build_job_cmds(args.repo_root.resolve(), args.worktree_root.resolve(),
                          [j.strip() for j in args.job_ids.split(",") if j.strip()],
                          args.iters, args.candidate_cmd, args.set_budget,
                          args.champion_ref)
    rc = run_pool(jobs, cap=args.concurrency)
    print({jid: code for jid, code in rc.items()})
    return 0 if all(c == 0 for c in rc.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Test `build_job_cmds` + `run_pool` without real jobs — create `tests/test_launch_parallel.py`:

```python
"""tests/test_launch_parallel.py — launcher pool unit tests (no real jobs)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.worktree
def test_build_job_cmds_creates_one_worktree_per_job(tmp_path):
    from scripts import launch_parallel as lp
    from harness import gitops
    main = tmp_path / "main"
    main.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=main, check=True, capture_output=True, text=True)
    (main / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=main, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=main, check=True, capture_output=True, text=True)
    gitops.ensure_champion_ref(main, "champion")
    jobs = lp.build_job_cmds(main, tmp_path / "wts", ["a", "b"], 5, "claude -p", 4)
    assert len(jobs) == 2
    assert all(wt.exists() for _, wt, _ in jobs)
    assert jobs[0][2][:3] == [sys.executable, "scripts/evolve.py", "--job-id"]


def test_run_pool_respects_cap(monkeypatch):
    from scripts import launch_parallel as lp
    peak = {"n": 0}
    live = {"n": 0}

    class FakeProc:
        def __init__(self):
            self.n = 0
            self.returncode = 0
            live["n"] += 1
            peak["n"] = max(peak["n"], live["n"])

        def poll(self):
            self.n += 1
            if self.n >= 2:
                live["n"] -= 1
                return 0
            return None

    monkeypatch.setattr(lp.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(lp.time, "sleep", lambda *_: None)
    jobs = [(f"j{i}", Path("."), ["x"]) for i in range(5)]
    rc = lp.run_pool(jobs, cap=2, poll_s=0)
    assert peak["n"] <= 2 and len(rc) == 5 and all(c == 0 for c in rc.values())
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_harness_runner.py tests/test_launch_parallel.py -v`
Expected: PASS (legacy single-shot tests unaffected — default `set_budget=1`, `repo_root=tmp_path`).

- [ ] **Step 7: Commit**

```bash
git add harness/runner.py scripts/launch_parallel.py scripts/__init__.py \
        tests/test_harness_runner.py tests/test_launch_parallel.py
git commit -m "feat(runner,launcher): job-in-worktree repair→lineage-head, refine parent=lineage head, parallel launcher (phase2)"
```

---

## Task 3: Serialized gated promotion — lock + re-validate + ref CAS + map + champion-CER seeding

The heart of Phase 3. Replaces the unguarded promote arm (`runner.py:2236-2243` decision + `runner.py:2277-2279` advance) with a serialized gate so concurrent jobs cannot race the single `champion` (HARNESS-REDESIGN §52 promotion lane, §82 splice-one-verified-commit).

**Design (local, solo operator — file lock, NOT GitHub branch protection):**
1. **One promoter at a time** — `fcntl.flock(LOCK_EX)` on `<main_repo>/.git/champion_promote.lock`. flock auto-releases on process death (kill/crash), so a dead promoter never wedges the lane.
2. **Seed the live champion CER (I3 fix)** — at bootstrap there are no promotions yet, so the map is empty and `live_champion_cer` would return `None`, which makes `decide_promotion(champion_cer=None)` treat the FIRST candidate as an automatic win even when `champion` already encodes a better baseline CER. We therefore **seed `promotion_map.jsonl` with the bootstrap champion's recorded CER** at `ensure_champion_ref` time (read from `baseline/target_cer.json:baseline_cer`). So re-validation always has a real number.
3. **Re-validate against the LIVE champion CER under the lock** — between a job deciding "I beat champion 0.154" and acquiring the lock, another job may have already promoted to 0.150. Re-read the current champion CER (lowest `cer` in `promotion_map.jsonl`, which now always has at least the seed row) and re-run `decide_promotion(champion_cer=live)`. If it no longer beats → **lost race**.
4. **Ref CAS** — `git update-ref refs/heads/champion <new_commit> <old_expected>`. update-ref's old-value guard fails atomically if champion moved since we read it (belt-and-suspenders with the lock). On CAS failure → lost race.
5. **Splice only `transcribe.py`** — build the new champion commit off champion's tree with ONLY the candidate's `workspace/transcribe.py` overlaid (via a temp index — no worktree needed since champion is never checked out), keeping `champion` linear + free of job metadata.
6. **Record** `runs/_summary/promotion_map.jsonl`: `{job_id, source_commit, champion_commit, cer, ts}`.
7. **Lost-race handling lives in the RUNNER, modeled at the `step_set` INPUT (C2 fix)** — when the gate returns LOST, the runner does NOT mutate the closed `Transition`. Instead it re-derives the `Outcome` with `beats_champion=False` and calls `step_set` again. The candidate is verify-OK and may still improve the lineage, so `step_set`'s normal `advance`/`hold` refine logic keeps it as the lineage head and the set continues. No `Transition` mutation, no `_reopen_as_refine` helper.

**Files:**
- Modify: `harness/gitops.py` (add `read_ref`, `promote_to_champion`, champion-CER seeding helper)
- Create: `harness/promotion.py` (lock + re-validate orchestration + map + seed)
- Modify: `harness/runner.py` (`main_repo_root` field, `_main_repo_root` helper, bootstrap on main repo, route promote arm through the gate, model lost-race via `beats_champion=False`)
- Test: `tests/test_promotion.py`, plus gitops CAS tests in `tests/test_gitops_worktree.py`

- [ ] **Step 1: gitops — `read_ref` + `promote_to_champion` (CAS splice)**

Append to `harness/gitops.py`:

```python
def read_ref(repo_root: Path, ref: str) -> str | None:
    """Resolve ``ref`` to a commit sha, or None if it does not exist."""
    r = _git(repo_root, ["rev-parse", "--verify", "--quiet", ref], check=False)
    sha = r.stdout.strip()
    return sha or None


def promote_to_champion(
    repo_root: Path, champion_ref: str, source_commit: str,
    rel_path: Path, expected_old: str | None, message: str,
) -> str | None:
    """Splice ONLY ``rel_path`` from ``source_commit`` onto ``champion_ref`` as a
    new linear commit, then CAS-advance the ref (HARNESS-REDESIGN §82).

    Returns the new champion commit sha on success, or None if the compare-and-
    swap lost (champion moved since ``expected_old`` was read → caller treats as
    a lost race and keeps the candidate as its lineage head).

    Implemented with a detached temp index off champion so no worktree is needed
    (champion is never checked out): read champion's tree, overlay the file blob
    from source_commit, write-tree, commit-tree with champion as parent, then
    ``git update-ref <ref> <new> <expected_old>`` (atomic old-value guard).
    """
    import os
    import tempfile

    champ = read_ref(repo_root, f"refs/heads/{champion_ref}")
    if champ is None:
        return None
    if expected_old is not None and champ != expected_old:
        return None  # already moved before we even started
    # blob of rel_path at source_commit
    blob = _git(repo_root, ["rev-parse", f"{source_commit}:{rel_path.as_posix()}"]).stdout.strip()
    # build a tree = champion's tree with rel_path replaced by blob, via a temp index
    with tempfile.NamedTemporaryFile(prefix="champ_idx_", delete=False) as tf:
        idx = tf.name
    try:
        env = {**os.environ, "GIT_INDEX_FILE": idx}
        subprocess.run(["git", "read-tree", champ], cwd=repo_root, env=env,
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                        f"100644,{blob},{rel_path.as_posix()}"],
                       cwd=repo_root, env=env, check=True, capture_output=True, text=True)
        tree = subprocess.run(["git", "write-tree"], cwd=repo_root, env=env,
                              check=True, capture_output=True, text=True).stdout.strip()
    finally:
        os.unlink(idx)
    new = _git(repo_root, ["commit-tree", tree, "-p", champ, "-m", message]).stdout.strip()
    # atomic CAS: fails (nonzero) if champion moved since `expected_old`.
    cas_old = expected_old or champ
    r = _git(repo_root, ["update-ref", f"refs/heads/{champion_ref}", new, cas_old],
             check=False)
    return new if r.returncode == 0 else None
```

Add gitops CAS tests to `tests/test_gitops_worktree.py` (the `repo` fixture already creates `champion`):

```python
def _git_rc(root: Path, *args: str) -> int:
    return subprocess.run(["git", *args], cwd=root,
                          capture_output=True, text=True).returncode


def test_promote_to_champion_splices_only_file_and_cas(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt"
    gitops.prepare_job_worktree(repo, wt, "job/j", "champion")
    (wt / "workspace" / "transcribe.py").write_text("WINNER\n", encoding="utf-8")
    (wt / "noise.txt").write_text("metadata junk\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "lineage + junk")
    src = _git(wt, "rev-parse", "HEAD")
    old = gitops.read_ref(repo, "refs/heads/champion")
    new = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=old, message="promote j")
    assert new is not None and new != old
    assert gitops.read_ref(repo, "refs/heads/champion") == new
    # champion got the file but NOT the junk → linear, metadata-free.
    assert _git(repo, "show", "champion:workspace/transcribe.py") == "WINNER"
    assert _git_rc(repo, "cat-file", "-e", "champion:noise.txt") != 0


def test_promote_to_champion_cas_loses_when_champion_moved(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt"
    gitops.prepare_job_worktree(repo, wt, "job/j", "champion")
    (wt / "workspace" / "transcribe.py").write_text("A\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "a")
    src = _git(wt, "rev-parse", "HEAD")
    stale_old = gitops.read_ref(repo, "refs/heads/champion")
    # someone else promotes first (advances champion off stale_old).
    won = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=stale_old, message="b1")
    assert won is not None
    # our promote with the now-stale expected_old must LOSE (return None).
    lost = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=stale_old, message="b2")
    assert lost is None
```

- [ ] **Step 2: `harness/promotion.py` — lock + seed + re-validate orchestration**

Create `harness/promotion.py`:

```python
"""harness/promotion.py
Serialized, gated promotion to the protected champion (HARNESS-REDESIGN §52/§82).

Only one promoter at a time (fcntl.flock, auto-released on death). Under the lock
we RE-VALIDATE against the LIVE champion CER (a peer job may have promoted lower
while we queued — the A-vs-B race), then CAS-advance champion splicing only
transcribe.py. A lost race returns LOST; the runner then re-decides the set step
with beats_champion=False so the candidate is kept as the lineage head (NOT
discarded). NOT GitHub branch protection — the operator is solo + local, so the
file lock is the real mechanism.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from harness import gitops
from harness.policy import PolicyConfig, decide_promotion

PROMOTE = "promoted"
LOST = "lost_race"


@dataclass(frozen=True)
class PromotionResult:
    status: str                 # "promoted" | "lost_race"
    champion_commit: str | None
    live_champion_cer: float | None
    reason: str


def _lock_path(repo_root: Path) -> Path:
    return repo_root / ".git" / "champion_promote.lock"


def _map_path(repo_root: Path, summary_dir: Path) -> Path:
    return repo_root / summary_dir / "promotion_map.jsonl"


def seed_champion_cer(repo_root: Path, summary_dir: Path, baseline_cer: float,
                      champion_commit: str | None = None) -> None:
    """I3 fix: write a bootstrap row into promotion_map.jsonl so live_champion_cer
    never returns None (which decide_promotion treats as "first candidate always
    wins"). Idempotent: a no-op if the map already has any row. ``baseline_cer`` is
    the measured CER of the bootstrap champion (baseline/target_cer.json:baseline_cer)."""
    p = _map_path(repo_root, summary_dir)
    if p.is_file() and any(line.strip() for line in p.read_text(encoding="utf-8").splitlines()):
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"job_id": "__bootstrap__", "source_commit": champion_commit,
           "champion_commit": champion_commit, "cer": float(baseline_cer),
           "ts": time.time()}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def live_champion_cer(repo_root: Path, summary_dir: Path) -> float | None:
    """Lowest CER recorded so far (promotion_map.jsonl, incl. the bootstrap seed
    row). None only if the map is genuinely empty AND was never seeded — in normal
    operation seed_champion_cer guarantees at least one row."""
    p = _map_path(repo_root, summary_dir)
    if not p.is_file():
        return None
    best: float | None = None
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cer = json.loads(line).get("cer")
        if isinstance(cer, (int, float)) and (best is None or cer < best):
            best = cer
    return best


def try_promote(
    repo_root: Path, *, job_id: str, source_commit: str, rel_path: Path,
    candidate_report: dict, baseline: dict, sigma: float | None,
    sigma_is_provisional: bool, champion_ref: str = "champion",
    summary_dir: Path = Path("runs/_summary"),
    absolute_delta_fallback: float | None = None,
) -> PromotionResult:
    """Acquire the promotion lock, re-validate vs the LIVE champion, CAS-advance,
    record the map. ``repo_root`` is the SHARED main repo (where champion lives),
    NOT the job worktree."""
    lock = _lock_path(repo_root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)             # serialize; auto-released on death
        live = live_champion_cer(repo_root, summary_dir)
        pcfg = (PolicyConfig(absolute_delta_fallback=absolute_delta_fallback)
                if absolute_delta_fallback is not None else None)
        revalidate = decide_promotion(
            report=candidate_report, baseline=baseline, champion_cer=live,
            sigma=sigma, sigma_is_provisional=sigma_is_provisional, config=pcfg,
        )
        if revalidate.status not in ("keep", "success"):
            return PromotionResult(LOST, None, live,
                                   f"lost race: live champion {live} not beaten")
        expected_old = gitops.read_ref(repo_root, f"refs/heads/{champion_ref}")
        new = gitops.promote_to_champion(
            repo_root, champion_ref, source_commit, rel_path,
            expected_old=expected_old,
            message=f"promote {job_id} {source_commit[:8]} cer={revalidate.candidate_cer:.6f}",
        )
        if new is None:
            return PromotionResult(LOST, None, live, "lost race: champion CAS failed")
        rec = {"job_id": job_id, "source_commit": source_commit,
               "champion_commit": new, "cer": revalidate.candidate_cer,
               "ts": time.time()}
        mp = _map_path(repo_root, summary_dir)
        mp.parent.mkdir(parents=True, exist_ok=True)
        with mp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return PromotionResult(PROMOTE, new, revalidate.candidate_cer, "promoted")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
```

- [ ] **Step 3: Write the promotion tests**

Create `tests/test_promotion.py` (`promotion` marker). Cover: (a) clean promote records the map + advances champion; (b) **lost race by re-validation** — pre-seed `promotion_map.jsonl` with a lower live CER so re-validate returns LOST and champion does NOT move; (c) **lock serializes** — two threads calling `try_promote` don't interleave (every map line is valid JSON — no torn write — and at least one promotes); (d) **seed_champion_cer** makes the first real promote compare against the baseline; (e) lost CAS path is already covered in `test_gitops_worktree.py`. Mirror the `repo` fixture from `test_gitops_worktree.py`.

```python
"""tests/test_promotion.py — serialized gated promotion (phase3). No audio data."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from harness import gitops, promotion

pytestmark = pytest.mark.promotion


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "workspace").mkdir()
    (root / "workspace" / "transcribe.py").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c1")
    gitops.ensure_champion_ref(root, "champion")
    return root


def _baseline() -> dict:
    # target_cer high enough that "success" never triggers → tests exercise keep/reject.
    return {"target_cer": 0.0, "total_inference_time_s": 100.0}


def _rep(cer: float) -> dict:
    return {"corpus_cer": cer, "total_inference_time_s": 90.0}


def _commit_candidate(repo: Path, tmp_path: Path, name: str, body: str) -> str:
    wt = tmp_path / f"wt-{name}"
    gitops.prepare_job_worktree(repo, wt, f"job/{name}", "champion")
    (wt / "workspace" / "transcribe.py").write_text(body, encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", f"cand {name}")
    return _git(wt, "rev-parse", "HEAD")


def test_clean_promote_advances_champion_and_records_map(repo: Path, tmp_path) -> None:
    src = _commit_candidate(repo, tmp_path, "j", "WIN\n")
    res = promotion.try_promote(
        repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.12),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.PROMOTE
    assert gitops.read_ref(repo, "refs/heads/champion") == res.champion_commit
    rows = [l for l in (repo / "runs/_summary/promotion_map.jsonl").read_text().splitlines() if l.strip()]
    assert json.loads(rows[-1])["job_id"] == "j"
    assert _git(repo, "show", "champion:workspace/transcribe.py") == "WIN"


def test_lost_race_when_live_champion_already_lower(repo: Path, tmp_path) -> None:
    # pre-seed a live champion at 0.10; our 0.12 candidate must LOSE re-validation.
    mp = repo / "runs/_summary/promotion_map.jsonl"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"job_id": "x", "cer": 0.10, "champion_commit": "deadbeef"}) + "\n",
                  encoding="utf-8")
    before = gitops.read_ref(repo, "refs/heads/champion")
    src = _commit_candidate(repo, tmp_path, "j", "LATE\n")
    res = promotion.try_promote(
        repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.12),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.LOST
    assert gitops.read_ref(repo, "refs/heads/champion") == before   # unmoved


def test_seed_makes_first_real_promote_compare_against_baseline(repo: Path, tmp_path) -> None:
    # seed the bootstrap champion CER at 0.15; a 0.16 candidate must NOT beat it
    # (without the seed, an empty map would let it auto-win — the I3 defect).
    promotion.seed_champion_cer(repo, Path("runs/_summary"), baseline_cer=0.15)
    src = _commit_candidate(repo, tmp_path, "j", "WORSE\n")
    res = promotion.try_promote(
        repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.16),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.LOST
    # idempotent: seeding again does not add a second bootstrap row.
    promotion.seed_champion_cer(repo, Path("runs/_summary"), baseline_cer=0.99)
    rows = [l for l in (repo / "runs/_summary/promotion_map.jsonl").read_text().splitlines() if l.strip()]
    assert sum(1 for r in rows if json.loads(r)["job_id"] == "__bootstrap__") == 1


def test_lock_serializes_concurrent_promoters(repo: Path, tmp_path) -> None:
    # two threads, each promoting a distinct improving candidate; assert every map
    # line is valid JSON (no torn write) and at least one promotes.
    src_a = _commit_candidate(repo, tmp_path, "a", "AAA\n")
    src_b = _commit_candidate(repo, tmp_path, "b", "BBB\n")
    results: list[promotion.PromotionResult] = []
    lock = threading.Lock()

    def go(jid: str, cer: float, src: str) -> None:
        r = promotion.try_promote(
            repo, job_id=jid, source_commit=src,
            rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(cer),
            baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
        with lock:
            results.append(r)

    ts = [threading.Thread(target=go, args=a) for a in (("a", 0.13, src_a), ("b", 0.11, src_b))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows = [l for l in (repo / "runs/_summary/promotion_map.jsonl").read_text().splitlines() if l.strip()]
    assert all(json.loads(r) for r in rows)             # every line parses → no tear
    assert promotion.PROMOTE in {r.status for r in results}
```

- [ ] **Step 4: Run the gitops CAS + promotion tests**

Run: `python -m pytest tests/test_gitops_worktree.py tests/test_promotion.py -v`
Expected: PASS.

- [ ] **Step 5: Route the runner promote arm through `harness.promotion` (incl. C2 lost-race)**

Add `main_repo_root: Path | None = None` to `RunnerConfig` (frozen → constructor arg; default None = single-lane where main repo == repo_root). Add a `_main_repo_root` helper near `_set_state_from`:

```python
def _main_repo_root(config: RunnerConfig) -> Path:
    """The SHARED repo where `champion` lives. In a worktree, derive it from
    `git rev-parse --git-common-dir`; in single-lane it is just repo_root."""
    if config.main_repo_root is not None:
        return config.main_repo_root.resolve()
    common = _run_git(config.repo_root, ["rev-parse", "--git-common-dir"],
                      check=False).stdout.strip()
    if common:
        p = (config.repo_root / common).resolve()
        return p.parent if p.name == ".git" else p
    return config.repo_root.resolve()
```

In `run_iteration`'s scored set path, the `promote` action is decided at `runner.py:2236-2243` and the actual champion advance happens at `runner.py:2277-2279`. **Restructure** so the promote arm runs the gate *after* `state.save` + the candidate commit (the gate needs a `source_commit`), and a LOST race re-decides `step_set` with `beats_champion=False`:

Replace the `if t.action == "promote":` decision block (`runner.py:2236-2243`) so it only sets a provisional status, and replace the post-save `if t.action == "promote": advance_champion_ref(...)` block (`runner.py:2277-2279`) with the gated lane. Concretely, the post-save tail becomes:

```python
    state.save(state_path)
    if config.commit_results:
        commit_iteration(config, state_path, decision_status, hyp_id,
                         state.iteration, reason=reason)
        if t.action == "promote":
            from harness import promotion as promo_mod
            source_commit = _run_git(repo_root, ["rev-parse", "HEAD"]).stdout.strip()
            main_repo = _main_repo_root(config)
            pres = promo_mod.try_promote(
                main_repo, job_id=config.job_id, source_commit=source_commit,
                rel_path=config.allowed_path, candidate_report=verify_result.report,
                baseline=baseline, sigma=noise.get("sigma"),
                sigma_is_provisional=bool(noise.get("is_provisional")),
                champion_ref=state.champion_ref, summary_dir=config.summary_dir,
                absolute_delta_fallback=config.absolute_delta_fallback,
            )
            if pres.status == promo_mod.PROMOTE:
                # champion advanced by the gate; record_best already done in the
                # promote-decision block below. Mark success if the policy said so.
                pass
            else:
                # LOST race (C2 fix): do NOT mutate the closed Transition. Re-decide
                # the set step with beats_champion=False — the candidate is verify-OK
                # and may still improve the lineage, so step_set keeps it as the
                # lineage head (advance/hold) and the set continues. The candidate
                # stays committed on the job branch (it is the new lineage head).
                outcome2 = Outcome(verify_ok=True, lineage_status=lin.status,
                                   cer=cand_cer, beats_champion=False, hyp_id=hyp_id)
                t2 = step_set(s, outcome2,
                              SetBudget(config.max_repairs, config.max_refines))
                _persist_set_state(state, t2.state)
                if t2.action in ("repair", "reset"):
                    # the no-longer-champion-beating candidate is not even a local
                    # gain → roll back to lineage head (repair) / champion (reset).
                    if t2.action == "reset":
                        rollback_paths(repo_root, candidate_owned_statuses(
                            git_status(repo_root), config))
                        gitops.restore_file_from_ref(
                            repo_root, state.champion_ref, config.allowed_path)
                    else:
                        gitops.restore_lineage_head(repo_root, config.allowed_path)
                        rollback_paths(repo_root, [st for st in candidate_owned_statuses(
                            git_status(repo_root), config) if st.untracked])
                state.save(state_path)
    return result
```

And in the **promote-decision block** (`runner.py:2236-2243`), keep `record_best` but make the champion-ref advance no longer happen here (it now happens inside `try_promote`'s CAS):

```python
    if t.action == "promote":
        # provisionally a champion beat → record_best now; the serialized gate
        # below re-validates against the LIVE champion and does the ref CAS. If
        # the gate LOSES, the post-save block re-decides with beats_champion=False.
        decision_status = "success" if promo.status == "success" else "keep"
        reason = promo.reason
        state.record_best(hyp_id, cand_cer)
        if promo.status == "success":
            state.status = "success"
```

**Delete** the old `if t.action == "promote": head = rev-parse HEAD; advance_champion_ref(...)` lines (`runner.py:2277-2279`) — promotion now goes through `promotion.try_promote` (CAS). `advance_champion_ref` stays in gitops for the single-lane/bootstrap path but is no longer the set-path promote mechanism. There is **no** `_reopen_as_refine` helper anywhere.

> **Note on `record_best` during a lost race:** the lost-race branch re-decides the set step but leaves `state.best_cer` as set by `record_best`. That is acceptable: the candidate genuinely beat the *old* champion, and `best_cer` is the job's local best pointer (the authoritative shared champion is the `champion` ref + `promotion_map.jsonl`, which the gate did NOT move). If a stricter "don't bank a lost candidate as best" is wanted, snapshot `state.best_cer`/`best_hyp_id` before `record_best` and restore them in the LOST branch — flagged as an open question, not implemented here.

> **`run_job` bootstrap (`runner.py:2286-2288`):** `ensure_champion_ref` must run on the **main repo** (champion lives there), and the bootstrap CER must be seeded. Change to:
> ```python
>     if config.set_budget > 1 and config.commit_results:
>         from harness import gitops, promotion as promo_mod
>         main_repo = _main_repo_root(config)
>         gitops.ensure_champion_ref(main_repo, state.champion_ref)
>         baseline = _read_json(config.repo_root / config.baseline_file)
>         promo_mod.seed_champion_cer(
>             main_repo, config.summary_dir,
>             baseline_cer=float(baseline.get("baseline_cer",
>                                             baseline.get("target_cer", 0.0)) or 0.0),
>             champion_commit=gitops.read_ref(main_repo, f"refs/heads/{state.champion_ref}"))
> ```
> In single-lane (no worktree), `_main_repo_root` returns `repo_root` → unchanged location.

Write a runner-level integration test (`worktree`+`promotion` markers): a job in a worktree produces a champion-beating candidate → champion advances on the **main repo**, `promotion_map.jsonl` gets a row, the job branch keeps the commit. And a lost-race variant (pre-seed a lower live CER → the candidate stays the lineage head, champion unmoved, the set is still alive). Use the corrected `_init_repo(tmp_path); gitops.ensure_champion_ref(tmp_path, "champion")` pattern, and read champion via `_main_repo_root` / `tmp_path` (the main repo), not the worktree.

- [ ] **Step 6: Add `--main-repo-root` CLI flag (optional; auto-derived otherwise)**

In `main` argparse, add `--main-repo-root` (default None → auto-derive via `--git-common-dir`). Thread into `RunnerConfig(main_repo_root=...)`. The launcher does not need to pass it — auto-derivation from the worktree suffices; the flag is an escape hatch.

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_gitops_worktree.py tests/test_promotion.py tests/test_harness_runner.py -v`
Expected: PASS. Full suite: `python -m pytest -q` green (legacy single-shot tests unaffected — `set_budget=1` never enters the gate; `main_repo_root` defaults None → `repo_root`).

- [ ] **Step 8: Commit**

```bash
git add harness/gitops.py harness/promotion.py harness/runner.py \
        tests/test_gitops_worktree.py tests/test_promotion.py tests/test_harness_runner.py
git commit -m "feat(promotion): serialized gated champion promotion (flock + seed + re-validate + ref CAS + map), lost-race keeps lineage head (phase3)"
```

---

## Self-Review

**Spec coverage (HARNESS-REDESIGN §195 change-points table → tasks):**

| §195 change-point | This plan |
|---|---|
| new `harness/gitops.py`: `prepare_job_worktree`, `restore_lineage_head`, `promote_to_champion`, `cleanup_worktree` | Task 1 (prepare/restore-lineage/cleanup/list) + Task 3 (`read_ref`, `promote_to_champion` CAS) ✓ |
| `RunnerConfig`: `champion_ref`, `job_ref`, `job_worktree`, `set_max_steps`, `max_repairs`, `max_refines` | `champion_ref` lives on `HarnessState`; `max_repairs`/`max_refines` already in Phase 1; Phase 2 adds `main_repo_root`. `job_ref`/`job_worktree` are launcher concerns (the worktree IS `repo_root`), so a separate field is redundant — documented divergence ✓/△ |
| `ensure_worktree_ready`: separate champion vs job dirty rules | champion is **never** a runner `repo_root` (never checked out), so `ensure_worktree_ready` runs only against the job worktree — no separate rule needed; the protection is structural (Tasks 1–2). Documented △ |
| `rollback_paths`: target active lineage HEAD not champion | Task 2 Step 3: `repair`→`restore_lineage_head` (worktree HEAD), `reset`→`restore_file_from_ref(champion)`; `rollback_paths` itself unchanged ✓ |
| `_decide_iteration`: set_phase + set_seed_ref | set_phase override already Phase 1; Task 2 Step 4 makes the refine **parent/seed** the lineage head ✓ |
| `run_iteration`: dual decision | Phase 1 (`decide_lineage_progress`+`decide_promotion`); Task 3 routes promote through the gate + models lost-race at the `step_set` input ✓ |
| `commit_iteration`: split code vs metadata / promotion commit path | code/metadata split is **Phase 1.5 (prerequisite, landed first)**; the promotion commit path is `harness.promotion`'s splice-only-`transcribe.py` ✓ |
| `scripts/evolve.py` CLI: `--worktree-root` etc. | worktree creation moved to `scripts/launch_parallel.py` (per-job); `evolve.py` stays thin, runs inside an already-prepared worktree; `--main-repo-root` added to `runner.main` (Task 3 Step 6) ✓ |
| `tests/`: `requires_data`/`integration` markers + worktree/promotion tests | `worktree`/`promotion` markers registered up front (Task 0) + their tests (Tasks 1/3); `requires_data`/`integration` markers **deferred** (see below) ✓/deferred |
| branch protection (§168) | OPTIONAL note only — solo/local operator; the **flock lock is the real mechanism** (Task 3). Not a task ✓ |
| packaging `git archive` + `.gitattributes export-ignore` (§275) | **deferred** (see below) ✓ |

**C1 (`_init_repo`) fix — every place audited & corrected:** the real `_init_repo(root: Path) -> None` returns `None` and creates **no** `champion`. The old draft's `main = _init_repo(tmp_path); gitops.ensure_champion_ref(main, …)` / `prepare_job_worktree(main, …)` would crash on `None`. Fixed in: (1) Task 2 Step 1 integration test (`_init_repo(tmp_path)` statement, then `gitops.ensure_champion_ref(tmp_path, "champion")`, then `prepare_job_worktree(tmp_path, …)`); (2) Task 2 Step 4 refine-parent unit test (same pattern); (3) Task 3 Step 5 runner-level integration test (explicit instruction to use `_init_repo(tmp_path); gitops.ensure_champion_ref(tmp_path, "champion")` and read champion via the main repo). The `tests/test_gitops_worktree.py` and `tests/test_promotion.py` fixtures build their own temp repo from scratch (mirroring `tests/test_gitops.py`), so they never touch `_init_repo` — also corrected vs the draft, which referenced a misleading "`_init_repo` already creates `champion` per phase1."

**C2 (lost-race) fix:** modeled at the **input** to `step_set`. The gate (`promotion.try_promote`) returns `LOST`; the runner then constructs a fresh `Outcome(verify_ok=True, lineage_status=lin.status, cer=cand_cer, beats_champion=False, …)` and calls `step_set` again. With `beats_champion=False`, `step_set` runs its normal refine `advance`/`hold` logic and keeps the candidate as the lineage head (or rolls it back to lineage head/champion if it is not even a local gain). No `replace()` on a closed `Transition`, no `phase="refine"` injection the pure machine never emits, and **`_reopen_as_refine` is deleted entirely** (it appeared in the draft's promote-arm and helpers and is gone).

**I3 (live champion CER) fix:** `seed_champion_cer` writes a `__bootstrap__` row into `promotion_map.jsonl` (CER = `baseline/target_cer.json:baseline_cer`, the measured CER of the seed `transcribe.py`) at `run_job` bootstrap time, right after `ensure_champion_ref` on the main repo (Task 3 Step 5 `run_job` note). It is idempotent (no second row if the map already has one). `live_champion_cer` therefore never returns `None` in normal operation, so the first parallel promote is re-validated against a real CER instead of auto-winning.

**Phase-1.5 dependency:** restated as a **prerequisite that lands first** (own section). The shared `repair`/`reset`/promote edits are rebased onto it (Tasks 2 and 3 say so explicitly): add `restore_lineage_head`, keep `reset`→champion, route promote through the gate; do NOT re-introduce metadata commits.

**Scope split:** Tasks 1–3 are the shipped coherent core (worktree helpers + single-job-in-worktree + refine-parent fix + parallel launcher + gated promotion lock/seed/re-validate/CAS/map). Marker registration for `worktree`/`promotion` is pulled forward into **Task 0** (so no test trips `PytestUnknownMarkWarning` under `-W error`). The broader `requires_data`/`integration` marker machinery and `git archive` packaging are in the **Deferred** section below — not load-bearing for the promotion-race fix.

**Ordering / always-working:** Task 0 (markers) → Task 1 (pure git helpers) → Task 2 (single job in a worktree + launcher *defined*, not yet load-bearing) → Task 3 (gated promotion + seed + lost-race + wiring). The launcher's `run_pool` is committed in Task 2 but **concurrency is only safe to enable after Task 3's gate exists** — an operator must not run jobs concurrently until Task 3 lands (stated in Task 2 Step 5 and the ordering rationale).

**Placeholder scan:** all `...` ellipses from the prior draft (especially in `tests/test_promotion.py`) are **filled with real code** — the fixture, `_commit_candidate` helper, and every assertion are concrete and runnable. `gitops.py`, `promotion.py`, `launch_parallel.py` are complete. The only "copy from the file" notes are for the exact `VerifyResult(...)` kwargs (which the test file already imports/uses) — behavioral assertions are all concrete.

**Type consistency:** `promote_to_champion` returns `str | None` (None = CAS lost / champion missing) ← consumed by `try_promote` as the lost-race signal → `PromotionResult.status ∈ {"promoted","lost_race"}` (module constants `PROMOTE`/`LOST`). `decide_promotion` returns a `policy.Decision` whose `.status ∈ {"keep","reject","success"}` and `.candidate_cer: float` — both read correctly (`revalidate.status not in ("keep","success")` for the lost gate; `revalidate.candidate_cer` for the map row + commit message). `read_ref`/`live_champion_cer` return `str | None`/`float | None`; `decide_promotion(champion_cer=...)` accepts `None` (Phase-1 signature). `RunnerConfig.main_repo_root: Path | None`; `_main_repo_root(config) -> Path`. `list_worktrees -> list[str]`. All git helpers keep the Phase-1 `(repo_root: Path, …)` first-arg convention. `step_set`'s `Outcome` is reconstructed (not mutated) for the lost-race re-decision.

**Genuine inconsistencies found beyond the known corrections:**
- The draft's Task 2 integration test passed `RunnerConfig(..., manual=False, candidate_cmd=None)` as if asserting they exist — they DO (`runner.py:225-226`), so harmless, but the draft's worry was misplaced; the real crash was `main = _init_repo(...)` (None). Corrected.
- The draft's `list_worktrees` read `.stdout` off the `_git` `CompletedProcess` — correct (`_git` returns `CompletedProcess`), preserved.
- `promote_to_champion` did not guard `champ is None` (champion ref missing). Added an explicit `if champ is None: return None` so a missing champion is a clean lost-race rather than a crash in `read-tree`.
- The draft's `run_pool` carried a redundant `tuple[Popen, str]` value in the `running` dict (the `str` was never used); simplified to `dict[str, subprocess.Popen]`.
- Baseline CER source for I3: the draft hand-waved "champion commit's recorded score." The concrete authoritative value is `baseline/target_cer.json:baseline_cer` (present in the real file, = measured seed CER); `target_cer` is only a *goal*, not the champion's CER, so seeding from `target_cer` would be wrong. The `run_job` seed reads `baseline_cer` with a `target_cer` fallback only as a last resort.

**Open questions for the operator:**
1. **flock vs the launcher being the only promoter** — the file lock defends even a stray hand-run `evolve.py`. Keep flock as the source of truth (recommended), or rely on the launcher being the sole entrypoint? (Plan assumes flock.)
2. **Live champion CER source** — this plan uses the lowest `cer` in `promotion_map.jsonl` (seeded with `baseline_cer`). Alternative: a dedicated `runs/_summary/champion.json` updated under the lock. Map-min is simplest + append-only; confirm it's authoritative.
3. **`record_best` during a lost race** — a candidate that beat the *old* champion but lost the race keeps `state.best_cer` updated (local best pointer) while the shared `champion` ref is unmoved. Acceptable, or snapshot+restore `best_cer` on LOST? (Plan keeps it; flagged in Task 3 Step 5 note.)
4. **Concurrency cap default 2** — confirm; raise if GPU-compute headroom proves larger (memory is not the limit).
5. **Worktree root location** — launcher defaults `--worktree-root` to the repo's parent (`ROOT.parent`). Confirm that's writable/desired vs a dedicated `../wt/`.

---

## Deferred to a later phase (not required for the core)

The following were Tasks 4–5 in the first draft. They are **not load-bearing for the promotion-race fix** and ship separately. (The `worktree`/`promotion` marker *registration* needed by the core tests is NOT deferred — it is Task 0.)

### Deferred Task A: full test-marker separation (`requires_data`/`integration` + default no-data run)

Register `requires_data`/`integration` markers and set `addopts = "-m 'not requires_data'"` so the default suite is the no-data gate (HARNESS-REDESIGN §166). Then audit existing tests that touch private audio/holdout data and tag them `@pytest.mark.requires_data` (untagged data tests would still run under the default `markexpr`). `tests/test_markers.py` asserts the four markers are registered and the default `addopts` excludes `requires_data`. Caution: a data-free test wrongly tagged just gets skipped (safe); a data test left untagged breaks no-data CI (caught by running `-m "not requires_data"` with no data present).

### Deferred Task B: packaging — `git archive` source tarball + separate run-artifact tarball

HARNESS-REDESIGN §170/§275-291: `scripts/package_source.py` with `build_source_archive` (`git archive --worktree-attributes` honoring `.gitattributes export-ignore` so `runs/`/caches never leak) + `build_artifact_archive` (a SEPARATE tarball of `runs/_summary` only). New `.gitattributes` with `export-ignore` for `runs/`, `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.cache/`, `docs/reviews/`. `tests/test_package_source.py` (the `integration` marker from Deferred Task A) asserts the source archive includes `harness/` but excludes `runs/`.

### Deferred Task C: docs + SSOT sync

Add `promotion` to the `harness/` module list in `docs/SSOT.md` §3 (with a phase2+3 changelog entry), and add HARNESS-MECHANICS §13 documenting the worktree layout (protected champion never checked out; `job/<id>` worktree per job; lineage head = worktree HEAD; `repair`→`restore_lineage_head`, `reset`→`restore_file_from_ref(champion)`), the parallel model (Popen pool, cap 2, per-job `runs/_summary/<job_id>_*` isolation), and the gated promotion (flock, seed, re-validate vs live champion CER, ref CAS splicing only `transcribe.py`, `promotion_map.jsonl`, lost-race-keeps-lineage-head). Run `tests/test_doc_consistency.py` if it enforces a module↔SSOT map.
