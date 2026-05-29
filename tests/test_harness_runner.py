"""
tests/test_harness_runner.py
Unit tests for the terminal-driven Phase 3 runner.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from harness.runner import (
    LANES,
    GitPathStatus,
    RunnerConfig,
    _CLAUDE_HARDENING_FLAGS,
    _HARDEN_BYPASS_ENV,
    _harden_candidate_cmd,
    _recent_iters,
    build_candidate_prompt,
    candidate_owned_statuses,
    disallowed_candidate_paths,
    parse_candidate_metadata,
    run_candidate_command,
    run_iteration,
    run_job,
)
from harness.state import HarnessState
from harness.verify import VerifyResult, check_workspace_static


_VALID_META_STDOUT = """diff applied.

```yaml
lane: decoding
diff_fingerprint: [beam, length_penalty]
why_different_from_last_5: trying widened beam with length bias
```
"""


def _write_valid_meta(out_dir: Path) -> None:
    """Write a minimal valid candidate stdout so the runner's A' YAML
    metadata check passes. Tests that inject a fake candidate_func must
    call this to satisfy the format gate added in proposal
    2026-05-29-agent-design (§2.1)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "claude_stdout.txt").write_text(_VALID_META_STDOUT, encoding="utf-8")


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=root,
        check=True,
    )
    (root / "workspace").mkdir()
    (root / "baseline").mkdir()
    (root / "runs/_summary").mkdir(parents=True)
    # Mirror production .gitignore so runs/<hyp_id>/ artifacts (candidate.diff,
    # candidate_meta.json, claude_stdout.txt, score_report.json, …) don't
    # surface as untracked and trip disallowed_candidate_paths.
    (root / ".gitignore").write_text(
        "runs/*\n!runs/_summary/\n",
        encoding="utf-8",
    )
    (root / "workspace/transcribe.py").write_text(
        "def transcribe(audio, sr):\n    return ''\n",
        encoding="utf-8",
    )
    (root / "baseline/target_cer.json").write_text(
        json.dumps(
            {
                "target_cer": 0.10,
                "total_inference_time_s": 100.0,
                "guard_baseline": {},
            }
        ),
        encoding="utf-8",
    )
    (root / "baseline/noise_floor.json").write_text(
        json.dumps({"sigma": 0.0, "is_provisional": True}),
        encoding="utf-8",
    )
    (root / "runs/_summary/HISTORY.md").write_text("# history\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore", "workspace/transcribe.py", "baseline",
         "runs/_summary/HISTORY.md"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True)


def test_static_check_blocks_forbidden_backend(tmp_path: Path) -> None:
    path = tmp_path / "transcribe.py"
    path.write_text("import ctranslate2\n", encoding="utf-8")
    assert "static backend" in (check_workspace_static(path) or "")


def test_static_check_blocks_from_import_and_dynamic_import(tmp_path: Path) -> None:
    path = tmp_path / "transcribe.py"
    path.write_text("from ctranslate2 import Translator\n", encoding="utf-8")
    assert "static backend" in (check_workspace_static(path) or "")

    path.write_text("__import__('transformers')\n", encoding="utf-8")
    assert "static backend" in (check_workspace_static(path) or "")


def test_candidate_scope_only_allows_workspace() -> None:
    config = RunnerConfig(job_id="job")
    statuses = [
        GitPathStatus(" M", Path("workspace/transcribe.py")),
        GitPathStatus(" M", Path("runs/_summary/HISTORY.md")),
        GitPathStatus(" M", Path("docs/PHASE3-PLAN.md")),
    ]
    bad = disallowed_candidate_paths(statuses, config)
    assert [item.path for item in bad] == [
        Path("runs/_summary/HISTORY.md"),
        Path("docs/PHASE3-PLAN.md"),
    ]


def test_candidate_owned_statuses_rolls_back_summary_but_not_run_artifacts() -> None:
    config = RunnerConfig(job_id="job")
    statuses = [
        GitPathStatus(" M", Path("workspace/transcribe.py")),
        GitPathStatus(" M", Path("runs/_summary/HISTORY.md")),
        GitPathStatus("??", Path("runs/_summary/poison.txt")),
        GitPathStatus("??", Path("runs/job_iter_001/prompt.md")),
    ]
    owned = candidate_owned_statuses(statuses, config)
    assert [item.path for item in owned] == [
        Path("workspace/transcribe.py"),
        Path("runs/_summary/HISTORY.md"),
        Path("runs/_summary/poison.txt"),
    ]


