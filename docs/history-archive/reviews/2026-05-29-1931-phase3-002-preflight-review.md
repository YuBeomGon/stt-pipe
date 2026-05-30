# Phase 3 phase3_002 50-iter Preflight Review — 2026-05-29 19:31 KST

## Scope

- Target: `phase3_002` 50-iteration production job immediately before launch.
- Repository state reviewed: current HEAD `c01e7a0` (`docs: hardening 정본 동기화 ...`).
- Worktree at review start: clean.
- Review axes:
  1. Run blocker: execution interruption, rollback/state/git/HISTORY pollution, dry artifacts, holdout, `--commit-results`, hardening bypass.
  2. Candidate isolation / anti-cheating: candidate write/read surface, runtime cheating, Tool hardening, holdout isolation.
  3. Doc-code alignment / interpretability: README / PHASE3-PLAN / PHASE3-STATUS / SSOT / CANDIDATE-CONTEXT / candidate prompt vs code.

No code or docs were modified during the review itself.

## Bottom Line

**Do not start the 50-iter `phase3_002` production job yet.**

The normal loop mechanics are mostly in place: clean worktree, `--iters > 1` requires
`--commit-results`, production hardening bypass is rejected, state save is atomic
replace, and protected-path post-verify rollback exists.

However, the current isolation model still trusts too much Python runtime behavior
inside `workspace.transcribe.transcribe()`. Claude Tool hardening reduces interactive
Tool cheating, but it does not stop Python code executed during evaluation from
reading labels / past results or modifying run artifacts.

## Critical

### C1 — `transcribe()` runtime can read eval labels, baseline, runs, and judge internals

Evidence:

- Static guard only inspects `workspace/transcribe.py` source for backend/profile
  patterns: `harness/verify.py:56-85`.
- `judge.evaluate` calls candidate-controlled `transcribe(audio, sr)` directly:
  `judge/evaluate.py:113-119`.
- The evaluator exposes the current file id through env vars:
  `judge/evaluate.py:106-107`.
- Eval label injection is explicitly forbidden by SPEC:
  `docs/STT-PIPELINE-SPEC.md:484-490`.

Impact:

Candidate code can construct a path such as `data/raw/label/.../<file_id>.txt`, read
the reference text, and return it. It can also read `baseline/`, `judge/`, `runs/`,
or previous reports using normal Python I/O. Claude Read/Bash hardening does not
matter once the malicious code is inside `transcribe()`.

Minimum fix before 50 iter:

- Add runtime sandboxing for evaluation, or
- Add a conservative static deny list for `open`, `Path.read_text`, `Path.open`,
  `glob`, `os.listdir`, `subprocess`, `import os/pathlib/glob/subprocess/atexit`,
  `data/raw`, `label`, `baseline`, `judge`, `runs`, `score_report`, `per_file`,
  etc., accepting false positives for Phase 3.

### C2 — Candidate can poison `runs/<hyp_id>/score_report.json` / `per_file.jsonl`

Evidence:

- `run_verify()` runs `judge.evaluate`, then reads `score_report.json` and
  `per_file.jsonl`: `harness/verify.py:117-150`.
- Post-verify scope check explicitly allows all paths under `runs/<hyp_id>/`:
  `harness/runner.py:242-263`.

Impact:

Candidate runtime can register late writes or perform side effects during
`transcribe()` so that the final files under `runs/<hyp_id>/` are candidate-written,
not judge-written. Since those paths are allowed, post-verify scope re-check will not
reject. If the forged `score_report.json` and `per_file.jsonl` are internally
consistent, numeric guards may pass and policy can keep/success a false result.

Minimum fix before 50 iter:

- Make judge artifacts authoritative from a process that candidate code cannot write
  into, or
- After judge returns, validate ownership/content freshness against expected writer
  behavior, or
- Deny candidate runtime writes to `runs/<hyp_id>/score_report.json`,
  `per_file.jsonl`, and `diagnosis_report.json`; reserve candidate sidecars to
  runner-only writes.

### C3 — Candidate can mutate `workspace/transcribe.py` after static check

Evidence:

- Static check runs before `judge.evaluate`: `harness/verify.py:94-102`.
- Post-verify scope check ignores `workspace/transcribe.py` changes:
  `harness/runner.py:257-259`.
- Keep path commits `workspace/transcribe.py` after policy decision:
  `harness/runner.py:902-927`.

Impact:

A candidate can pass the pre-verify static check, then during `transcribe()` rewrite
`workspace/transcribe.py` to a different source file. The evaluated function was the
already-loaded code, but the committed file can be different and may include forbidden
imports or behavior. This breaks the invariant that the kept commit is the code that
was actually verified.

Minimum fix before 50 iter:

- Re-run `check_workspace_static()` after verify and before policy/commit.
- Treat post-verify changes to `workspace/transcribe.py` as suspicious unless the file
  hash matches the pre-verify hash.
- Commit only the exact source hash that passed static check and was evaluated.

### C4 — Claude Tool hardening is not a complete allowlist

Evidence:

- Runner injects only
  `--disallowedTools=Bash,WebFetch,WebSearch,Task`:
  `harness/runner.py:73-82`.
- `.claude/settings.json` denies `Read(...)` for several protected trees but does not
  explicitly deny `Grep`, `Glob`, or `LS`: `.claude/settings.json:35-43`.
- The current hardening code skips adding its default `--disallowedTools` when any
  `--disallowedTools` already exists: `harness/runner.py:149-156`.
- `env claude -p` / wrapper commands pass through without hardening because only
  argv[0] basename `claude` is recognized: `harness/runner.py:126-147`.

Impact:

The recommended README command (`--candidate-cmd "claude -p"`) receives the intended
flags. But partial operator-provided `--disallowedTools=...` can accidentally remove
Bash/WebFetch/WebSearch/Task from the deny set, and wrapper commands silently skip
hardening. Also, if Claude treats `Grep`/`Glob`/`LS` as separate Tools not covered by
Read deny, candidate can still enumerate protected files.

Minimum fix before 50 iter:

- Merge default disallowed tool set with any user-provided set instead of skipping.
- Reject production wrapper commands unless hardening sidecar proves required flags
  are present.
- Explicitly include `Grep`, `Glob`, `LS` in disallowed tools or test that settings
  deny rules actually cover them.

## Important

### I1 — README post-job commands omit `--job-id phase3_002`

Evidence:

- README instructs:
  `python3 scripts/analyze_run.py` and
  `python3 scripts/evaluate_holdout.py --unseal`: `README.md:113-121`.
- `analyze_run` auto-detects job id only when exactly one `*_state.json` exists:
  `scripts/analyze_run.py:1082-1094`.
- Current `runs/_summary/` already contains `phase3_001_state.json`.

Impact:

After `phase3_002`, there will be at least two state files. Unqualified analysis may
fall back to cross-job discovery or use an `unknown` report prefix. Holdout can also
anchor against the wrong eval job unless `--job-id phase3_002` is supplied.

Fix:

Document:

```bash
python3 scripts/analyze_run.py --job-id phase3_002
python3 scripts/evaluate_holdout.py --job-id phase3_002 --unseal
```

### I2 — `scripts/verify.sh` static guard is weaker than `harness.verify`

Evidence:

- `harness.verify` blocks AST imports plus `from ... import`, `importlib`,
  `__import__`: `harness/verify.py:19-85`.
- `scripts/verify.sh` grep only blocks `import ctranslate2`,
  `import transformers`, `from_pretrained`, `Whisper(`:
  `scripts/verify.sh:33-36`.

Impact:

Human preflight via `bash scripts/verify.sh` can pass a workspace source that the
production harness would reject, or give the operator a stale picture of guard
coverage.

Fix:

Make `scripts/verify.sh` delegate static checking to `python -m harness.verify` or
share the same checker.

### I3 — `PHASE3-STATUS.md` still has preflight checkboxes open

Evidence:

- `PHASE3-STATUS.md:48-50` leaves normal verify, intentional violation smoke, and
  dry run unchecked.

Impact:

This is not a code defect, but it is a launch readiness signal. Do not mark Phase 3
ready until these are run against the final hardened code.

### I4 — Dry/manual run artifact directories exist

Evidence:

- Ignored directories exist under `runs/`: `dry_iter_001`, multiple `manual_*`.
- Worktree is clean because they are ignored.

Impact:

They should not block `ensure_worktree_ready`, and `analyze_run` excludes dry/manual
prefixes in legacy mode. Still, they add interpretability noise and should not be
confused with production iterations.

Fix:

Optional cleanup before production, or leave as ignored historical smoke artifacts.

## Minor

- `docs/CANDIDATE-CONTEXT.md` still contains an older PULL table saying protected
  trees were readable (`docs/CANDIDATE-CONTEXT.md:85-105`), while §7.7 later says
  Read deny was added. The later section is current, but readers can be confused.
- `.claude/settings.json` `_doc` still describes swap activation. This is marked
  legacy elsewhere, so not a launch blocker.

## Verified

Commands run:

```bash
git status --short
python3 -m pytest tests/test_harness_runner.py tests/test_claude_phase3_settings.py tests/test_verify_check.py -q
```

Results:

- Worktree clean at review start.
- Targeted tests: `73 passed in 1.11s`.
- `uv run pytest ...` was attempted first but this repo has no `[project]` table in
  `pyproject.toml`; reran with `python3 -m pytest`.

## Recommended Pre-Launch Gate

Before running:

```bash
python3 scripts/evolve.py \
  --job-id phase3_002 \
  --iters 50 \
  --candidate-cmd "claude -p" \
  --commit-results
```

fix at least C1, C2, C3, C4, and README I1. Then rerun:

```bash
python3 -m pytest tests/test_harness_runner.py tests/test_claude_phase3_settings.py tests/test_verify_check.py -q
bash scripts/verify.sh
# intentional static violation smoke
python3 scripts/evolve.py --job-id dry --iters 1 --manual
```

Only then start the 50-iter production job.
