# Harness Refactor — Phase 2+3: Worktree Execution Lane + Parallel Jobs + Gated Promotion

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run each evolution job in its own `git worktree` cut from a protected `champion` ref (never a runner `repo_root`), run **N jobs in parallel** (default cap 2), and make promotion to the single shared `champion` **serialized + gated**: under a local file lock, re-validate against the *live* champion CER, then advance the champion via a ref compare-and-swap (CAS) that splices only `workspace/transcribe.py` for linear history. Parallel **requires** gated promotion — concurrent jobs racing to advance one champion re-introduce the exact single-HEAD race this refactor exists to kill, so Phase 2 (parallel) and Phase 3 (governance) ship as **one coherent unit**.

**Architecture (2-3 sentences):** A protected `champion` branch holds the last promoted code and is *never* checked out by a runner; each job gets a linked worktree `git worktree add <wt_root> -b job/<job_id> champion`, and the runner's existing git ops (which already `cwd` into `config.repo_root`) operate worktree-local — the on-disk `transcribe.py` is that worktree's lineage head, rollback/reset target the worktree's own HEAD / the `champion` ref read through the worktree. Promotion is pulled out of the per-iteration `run_iteration` hot path into a serialized lane guarded by an `fcltl.flock` lock-file that auto-releases on process death; under the lock the candidate is re-scored against the *current* champion (the A-vs-B concurrent-beat race), then `git update-ref champion <new> <old-expected>` (CAS) advances it, recording `runs/_summary/promotion_map.jsonl`; a lost CAS keeps the candidate as the job's lineage head (not discarded).

**Tech Stack:** Python 3.11 (conda env `evolve`), pytest, git CLI via `subprocess`, `fcntl.flock` (POSIX, Linux operator), `subprocess.Popen` pool for the launcher. Builds **on** Phase 1 modules (`harness/lineage.py`, `harness/gitops.py`, `harness/state.py`, the two policy functions) — none are rewritten.

---

> ## ⚠️ CRITICAL CORRECTIONS (2026-06-05 review + controller meta-review — apply before implementing; plan verdict: needs-rework)
>
> The design (worktree isolation, `fcntl.flock` serialized promotion, live-CER re-validate, ref CAS) is sound, but the following defects were verified against the real code and MUST be fixed:
>
> 1. **C1 — `_init_repo` does NOT return the repo root and does NOT create `champion`.** Confirmed: `tests/test_harness_runner.py` `def _init_repo(root: Path) -> None:` (returns `None`; creates git repo + workspace/baseline/runs but no `champion` ref). Every worktree/promotion test in this plan that does `main = _init_repo(tmp_path); gitops.ensure_champion_ref(main, …)` / `prepare_job_worktree(main, …)` will crash on `None`. **Fix:** in each new test, call `_init_repo(tmp_path)` for its side effects, then make an **initial commit** (so `champion` can point at something) and call `gitops.ensure_champion_ref(tmp_path, "champion")` with `tmp_path` as the root. Do not rely on `_init_repo`'s return value or assume it creates `champion`.
>
> 2. **C2 — "promotion lost" must be a first-class `step_set` outcome, not a post-hoc mutation of a frozen closed `Transition`.** The promote-arm rewrite references an undefined/truncated `_reopen_as_refine(...)` and `replace()`s a `Transition` that `step_set` already closed (`promote` → `phase="closed"`) into a `refine` state the pure machine never emits — corrupting set state. **Fix:** model the lost race at the *input* to `step_set`, not the output. When the CAS / re-validation loses, do NOT call `step_set` with `beats_champion=True`. Instead the runner re-runs the set decision with `beats_champion=False` (the candidate is verified-OK and may still improve the lineage), so `step_set`'s normal `advance`/`hold` refine logic keeps it as the lineage head and the set continues — no `Transition` mutation, no undefined helper. Delete `_reopen_as_refine`.
>
> 3. **I3 — live champion CER must NOT fall back to `None`.** `live_champion_cer` reads the tail of `promotion_map.jsonl`; when empty (bootstrap, no promotions yet) it returns `None`, and `decide_promotion(champion_cer=None)` treats "no champion" as "first candidate always wins" — so the FIRST parallel promotion always succeeds even when the committed `champion` already encodes a better baseline CER. **Fix:** seed `promotion_map.jsonl` with the bootstrap champion's recorded CER at `ensure_champion_ref` time (or read the champion commit's score), so re-validation always has a real CER.
>
> 4. **Merge with phase1.5 (shared lines).** phase1.5 and this plan both edit the set `repair`/`reset` blocks (`runner.py:~2249-2254`) and the promote arm. If **phase1.5 lands first**, the code-only `commit_iteration`, the `reset`-as-code-checkpoint status, and metadata-off-git are already done — then this plan's edits there reduce to: add `restore_lineage_head` for the worktree `repair` and wire the promotion lane; do NOT re-introduce metadata commits. State this rebasing explicitly in Tasks 2-3/3-4. (The promotion splice insulates `champion` regardless of phase1.5.)
>
> 5. **Scope split (recommended).** Tasks 4–5 (`requires_data`/`integration` pytest markers, `git archive` packaging) are NOT load-bearing for the promotion-race fix. Split them into a later phase; ship Tasks 1–3 (worktree + parallel launcher + gated promotion lock) as the coherent core. Also: register markers in `pyproject.toml` **before** any test uses `pytest.mark.worktree/promotion` (else `PytestUnknownMarkWarning`, fails under `-W error`).
>
> **Dependency note:** implement **phase1.5 first** (it fixes the live false-positive scope-reject that already bit phase3_015), then rebase this plan onto it per (4).

## Ground truth: what Phase 1 already landed (READ before starting)

Phase 1 (`docs/superpowers/plans/2026-06-05-harness-lineage-set-phase1.md`) is **merged on `refactor-harness`**. Confirmed in code:

