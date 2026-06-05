# Phase 3 Promotion Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the gated-promotion lost-race bugs (dirty-tree crash + portfolio/gate corruption), align the stub-start seed so the first scored candidate wins, preserve near-champion lineages as parents, and let a still-improving lineage spend less refine budget.

**Architecture:** Three bundles. **Bundle 1 (P0)** restructures the set-path promote arm in `harness/runner.py` so a candidate is committed only as a pool-inert `lineage_advance` checkpoint *before* the serialized gate runs; `record_best`/`global_best`/`success` are recorded *only after the gate WINS*; on a LOST race the working tree and HEAD are reconciled (advance = no-op, reset = restore+commit champion, repair = rewind to the prior lineage head) so the next iter's `ensure_worktree_ready` always sees a clean tree. Bundle 1 also makes the `promotion_map` seed opt-in via a new `--champion-cer` flag so a stub-start leaves the map empty and the first candidate wins both gates. **Bundle 2** registers a set's near-champion lineage best into the parent pool at set close. **Bundle 3** adds a "still-improving refine doesn't consume budget" rule in the pure `step_set` state machine.

**Tech Stack:** Python 3, stdlib `subprocess`/`json`, pytest (run with **base python**: `python -m pytest …`, NOT the `evolve` conda env). Git CLI for refs/worktrees. No new third-party deps.

---

## Ground-truth code anchors (re-verified 2026-06-05 against the branch)

- `harness/runner.py`
  - Set-path scored block: lines **2388–2500** (comment `# ── set path (set_budget>1)`). Promote/advance/repair/reset branches: **2410–2437**. `commit_iteration(...)` call: **2458–2459**. `try_promote` + lost-race `else:` branch: **2460–2499**.
  - `run_job` bootstrap (`ensure_champion_ref` + `seed_champion_cer`): **2506–2515**.
  - `commit_iteration` + `_CODE_CHECKPOINT_STATUSES`: **1968–2002**.
  - `_persist_decision` → `portfolio.update`: **1891–1906**.
  - `_main_repo_root`: **1374–1384**. `_set_state_from`/`_persist_set_state`: **1387–1413**. `_decide_iteration` refine-parent injection: **1334–1350**.
  - `RunnerConfig`: **221–248**. argparse: **2587–2645**.
- `harness/promotion.py`: `seed_champion_cer` **44–58**, `live_champion_cer` **61–75**, `try_promote` **78–121**.
- `harness/lineage.py`: `step_set` **80–133** (refine branch **114–131**).
- `harness/portfolio.py`: `update` **250–335** (`lineage_advance` near_best guard **332**, `global_best` on keep **289–291**), `parents_for_mode` **173–215**, `_retain_near_best` **337–362**.
- `harness/state.py`: `best_cer`/`record_best` **18–19, 77–81**; set_best fields **49–53**.
- `harness/gitops.py`: `ensure_champion_ref` **27–31**, `restore_file_from_ref` **34–37**, `restore_lineage_head` **67–71**, `read_ref` **92–96**, `promote_to_champion` **99–144**.
- Tests: `tests/test_harness_runner.py` (`_init_repo` **63–104** creates NO champion; set-path tests **1804–2154**), `tests/test_promotion.py` (`repo` fixture **21–33**), `tests/test_gitops_worktree.py`.
- **README §절대 금지:** re-measuring/overwriting `baseline/*.json` is FORBIDDEN — no task below reads, writes, or re-measures any `baseline/*.json`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `harness/runner.py` | Set-path promote arm restructure; `--champion-cer` → `RunnerConfig`; opt-in seeding in `run_job` bootstrap; Bundle 2 set-close parent registration call | Modify |
| `harness/lineage.py` | Pure `step_set` state machine; Bundle 3 "still-improving refine doesn't consume budget" rule | Modify |
| `harness/gitops.py` | New `rewind_to_prior_lineage_head` helper (HEAD~1 rewind, clean tree) | Modify |
| `harness/portfolio.py` | New `register_lineage_survivor` method (Bundle 2 explicit near-champion preservation) | Modify |
| `tests/test_gitops_worktree.py` | Test for `rewind_to_prior_lineage_head` | Modify |
| `tests/test_harness_runner.py` | Bundle 1 lost-race reset/repair/advance + WIN + F4 stub-start tests; Bundle 2 set-close; Bundle 3 CLI/threading | Modify |
| `tests/test_promotion.py` | (unchanged — existing seed semantics still valid for the explicit-seed case) | None |
| `tests/test_lineage_state_machine.py` | Bundle 3 pure-machine budget tests | Modify |
| `tests/test_portfolio.py` | Bundle 2 `register_lineage_survivor` unit test | Modify |

---

# BUNDLE 1 — P0 (ship together: one coherent PR)