def test_build_candidate_prompt_mentions_claude_constraints(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    prompt = build_candidate_prompt(config, HarnessState(job_id="job", iteration=1))

    assert "Modify only workspace/transcribe.py" in prompt
    assert "Do not import ctranslate2" in prompt
    assert "Do not read assets/audio_profile" in prompt


def test_candidate_command_receives_prompt_and_writes_logs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    out_dir = tmp_path / "runs/job_iter_001"
    command = (
        f"{sys.executable} -c "
        "\"from pathlib import Path; import sys; "
        "Path('workspace/transcribe.py').write_text(sys.argv[1], encoding='utf-8')\""
    )

    result = run_candidate_command(
        command,
        "PROMPT_TEXT",
        out_dir,
        tmp_path,
        Path("workspace/transcribe.py"),
    )

    assert result.returncode == 0
    assert (out_dir / "prompt.md").read_text(encoding="utf-8") == "PROMPT_TEXT"
    assert "PROMPT_TEXT" in (tmp_path / "workspace/transcribe.py").read_text(
        encoding="utf-8"
    )
    assert (out_dir / "candidate.diff").is_file()


def test_run_iteration_keeps_first_valid_candidate(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    def candidate(_prompt: str, out_dir: Path):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(audio, sr):\n    return 'ok'\n",
            encoding="utf-8",
        )
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id: str) -> VerifyResult:
        return VerifyResult(
            ok=True,
            hyp_id=hyp_id,
            out_dir=tmp_path / "runs" / hyp_id,
            report={"corpus_cer": 0.40, "total_inference_time_s": 90.0},
            per_file=[],
        )

    result = run_iteration(config, state, state_path, candidate, verifier)

    assert result.status == "keep"
    assert state.best_cer == 0.40
    assert state.best_hyp_id == "job_iter_001"
    assert state_path.is_file()
    assert "job_iter_001" in (tmp_path / "runs/_summary/HISTORY.md").read_text(
        encoding="utf-8"
    )


def test_run_iteration_rejects_and_rolls_back_summary_scope_violation(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    original_history = (tmp_path / "runs/_summary/HISTORY.md").read_text(
        encoding="utf-8"
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    def candidate(_prompt: str, out_dir: Path):
        (tmp_path / "runs/_summary/HISTORY.md").write_text(
            "candidate injected text\n",
            encoding="utf-8",
        )
        (tmp_path / "runs/_summary/poison.txt").write_text(
            "candidate injected text\n",
            encoding="utf-8",
        )
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    result = run_iteration(config, state, state_path, candidate, lambda _hyp: None)

    assert result.status == "reject"
    history = (tmp_path / "runs/_summary/HISTORY.md").read_text(encoding="utf-8")
    assert "candidate injected text" not in history
    assert original_history in history
    assert "candidate scope" in history
    assert not (tmp_path / "runs/_summary/poison.txt").exists()


def test_run_iteration_rejects_when_candidate_writes_summary_during_verify(
    tmp_path: Path,
) -> None:
    """N1 regression: candidate's transcribe code may run during verify and
    write outside its allowed surface (e.g. runs/_summary/HISTORY.md, baseline/).
    The post-verify scope check must catch this BEFORE baseline/noise are read,
    so a poisoned baseline cannot influence keep/success decision.
    """
    _init_repo(tmp_path)
    original_history = (tmp_path / "runs/_summary/HISTORY.md").read_text(
        encoding="utf-8"
    )
    original_baseline = (tmp_path / "baseline/target_cer.json").read_text(
        encoding="utf-8"
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    def candidate(_prompt: str, out_dir: Path):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(audio, sr):\n    return 'ok'\n",
            encoding="utf-8",
        )
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id: str) -> VerifyResult:
        # Simulate candidate's transcribe code touching protected paths
        # while verify is running.
        (tmp_path / "runs/_summary/HISTORY.md").write_text(
            "poisoned by candidate during verify\n",
            encoding="utf-8",
        )
        (tmp_path / "baseline/target_cer.json").write_text(
            json.dumps({"target_cer": 999.0, "total_inference_time_s": 1.0}),
            encoding="utf-8",
        )
        return VerifyResult(
            ok=True,
            hyp_id=hyp_id,
            out_dir=tmp_path / "runs" / hyp_id,
            report={"corpus_cer": 0.40, "total_inference_time_s": 90.0},
            per_file=[],
        )

    result = run_iteration(config, state, state_path, candidate, verifier)

    assert result.status == "reject"
    assert state.best_cer is None
    assert state.best_hyp_id is None
    # baseline restored (so decision could not have been made against a poisoned target)
    assert (tmp_path / "baseline/target_cer.json").read_text(
        encoding="utf-8"
    ) == original_baseline
    # HISTORY restored, then reject narrative appended
    history = (tmp_path / "runs/_summary/HISTORY.md").read_text(encoding="utf-8")
    assert "poisoned by candidate during verify" not in history
    assert original_history in history
    assert "verify 중 scope" in history


def test_run_job_breaks_immediately_on_success(tmp_path: Path, monkeypatch) -> None:
    """run_job must exit as soon as state.status == 'success', not burn the
    remaining iteration budget. Otherwise a successful candidate gets
    overwritten by a subsequent reject's rollback."""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, iterations=3)

    call_count = {"n": 0}

    def fake_run_iteration(cfg, state, state_path):
        call_count["n"] += 1
        state.advance()
        state.record_best(f"job_iter_{state.iteration:03d}", 0.05)
        state.status = "success"

    monkeypatch.setattr("harness.runner.run_iteration", fake_run_iteration)
    state = run_job(config)

    assert state.status == "success"
    assert call_count["n"] == 1
    assert state.iteration == 1