- `harness/lineage.py` — pure `SetBudget`/`Outcome`/`SetState`/`Transition`/`step_set`. **Reuse verbatim.** Actions: `advance | repair | promote | reset`.
- `harness/gitops.py` — `_git(repo_root, args, check=True)`, `_ref_exists`, `ensure_champion_ref(repo_root, champion_ref="champion")`, `restore_file_from_ref(repo_root, ref, rel_path)`, `advance_champion_ref(repo_root, champion_ref, commit)`. **Phase 2 ADDS to this file** (§195 new module row): `prepare_job_worktree`, `restore_lineage_head`, `cleanup_worktree`, `list_worktrees`, and (Phase 3) `promote_to_champion`.
- `harness/state.py` — `HarnessState` has `champion_ref="champion"`, `set_id`, `set_phase` (`idle|explore|repair|refine|closed`), `set_best_cer`, `set_best_hyp_id`, `set_repairs_used`, `set_refines_used`, `last_failure_hyp_id`. `load` ignores unknown keys (forward-compatible); `save` is atomic (tmp + `os.replace`).
- `harness/runner.py`:
  - `RunnerConfig` (frozen dataclass, `runner.py:221`): `repo_root: Path`, `allowed_path=Path("workspace/transcribe.py")`, `runs_dir`, `summary_dir`, `commit_results`, and the Phase-1 set fields `set_budget=1`, `max_repairs`, `max_refines`.
  - `_run_git(repo_root, args, check=True)` (`runner.py:282`) — **all** runner git ops pass `cwd=repo_root`, so they are already worktree-correct. (Confirmed: `git_status`, `ensure_worktree_ready`, `rollback_paths`, `commit_iteration`, the promote arm all thread `config.repo_root`/`repo_root = config.repo_root.resolve()`.)
  - `run_iteration` (`runner.py:1872`): legacy single-shot path when `set_budget<=1`; the **set path** (`set_budget>1`, `runner.py:2214`) computes `decide_promotion` + `decide_lineage_progress`, calls `step_set`, and on `t.action`:
    - `promote` → `record_best`, then (after `state.save`) `commit_iteration(..., "keep"/"success")`, then **`head = rev-parse HEAD` then `advance_champion_ref(repo_root, state.champion_ref, head)`** (`runner.py:2274-2279`). **This unguarded promote is the race to replace in Phase 3.**
    - `advance` → status `lineage_advance` (committed code checkpoint, portfolio pool-inert — `portfolio.py:332`).
    - `repair`/`reset` → `rollback_paths`; on `reset` also `restore_file_from_ref(repo_root, champion_ref, allowed_path)`.
  - `run_job` (`runner.py:2283`): `load_or_init_state`, then `if set_budget>1 and commit_results: gitops.ensure_champion_ref(config.repo_root.resolve(), state.champion_ref)`. Evaluated-iteration budget loop.
  - `_decide_iteration` (`runner.py:1201`): set-phase override of scheduler `chosen_mode` (`runner.py:1215`); parent selection via `pf.parents_for_mode`.
  - `main` (`runner.py:2377`): `--set-budget`/`--max-repairs`/`--max-refines` + the `--set-budget>1 requires --commit-results` guard (`runner.py:2415`).
- `harness/portfolio.py` — `lineage_advance` is pool-inert (`portfolio.py:282,332`); `parents_for_mode("refine")` rotates over `_ranked_pool` keyed off `global_best`/`near_best` (champion family).
- `commit_iteration` (`runner.py:1826`) commits **code + metadata together** (Phase-1.5 metadata-off-git deferred); `runs/_summary/` is **git-tracked** (`.gitignore`: `runs/*` + `!runs/_summary/`).
- `tests/test_gitops.py` — the temp-repo fixture pattern (`_git` helper + `repo` fixture) to mirror for all new git-touching tests.
- `pyproject.toml` — only `[tool.pytest.ini_options] testpaths=["tests"]`; **no markers registered yet.**

**Source-of-truth docs (cite these ONLY, NOT `docs/archive/`):** `docs/HARNESS-REDESIGN.md` (§50-82 lanes/branch-strategy/rollback/commit-policy, §111-170 markers + `git archive` + branch protection, §172 timeline, §195 change-points, §217-291 script examples), `docs/HARNESS-MECHANICS.md` (§3 git lifecycle, §12 phase1 lineage-set as built), the phase1 plan, and the actual code above.

---

## Dependency on Phase 1.5 (metadata-off-git) — state it, don't block on it

Phase 1.5 (`runs/` fully gitignored; metadata append-only off-git; `commit_iteration` stages code-only) **may or may not land before this work.** This plan does **not** require it:

- **Worktrees already give per-job `runs/` isolation** — each worktree has its own working tree, so `runs/_summary/<job_id>_*` never collide across jobs even with metadata still tracked.
- **If 1.5 has NOT landed:** `commit_iteration` commits code+metadata together (current behavior). Per-worktree commits live on each `job/<job_id>` branch — fine, they're isolated. The promotion splice (Task 3-3) cherry-picks **only `workspace/transcribe.py`**, so the noisy per-iter metadata commits on a job branch never reach `champion` → `champion` history stays clean regardless of 1.5.
- **If 1.5 HAS landed:** identical, just cleaner (job branches carry code-only commits; metadata is on disk). No task here changes behavior based on it.

**Decision:** build against the *current* (`commit_iteration` unchanged) behavior; note the splice is what insulates `champion` from metadata either way.

---

## Decision: the Phase-1 refine-parent wart — fix scope

**The wart (phase1 plan Open Q, confirmed in code):** in set mode, a `refine`-phase iter calls `parents_for_mode(portfolio, "refine", ...)` (`runner.py:1223`) which rotates `_ranked_pool` — keyed off `global_best`/`near_best` = the **champion** family, NOT the worktree's lineage head. The *prompt parent hint* therefore shows champion-family diffs, while the on-disk file (which the candidate actually edits) IS the lineage head. So the candidate edits the right file but is shown a misleading "parent."