> Root problem: the set-path promote arm records `record_best` + commits `keep`/`success` + (via `commit_iteration`→`_persist_decision`→`portfolio.update`) sets `global_best`/near_best **before** `try_promote` runs the real gate. When the gate LOSES the working tree is rolled back without moving HEAD (dirty-tree crash next iter — **F3**) and the lost candidate is banked as `state.best_cer`/`global_best` (**Missed#1**). On a stub-start the seed CER (0.1714) ≠ the stub champion's real CER (~0.41) so every improving iter funnels promote→gate→LOST every iteration (**F4**).

Bundle 1 keeps the **full suite green at every commit**. Tasks run in order; later tasks depend on the helper and config added earlier.

---

### Task 1: `rewind_to_prior_lineage_head` gitops helper (for F3 lost-race repair)

After the restructure, on `t.action == "promote"` the candidate is committed as a `lineage_advance` checkpoint (HEAD = candidate). If the gate then LOSES and the re-decided step is `repair`, the candidate must NOT remain HEAD: rewind to the **prior** lineage head (the commit before the candidate's checkpoint = `HEAD~1`) so HEAD == worktree, clean. `git reset --hard HEAD~1` is correct and clean because phase1.5 commits exactly one file (`workspace/transcribe.py`) per checkpoint — nothing else is in the tree to lose.

**Files:**
- Modify: `harness/gitops.py`
- Test: `tests/test_gitops_worktree.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gitops_worktree.py`:

```python
def test_rewind_to_prior_lineage_head_resets_head_and_tree(repo: Path, tmp_path) -> None:
    """A candidate committed as a checkpoint (HEAD) is rewound to the prior
    lineage head: HEAD moves back one commit AND the working tree matches it
    (clean), so the next ensure_worktree_ready passes."""
    import subprocess
    from harness import gitops

    def _git(root, *a):
        return subprocess.run(["git", *a], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    rel = Path("workspace/transcribe.py")
    prior_head = _git(repo, "rev-parse", "HEAD")
    prior_body = (repo / rel).read_text(encoding="utf-8")

    # commit a candidate checkpoint on top (HEAD advances).
    (repo / rel).write_text("CANDIDATE\n", encoding="utf-8")
    _git(repo, "add", "--", rel.as_posix())
    _git(repo, "commit", "-qm", "iter1: lineage_advance cand")
    assert _git(repo, "rev-parse", "HEAD") != prior_head

    gitops.rewind_to_prior_lineage_head(repo)

    assert _git(repo, "rev-parse", "HEAD") == prior_head        # HEAD back one commit
    assert (repo / rel).read_text(encoding="utf-8") == prior_body  # tree restored
    status = _git(repo, "status", "--porcelain")
    assert status == ""                                          # clean tree
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gitops_worktree.py::test_rewind_to_prior_lineage_head_resets_head_and_tree -v`
Expected: FAIL with `AttributeError: module 'harness.gitops' has no attribute 'rewind_to_prior_lineage_head'`

- [ ] **Step 3: Write minimal implementation**

In `harness/gitops.py`, add after `restore_lineage_head` (after line 71):

```python
def rewind_to_prior_lineage_head(repo_root: Path) -> None:
    """Rewind HEAD one commit (to the PRIOR lineage head) and match the working
    tree to it — clean. Used on a lost-race ``repair``: the candidate was already
    committed as a ``lineage_advance`` checkpoint (HEAD), but the re-decided set
    step rejected it as not even a local gain, so it must not remain the lineage
    head. ``--hard`` is safe because a phase1.5 checkpoint commits exactly one
    file (workspace/transcribe.py); there is nothing else in the tree to lose.
    Leaves HEAD == worktree so the next ensure_worktree_ready passes."""
    _git(repo_root, ["reset", "--hard", "HEAD~1"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gitops_worktree.py::test_rewind_to_prior_lineage_head_resets_head_and_tree -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/gitops.py tests/test_gitops_worktree.py
git commit -m "feat(gitops): rewind_to_prior_lineage_head for lost-race repair

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `--champion-cer` flag + opt-in seeding (F4)

The `run_job` bootstrap currently calls `seed_champion_cer(baseline_cer=baseline["baseline_cer"])` unconditionally, which assumes the bootstrap champion's CER == `baseline_cer` — false for a stub champion (real CER ~0.41 ≠ 0.1714). Make seeding **opt-in**:

- Add `champion_cer: float | None = None` to `RunnerConfig` and a `--champion-cer` CLI flag.
- In `run_job` bootstrap: seed the map ONLY when `config.champion_cer is not None` (operator explicitly knows the champion's CER) **OR** the map already has rows (a prior run seeded/promoted). Otherwise leave the map empty → `live_champion_cer` returns `None` → `decide_promotion(champion_cer=None)` → "first valid candidate wins", which aligns with the in-process gate (`state.best_cer is None` → keep). The first real candidate then promotes and champion advances from the stub.
- A *real* (non-stub) champion start: operator passes `--champion-cer <measured>`, OR the `promotion_map.jsonl` already carries rows from a prior run (the `seed_champion_cer` idempotency guard already no-ops then). **MUST NOT** re-measure or touch `baseline/*.json`.

`seed_champion_cer` itself is unchanged (still callable for the explicit case).

**Files:**
- Modify: `harness/runner.py` (`RunnerConfig` 221–248; `run_job` bootstrap 2506–2515; argparse 2609–2644)
- Test: `tests/test_harness_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_harness_runner.py`:

```python
@pytest.mark.worktree
@pytest.mark.promotion
def test_stub_start_no_champion_cer_first_candidate_promotes(tmp_path):
    """F4: on a stub-start with NO --champion-cer the promotion_map is left
    empty (live_champion_cer → None), so the first scored candidate WINS the gate
    and the champion advances — no false LOST funnel."""
    import json as _json
    import subprocess
    from harness import gitops, runner
    from harness.runner import RunnerConfig
    from harness.verify import VerifyResult

    def _git(root, *a):
        return subprocess.run(["git", *a], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    _init_repo(tmp_path)
    gitops.ensure_champion_ref(tmp_path, "champion")
    champ_before = gitops.read_ref(tmp_path, "refs/heads/champion")

    cfg_ = RunnerConfig(job_id="job", repo_root=tmp_path, main_repo_root=tmp_path,
                        set_budget=4, commit_results=True, candidate_cmd=None)

    def candidate(_prompt, out_dir):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(a, sr):\n    return 'STUBWIN'\n", encoding="utf-8")
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id):
        return VerifyResult(ok=True, hyp_id=hyp_id, out_dir=tmp_path / "runs" / hyp_id,
                            report={"corpus_cer": 0.41, "total_inference_time_s": 90.0},
                            per_file=[])

    runner.run_job.__wrapped__ if False else None  # no-op; documents run_job entry
    state, state_path = runner.load_or_init_state(cfg_)
    # mirror run_job's bootstrap (seeding) then run one iteration:
    gitops.ensure_champion_ref(runner._main_repo_root(cfg_), state.champion_ref)
    from harness import promotion as promo_mod
    # opt-in: champion_cer None + empty map → NO seed row written.
    assert cfg_.champion_cer is None
    if cfg_.champion_cer is not None:
        promo_mod.seed_champion_cer(runner._main_repo_root(cfg_), cfg_.summary_dir,
                                    baseline_cer=cfg_.champion_cer)
    mp = tmp_path / "runs/_summary/promotion_map.jsonl"
    assert not mp.is_file() or mp.read_text().strip() == ""   # empty → first wins

    runner.run_iteration(cfg_, state, state_path,
                         candidate_func=candidate, verify_func=verifier)

    champ_after = gitops.read_ref(tmp_path, "refs/heads/champion")
    assert champ_after is not None and champ_after != champ_before   # champion advanced
    assert _git(tmp_path, "show", "champion:workspace/transcribe.py") == (
        "def transcribe(a, sr):\n    return 'STUBWIN'")
    rows = [l for l in mp.read_text().splitlines() if l.strip()]
    assert any(_json.loads(r)["job_id"] == "job" for r in rows)
```

> This test bypasses `run_job`'s loop (it drives one `run_iteration`) but asserts the **opt-in seeding contract** (`config.champion_cer is None` ⇒ no seed row) plus the WIN behaviour that depends on Task 3's restructure. It will fully pass only after Task 3 lands; for Task 2 it pins the config field + the `champion_cer is None` default. Run it again at the end of Task 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest "tests/test_harness_runner.py::test_stub_start_no_champion_cer_first_candidate_promotes" -v`
Expected: FAIL with `AttributeError: 'RunnerConfig' object has no attribute 'champion_cer'`

- [ ] **Step 3a: Add the config field**

In `harness/runner.py`, in `RunnerConfig` add after `main_repo_root` (after line 248):

```python
    # phase3 F4: the measured CER of the bootstrap champion, used ONLY to seed
    # the promotion_map so the serialized gate has a baseline. None (default) →
    # do NOT seed: live_champion_cer returns None and the first scored candidate
    # wins the gate (aligns with the in-process gate's best_cer-None path). A
    # stub-start MUST leave this None; a real champion start passes the measured
    # value (or relies on a promotion_map that a prior run already populated).
    champion_cer: float | None = None
```

- [ ] **Step 3b: Make the bootstrap seeding opt-in**

In `harness/runner.py`, replace the `run_job` bootstrap block (lines 2506–2515):

```python
    if config.set_budget > 1 and config.commit_results:
        from harness import gitops, promotion as promo_mod
        main_repo = _main_repo_root(config)
        gitops.ensure_champion_ref(main_repo, state.champion_ref)
        baseline = _read_json(config.repo_root / config.baseline_file)
        promo_mod.seed_champion_cer(
            main_repo, config.summary_dir,
            baseline_cer=float(baseline.get("baseline_cer",
                                            baseline.get("target_cer", 0.0)) or 0.0),
            champion_commit=gitops.read_ref(main_repo, f"refs/heads/{state.champion_ref}"))
```

with:

```python
    if config.set_budget > 1 and config.commit_results:
        from harness import gitops, promotion as promo_mod
        main_repo = _main_repo_root(config)
        gitops.ensure_champion_ref(main_repo, state.champion_ref)
        # F4: seed the gate's baseline ONLY when the champion's real CER is known
        # (operator passed --champion-cer). Do NOT derive it from baseline_cer —
        # that assumes champion==baseline pipeline, false for a stub champion
        # (~0.41 ≠ baseline 0.1714) and funnels every improving iter into a false
        # LOST race. seed_champion_cer is idempotent, so if a prior run already
        # populated promotion_map.jsonl this is a no-op there too. When left
        # unseeded, live_champion_cer returns None → the first scored candidate
        # wins the gate (consistent with the in-process best_cer-None path), so a
        # stub-start champion advances from the first real candidate.
        if config.champion_cer is not None:
            promo_mod.seed_champion_cer(
                main_repo, config.summary_dir,
                baseline_cer=float(config.champion_cer),
                champion_commit=gitops.read_ref(
                    main_repo, f"refs/heads/{state.champion_ref}"))
```

- [ ] **Step 3c: Add the CLI flag + thread it into RunnerConfig**

In `harness/runner.py` argparse, add after the `--max-refines` argument (after line 2621):

```python
    parser.add_argument(
        "--champion-cer", type=float, default=None,
        help="measured CER of the bootstrap champion, to seed the gate. Omit on "
             "a stub-start (the first scored candidate then wins the gate and "
             "the champion advances). Never re-measures baseline/*.json.",
    )
```

and in the `RunnerConfig(...)` constructed in `main` (lines 2632–2644), add the field after `max_refines=args.max_refines,`:

```python
            champion_cer=args.champion_cer,
```

- [ ] **Step 4: Run the config-contract assertions (full WIN passes after Task 3)**

Run: `python -m pytest "tests/test_harness_runner.py::test_stub_start_no_champion_cer_first_candidate_promotes" -v`
Expected at this point: may still FAIL on the WIN assertions (champion advance) because the promote arm still records before the gate — that is fixed in Task 3. The `champion_cer is None` + empty-map assertions PASS. Confirm the failure is on a `champ_after`/`global_best` assertion, not on `AttributeError`.

Run the full suite to confirm no regression from the config/bootstrap change:
Run: `python -m pytest tests/ -q`
Expected: PASS (existing tests unaffected — they never set `champion_cer`, and the bootstrap change only removes the unconditional stub seed; existing set-path tests pre-seed the map themselves or assert first-candidate wins).

- [ ] **Step 5: Commit**

```bash
git add harness/runner.py tests/test_harness_runner.py
git commit -m "feat(runner): opt-in promotion seed via --champion-cer (F4)

Stub-start champions are not the baseline pipeline; deriving the gate seed
from baseline_cer funnels every improving iter into a false lost race. Seed
only when the champion CER is known; otherwise let the first scored candidate
win both gates.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Restructure the set-path promote arm (F3 + Missed#1)

This is the core. Nothing is recorded/committed as `keep`/`success` and `record_best` is NOT called until the gate WINS. The candidate is committed as a pool-inert `lineage_advance` checkpoint BEFORE the gate (gives `try_promote` a `source_commit`, advances HEAD to the verify-OK candidate, does not pollute `global_best`/`best_cer`). On WIN, record the promotion. On LOSE, re-decide and reconcile the tree.

**Concrete design (locked in):**

1. `t.action == "promote"`: set `decision_status = commit_status = "lineage_advance"`, `reason = promo.reason`. Do **NOT** call `state.record_best` and do **NOT** set `state.status="success"` yet. (advance/repair/reset branches unchanged.)
2. After `state.save` + `commit_iteration(... "lineage_advance" ...)`, HEAD == candidate. Capture `source_commit = HEAD` and run `try_promote`.
3. **WIN** (`pres.status == PROMOTE`): now record the promotion — `state.record_best(hyp_id, cand_cer)`; if `promo.status == "success"` set `state.status = "success"`; then call `commit_iteration(config, state_path, "keep"/"success", hyp_id, ...)` again so `_persist_decision` mirrors `global_best`/near_best for a real promotion. The job-branch HEAD commit stays labelled `lineage_advance` (cosmetic; champion ref + promotion_map are the source of truth) — the second `commit_iteration` is a no-op for the code commit (tree already == HEAD, `git diff --cached --quiet` returns 0) and runs `_persist_decision` only. `state.save` after recording.
4. **LOSE**: re-decide `t2 = step_set(s, Outcome(beats_champion=False, lineage_status=lin.status, …))`:
   - `advance`: candidate stays the `lineage_advance` HEAD — no-op, tree already clean. `_persist_set_state(state, t2.state)`, `state.save`.
   - `reset`: `rollback_paths(...)` + `restore_file_from_ref(champion)` to restore the worktree, then `commit_iteration(config, state_path, "reset", …)` so HEAD == worktree == champion (clean). `_persist_set_state`, `state.save`.
   - `repair`: the candidate is currently HEAD. `gitops.rewind_to_prior_lineage_head(repo_root)` (HEAD~1) so HEAD == worktree == prior lineage head, clean. `_persist_set_state`, `state.save`. (Lost-race `repair` arises when `lin.status` is `hold`/`dead_end` — e.g. the stub-start case where the first candidate has no lineage best yet but a later peer-seeded gate loss leaves a `hold`.)
5. `record_best` is reached ONLY on WIN; the pre-gate commit is `lineage_advance` (pool-inert by `portfolio.update`'s line-332 guard), so `global_best`/near_best are clean by construction on LOSE.

**Files:**
- Modify: `harness/runner.py` (set-path block 2410–2500)
- Test: `tests/test_harness_runner.py`

- [ ] **Step 1: Write the failing tests (WIN + lost-race reset/repair/advance + corruption guards)**

Append to `tests/test_harness_runner.py`:

```python
def _run_set_iter(tmp_path, *, cand_body, cer, state, premap=None,
                  main_repo=None, set_budget=4, max_repairs=2, max_refines=3):
    """Drive one set-path run_iteration in tmp_path with a champion ref. Returns
    (cfg, state_path). premap=text seeds promotion_map.jsonl before the iter."""
    import subprocess
    from harness import gitops, runner
    from harness.runner import RunnerConfig
    from harness.verify import VerifyResult
    gitops.ensure_champion_ref(tmp_path, "champion")
    if premap is not None:
        mp = tmp_path / "runs/_summary/promotion_map.jsonl"
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(premap, encoding="utf-8")
    cfg_ = RunnerConfig(job_id="job", repo_root=tmp_path,
                        main_repo_root=main_repo or tmp_path,
                        set_budget=set_budget, max_repairs=max_repairs,
                        max_refines=max_refines, commit_results=True,
                        candidate_cmd=None)
    state_path = tmp_path / "runs/_summary/job_state.json"

    def candidate(_prompt, out_dir):
        (tmp_path / "workspace/transcribe.py").write_text(cand_body, encoding="utf-8")
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id):
        return VerifyResult(ok=True, hyp_id=hyp_id, out_dir=tmp_path / "runs" / hyp_id,
                            report={"corpus_cer": cer, "total_inference_time_s": 90.0},
                            per_file=[])

    runner.run_iteration(cfg_, state, state_path,
                         candidate_func=candidate, verify_func=verifier)
    return cfg_, state_path


def _clean(tmp_path) -> bool:
    import subprocess
    out = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path,
                         check=True, capture_output=True, text=True).stdout
    return out.strip() == ""


@pytest.mark.worktree
@pytest.mark.promotion
def test_set_win_records_best_and_clean_tree(tmp_path):
    """WIN: champion advances via the gate CAS, global_best/best_cer update, tree
    clean, next ensure_worktree_ready passes."""
    from harness import gitops
    from harness.runner import RunnerConfig, ensure_worktree_ready
    from harness.state import HarnessState
    _init_repo(tmp_path)
    state = HarnessState(job_id="job", best_cer=0.20, best_hyp_id="champ")
    cfg_, _ = _run_set_iter(tmp_path, cand_body="def t():\n return 'W'\n",
                            cer=0.15, state=state)
    assert state.best_cer == 0.15                    # global best advanced ONLY on win
    champ = gitops.read_ref(tmp_path, "refs/heads/champion")
    assert champ is not None
    assert _clean(tmp_path)
    ensure_worktree_ready(RunnerConfig(job_id="job", repo_root=tmp_path,
                                       set_budget=4))   # no crash
    pf = _json.loads((tmp_path / "runs/_summary/job_portfolio.json").read_text())
    assert pf["global_best"] == "job_iter_001"


@pytest.mark.worktree
@pytest.mark.promotion
def test_set_lost_race_advance_does_not_bank_loser(tmp_path):
    """LOSE→advance: candidate beats local best (0.20) so step_set keeps it as the
    lineage head, but it lost the live gate (peer 0.10). best_cer/global_best must
    NOT be the lost candidate; tree clean."""
    from harness import gitops
    from harness.runner import RunnerConfig, ensure_worktree_ready
    from harness.state import HarnessState
    _init_repo(tmp_path)
    state = HarnessState(job_id="job", best_cer=0.20, best_hyp_id="champ")
    champ_before = None
    cfg_, _ = _run_set_iter(
        tmp_path, cand_body="def t():\n return 'LATE'\n", cer=0.15, state=state,
        premap=_json.dumps({"job_id": "peer", "cer": 0.10,
                            "champion_commit": "dead"}) + "\n")
    assert "LATE" in (tmp_path / "workspace/transcribe.py").read_text()
    assert state.set_phase not in ("idle", "closed")          # set alive
    assert state.best_cer == 0.20                              # NOT the loser 0.15
    assert state.best_hyp_id == "champ"
    assert _clean(tmp_path)
    pf = _json.loads((tmp_path / "runs/_summary/job_portfolio.json").read_text())
    assert pf.get("global_best") != "job_iter_001"            # loser not banked
    ensure_worktree_ready(RunnerConfig(job_id="job", repo_root=tmp_path, set_budget=4))


@pytest.mark.worktree
@pytest.mark.promotion
def test_set_lost_race_reset_commits_champion_clean_tree(tmp_path):
    """LOSE→reset (refine budget exhausted): champion restored AND committed so
    HEAD==worktree==champion (clean) — the F3 crash scenario."""
    from harness import gitops
    from harness.runner import RunnerConfig, ensure_worktree_ready
    from harness.state import HarnessState
    _init_repo(tmp_path)
    # an in-refine set with refines_used at the budget edge: a champion-beating
    # candidate that LOSES the gate → step_set(refine, beats=False) → reset.
    state = HarnessState(job_id="job", best_cer=0.20, best_hyp_id="champ",
                         set_id=1, set_phase="refine", set_best_cer=0.16,
                         set_best_hyp_id="prev", set_refines_used=2)
    cfg_, _ = _run_set_iter(
        tmp_path, cand_body="def t():\n return 'LOSE'\n", cer=0.15, state=state,
        max_refines=3,
        premap=_json.dumps({"job_id": "peer", "cer": 0.10,
                            "champion_commit": "dead"}) + "\n")
    assert state.set_phase == "idle"                          # set closed (reset)
    assert state.best_cer == 0.20                             # loser not banked
    assert _clean(tmp_path)                                   # F3: HEAD==worktree
    ensure_worktree_ready(RunnerConfig(job_id="job", repo_root=tmp_path, set_budget=4))


@pytest.mark.worktree
@pytest.mark.promotion
def test_set_lost_race_repair_rewinds_to_prior_head(tmp_path):
    """LOSE→repair: the candidate (committed as lineage_advance HEAD) is rewound
    to the prior lineage head; HEAD==worktree==prior head, clean. Triggered by a
    'hold' candidate that beat local best in-process but lost the live gate."""
    import subprocess
    from harness import gitops
    from harness.runner import RunnerConfig, ensure_worktree_ready
    from harness.state import HarnessState

    def _git(root, *a):
        return subprocess.run(["git", *a], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    _init_repo(tmp_path)
    gitops.ensure_champion_ref(tmp_path, "champion")
    # seed a lineage head commit (the prior head) so HEAD~1 exists distinct from
    # champion: commit a lineage_advance manually.
    (tmp_path / "workspace/transcribe.py").write_text(
        "def t():\n return 'PRIOR'\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "workspace/transcribe.py")
    _git(tmp_path, "commit", "-qm", "iter0: lineage_advance prior")
    prior_head = _git(tmp_path, "rev-parse", "HEAD")
    prior_body = (tmp_path / "workspace/transcribe.py").read_text()

    # refine state with a lineage best EQUAL to the incoming cer → 'hold'
    # (no local gain) but the candidate beats the local best in-process; the live
    # gate (peer 0.10) loses → step_set(refine, hold, beats=False) → repair.
    state = HarnessState(job_id="job", best_cer=0.20, best_hyp_id="champ",
                         set_id=1, set_phase="refine", set_best_cer=0.16,
                         set_best_hyp_id="prev", set_refines_used=0)
    _run_set_iter(
        tmp_path, cand_body="def t():\n return 'HOLDCAND'\n", cer=0.16, state=state,
        max_refines=3,
        premap=_json.dumps({"job_id": "peer", "cer": 0.10,
                            "champion_commit": "dead"}) + "\n")
    assert _git(tmp_path, "rev-parse", "HEAD") == prior_head      # rewound
    assert (tmp_path / "workspace/transcribe.py").read_text() == prior_body
    assert _clean(tmp_path)
    assert state.best_cer == 0.20                                 # loser not banked
    ensure_worktree_ready(RunnerConfig(job_id="job", repo_root=tmp_path, set_budget=4))
```

> Add `import json as _json` near the top of the test imports if not already present (the file imports `json`; reuse it instead — replace `_json.loads`/`_json.dumps` with `json.loads`/`json.dumps`). Verify the existing `import json` at line 8 and use `json.` to avoid a new import.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest "tests/test_harness_runner.py::test_set_win_records_best_and_clean_tree" "tests/test_harness_runner.py::test_set_lost_race_advance_does_not_bank_loser" "tests/test_harness_runner.py::test_set_lost_race_reset_commits_champion_clean_tree" "tests/test_harness_runner.py::test_set_lost_race_repair_rewinds_to_prior_head" -v`
Expected: FAILs — `test_set_lost_race_*` fail because the current code banks the loser (`state.best_cer == 0.15`) and leaves a dirty tree on reset (F3); the WIN test fails on the `global_best`/portfolio mirror timing.

- [ ] **Step 3: Implement the restructure**

In `harness/runner.py`, replace the promote branch (lines 2410–2419):

```python
    if t.action == "promote":
        # provisionally a champion beat → record_best now; the serialized gate
        # (post-save) re-validates against the LIVE champion and does the ref CAS.
        # If the gate LOSES, the post-save block re-decides with beats_champion=False.
        decision_status = "success" if promo.status == "success" else "keep"
        commit_status = decision_status
        reason = promo.reason
        state.record_best(hyp_id, cand_cer)
        if promo.status == "success":
            state.status = "success"
```

with:

```python
    if t.action == "promote":
        # F3/Missed#1: commit the candidate as a POOL-INERT lineage_advance
        # checkpoint BEFORE the gate. This gives try_promote a source_commit,
        # advances HEAD to the verify-OK candidate (it IS the lineage head), and
        # does NOT touch global_best/best_cer. record_best + keep/success are
        # deferred to the post-gate WIN branch so a lost race never banks the
        # loser or dirties the tree.
        decision_status = "lineage_advance"
        commit_status = decision_status
        reason = promo.reason
```

Then replace the post-commit promote/lost-race block (lines 2460–2499):

```python
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
                # promote-decision block above.
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

with:

```python
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
                # WIN: champion advanced via the gate CAS-splice. NOW record the
                # promotion — record_best + global_best/near_best mirror. The job
                # HEAD commit stays labelled lineage_advance (cosmetic; champion
                # ref + promotion_map are the source of truth). The second
                # commit_iteration is a no-op for the code commit (tree already ==
                # HEAD) but runs _persist_decision so portfolio.global_best updates.
                promote_status = "success" if promo.status == "success" else "keep"
                state.record_best(hyp_id, cand_cer)
                if promo.status == "success":
                    state.status = "success"
                state.save(state_path)
                commit_iteration(config, state_path, promote_status, hyp_id,
                                 state.iteration, reason=promo.reason)
            else:
                # LOST race: re-decide with beats_champion=False. record_best was
                # NOT called (the pre-gate commit is pool-inert lineage_advance), so
                # global_best/best_cer are clean. Reconcile HEAD==worktree per t2.
                outcome2 = Outcome(verify_ok=True, lineage_status=lin.status,
                                   cer=cand_cer, beats_champion=False, hyp_id=hyp_id)
                t2 = step_set(s, outcome2,
                              SetBudget(config.max_repairs, config.max_refines))
                _persist_set_state(state, t2.state)
                if t2.action == "reset":
                    # close the set: restore champion to the worktree AND commit it
                    # so HEAD==worktree==champion (clean) — F3 fix.
                    rollback_paths(repo_root, candidate_owned_statuses(
                        git_status(repo_root), config))
                    gitops.restore_file_from_ref(
                        repo_root, state.champion_ref, config.allowed_path)
                    state.save(state_path)
                    commit_iteration(config, state_path, "reset", hyp_id,
                                     state.iteration, reason=lin.reason)
                elif t2.action == "repair":
                    # the candidate is the lineage_advance HEAD but is not even a
                    # local gain → rewind to the PRIOR lineage head (HEAD~1) so
                    # HEAD==worktree, clean — F3 fix (restore_lineage_head would
                    # WRONGLY keep the candidate, which IS the current HEAD).
                    gitops.rewind_to_prior_lineage_head(repo_root)
                    state.save(state_path)
                else:
                    # advance/hold: the candidate stays the lineage_advance HEAD —
                    # no-op, tree already clean.
                    state.save(state_path)
    return result
```

> Note: the pre-gate `commit_iteration(... commit_status ...)` at line 2458 now commits `"lineage_advance"` for the promote action (it is in `_CODE_CHECKPOINT_STATUSES`, so the candidate is committed → HEAD advances, giving `try_promote` its `source_commit`). The `decision_status` in the `IterationResult` is also `"lineage_advance"` on a promote attempt until/unless the WIN branch records it — acceptable: callers key off champion ref + promotion_map, and the WIN branch's second `commit_iteration` persists the keep/success decision trace.

- [ ] **Step 4: Run the Task 3 tests + the Task 2 stub-start test + full suite**

Run: `python -m pytest "tests/test_harness_runner.py::test_set_win_records_best_and_clean_tree" "tests/test_harness_runner.py::test_set_lost_race_advance_does_not_bank_loser" "tests/test_harness_runner.py::test_set_lost_race_reset_commits_champion_clean_tree" "tests/test_harness_runner.py::test_set_lost_race_repair_rewinds_to_prior_head" "tests/test_harness_runner.py::test_stub_start_no_champion_cer_first_candidate_promotes" -v`
Expected: all PASS

Run: `python -m pytest tests/ -q`
Expected: PASS. **Note** the existing `test_set_promotes_when_beating_champion` (line 1840) and `test_job_in_worktree_promotes_to_main_repo_champion` (line 2042) assert `state.best_cer == 0.15` and champion advance on a WIN — still true (recorded in the WIN branch). The existing `test_job_in_worktree_lost_race_keeps_lineage_head` (line 2099) asserts the candidate stays the lineage head and champion is unmoved on a LOST→advance — still true. If `test_set_promotes_when_beating_champion` asserts `state.set_phase == "idle"`, confirm it still holds: `_persist_set_state` runs on the pre-gate `_persist_set_state(state, t.state)` (line 2439) with the promote transition (`phase="closed"` → idle), and the WIN branch does not re-open it. PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/runner.py tests/test_harness_runner.py
git commit -m "fix(runner): defer promotion record until gate wins; reconcile tree on lost race (F3+Missed#1)

Commit the candidate as a pool-inert lineage_advance checkpoint before the
serialized gate; only record_best/global_best/success after the gate WINS. On a
lost race, reconcile HEAD==worktree: advance=no-op, reset=restore+commit
champion, repair=rewind to the prior lineage head. Stops the dirty-tree crash
and stops banking the lost candidate as best/global_best.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# BUNDLE 2 — design-improvement (F2): preserve near-champion lineages as parents

> At set close (reset), if the set's lineage best (`state.set_best_*`) is within `near_best × factor` of `global_best`, register it into the parent pool so a near-champion lineage can be reused as a combine/refine parent. Reconcile with the already-landed refine-parent injection in `_decide_iteration` (lines 1334–1350, which injects the lineage head as the refine parent) so lineage material flows through ONE path. The registered entry's `diff_path` must point at `runs/<hyp>/candidate.diff` so the parent hint is non-empty.

Lineage best preservation must use `state.set_best_hyp_id`/`state.set_best_cer` (the set's recorded best), NOT the reset-iter candidate — that is the F2 gap (the mid-set best may not be the reset-iter candidate). It must read the lineage best's `score_report.json` to build a proper portfolio entry.

### Task 4: `Portfolio.register_lineage_survivor`

**Files:**
- Modify: `harness/portfolio.py`
- Test: `tests/test_portfolio.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_portfolio.py`:

```python
def test_register_lineage_survivor_adds_near_best_with_diff_path() -> None:
    from harness.portfolio import Portfolio
    p = Portfolio(job_id="job", global_best="champ")
    # a global best entry must exist for the near-best cutoff to resolve.
    p.family_best["f0"] = {"hyp_id": "champ", "cer": 0.16,
                           "harness_family_id": "f0",
                           "axis_metric": {}}
    survivor_report = {"corpus_cer": 0.17, "total_inference_time_s": 90.0,
                       "error_breakdown": {"del_ratio": 0.1, "sub_ratio": 0.1},
                       "hallucination_hit_rate": 0.0}
    added = p.register_lineage_survivor(
        hyp_id="job_iter_009", iteration=9, report=survivor_report,
        harness_family_id="lineage",
        diff_path="runs/job_iter_009/candidate.diff", factor=1.20)
    assert added is True
    hyps = {e["hyp_id"] for e in p.near_best}
    assert "job_iter_009" in hyps
    entry = next(e for e in p.near_best if e["hyp_id"] == "job_iter_009")
    assert entry["diff_path"] == "runs/job_iter_009/candidate.diff"
    assert entry["harness_family_id"] == "lineage"


def test_register_lineage_survivor_skips_far_from_champion() -> None:
    from harness.portfolio import Portfolio
    p = Portfolio(job_id="job", global_best="champ")
    p.family_best["f0"] = {"hyp_id": "champ", "cer": 0.16,
                           "harness_family_id": "f0", "axis_metric": {}}
    far_report = {"corpus_cer": 0.40, "total_inference_time_s": 90.0}
    added = p.register_lineage_survivor(
        hyp_id="job_iter_009", iteration=9, report=far_report,
        harness_family_id="lineage",
        diff_path="runs/job_iter_009/candidate.diff", factor=1.20)
    assert added is False
    assert all(e["hyp_id"] != "job_iter_009" for e in p.near_best)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest "tests/test_portfolio.py::test_register_lineage_survivor_adds_near_best_with_diff_path" "tests/test_portfolio.py::test_register_lineage_survivor_skips_far_from_champion" -v`
Expected: FAIL with `AttributeError: 'Portfolio' object has no attribute 'register_lineage_survivor'`

- [ ] **Step 3: Implement**

In `harness/portfolio.py`, add as a method on `Portfolio` after `_retain_near_best` (after line 362):

```python
    def register_lineage_survivor(
        self,
        *,
        hyp_id: str,
        iteration: int,
        report: dict[str, Any],
        harness_family_id: str,
        diff_path: str | None,
        factor: float = _NEAR_BEST_FACTOR,
    ) -> bool:
        """F2: explicitly preserve a closed set's near-champion lineage best as a
        parent. Unlike the incidental reset-iter near_best capture, this uses the
        set's RECORDED lineage best (its own hyp_id/report/diff), so a mid-set best
        that was later slightly regressed is not lost. Only retained when within
        global_best CER × factor — keeps the C2/C1 guard against far-from-champion
        pool pollution. Returns True if retained. diff_path must point at the
        survivor's own runs/<hyp>/candidate.diff so the parent hint is non-empty."""
        entry = _entry(
            hyp_id, iteration, "lineage_survivor", report,
            harness_signature="",
            harness_family_id=harness_family_id,
            self_declared_family_id=None,
            fingerprint=None,
            mode="refine",
            diff_path=diff_path,
        )
        if entry["cer"] is None:
            return False
        prev_factor = _NEAR_BEST_FACTOR
        if factor == prev_factor:
            return self._retain_near_best(entry)
        # honor a custom factor by temporarily comparing against the explicit cut.
        gb = global_best_entry(self)
        ref = gb.get("cer") if gb else None
        if not isinstance(ref, (int, float)):
            return False
        if entry["cer"] > ref * factor:
            return False
        return self._retain_near_best(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest "tests/test_portfolio.py::test_register_lineage_survivor_adds_near_best_with_diff_path" "tests/test_portfolio.py::test_register_lineage_survivor_skips_far_from_champion" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/portfolio.py tests/test_portfolio.py
git commit -m "feat(portfolio): register_lineage_survivor preserves near-champion lineage best (F2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Call `register_lineage_survivor` at set close (reset) in the runner

On a set-close `reset` (both the direct `t.action == "reset"` path and the lost-race `t2.action == "reset"` path), register the set's lineage best as a parent survivor. Reconcile with the refine-parent injection (lines 1334–1350): that path already exposes the *live* lineage head during an active set; the survivor registration exposes the *closed* set's best afterward — together one consistent "lineage material → parent" flow (active set: HEAD injection; closed set: portfolio survivor).

**Files:**
- Modify: `harness/runner.py` (add a helper + call it from both reset paths)
- Test: `tests/test_harness_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_harness_runner.py`:

```python
@pytest.mark.worktree
@pytest.mark.promotion
def test_set_close_reset_registers_lineage_survivor(tmp_path):
    """F2: when a set closes via reset and its lineage best is within
    near-champion factor, the lineage best is registered into the portfolio
    near_best pool with a diff_path pointing at its own candidate.diff."""
    from harness.runner import RunnerConfig
    from harness.state import HarnessState
    _init_repo(tmp_path)
    # set in refine at budget edge; a champion-LOSING candidate triggers reset.
    state = HarnessState(job_id="job", best_cer=0.16, best_hyp_id="champ",
                         set_id=1, set_phase="refine", set_best_cer=0.17,
                         set_best_hyp_id="job_iter_008", set_refines_used=2)
    # the lineage best's run dir + diff must exist for diff_path to be meaningful.
    survivor_dir = tmp_path / "runs/job_iter_008"
    survivor_dir.mkdir(parents=True, exist_ok=True)
    (survivor_dir / "candidate.diff").write_text("--- a\n+++ b\n", encoding="utf-8")
    (survivor_dir / "score_report.json").write_text(
        json.dumps({"corpus_cer": 0.17, "total_inference_time_s": 90.0}),
        encoding="utf-8")
    _run_set_iter(
        tmp_path, cand_body="def t():\n return 'LOSE'\n", cer=0.155, state=state,
        max_refines=3,
        premap=json.dumps({"job_id": "peer", "cer": 0.10,
                           "champion_commit": "dead"}) + "\n")
    assert state.set_phase == "idle"          # set closed (reset)
    pf = json.loads((tmp_path / "runs/_summary/job_portfolio.json").read_text())
    near = {e["hyp_id"]: e for e in pf.get("near_best", [])}
    assert "job_iter_008" in near
    assert near["job_iter_008"]["diff_path"] == "runs/job_iter_008/candidate.diff"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest "tests/test_harness_runner.py::test_set_close_reset_registers_lineage_survivor" -v`
Expected: FAIL (`job_iter_008` not in `near_best`)

- [ ] **Step 3: Implement the helper + wire both reset paths**

In `harness/runner.py`, add this helper just before `commit_iteration` (before line 1977):

```python
def _register_lineage_survivor(config: RunnerConfig, state: HarnessState) -> None:
    """F2: on set close (reset), preserve the set's near-champion lineage best as
    a parent. Best effort — never raises into the commit/close path. Uses the
    set's RECORDED best (state.set_best_*), reading its own runs/<hyp>/ report +
    diff, so a mid-set best is preserved even if the reset candidate regressed."""
    try:
        if not state.set_best_hyp_id:
            return
        out_dir = config.repo_root / config.runs_dir / state.set_best_hyp_id
        report = _read_score_report(out_dir)
        if not report:
            return
        diff_path = (config.runs_dir / state.set_best_hyp_id / "candidate.diff").as_posix()
        portfolio_path = (
            config.repo_root / config.summary_dir / f"{config.job_id}_portfolio.json"
        )
        from harness.portfolio import Portfolio
        portfolio = Portfolio.load(portfolio_path)
        portfolio.job_id = config.job_id
        added = portfolio.register_lineage_survivor(
            hyp_id=state.set_best_hyp_id, iteration=state.iteration,
            report=report, harness_family_id="lineage", diff_path=diff_path)
        if added:
            portfolio.save(portfolio_path)
    except Exception:
        return
```

Then call it in the **direct reset** branch. In the set-path block, after the `commit_iteration(...)` for a reset action and before `return result`, the simplest reconciliation is to register right after `_persist_set_state` knows the set closed. Add the call inside the existing `else:  # "repair" or "reset"` branch is too early (state not yet persisted), so add it after `state.save(state_path)` at line 2456 guarded on the closed reset. Concretely, in the direct path, replace the `if config.commit_results:` block opener at line 2457 region by inserting the survivor call right after the **non-promote** `commit_iteration` runs. Add immediately after line 2459 (`state.iteration, reason=reason)`):

```python
        if t.action == "reset":
            _register_lineage_survivor(config, state)
```

And in the **lost-race reset** branch (Task 3's WIN/LOSE block), after the `commit_iteration(config, state_path, "reset", …)` call, add:

```python
                    _register_lineage_survivor(config, state)
```

(place it immediately after the reset `commit_iteration(...)` line, before the branch ends.)

- [ ] **Step 4: Run test + full suite**

Run: `python -m pytest "tests/test_harness_runner.py::test_set_close_reset_registers_lineage_survivor" -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/runner.py tests/test_harness_runner.py
git commit -m "feat(runner): register lineage survivor at set close (F2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# BUNDLE 3 — tuning (F1): a still-improving refine should not burn budget

> `--max-refines` is already a CLI knob (verified: argparse line 2618, threaded into `RunnerConfig.max_refines`). F1 proposal 1 is therefore already available operationally. This bundle adds proposal 2: in the pure `step_set` refine branch, a refine that **strictly advances** the lineage does NOT consume the refine budget — budget is only spent on a `hold`/broken/dead_end refine. This structurally prevents a monotonically-improving lineage from being cut. Lands after Bundle 1 so the reset path is already correct.

The change is confined to `harness/lineage.py` `step_set` refine branch (lines 114–131). Currently `used = state.refines_used + 1` is computed unconditionally and applied to every refine outcome. New rule: on a strict `advance`, keep `refines_used` unchanged (do not increment) and never close on budget for that step; on `hold`/broken/`dead_end`, increment and close at budget as before.

### Task 6: still-improving refine does not consume budget

**Files:**
- Modify: `harness/lineage.py` (`step_set` refine branch 114–131)
- Test: `tests/test_lineage_state_machine.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lineage_state_machine.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest "tests/test_lineage_state_machine.py::test_refine_strict_advance_does_not_consume_budget" "tests/test_lineage_state_machine.py::test_refine_hold_still_consumes_budget_and_closes" -v`
Expected: the advance test FAILs (old rule increments to 3 and closes via `refine_budget`); the hold test PASSes (unchanged behavior).

- [ ] **Step 3: Implement**

In `harness/lineage.py`, replace the refine branch (lines 114–131):

```python
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
```

with:

```python
    if state.phase == "refine":
        if outcome.verify_ok and outcome.lineage_status == "advance":
            # F1: a refine that STRICTLY advances the lineage does NOT consume the
            # refine budget — budget exists to bound *unproductive* refining, so a
            # monotonically-improving lineage is never cut mid-climb. Budget is
            # spent only on hold / broken / dead_end below.
            return Transition(
                replace(state, best_cer=outcome.cer, best_hyp_id=outcome.hyp_id),
                "advance",
            )
        used = state.refines_used + 1
        if not outcome.verify_ok or outcome.lineage_status == "dead_end":
            # broken/catastrophic refine: roll back to lineage head, keep set
            # alive until budget so another refine can try a different edit.
            if used >= budget.max_refines:
                return _close(replace(state, refines_used=used), "refine_budget")
            return Transition(replace(state, refines_used=used), "repair")
        # hold: no local gain — roll back candidate, retry refine until budget.
        if used >= budget.max_refines:
            return _close(replace(state, refines_used=used), "refine_budget")
        return Transition(replace(state, refines_used=used), "repair")
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest "tests/test_lineage_state_machine.py::test_refine_strict_advance_does_not_consume_budget" "tests/test_lineage_state_machine.py::test_refine_hold_still_consumes_budget_and_closes" -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: PASS. **Note:** check existing `test_lineage_state_machine.py` cases that assert `refines_used` increments on an `advance` refine — if any exist, they encode the OLD rule and must be updated to the new semantics (an advancing refine no longer increments). Update only those whose intent was the budget-counting behavior, not the transition action.

- [ ] **Step 5: Commit**

```bash
git add harness/lineage.py tests/test_lineage_state_machine.py
git commit -m "feat(lineage): strict-advance refine does not consume refine budget (F1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

### Spec coverage

| Spec item | Bundle | Task(s) |
|---|---|---|
| **F4** — opt-in seed; stub-start first candidate wins both gates; no `baseline/*.json` re-measure | 1 | Task 2 (`--champion-cer`, opt-in `run_job` seed) + Task 3 (WIN records on first candidate) |
| **F3** — restructure promote arm; commit `lineage_advance` pre-gate; lost-race reset commits champion; lost-race repair rewinds to prior head; clean tree next iter | 1 | Task 1 (`rewind_to_prior_lineage_head`) + Task 3 (restructure, reset commit, repair rewind, advance no-op) |
| **Missed#1** — lost candidate not banked as `best_cer`/`global_best`; `record_best` only on WIN | 1 | Task 3 (defer `record_best`; pre-gate commit is pool-inert `lineage_advance`) + tests asserting `best_cer`/`global_best` not the loser |
| **F2** — set-close lineage best registered as parent; reconcile with refine-parent injection; non-empty `diff_path` | 2 | Task 4 (`register_lineage_survivor`) + Task 5 (call at both reset paths; `diff_path` → `runs/<hyp>/candidate.diff`) |
| **F1** — refine budget knob + still-improving refine doesn't consume budget | 3 | `--max-refines` already exists (noted); Task 6 (strict-advance refine no budget spend) |

### Placeholder scan

No `TBD`/`TODO`/`...`/"add error handling"/"write tests for the above" remain. Every code step shows complete real code. Every test step shows the full test body. Every run step gives an exact `python -m pytest …` command and expected PASS/FAIL.

### Type consistency

- `rewind_to_prior_lineage_head(repo_root: Path) -> None` — defined Task 1, called Task 3 (`gitops.rewind_to_prior_lineage_head(repo_root)`). Match.
- `RunnerConfig.champion_cer: float | None = None` — defined Task 2, read in `run_job` (`config.champion_cer is not None`) and `main` (`champion_cer=args.champion_cer`). Match.
- `Portfolio.register_lineage_survivor(*, hyp_id, iteration, report, harness_family_id, diff_path, factor=_NEAR_BEST_FACTOR) -> bool` — defined Task 4, called Task 5 with all keyword args except `factor` (defaulted). Match.
- `_register_lineage_survivor(config: RunnerConfig, state: HarnessState) -> None` — defined Task 5, called from both reset branches. Match.
- `step_set` signature unchanged (Task 6 edits the body only); `Transition`/`SetState`/`Outcome` field names (`refines_used`, `best_cer`, `best_hyp_id`, `phase`, `action`) used consistently with `harness/lineage.py`.
- `commit_iteration(config, state_path, status, hyp_id, iteration, reason=…)` — call sites in Task 3 (WIN `keep`/`success`, lost-race `reset`) match the existing signature (lines 1977–1984). `"lineage_advance"`, `"keep"`, `"success"`, `"reset"` are all in `_CODE_CHECKPOINT_STATUSES` (line 1974).

### Risk notes for the executor

- Task 3 must keep the **pre-gate** `_persist_set_state(state, t.state)` (line 2439) and `commit_iteration(..., commit_status, ...)` (line 2458) — the promote action now commits `"lineage_advance"` there. Do not remove them.
- When running the full suite after Task 3, re-read the three existing set-path WIN/lost-race tests (lines 1840, 2042, 2099) before assuming green; they assert champion advance / lineage-head retention that the restructure preserves, but confirm the `state.set_phase`/`best_cer` timing matches (WIN records in the new WIN branch).
- Bundle 1 is the PR; Bundles 2 and 3 may be separate PRs or follow-on commits. Each task leaves the full suite green.
