# Phase 2 ↔ Phase 3 drift review (post `phase3_001`)

Scope: `scripts/analyze_run.py`, `scripts/evaluate_holdout.py`,
`docs/templates/REPORT.md`, and their two smoke tests. Read-only; no
analyzer/holdout runs against real data — only `pytest` on the smoke suite.

---

## Verdict: **YELLOW**

Both scripts **run** (smoke tests pass), **honor all template variables**,
and **produce a syntactically valid REPORT.md / HOLDOUT.md**. But against the
real `phase3_001` state, `analyze_run.py` will produce a **materially
misleading** report:

- the universe of "iterations" picks up 3 stub probes + a dry run as if they
  were real evolution iters (inflates `n_total_iter` 25 → 29, distorts wall
  clock, top3-share Pareto, recovery rate, `_per_file_movers`),
- `final_corpus_cer` is the *time-last* run, not the *best* run (last
  produced is iter25 cer=0.2580; best is iter9 cer=0.2545),
- accept counts mostly align with the harness, but the commit attribution is
  off by one for ~1/3 of iters (timestamp heuristic places `produced_at`
  ahead of the iter's own commit),
- the D. category-distribution axis is degenerate (every commit subject is
  `iterN: keep/reject phase3_001_iter_NNN`, which carries zero keywords
  from `chunking|prompt|decode|post`; therefore **100% unclassified**),
- the H. reasoning-alignment axis is silent (`n_checked=0`) for the same
  reason.

`evaluate_holdout.py` looks correct against PHASE2/PHASE3-PLAN: lock-gated,
default refuses chmod without `--unseal`, finally-reseal is unconditional
and handles per-target failure, `--dry-run` does not touch the sealed
directories. One minor leftover (`_is_sealed` is dead code).

---

## Critical (blocks Phase 2 execution)

None. Both scripts execute and produce non-empty markdown.

---

## Important (output is wrong or misleading)

### I1 — `analyze_run.py` does not exclude `manual_*`, `dry_iter_001`, or other non-eval-iter directories

`scripts/analyze_run.py:32-35`

```
_EXCLUDED_DIRNAMES = {"_summary", "_telemetry"}
_EXCLUDED_PREFIXES = ("_", "holdout_", "holdout")
```

`runs/` currently contains:

```
phase3_001_iter_001 … phase3_001_iter_025    (the actual 25 iters)
manual_1780023598 / 1780023620 / 1780024457   (3 stub-probe runs, cer≈0.9924)
dry_iter_001                                  (Phase 3.7 dry-run leftover)
sigma_0_… / sigma_1_… (×4)                   (no score_report → already skipped)
```

`discover_iterations()` against the real tree returns **29 iters**, not 25.
The 3 `manual_*` runs and `dry_iter_001` all carry
`"batch": "AIG_녹취반출_20250715"` so the §170 batch filter does not catch
them either. Verified:

```
phase3_001_iter_001 cer=0.4114 KEEP   ← first KEEP after manual stubs
manual_1780023598    cer=0.9924 KEEP   ← becomes running_best
manual_1780023620    cer=0.9924 rev
manual_1780024457    cer=0.9924 rev
dry_iter_001         cer=0.9924 rev
```

Downstream impact on REPORT.md:

- `n_total_iter` = 29 (should be 25), `accept_rate_pct` distorted.
- `total_wall_clock` runs from 03:00 (manual probe) to 05:30 (iter25) ~2.5h
  instead of phase3_001 start→end ~2h.
- `_per_file_movers` compares first iter (manual stub, cer~0.99) against
  last iter (iter25 cer~0.26) — every file shows ~0.7 absolute improvement,
  meaningless.
- `delta_cer_per_min` uses `initial_cer=0.9924 − final_cer=0.2580` → fake
  ~5× improvement.
- `_top3_share` and `_recovery_and_streak` include the stub-probe rejects
  as rollback episodes.

PHASE3-PLAN §3.7 explicitly warns that dry artifacts pollute SSOT and lists
`rm -rf runs/dry_*` as cleanup — but `manual_*` and `dry_iter_001` are
*still in the tree right now*, so analyze_run inherits them.

Fix surface: either tighten `_EXCLUDED_PREFIXES` to include
`("manual_", "dry_", "sigma_")` (or invert: require the prefix to match the
`--job-id`), or accept a `--job-id phase3_001` filter and discover only
`runs/phase3_001_iter_*/`. The CLI already has `--job-id` for the report
header but does not use it as a discovery filter.

### I2 — `final_corpus_cer` reports the time-last iter, not the best iter

`scripts/analyze_run.py:741-744`

```python
final = iters[-1] if iters else None
final_cer = final.corpus_cer if final else None
target_cer = target_cer_json.get("target_cer")
target_reached = (
    final_cer is not None and target_cer is not None and final_cer <= target_cer
)
```

After `phase3_001`, iters[-1] is `phase3_001_iter_025` (cer=0.2580, rejected).
The actual harness `best_cer` in `runs/_summary/phase3_001_state.json` is
`0.2545004618…` (iter9). Reporting 0.2580 understates the run by ~0.0035 and
makes `target_reached` use the wrong number.

Phase 2 spec wording (§3.3 A) says "final cer, target 도달 여부" — ambiguous
but the harness's contract is *best-so-far*: PHASE3-PLAN §6 says target
success is checked per-iter, and `HarnessState.best_cer` is the SSOT. The
report should read `state.best_cer` (or the running-best computed during
classify), not iters[-1].

Same field is also used for the holdout-eval comparison in
`evaluate_holdout._last_accepted_eval_run` (line 130: "candidates.sort →
return candidates[-1][1]"), which picks the *most recent produced* eval
report rather than the *best-accepted* run. After phase3_001 the most
recent eval run is `phase3_001_iter_025` (rejected, cer=0.2580); the holdout
should be compared against `phase3_001_iter_009` (kept, cer=0.2545). The
function name `_last_accepted_eval_run` is therefore a lie — it returns
`_last_produced_eval_run`. Docstring even acknowledges this.

### I3 — git commit attribution is off by one for ~1/3 of iters

`scripts/analyze_run.py:219-267` — `enrich_with_git` matches the nearest
*preceding* commit by unix timestamp. But the harness writes the commit
*after* `produced_at` is recorded:

```
iter 9 score_report.produced_at = 04:04:54  (judge.evaluate finished)
iter 9 commit ts                ≈ 04:05:xx  (commit_iteration ran after)
```

So `ts <= target_unix` picks the *previous* iter's commit. Verified:

```
phase3_001_iter_001 -> iter1: keep phase3_001_iter_001   ✓
phase3_001_iter_002 -> iter2: reject phase3_001_iter_002 ✓
phase3_001_iter_005 -> iter4: keep phase3_001_iter_004   ✗ off by one
phase3_001_iter_006 -> iter5: keep phase3_001_iter_005   ✗
phase3_001_iter_011 -> iter10: reject phase3_001_iter_010 ✗
phase3_001_iter_014 -> iter13: reject phase3_001_iter_013 ✗
phase3_001_iter_015 -> iter14: reject phase3_001_iter_014 ✗
phase3_001_iter_021 -> iter20: reject phase3_001_iter_020 ✗
phase3_001_iter_022 -> iter21: reject phase3_001_iter_021 ✗
```

Direct impact is muted because the harness commit subjects are
`iterN: keep|reject phase3_001_iter_NNN` and carry no keywords matching
`_CATEGORY_PATTERNS` — both correct and shifted attributions land on
`unclassified`. But once a future harness writes meaningful subjects
(or someone amends old commits), the off-by-one becomes a visible bug.

Cheaper fix than nearest-by-time: the harness already encodes the iter id
in the subject (`iterN: status hyp_id`). Parse `commit_subject` and match
on `hyp_id` directly, falling back to timestamp only if no match. Or
match nearest *following* commit (ts >= produced_at), then take the
first whose subject mentions `hyp_id`.

### I4 — D. category-distribution and H. reasoning-alignment are degenerate on the current commit format

`scripts/analyze_run.py:43-81` keyword patterns include `chunk|window|...`,
`prompt|token|language`, etc., plus their Korean equivalents. The current
harness commits (`harness/runner.py:300`) are:

```python
["commit", "-m", f"iter{iteration}: {status} {hyp_id}"]
# → "iter9: keep phase3_001_iter_009"
```

No subject ever contains a category or reasoning keyword. Therefore:

- `cat_chunking = cat_prompt = cat_decode = cat_post = 0`,
- `cat_other` (unclassified) = `n_accepted` (whatever it ends up being),
- `concentration_warning` → 100% unclassified, prints "다양성 부족" warning
  spuriously,
- `_reasoning_alignment` → `n_checked = 0` → `auto_alignment_pct = "n/a"`.

Two valid fixes:

1. **At the harness layer** — include a one-line agent-supplied rationale
   in the commit body. PHASE3-PLAN §7 already says HISTORY captures
   "관찰/분석/다음 후보" and `harness/history.py` writes that to HISTORY.md;
   the iter commit subject could pull the keyword from there.
2. **At the analyzer layer** — parse `claude_stdout.txt` /
   `candidate.diff` instead of the commit subject. The diff is the actual
   change; keyword-search the diff body for category classification, and
   keyword-search the candidate's `prompt.md` reflection (or HISTORY entry
   `### 분석` paragraph) for reasoning alignment.

PHASE2-PLAN §3.3 D explicitly says "채택된 commit 의 메시지·diff 에서 키워드
매칭" — diff was always in scope; analyzer just doesn't actually look at
`candidate.diff` files even though they exist in every `runs/<hyp_id>/`.

### I5 — `analyze_run` ignores `phase3_001_state.json` entirely

`harness/state.py` writes `{job_id, iteration, best_cer, best_hyp_id, status}`
to `runs/_summary/<job_id>_state.json`. This is the SSOT for accept/best.
`analyze_run.py` never reads it — it independently re-derives running_best
via `classify_iterations` using a delta threshold.

For `phase3_001` the two paths happen to agree on keep/reject because both
fall back to the same `0.01` absolute-delta when sigma is provisional
(`baseline/noise_floor.json: sigma=0, is_provisional=true` → matches
`_NOISE_DEFAULT_DELTA=0.01` in analyzer and harness's `PolicyConfig
.absolute_delta_fallback=0.01`). But this is coincidence, not contract.
If anyone tweaks one constant the report and the actual harness decisions
will silently diverge.

Recommended: read `runs/_summary/<job_id>_state.json` as the authoritative
best, and tag each iter's `accepted` by walking the HarnessState's best
trajectory. Or at minimum cross-check and warn on divergence.

---

## Minor (cleanup)

### M1 — `_is_sealed()` is dead code

`scripts/evaluate_holdout.py:77-91` defines `_is_sealed` and the function
is never called anywhere in the file. Safe to delete.

### M2 — Local variable `target` shadowed in `evaluate_holdout.main`

`scripts/evaluate_holdout.py:309` binds `target = _read_json(baseline_dir
/ "target_cer.json")`, then line 372 `for target in (wav_dir, label_dir):`
reuses the name. Currently harmless because nothing after the for-loop
reads the original `target` dict, but a future edit could be confusing.

### M3 — Stale "autoresearch" references in `analyze_run.py`

`scripts/analyze_run.py:41,222,224` mention "autoresearch" in docstrings.
PHASE3-PLAN §3 explicitly says autoresearch is historical (see
`docs/AUTORESEARCH.md`). Replace with "harness" / "Phase 3 harness".

### M4 — `REPORT.md` template uses `KEEP/REVERT`; harness and PHASE3-PLAN use `keep/reject`

`scripts/analyze_run.py:404` formats the accept-timeline as
`'KEEP' if it.accepted else 'REVERT'`. PHASE3-PLAN §6 and
`harness/policy.py` consistently use `keep`/`reject`/`success`.
Cosmetic but trips a reader who searches for the canonical term.

### M5 — `datetime.utcnow()` deprecation warning

`scripts/analyze_run.py:768` raises `DeprecationWarning` on Python 3.12
(observed in the test run). Replace with `datetime.now(UTC).isoformat()`.

### M6 — `commit.txt` sidecar branch in `enrich_with_git` is unused

`scripts/analyze_run.py:244-252` first looks for
`runs/<hyp_id>/commit.txt`. The current harness never writes that file
(`harness/runner.py` does not produce a `commit.txt`). Either drop the
sidecar branch or have the harness emit it (would also fix I3).

### M7 — Phase 2 plan reference vs reality on diagnosis schema

Comment at `scripts/analyze_run.py:476-498` reads `diag.get("focus_files")`
which matches the real schema (`runs/.../diagnosis_report.json` has
`focus_files: [{wav, why_selected}]`). Fine. But PHASE2-PLAN §3.1 phrase
"`diagnosis_report.json` — 당시 에이전트에게 노출된 11파일 summary +
focus 표시" anchors on a fixed 11-file profile — current diagnosis carries
`per_file_diagnosis` (all 11 files) + `focus_files` (subset by
`why_selected`). Consistent with the spec; flagging only because PHASE2-PLAN
says "11파일 summary + focus 표시" and the analyzer only consumes
`focus_files`, never the `per_file_diagnosis` payload. If the spec's intent
was to *also* trend per-file diagnosis flags over time, the analyzer is
under-reading.

---

## Test results

```
tests/test_analyze_smoke.py::test_analyze_run_renders_template PASSED
tests/test_analyze_smoke.py::test_category_distribution_sum_matches_accepted PASSED
tests/test_analyze_smoke.py::test_handles_no_iterations PASSED
tests/test_evaluate_holdout_smoke.py::test_refuses_without_lock PASSED
tests/test_evaluate_holdout_smoke.py::test_dry_run_with_lock_succeeds PASSED
tests/test_evaluate_holdout_smoke.py::test_mutually_exclusive_unseal_and_dry_run PASSED
tests/test_evaluate_holdout_smoke.py::test_default_refuses_without_unseal PASSED
======================== 7 passed, 2 warnings in 0.15s =========================
```

Both smoke suites pass clean. Worth noting they only exercise the *schema*
contract, not the harness-integration contract:

- `test_analyze_smoke.py` writes synthetic `runs/iter_0N/` directories with
  the documented score_report schema — same shape as `judge/evaluate.py`
  produces, so the analyzer's *schema reader* is verified. The test never
  exercises (a) commit-subject parsing, (b) state.json consumption,
  (c) exclusion of manual/dry/sigma directories, (d) Korean commit
  formats — all the things that drift between Phase 2 (autoresearch-era)
  and Phase 3 (self-built harness era).
- `test_evaluate_holdout_smoke.py` verifies argument refusal contracts
  (lock, --unseal, --dry-run) but does not exercise the real chmod path
  or the `_last_accepted_eval_run` selection (I2). A targeted regression
  for I2 would `mkdir runs/iterA`, `mkdir runs/iterB` with `produced_at`
  ordering vs `corpus_cer` ordering inverted, and assert that the holdout
  comparison anchor is the *best*, not the *most recent*.

---

## Recommendation (priority order)

The job has already finished; the immediate goal is making `analyze_run.py`
+ `evaluate_holdout.py` produce a trustworthy report for `phase3_001`
without rewriting the contract.

1. **Fix I1 first** (one-line change in `_EXCLUDED_PREFIXES` or wire
   `--job-id phase3_001` into `discover_iterations` as a positive filter).
   This single fix removes the manual_/dry_ pollution and stops the bogus
   `_per_file_movers`, `total_wall_clock`, and `delta_cer_per_min` numbers.
2. **Fix I2** — make `final_corpus_cer` and `_last_accepted_eval_run` read
   `runs/_summary/<job_id>_state.json:best_cer` / `best_hyp_id`. Two-line
   change. Closes I5 cross-check at the same time.
3. **Document I4** in the REPORT explicitly: add a one-liner near
   `{{cat_chunking}}` and `{{auto_alignment_pct}}` that says "current
   harness commit subjects carry no category/reasoning keywords; manual
   diff review required for D + H axes". Until the harness commit format
   is enriched, automatic D/H signals will be empty regardless of analyzer
   changes.
4. **Defer I3** — the off-by-one only matters once commit subjects carry
   meaningful keywords (i.e. only matters after I4 is addressed
   upstream). Safe to ship the report without this fix for `phase3_001`.
5. **Minor M1–M5** — opportunistic cleanup in the same PR.

Recommended *not* to change before producing `phase3_001`'s first report:
the test suite, the template structure (`REPORT.md` is in spec shape and
all vars resolve), `evaluate_holdout.py` core flow (lock → unseal →
evaluate → reseal — correct as written).