**Decision for Phase 2:** **fix it, minimally, in Task 2-4** — once jobs run in worktrees, the lineage head is an unambiguous artifact (the worktree's HEAD commit of `transcribe.py` + the live on-disk file). When `set_phase=="refine"` and a set is active, inject the **lineage head itself** as the refine parent hint (its diff = `git diff champion..HEAD -- transcribe.py` in the job worktree), instead of a portfolio-pool rotation. This is cheap (one gitops read), is directly enabled by the worktree split (lineage head = worktree HEAD), and removes a real prompt/behavior mismatch. We do **not** pull forward the full §209 `Portfolio.job_best`/`global_best` split (that stays a later phase) — only the refine-parent hint is corrected.

---

## File Structure

| File | Responsibility | Phase 2+3 change |
|------|----------------|------------------|
| `harness/gitops.py` | git ref + worktree helpers | **add** `prepare_job_worktree`, `restore_lineage_head`, `cleanup_worktree`, `list_worktrees` (Task 1); **add** `promote_to_champion` CAS + `read_ref` (Task 3) |
| `harness/promotion.py` | **NEW** — serialized gated promotion (lock + re-validate + CAS + map) | new file (Task 3) |
| `harness/runner.py` | iteration loop / promote arm / refine parent | route promote arm through `harness.promotion` under the lock (Task 3-4); inject lineage-head refine parent (Task 2-4); `run_job` worktree-aware bootstrap is a no-op (already `cwd`-correct) — confirm (Task 2-3) |
| `scripts/launch_parallel.py` | **NEW** — Popen pool launching `scripts/evolve.py` per job in its worktree | new file (Task 2-5) |
| `pyproject.toml` | pytest marker registry | **add** `requires_data`/`integration`/`worktree`/`promotion` markers + default `-m "not requires_data"` (Task 4) |
| `scripts/package_source.py` | **NEW** — `git archive` source tarball + run-artifact tarball | new file (Task 5) |
| `.gitattributes` | `export-ignore` for `git archive` | **NEW/append** (Task 5) |
| `runs/_summary/promotion_map.jsonl` | append-only source-commit ↔ champion-commit ledger | written by `harness.promotion` (Task 3) |
| `tests/test_gitops_worktree.py` | **NEW** | worktree helper tests (`worktree` marker) |
| `tests/test_promotion.py` | **NEW** | lock + re-validate + CAS + map tests (`promotion` marker) |
| `tests/test_launch_parallel.py` | **NEW** | launcher pool unit tests (no real jobs) |
| `tests/test_markers.py` | **NEW** | marker registration / `requires_data` skip test |
| `tests/test_package_source.py` | **NEW** | `git archive` excludes `runs/`/caches (`integration` marker) |

**Ordering rationale (each task yields working/testable software):** worktree helpers (pure git, temp-repo testable) → single job *in a worktree* end-to-end (no parallelism yet) → promotion lock + re-validation + CAS (still single job, promote goes through the gate) → **only then** the parallel launcher (concurrent promotion is now safe because the gate exists) → markers → packaging. Promotion gating lands **before** concurrency is enabled.

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


pytestmark = pytest.mark.worktree


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
    # second call must not recreate / move it (job may have advanced its lineage).
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
    args = ["worktree", "add", worktree_path.as_posix()]
    if _ref_exists(repo_root, job_ref.removeprefix("refs/heads/")):
        # branch already exists (prior run) but its worktree was pruned → re-link.
        args += [job_ref]
    else:
        args += ["-b", job_ref, champion_ref]
    _git(repo_root, args)


def restore_lineage_head(repo_root: Path, rel_path: Path) -> None:
    """Restore one file to the worktree's OWN HEAD (the lineage head), NOT to
    champion (HARNESS-REDESIGN §78: reject/refine-rollback targets the job-local
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

Note on `restore_lineage_head` vs Phase-1 `restore_file_from_ref`: Phase 1's single-lane reset restored from `champion` (HEAD==lineage head in single lane, but reset deliberately drops to champion). In the worktree model, `repair` rollback must go to the **worktree's HEAD** (lineage head), while `reset` (set closed) still goes to `champion`. `restore_lineage_head` is the `repair` target; the existing `restore_file_from_ref(wt, champion_ref, path)` remains the `reset` target. Task 2-3 wires this.

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

Prove the runner works unchanged when `config.repo_root` is a job worktree, and split `repair` (→ lineage head) from `reset` (→ champion) in the set path. Plus the refine-parent wart fix.

**Files:**
- Modify: `harness/runner.py` (set-path verify-fail + scored `repair`/`reset` blocks; `_decide_iteration` refine parent)
- Test: `tests/test_harness_runner.py` (worktree integration test, `worktree` marker)

- [ ] **Step 1: Write the failing integration test**

Read `tests/test_harness_runner.py`'s existing `_init_repo` + injected `candidate_func`/`verify_func` fixtures first (the set-path test `test_set_keeps_worse_than_champion_explore` is the template). Add:

```python
@pytest.mark.worktree
def test_job_runs_in_worktree_and_rollback_targets_lineage_head(tmp_path, monkeypatch):
    """A job in a worktree: a kept lineage advance becomes the worktree HEAD; a
    later verify-fail repair rolls back to THAT head, not champion (phase2)."""
    from harness import gitops, runner
    from harness.runner import RunnerConfig
    from harness.state import HarnessState

    main = _init_repo(tmp_path)                  # seeds champion + transcribe.py
    gitops.ensure_champion_ref(main, "champion")
    wt = tmp_path / "wt-job1"
    gitops.prepare_job_worktree(main, wt, "job/job1", "champion")

    cfg_ = RunnerConfig(job_id="job1", repo_root=wt, set_budget=4,
                        commit_results=True, manual=False, candidate_cmd=None)
    state = HarnessState(job_id="job1", best_cer=0.20, best_hyp_id="champ")
    state_path = wt / "runs/_summary/job1_state.json"

    # iter1: explore worse-than-champion but valid → advance (lineage head moves).
    def cand_ok(prompt, out_dir):
        (wt / "workspace/transcribe.py").write_text(
            "def transcribe(a, sr):\n    return 'LINEAGE'\n", encoding="utf-8")
        return _ok_completed_process(out_dir)
    runner.run_iteration(cfg_, state, state_path, candidate_func=cand_ok,
                         verify_func=lambda h: _verify_ok({"corpus_cer": 0.30,
                             "total_inference_time_s": 90.0}))
    assert state.set_phase == "refine"
    assert "LINEAGE" in (wt / "workspace/transcribe.py").read_text()
    lineage_head = _git(wt, "rev-parse", "HEAD")

    # iter2: refine that breaks verify → repair rolls back to lineage head (not champion).
    def cand_break(prompt, out_dir):
        (wt / "workspace/transcribe.py").write_text("BROKEN\n", encoding="utf-8")
        return _ok_completed_process(out_dir)
    runner.run_iteration(cfg_, state, state_path, candidate_func=cand_break,
                         verify_func=lambda h: _verify_fail("boom"))
    assert "LINEAGE" in (wt / "workspace/transcribe.py").read_text()   # not champion's v1
    assert _git(wt, "rev-parse", "HEAD") == lineage_head               # head unchanged
```

(Adjust helper names `_ok_completed_process`/`_verify_ok`/`_verify_fail`/`_git` to the file's actual helpers; `_init_repo` already creates `champion` per phase1.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_harness_runner.py::test_job_runs_in_worktree_and_rollback_targets_lineage_head -v`
Expected: FAIL — the repair branch currently calls `rollback_paths` which `git restore`s `transcribe.py` to **HEAD** (correct here only because HEAD==lineage head) but the *reset* branch and the assertion that champion is NOT used need the split below; more directly, this test fails today because the set verify-fail block (`runner.py:2106`) on `repair` action does `rollback_paths` then nothing — that happens to leave HEAD intact, so confirm: it may PASS partially. If it passes, ADD an explicit reset-case assertion (Step 1b) to force the split.

> **Implementer note:** `rollback_paths` (`runner.py:356`) does `git restore -- <tracked>` (restores to HEAD = lineage head in a worktree) + `git clean -fd`. So `repair` already targets the worktree HEAD correctly *by accident of HEAD==lineage-head*. The real change is making `reset` target **champion via the worktree** explicitly and `repair` use the named `restore_lineage_head` for clarity + correctness under future metadata-off-git. Write the test to pin both.

- [ ] **Step 3: Make repair→lineage-head and reset→champion explicit in the set path**

In `run_iteration`, set-path **verify-fail** block (`runner.py:2106-2119`), replace the rollback choice:

```python
            t = step_set(s, outcome, SetBudget(config.max_repairs, config.max_refines))
            if t.action == "reset":
                # set closed → drop the whole lineage back to the protected champion.
                rollback_paths(repo_root, candidate_owned_statuses(git_status(repo_root), config))
                gitops.restore_file_from_ref(repo_root, state.champion_ref, config.allowed_path)
            else:  # "repair": keep the set alive, drop only the failed candidate
                gitops.restore_lineage_head(repo_root, config.allowed_path)
                # clean any stray untracked the candidate left (mirror rollback_paths' clean).
                rollback_paths(repo_root, [s for s in candidate_owned_statuses(
                    git_status(repo_root), config) if s.untracked])
            _persist_set_state(state, t.state)
```

And in the scored set path `repair`/`reset` block (`runner.py:2249-2254`):

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

`gitops` is already imported in both blocks (`from harness import gitops` at `runner.py:2111` and `2218`).

- [ ] **Step 4: Fix the refine-parent wart (inject the lineage head)**

In `_decide_iteration` (`runner.py:1222`, after the set override and before/within the parent loop), when a set is active in `refine`, replace the portfolio-pool refine parent with the lineage head:

```python
    parents: list[dict[str, Any]] = []
    if (config.set_budget > 1 and state.set_phase == "refine"
            and state.set_best_hyp_id):
        # Refine parent = the lineage head itself (this worktree's HEAD), NOT a
        # champion-family portfolio rotation (phase1 wart: parents_for_mode
        # ["refine"] keys off global_best/near_best). The on-disk file already IS
        # the lineage head; show its champion-delta as the parent diff so the
        # prompt hint matches what the candidate edits.
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
            ... (existing loop body unchanged) ...
```

Add a unit test:

```python
def test_refine_parent_is_lineage_head_not_portfolio(tmp_path, monkeypatch):
    from harness.runner import RunnerConfig, _decide_iteration
    from harness.state import HarnessState
    main = _init_repo(tmp_path)
    from harness import gitops
    gitops.ensure_champion_ref(main, "champion")
    cfg_ = RunnerConfig(job_id="j", repo_root=main, set_budget=4)
    st = HarnessState(job_id="j", set_phase="refine", set_best_hyp_id="h2",
                      set_best_cer=0.18, evaluated_count=20, best_cer=0.2,
                      best_hyp_id="champ")
    sched, parents = _decide_iteration(cfg_, st)
    assert sched.chosen_mode == "refine"
    assert len(parents) == 1 and parents[0]["hyp_id"] == "h2"
    assert parents[0]["harness_family_id"] == "lineage"
```

- [ ] **Step 5: Define the parallel launcher**

Create `scripts/launch_parallel.py` — a `subprocess.Popen` pool that prepares one worktree per job and runs `scripts/evolve.py` inside it, with a concurrency cap (default 2; operator's 24GB GPU fits CT2-turbo ~1.5GB/job, the binding limit is GPU-compute contention not memory — HARNESS-REDESIGN §80 lanes / spec). The promotion gate (Task 3) is what makes concurrent promotion safe; this launcher is added **after** the gate exists.

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
    """Pure: prepare worktrees and return (job_id, worktree, argv) tuples. Split
    out so it is unit-testable without launching processes."""
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
    running: dict[str, tuple[subprocess.Popen, str]] = {}
    rc: dict[str, int] = {}
    while pending or running:
        while pending and len(running) < cap:
            jid, wt, argv = pending.pop(0)
            running[jid] = (subprocess.Popen(argv, cwd=wt), jid)
        for jid, (proc, _) in list(running.items()):
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
@pytest.mark.worktree
def test_build_job_cmds_creates_one_worktree_per_job(tmp_path):
    import subprocess
    from scripts import launch_parallel as lp
    from harness import gitops
    main = tmp_path / "main"; main.mkdir()
    for a in (["init","-q"],["config","user.email","t@t"],["config","user.name","t"]):
        subprocess.run(["git",*a], cwd=main, check=True)
    (main/"f").write_text("x"); subprocess.run(["git","add","-A"],cwd=main,check=True)
    subprocess.run(["git","commit","-qm","c"],cwd=main,check=True)
    gitops.ensure_champion_ref(main, "champion")
    jobs = lp.build_job_cmds(main, tmp_path/"wts", ["a","b"], 5, "claude -p", 4)
    assert len(jobs) == 2
    assert all(wt.exists() for _, wt, _ in jobs)
    assert jobs[0][2][:3] == [__import__("sys").executable, "scripts/evolve.py", "--job-id"]


def test_run_pool_respects_cap(monkeypatch):
    from scripts import launch_parallel as lp
    peak = {"n": 0}; live = {"n": 0}
    class FakeProc:
        def __init__(self): self.n = 0; live["n"] += 1; peak["n"] = max(peak["n"], live["n"]); self.returncode = 0
        def poll(self):
            self.n += 1
            if self.n >= 2: live["n"] -= 1; return 0
            return None
    monkeypatch.setattr(lp.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(lp.time, "sleep", lambda *_: None)
    jobs = [("j%d"%i, None, ["x"]) for i in range(5)]
    rc = lp.run_pool(jobs, cap=2, poll_s=0)
    assert peak["n"] <= 2 and len(rc) == 5
```

(Ensure `tests/` can import `scripts.launch_parallel`: `scripts/` has no `__init__.py` today — add an empty `scripts/__init__.py`, or import via `importlib`/`sys.path` as the test prefers. Prefer adding `scripts/__init__.py`.)

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_harness_runner.py tests/test_launch_parallel.py -v`
Expected: PASS (legacy single-shot tests unaffected — default `set_budget=1`, `repo_root=repo`).

- [ ] **Step 7: Commit**

```bash
git add harness/runner.py scripts/launch_parallel.py scripts/__init__.py \
        tests/test_harness_runner.py tests/test_launch_parallel.py
git commit -m "feat(runner,launcher): job-in-worktree, repair→lineage-head reset→champion, parallel launcher (phase2)"
```

---

## Task 3: Serialized gated promotion — lock + re-validate + ref CAS + map

The heart of Phase 3. Replaces the unguarded promote arm (`runner.py:2274-2279`) with a serialized gate so concurrent jobs cannot race the single `champion` (HARNESS-REDESIGN §52 promotion lane, §82 splice-one-verified-commit).

**Design (local, solo operator — file lock, NOT GitHub branch protection):**
1. **One promoter at a time** — `fcntl.flock(LOCK_EX)` on `<repo_root>/.git/champion_promote.lock`. flock auto-releases on process death (kill/crash), so a dead promoter never wedges the lane.
2. **Re-validate against the LIVE champion CER under the lock** — between a job deciding "I beat champion 0.154" and acquiring the lock, another job may have already promoted to 0.150. Re-read the current champion CER (from `runs/_summary/promotion_map.jsonl` tail, or `champion`'s committed score) and re-run `decide_promotion(champion_cer=live)`. If it no longer beats → **lost race**: release lock, keep candidate as the job's lineage head (do NOT reset). This is the A-vs-B concurrent-beat race.
3. **Ref CAS** — `git update-ref refs/heads/champion <new_commit> <old_expected>`. update-ref's old-value guard fails atomically if champion moved since we read it (belt-and-suspenders with the lock). On CAS failure → lost race, same as (2).
4. **Splice only `transcribe.py`** — build the new champion commit by checking out champion, applying ONLY the candidate's `workspace/transcribe.py` from the job commit, and committing — keeps `champion` linear + free of job metadata (independent of metadata-off-git).
5. **Record** `runs/_summary/promotion_map.jsonl`: `{job_id, source_commit, champion_commit, cer, ts}`.

**Files:**
- Modify: `harness/gitops.py` (add `read_ref`, `promote_to_champion` CAS-splice)
- Create: `harness/promotion.py` (lock + re-validate orchestration + map)
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
    champ = read_ref(repo_root, f"refs/heads/{champion_ref}")
    if expected_old is not None and champ != expected_old:
        return None  # already moved before we even started
    # blob of rel_path at source_commit
    blob = _git(repo_root, ["rev-parse", f"{source_commit}:{rel_path.as_posix()}"]).stdout.strip()
    # build a tree = champion's tree with rel_path replaced by blob, via a temp index
    import os, tempfile
    with tempfile.NamedTemporaryFile(prefix="champ_idx_", delete=False) as tf:
        idx = tf.name
    try:
        env = {**os.environ, "GIT_INDEX_FILE": idx}
        subprocess.run(["git", "read-tree", champ], cwd=repo_root, env=env, check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                        f"100644,{blob},{rel_path.as_posix()}"],
                       cwd=repo_root, env=env, check=True, capture_output=True, text=True)
        tree = subprocess.run(["git", "write-tree"], cwd=repo_root, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()
    finally:
        os.unlink(idx)
    new = _git(repo_root, ["commit-tree", tree, "-p", champ, "-m", message]).stdout.strip()
    # atomic CAS: fails (nonzero) if champion moved since `expected_old`.
    cas_old = expected_old or champ
    r = _git(repo_root, ["update-ref", f"refs/heads/{champion_ref}", new, cas_old],
             check=False)
    return new if r.returncode == 0 else None
```

Add gitops CAS tests to `tests/test_gitops_worktree.py`:

```python
def test_promote_to_champion_splices_only_file_and_cas(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt"
    gitops.prepare_job_worktree(repo, wt, "job/j", "champion")
    (wt / "workspace" / "transcribe.py").write_text("WINNER\n", encoding="utf-8")
    (wt / "noise.txt").write_text("metadata junk\n", encoding="utf-8")
    _git(wt, "add", "-A"); _git(wt, "commit", "-qm", "lineage + junk")
    src = _git(wt, "rev-parse", "HEAD")
    old = gitops.read_ref(repo, "refs/heads/champion")
    new = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=old, message="promote j")
    assert new is not None and new != old
    assert gitops.read_ref(repo, "refs/heads/champion") == new
    # champion got the file but NOT the junk → linear, metadata-free.
    assert _git(repo, "show", "champion:workspace/transcribe.py") == "WINNER"
    junk = _git_rc(repo, "cat-file", "-e", "champion:noise.txt")  # helper returning rc
    assert junk != 0


def test_promote_to_champion_cas_loses_when_champion_moved(repo: Path, tmp_path) -> None:
    wt = tmp_path / "wt"
    gitops.prepare_job_worktree(repo, wt, "job/j", "champion")
    (wt / "workspace" / "transcribe.py").write_text("A\n", encoding="utf-8")
    _git(wt, "add", "-A"); _git(wt, "commit", "-qm", "a")
    src = _git(wt, "rev-parse", "HEAD")
    stale_old = gitops.read_ref(repo, "refs/heads/champion")
    # someone else promotes first.
    gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=stale_old, message="b1")
    # our promote with the now-stale expected_old must LOSE (return None).
    lost = gitops.promote_to_champion(repo, "champion", src,
            Path("workspace/transcribe.py"), expected_old=stale_old, message="b2")
    assert lost is None
```

- [ ] **Step 2: `harness/promotion.py` — lock + re-validate orchestration**

Create `harness/promotion.py`:

```python
"""harness/promotion.py
Serialized, gated promotion to the protected champion (HARNESS-REDESIGN §52/§82).

Only one promoter at a time (fcntl.flock, auto-released on death). Under the lock
we RE-VALIDATE against the LIVE champion CER (a peer job may have promoted lower
while we queued — the A-vs-B race), then CAS-advance champion splicing only
transcribe.py. A lost race returns LOST and the caller keeps the candidate as its
lineage head (it is NOT discarded). NOT GitHub branch protection — the operator
is solo + local, so the file lock is the real mechanism.
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


def live_champion_cer(repo_root: Path, summary_dir: Path) -> float | None:
    """Lowest CER promoted so far (tail of promotion_map.jsonl). None if no
    promotion yet (champion is the bootstrap baseline)."""
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
    not the job worktree."""
    lock = _lock_path(repo_root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)             # serialize; auto-released on death
        live = live_champion_cer(repo_root, summary_dir)
        pcfg = PolicyConfig(absolute_delta_fallback=absolute_delta_fallback) \
            if absolute_delta_fallback is not None else None
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

Create `tests/test_promotion.py` (`promotion` marker). Cover: (a) clean promote records the map + advances champion; (b) **lost race by re-validation** — pre-seed `promotion_map.jsonl` with a lower live CER so re-validate returns LOST and champion does NOT move; (c) **lock serializes** — two threads/processes calling `try_promote` don't interleave (assert the map has exactly the expected ordering, no torn writes); (d) lost CAS path. Mirror the temp-repo fixture + `harness.policy` baseline shape from `tests/test_lineage_policy.py`.

```python
import json
from pathlib import Path
import pytest
from harness import gitops, promotion

pytestmark = pytest.mark.promotion

# ... temp-repo fixture identical to test_gitops_worktree.repo, with champion ...

def _baseline(): return {"target_cer": 0.05, "total_inference_time_s": 100.0}
def _rep(cer): return {"corpus_cer": cer, "total_inference_time_s": 90.0}


def test_clean_promote_advances_champion_and_records_map(repo, tmp_path):
    wt = tmp_path / "wt"; gitops.prepare_job_worktree(repo, wt, "job/j", "champion")
    (wt/"workspace/transcribe.py").write_text("WIN\n"); 
    # ... commit, get src ...
    src = ...
    res = promotion.try_promote(repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.12),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.PROMOTE
    assert gitops.read_ref(repo, "refs/heads/champion") == res.champion_commit
    rows = (repo/"runs/_summary/promotion_map.jsonl").read_text().splitlines()
    assert json.loads(rows[-1])["job_id"] == "j"


def test_lost_race_when_live_champion_already_lower(repo, tmp_path):
    # pre-seed a live champion at 0.10; our 0.12 candidate must LOSE re-validation.
    mp = repo/"runs/_summary/promotion_map.jsonl"; mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"job_id":"x","cer":0.10,"champion_commit":"deadbeef"})+"\n")
    before = gitops.read_ref(repo, "refs/heads/champion")
    src = ...  # a job commit
    res = promotion.try_promote(repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.12),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.LOST
    assert gitops.read_ref(repo, "refs/heads/champion") == before   # unmoved


def test_lock_serializes_concurrent_promoters(repo, tmp_path):
    # two threads, each promoting a distinct improving candidate; assert both map
    # rows present, champion == the last winner, no torn jsonl line.
    import threading
    # ... build two source commits cer 0.13 and 0.11 ...
    results = []
    def go(jid, cer, src):
        results.append(promotion.try_promote(repo, job_id=jid, source_commit=src,
            rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(cer),
            baseline=_baseline(), sigma=0.0, sigma_is_provisional=True))
    ts = [threading.Thread(target=go, args=a) for a in (("a",0.13,src_a),("b",0.11,src_b))]
    [t.start() for t in ts]; [t.join() for t in ts]
    rows = [l for l in (repo/"runs/_summary/promotion_map.jsonl").read_text().splitlines() if l.strip()]
    assert all(json.loads(r) for r in rows)          # every line is valid json (no tear)
    statuses = {r.status for r in results}
    assert promotion.PROMOTE in statuses
```

(Fill the `...` from the fixture; the assertions are the contract.)

- [ ] **Step 4: Route the runner promote arm through `harness.promotion`**

Replace the unguarded promote in `run_iteration` (`runner.py:2236-2243` decision + `2274-2279` commit/advance). The promote arm must:
1. commit the candidate to the **job branch** (lineage head) as today (so `source_commit` exists), then
2. call `promotion.try_promote(MAIN_REPO_ROOT, ...)` against the **shared main repo** (where `champion` lives) — NOT the worktree.

The runner needs the **main repo root** (the worktree's parent repo). Add a `RunnerConfig.main_repo_root: Path | None = None` field (frozen → constructor arg, default None = single-lane: main repo == repo_root). Resolve it: `main_repo = (config.main_repo_root or config.repo_root).resolve()`. In a worktree, `git rev-parse --git-common-dir` gives `<main>/.git`; derive `main_repo` from it if `main_repo_root` is None and `repo_root` is a worktree.

Promote arm (in the scored set path, replacing the `promote` handling):

```python
    if t.action == "promote":
        decision_status = "success" if promo.status == "success" else "keep"
        reason = promo.reason
        # 1) commit the candidate to the job branch so it has a source commit.
        if config.commit_results:
            commit_iteration(config, state_path, decision_status, hyp_id,
                             state.iteration, reason=reason)
            source_commit = _run_git(repo_root, ["rev-parse", "HEAD"]).stdout.strip()
            # 2) serialized gated promotion against the SHARED champion.
            from harness import promotion as promo_mod
            main_repo = _main_repo_root(config)   # helper: parent repo of the worktree
            pres = promo_mod.try_promote(
                main_repo, job_id=config.job_id, source_commit=source_commit,
                rel_path=config.allowed_path, candidate_report=verify_result.report,
                baseline=baseline, sigma=noise.get("sigma"),
                sigma_is_provisional=bool(noise.get("is_provisional")),
                champion_ref=state.champion_ref, summary_dir=config.summary_dir,
                absolute_delta_fallback=config.absolute_delta_fallback,
            )
            if pres.status == promo_mod.PROMOTE:
                state.record_best(hyp_id, cand_cer)
                if promo.status == "success":
                    state.status = "success"
            else:
                # LOST race: keep the candidate as the lineage head (do NOT reset).
                # It stays committed on the job branch; the set continues from it.
                decision_status = "lineage_advance"
                reason = f"promotion lost race: {pres.reason}"
                # the set already closed (step_set promote→closed); reopen as refine
                # so the candidate keeps being cultivated against the higher champion.
                t = _reopen_as_refine(t, cand_cer, hyp_id)   # helper below
        else:
            # no commit_results (test/manual): mirror legacy best-tracking only.
            state.record_best(hyp_id, cand_cer)
```

Add helpers near `_set_state_from`:

```python
def _main_repo_root(config: RunnerConfig) -> Path:
    if config.main_repo_root is not None:
        return config.main_repo_root.resolve()
    common = _run_git(config.repo_root, ["rev-parse", "--git-common-dir"],
                      check=False).stdout.strip()
    if common:
        p = (config.repo_root / common).resolve()
        return p.parent if p.name == ".git" else p
    return config.repo_root.resolve()


def _reopen_as_refine(t, cer, hyp_id):
    from dataclasses import replace
    from harness.lineage import Transition
    return Transition(replace(t.state, phase="refine", close_reason=None,
                              best_cer=cer, best_hyp_id=hyp_id), "advance")
```

**Delete** the old `if t.action == "promote": head=rev-parse; advance_champion_ref(...)` block at `runner.py:2277-2279` — promotion now happens via `promotion.try_promote` (which does the CAS), NOT a raw `advance_champion_ref`. (`advance_champion_ref` remains in gitops for the bootstrap/single-lane path but is no longer the promote mechanism in the set path.)

> **Note on `run_job` bootstrap (`runner.py:2286`):** `ensure_champion_ref` must run on the **main repo**, not the worktree (champion lives there). Change to `gitops.ensure_champion_ref(_main_repo_root(config), state.champion_ref)`. In single-lane (no worktree), `_main_repo_root` returns `repo_root` → unchanged behavior.

Write a runner-level integration test (`worktree`+`promotion` markers): job in a worktree produces a champion-beating candidate → champion advances on the **main repo**, `promotion_map.jsonl` gets a row, job branch keeps the commit. And a lost-race variant (pre-seed lower live CER → candidate stays lineage head, champion unmoved).

- [ ] **Step 5: Add `--main-repo-root` CLI flag (optional; auto-derived otherwise)**

In `main` argparse, add `--main-repo-root` (default None → auto-derive via `--git-common-dir`). Thread into `RunnerConfig`. The launcher (Task 2) doesn't need to pass it — auto-derivation from the worktree is enough; the flag is an escape hatch.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_gitops_worktree.py tests/test_promotion.py tests/test_harness_runner.py -v`
Expected: PASS. Full suite: `python -m pytest -q` green.

- [ ] **Step 7: Commit**

```bash
git add harness/gitops.py harness/promotion.py harness/runner.py \
        tests/test_gitops_worktree.py tests/test_promotion.py tests/test_harness_runner.py
git commit -m "feat(promotion): serialized gated champion promotion (flock + re-validate + ref CAS + map) (phase3)"
```

---

## Task 4: Test marker separation (`pyproject.toml`)

Register markers so default CI runs only data-free tests (HARNESS-REDESIGN §166). Today `pyproject.toml` has no `[tool.pytest.ini_options].markers`.

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_markers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_markers.py`:

```python
"""tests/test_markers.py — marker registration + no-data default (phase3, §166)."""
from __future__ import annotations
import configparser, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_markers_registered():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    names = {m.split(":", 1)[0].strip() for m in markers}
    assert {"requires_data", "integration", "worktree", "promotion"} <= names


def test_default_addopts_excludes_requires_data():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = data["tool"]["pytest"]["ini_options"].get("addopts", "")
    assert "not requires_data" in addopts


def test_requires_data_test_is_skipped_by_default(pytestconfig):
    # a test tagged requires_data must not run under the default addopts.
    assert "not requires_data" in (pytestconfig.getoption("markexpr") or "")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_markers.py -v`
Expected: FAIL — `KeyError: 'markers'`.

- [ ] **Step 3: Register markers + default `-m "not requires_data"`**

Edit `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
# Default run excludes private-audio-data tests so the no-data suite is the
# required gate (HARNESS-REDESIGN §166). Opt in with -m requires_data / -m integration.
addopts = "-m 'not requires_data'"
markers = [
    "requires_data: needs private audio/holdout data (skipped in default no-data CI)",
    "integration: end-to-end across subprocess/git/fs (slower)",
    "worktree: exercises git worktree creation/cleanup",
    "promotion: exercises the serialized champion promotion lock + CAS",
]
```

> **Implementer caution:** existing tests that DO need data must be tagged `@pytest.mark.requires_data` or the new default silently *runs* them (markexpr only filters tagged tests; untagged always run). Audit: `grep -rn "data/raw\|holdout\|batch=\|run_verify\|judge.evaluate" tests/` and tag the ones that touch real data (likely `test_evaluate_*`, `test_verify_timeout` if it shells real verify, `test_seal_holdout`). Tag conservatively; a data-free test wrongly tagged just gets skipped in default CI (safe), a data test left untagged breaks no-data CI (caught immediately by running `-m "not requires_data"` with no data present).

- [ ] **Step 4: Run**

Run: `python -m pytest -q` (default = no-data) then `python -m pytest -m "worktree or promotion" -q`
Expected: default run green with the data tests skipped; marker-selected suites green.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_markers.py tests/  # + any newly-tagged tests
git commit -m "test: register requires_data/integration/worktree/promotion markers, default no-data (phase3, §166)"
```

---

## Task 5: Packaging — `git archive` source tarball + separate run-artifact tarball (lower priority)

HARNESS-REDESIGN §170/§273-291: source-only tarball via `git archive` with `.gitattributes export-ignore`; run artifacts as a separate tarball.

**Files:**
- Create: `.gitattributes`
- Create: `scripts/package_source.py`
- Test: `tests/test_package_source.py` (`integration` marker)

- [ ] **Step 1: Write the failing test**

Create `tests/test_package_source.py`:

```python
"""tests/test_package_source.py — git archive excludes runs/caches (phase3, §275)."""
from __future__ import annotations
import subprocess, tarfile
from pathlib import Path
import pytest

pytestmark = pytest.mark.integration


def _git(root, *a): subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True)


def test_source_archive_excludes_runs_and_caches(tmp_path):
    from scripts import package_source
    root = tmp_path / "r"; root.mkdir()
    _git(root, "init", "-q"); _git(root, "config", "user.email", "t@t"); _git(root, "config", "user.name", "t")
    (root / "harness").mkdir(); (root / "harness" / "runner.py").write_text("x\n")
    (root / "runs").mkdir(); (root / "runs" / "junk.json").write_text("{}\n")
    (root / ".gitattributes").write_text("runs/ export-ignore\n.pytest_cache/ export-ignore\n")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "c")
    out = tmp_path / "src.tar.gz"
    package_source.build_source_archive(root, "HEAD", out)
    names = tarfile.open(out).getnames()
    assert any("harness/runner.py" in n for n in names)
    assert not any(n.startswith("runs/") or "/runs/" in n for n in names)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_package_source.py -m integration -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.package_source'`.

- [ ] **Step 3: Create `.gitattributes` + `scripts/package_source.py`**

`.gitattributes` (HARNESS-REDESIGN §277):

```gitattributes
runs/ export-ignore
.venv/ export-ignore
.pytest_cache/ export-ignore
.ruff_cache/ export-ignore
.cache/ export-ignore
docs/reviews/ export-ignore
```

`scripts/package_source.py`:

```python
#!/usr/bin/env python3
"""scripts/package_source.py — reproducible source + run-artifact tarballs.

Source: `git archive` of the champion tree, honoring .gitattributes export-ignore
so runs/ and caches never leak (HARNESS-REDESIGN §275/§288). Run artifacts go in a
SEPARATE tarball (runs/_summary only — never the .git or per-iter caches)."""
from __future__ import annotations
import argparse, subprocess, tarfile
from pathlib import Path


def build_source_archive(repo_root: Path, ref: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "archive", "--format=tar.gz", "--worktree-attributes",
                    "-o", str(out), ref], cwd=repo_root, check=True,
                   capture_output=True, text=True)
    return out


def build_artifact_archive(repo_root: Path, out: Path,
                           summary_dir: Path = Path("runs/_summary")) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    src = repo_root / summary_dir
    with tarfile.open(out, "w:gz") as tar:
        if src.is_dir():
            tar.add(src, arcname=summary_dir.as_posix())
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Package source + run artifacts.")
    p.add_argument("--repo-root", type=Path, default=Path("."))
    p.add_argument("--ref", default="champion")
    p.add_argument("--out-dir", type=Path, default=Path("dist"))
    args = p.parse_args(argv)
    root = args.repo_root.resolve()
    short = subprocess.run(["git", "rev-parse", "--short", args.ref], cwd=root,
                           check=True, capture_output=True, text=True).stdout.strip()
    src = build_source_archive(root, args.ref, args.out_dir / f"aig-source-{short}.tar.gz")
    art = build_artifact_archive(root, args.out_dir / f"aig-artifacts-{short}.tar.gz")
    print(f"source={src}\nartifacts={art}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/test_package_source.py -m integration -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .gitattributes scripts/package_source.py tests/test_package_source.py
git commit -m "feat(packaging): git archive source + separate artifact tarball, export-ignore (phase3, §275)"
```

---

## Task 6: Docs + SSOT sync

**Files:**
- Modify: `docs/HARNESS-MECHANICS.md` (add §13 "worktree + parallel + gated promotion (phase2+3)")
- Modify: `docs/SSOT.md` (§3 code map: add `promotion` module; module list + active-work note)
- Verify: `tests/test_doc_consistency.py` if it enforces a module↔SSOT map (phase1 added `lineage`/`gitops` there).

- [ ] **Step 1: Run the doc-consistency guard**

Run: `python -m pytest tests/test_doc_consistency.py -v`
Expected: FAIL if it requires `promotion` in SSOT §3 (phase1 test `test_ssot_code_map_lists_every_harness_module` pattern).

- [ ] **Step 2: Update SSOT §3 + active-work**

Add `promotion`(serialized gated champion 승격: lock+re-validate+ref CAS) to the `harness/` module list in `docs/SSOT.md` §3; add a phase2+3 entry to the changelog section pointing at this plan.

- [ ] **Step 3: Add HARNESS-MECHANICS §13**

Document: worktree layout (protected champion never checked out; `job/<id>` worktree per job; lineage head = worktree HEAD; `repair`→`restore_lineage_head`, `reset`→`restore_file_from_ref(champion)`); parallel model (launcher Popen pool, concurrency cap 2, per-job `runs/_summary/<job_id>_*` isolation); gated promotion (flock lock, re-validate vs live champion CER, ref CAS splicing only `transcribe.py`, `promotion_map.jsonl`, lost-race-keeps-lineage-head); markers + packaging. Code-grounded (cite new functions).

- [ ] **Step 4: Run guard + full suite**

Run: `python -m pytest tests/test_doc_consistency.py -q && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/SSOT.md docs/HARNESS-MECHANICS.md
git commit -m "docs: worktree + parallel + gated promotion (phase2+3) in MECHANICS + SSOT"
```

---

## Self-Review

**Spec coverage (HARNESS-REDESIGN §195 change-points table → tasks):**

| §195 change-point | This plan |
|---|---|
| new `harness/gitops.py`: `prepare_job_worktree`, `restore_lineage_head`, `promote_to_champion`, `cleanup_worktree` | Task 1 (prepare/restore-lineage/cleanup/list) + Task 3 (`promote_to_champion` CAS) ✓ |
| `RunnerConfig`: `champion_ref`, `job_ref`, `job_worktree`, `set_max_steps`, `max_repairs`, `max_refines` | `champion_ref`/`max_repairs`/`max_refines` already in Phase 1; Phase 2 adds `main_repo_root` (job_ref/job_worktree are launcher concerns, not RunnerConfig — the worktree IS `repo_root`, so a separate field is redundant; documented divergence) ✓/△ |
| `ensure_worktree_ready`: separate champion vs job dirty rules | champion is **never** a runner `repo_root` (never checked out), so the existing `ensure_worktree_ready` runs only against the job worktree — no separate rule needed; the protection is structural (Task 1/2). Documented. △ |
| `rollback_paths`: target active lineage HEAD not champion | Task 2-3: `repair`→`restore_lineage_head` (worktree HEAD), `reset`→`restore_file_from_ref(champion)`; `rollback_paths` itself unchanged (already restores to HEAD=lineage head in a worktree) ✓ |
| `_decide_iteration`: set_phase + set_seed_ref | set_phase override already Phase 1; Task 2-4 makes the refine **seed/parent** the lineage head ✓ |
| `run_iteration`: dual decision | Phase 1 (`decide_lineage_progress`+`decide_promotion`); Task 3 routes promote through the gate ✓ |
| `commit_iteration`: split code vs metadata / promotion commit path | promotion commit path separated into `harness.promotion` (splice-only-`transcribe.py`); the code/metadata split is Phase-1.5 (stated dependency, not required here) ✓/deferred |
| `scripts/evolve.py` CLI: `--worktree-root` etc. | worktree creation moved to `scripts/launch_parallel.py` (per-job); `evolve.py` stays thin, runs inside an already-prepared worktree; `--main-repo-root` added to `main` (Task 3-5) ✓ |
| `tests/`: `requires_data`/`integration` markers + worktree/promotion tests | Task 4 (markers) + Tasks 1/3 (worktree/promotion tests) ✓ |
| branch protection (§168) | OPTIONAL note only — solo/local operator; the **flock lock is the real mechanism** (Task 3). Not a task. ✓ (per brief) |
| packaging `git archive` + `.gitattributes export-ignore` (§275) | Task 5 ✓ |

**Phase-1 wart decision:** refine-parent-hint-shows-champion → **fixed** in Task 2-4 (inject lineage head as the refine parent), scoped minimally (no full §209 portfolio `job_best` split).

**Phase-1.5 dependency:** stated explicitly (worktrees give per-job `runs/` isolation; off-git metadata only makes it cleaner; the promotion splice insulates `champion` either way). No task gated on it.

**Ordering / always-working:** Task 1 (pure git helpers) → Task 2 (single job in a worktree, no concurrency) → Task 3 (gated promotion) → Task 2-5 launcher is *defined* in Task 2 but concurrency is only *safe to enable* after Task 3's gate exists (the plan notes this; an operator running the launcher before Task 3 would hit the old unguarded promote — so the launcher commit is sequenced with the gate in mind; if strict, move the `run_pool` enablement note to post-Task-3). Markers/packaging last.

**Placeholder scan:** the `...` in `tests/test_promotion.py` (Step 3) and the worktree integration test mark **fixture wiring** (commit a file, capture its sha) that the implementer copies from the adjacent `repo` fixture / existing `test_harness_runner.py` helpers in-context; every **behavioral assertion** is concrete (status==PROMOTE/LOST, champion ref moved/unmoved, map row contents, no torn jsonl, cap<=2). No placeholder *implementation* code — all of `gitops.py`, `promotion.py`, `launch_parallel.py`, `package_source.py` is complete and runnable.

**Type consistency:** `promote_to_champion` returns `str | None` (None=CAS lost) ← consumed by `try_promote` as the lost-race signal → `PromotionResult.status ∈ {"promoted","lost_race"}` (module constants `PROMOTE`/`LOST`) → runner maps `PROMOTE`→`record_best`+champion advanced, `LOST`→`lineage_advance`+`_reopen_as_refine`. `read_ref` returns `str | None`; `promotion.try_promote` reads `champion_cer: float | None` from `live_champion_cer` and feeds `decide_promotion(champion_cer=...)` (Phase-1 signature, accepts None). `list_worktrees` returns `list[str]`. `RunnerConfig.main_repo_root: Path | None`. All git helpers keep the Phase-1 `(repo_root: Path, ...)` first-arg convention.

**Open questions for the operator:**
1. **flock vs the launcher being the only promoter** — the file lock defends even if the operator hand-runs a stray `evolve.py` outside the launcher. Keep flock as the source of truth (recommended), or rely on the launcher being the sole entrypoint? (Plan assumes flock.)
2. **Re-validation source for "live champion CER"** — this plan tails `promotion_map.jsonl`. Alternative: store the champion's CER in a `runs/_summary/champion.json` updated under the lock, or read it from the champion commit's committed score. Map-tail is simplest and append-only; confirm it's authoritative enough, or do you want a dedicated `champion.json`?
3. **Lost-race → reopen-as-refine** — a candidate that beat the *old* champion but lost the race is kept as the lineage head and cultivated against the *new* higher champion. Acceptable, or should a lost race instead bank the candidate and reset the set (cheaper, less GPU)? (Plan keeps + refines, per brief "lost race keeps the candidate as the lineage head".)
4. **Concurrency cap default 2** — confirm; raise if GPU-compute headroom proves larger in practice (memory is not the limit).
5. **`requires_data` tagging audit** — which existing tests actually touch private data must be hand-confirmed (Task 4 Step 3 caution); the operator knows the data-touching set best.
6. **Worktree root location** — launcher defaults `--worktree-root` to the repo's parent dir (`ROOT.parent`). Confirm that's writable/desired, vs a dedicated `../wt/` or `$XDG_CACHE`.
```