def test_run_iteration_rolls_back_workspace_on_verify_failure(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    original_workspace = (tmp_path / "workspace/transcribe.py").read_text(
        encoding="utf-8"
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    def candidate(_prompt: str, _out_dir: Path):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(audio, sr):\n    return 'bad'\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id: str) -> VerifyResult:
        return VerifyResult(
            ok=False,
            hyp_id=hyp_id,
            out_dir=tmp_path / "runs" / hyp_id,
            error="forced verify failure",
        )

    result = run_iteration(config, state, state_path, candidate, verifier)

    assert result.status == "reject"
    assert (tmp_path / "workspace/transcribe.py").read_text(
        encoding="utf-8"
    ) == original_workspace


# ----------------------------------------------------------------------- #
# A' (proposal 2026-05-29-agent-design) — candidate metadata + format gate #
# ----------------------------------------------------------------------- #


_VALID_YAML_BLOCK = """\
preamble prose

```yaml
lane: decoding
diff_fingerprint: [beam, length_penalty, patience]
why_different_from_last_5: iter 9 미시도 patience widening
```
"""


def test_parse_meta_happy_path(tmp_path: Path) -> None:
    meta, err = parse_candidate_metadata(_VALID_YAML_BLOCK, tmp_path)
    assert err is None
    assert meta == {
        "lane": "decoding",
        "diff_fingerprint": ["beam", "length_penalty", "patience"],
        "why_different_from_last_5": "iter 9 미시도 patience widening",
    }
    assert (tmp_path / "candidate_meta.json").is_file()


def test_parse_meta_missing_block(tmp_path: Path) -> None:
    meta, err = parse_candidate_metadata("just prose, no yaml", tmp_path)
    assert meta is None
    assert err is not None and "no yaml" in err
    assert (tmp_path / "candidate_meta.err").is_file()


def test_parse_meta_picks_last_block_when_multiple(tmp_path: Path) -> None:
    # Candidate may quote an example block earlier; only the last counts.
    stdout = """\
example before:

```yaml
lane: prompt
diff_fingerprint: [x]
why_different_from_last_5: ignored
```

actual at end:

```yaml
lane: telemetry
diff_fingerprint: [logprob, debug]
why_different_from_last_5: instrumenting decoder
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert err is None
    assert meta["lane"] == "telemetry"


def test_parse_meta_rejects_invalid_lane(tmp_path: Path) -> None:
    stdout = """\
```yaml
lane: refactor
diff_fingerprint: [misc]
why_different_from_last_5: x
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert meta is None
    assert "invalid lane" in err


def test_parse_meta_rejects_fingerprint_too_long(tmp_path: Path) -> None:
    stdout = """\
```yaml
lane: decoding
diff_fingerprint: [a, b, c, d, e, f, g]
why_different_from_last_5: x
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert meta is None
    assert "length 7" in err


def test_parse_meta_rejects_empty_why(tmp_path: Path) -> None:
    stdout = """\
```yaml
lane: decoding
diff_fingerprint: [beam]
why_different_from_last_5: "   "
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert meta is None
    assert "why_different_from_last_5" in err


def test_lanes_constant_matches_profile() -> None:
    """Sanity: the LANES tuple in code must match the lane set documented
    in harness/prompts/candidate.md. Drift between code and profile would
    cause silent reject of candidate responses."""
    profile_path = Path(__file__).resolve().parents[1] / "harness/prompts/candidate.md"
    body = profile_path.read_text(encoding="utf-8")
    for lane in LANES:
        assert f"| {lane}" in body or f"|{lane}" in body, f"lane {lane!r} not documented in profile"


def test_run_iteration_format_reject_when_no_yaml(tmp_path: Path) -> None:
    """Candidate that edits workspace but omits the YAML block must be
    rejected BEFORE verify runs (proposal §2.1). Workspace is rolled back."""
    _init_repo(tmp_path)
    original_workspace = (tmp_path / "workspace/transcribe.py").read_text(
        encoding="utf-8"
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    verify_called = {"yes": False}

    def candidate(_prompt: str, out_dir: Path):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(audio, sr):\n    return 'mutated'\n",
            encoding="utf-8",
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        # Note: NO YAML block.
        (out_dir / "claude_stdout.txt").write_text(
            "edited file, forgot the yaml.\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(_hyp_id: str) -> VerifyResult:
        verify_called["yes"] = True
        raise AssertionError("verify must not run on format reject")

    result = run_iteration(config, state, state_path, candidate, verifier)

    assert result.status == "reject"
    assert result.format_reject is True
    assert "format reject" in result.reason
    assert verify_called["yes"] is False
    # Workspace rolled back.
    assert (tmp_path / "workspace/transcribe.py").read_text(
        encoding="utf-8"
    ) == original_workspace
    assert (
        tmp_path / "runs/job_iter_001/candidate_meta.err"
    ).is_file()


def test_run_job_aborts_after_4_of_5_format_rejects(
    tmp_path: Path, monkeypatch
) -> None:
    """If the first 5 iters produce 4 format rejects, run_job stops and sets
    state.status = 'aborted_format_reject' so the operator rewrites the
    profile rather than burning the rest of the budget (proposal §2.1)."""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, iterations=25)

    rejects_to_emit = [True, True, False, True, True]
    call_log: list[bool] = []

    def fake_iter(cfg, state, state_path):
        idx = state.iteration  # 0-indexed call before advance
        will_reject = rejects_to_emit[idx] if idx < len(rejects_to_emit) else False
        call_log.append(will_reject)
        state.advance()
        from harness.runner import IterationResult

        if will_reject:
            return IterationResult(
                hyp_id=f"job_iter_{state.iteration:03d}",
                status="reject",
                decision=None,
                verify_result=None,
                reason="format reject: test",
                format_reject=True,
            )
        return IterationResult(
            hyp_id=f"job_iter_{state.iteration:03d}",
            status="reject",
            decision=None,
            verify_result=None,
            reason="plain reject",
            format_reject=False,
        )

    monkeypatch.setattr("harness.runner.run_iteration", fake_iter)
    state = run_job(config)

    assert state.status == "aborted_format_reject"
    # Aborts at end of the 5th iter (4th format reject), not earlier.
    assert state.iteration == 5
    assert len(call_log) == 5


# ----------------------------------------------------------------------- #
# Review followup fixes (F1–F5) regression guards                          #
# ----------------------------------------------------------------------- #


def test_recent_iters_skips_empty_dirs(tmp_path: Path) -> None:
    """F1: a directory that exists but has no candidate_meta / score_report
    (e.g. the current iter's freshly-created out_dir, or an aborted-job
    leftover) must NOT be returned by _recent_iters — otherwise it would
    displace a real prior iter from the last-N window."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    # iter 001 ran (has meta).
    d1 = runs_dir / "job_iter_001"
    d1.mkdir()
    (d1 / "candidate_meta.json").write_text(
        json.dumps({"lane": "decoding", "diff_fingerprint": ["beam"]}),
        encoding="utf-8",
    )

    # iter 002 is in progress (mkdir done, no metadata yet) — must be skipped.
    (runs_dir / "job_iter_002").mkdir()

    # iter 003 was format-rejected (has .err only).
    d3 = runs_dir / "job_iter_003"
    d3.mkdir()
    (d3 / "candidate_meta.err").write_text("missing yaml", encoding="utf-8")

    rows = _recent_iters(tmp_path, Path("runs"), "job", n=5)
    names = [r["iter"] for r in rows]
    assert names == ["job_iter_001", "job_iter_003"], names


def test_build_prompt_does_not_include_current_iter_in_recent_table(
    tmp_path: Path,
) -> None:
    """F1 end-to-end: build_candidate_prompt called from run_iteration must
    not include the current iter's own (empty) out_dir in the recent table.
    Verified by inspecting the prompt text directly."""
    _init_repo(tmp_path)
    runs_dir = tmp_path / "runs"
    # Pre-populate iter 001 as a "real" prior run.
    d1 = runs_dir / "job_iter_001"
    d1.mkdir(parents=True)
    (d1 / "candidate_meta.json").write_text(
        json.dumps(
            {"lane": "decoding", "diff_fingerprint": ["beam", "patience"]}
        ),
        encoding="utf-8",
    )

    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    # State now at iteration 2 (just advanced) — current out_dir is iter_002.
    state = HarnessState(job_id="job", iteration=2)
    prompt = build_candidate_prompt(config, state)

    # iter_001 must appear (real prior). iter_002 (current, empty) must NOT.
    assert "job_iter_001" in prompt
    assert "job_iter_002" not in prompt


def test_parse_meta_rejects_whitespace_only_fingerprint_tokens(
    tmp_path: Path,
) -> None:
    """F5: ` ` strips to `` which would inflate Jaccard equality to 100%
    between unrelated iters. normalize-then-validate must reject."""
    stdout = """\
```yaml
lane: decoding
diff_fingerprint: ["beam", "   "]
why_different_from_last_5: x
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert meta is None
    assert "empty/whitespace-only" in err


def test_parse_meta_falls_back_to_completed_process_stdout(tmp_path: Path) -> None:
    """F4 unit-level: parse_candidate_metadata takes stdout text directly.
    The fallback (file → CompletedProcess.stdout) lives in run_iteration; this
    test just locks the parser's text-in contract so the fallback caller can
    rely on it."""
    yaml_inline = """\
```yaml
lane: prompt
diff_fingerprint: [language]
why_different_from_last_5: switching language tag
```
"""
    meta, err = parse_candidate_metadata(yaml_inline, tmp_path)
    assert err is None
    assert meta["lane"] == "prompt"


def test_run_job_commits_aborted_state_when_commit_results(
    tmp_path: Path, monkeypatch
) -> None:
    """F2: when --commit-results is on and the abort guard fires, the final
    state file with status='aborted_format_reject' must be committed.
    Otherwise the next job's ensure_worktree_ready sees runs/_summary/
    <job>_state.json as a modified tracked file and refuses to start."""
    _init_repo(tmp_path)
    config = RunnerConfig(
        job_id="job", repo_root=tmp_path, iterations=25, commit_results=True
    )

    from harness.runner import IterationResult

    def fake_iter(cfg, state, state_path):
        state.advance()
        # Always emit format reject so abort fires at iter 5 (need 4 of 5).
        # Each call must also commit something tracked or commit_iteration
        # finds nothing staged. Touch state via save.
        state.save(state_path)
        # Stage + commit the per-iter reject so the abort commit at the end
        # only has the state-status change left to commit.
        subprocess.run(
            ["git", "add", "runs/_summary/job_state.json"],
            cwd=tmp_path, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"iter{state.iteration}: reject job_iter_{state.iteration:03d}"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        return IterationResult(
            hyp_id=f"job_iter_{state.iteration:03d}",
            status="reject",
            decision=None,
            verify_result=None,
            reason="format reject: test",
            format_reject=True,
        )

    monkeypatch.setattr("harness.runner.run_iteration", fake_iter)
    state = run_job(config)

    assert state.status == "aborted_format_reject"
    # The abort commit must exist at HEAD.
    head_subject = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert "abort format_reject" in head_subject, head_subject
    # And the working tree must be clean (no leftover modified state file).
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    ).stdout
    assert status == "", f"worktree dirty after abort commit: {status!r}"

def test_harden_candidate_cmd_injects_flags_for_claude() -> None:
    hardened, added = _harden_candidate_cmd('claude -p')
    parts = hardened.split()
    for flag in _CLAUDE_HARDENING_FLAGS:
        assert flag in parts, f'{flag} not injected: {hardened}'
    assert set(added) == set(_CLAUDE_HARDENING_FLAGS)


def test_harden_candidate_cmd_is_idempotent() -> None:
    cmd = 'claude -p --disable-slash-commands --strict-mcp-config'
    hardened, added = _harden_candidate_cmd(cmd)
    assert added == []
    assert hardened.count('--disable-slash-commands') == 1
    assert hardened.count('--strict-mcp-config') == 1


def test_harden_candidate_cmd_passes_through_non_claude() -> None:
    cmd = 'python3 tests/fake_candidate.py'
    hardened, added = _harden_candidate_cmd(cmd)
    assert hardened == cmd
    assert added == []


def test_harden_candidate_cmd_respects_bypass_env(monkeypatch) -> None:
    monkeypatch.setenv(_HARDEN_BYPASS_ENV, '1')
    hardened, added = _harden_candidate_cmd('claude -p')
    assert hardened == 'claude -p'
    assert added == []


def test_harden_candidate_cmd_recognizes_absolute_claude_path() -> None:
    hardened, added = _harden_candidate_cmd('/usr/local/bin/claude -p')
    assert '--disable-slash-commands' in hardened
    assert added  # at least one flag injected

