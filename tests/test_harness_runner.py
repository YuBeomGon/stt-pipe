"""
tests/test_harness_runner.py
Unit tests for the terminal-driven Phase 3 runner.
"""

from __future__ import annotations

import json
import subprocess
import sys
import shlex
from pathlib import Path

from harness.runner import (
    GitPathStatus,
    RunnerConfig,
    _CLAUDE_HARDENING_FLAGS,
    _HARDEN_BYPASS_ENV,
    _check_bypass_in_production,
    _dominant_axis,
    _format_error_profile,
    _format_recent_table,
    _harden_candidate_cmd,
    _read_score_report,
    _recent_iters,
    build_candidate_prompt,
    candidate_owned_statuses,
    diff_ignored_surface,
    disallowed_candidate_paths,
    parse_candidate_metadata,
    remove_ignored_poison,
    run_candidate_command,
    run_iteration,
    run_job,
    snapshot_ignored_surface,
)
from harness.state import HarnessState
from harness.verify import VerifyResult, check_workspace_static


_VALID_META_STDOUT = """diff applied.

```yaml
capability_investigated: frozen.asr_backend.generate return values
what_i_learned: generate accepts a length_penalty kwarg the stub ignored
hypothesis: add length bias to reduce tail deletion
fingerprint: [beam, length_penalty]
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
    # phase1.5: runs/ fully ignored — metadata is durable-on-disk + untracked.
    (root / ".gitignore").write_text("runs/\n", encoding="utf-8")
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
        ["git", "add", ".gitignore", "workspace/transcribe.py", "baseline"],
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
    # phase1.5: candidate_owned now precisely owns the allowed_path plus ANY
    # runs/ path (its own per-iter output surface). It no longer sweeps up
    # arbitrary non-runs/ untracked paths (the collateral-damage hazard).
    assert [item.path for item in owned] == [
        Path("workspace/transcribe.py"),
        Path("runs/_summary/HISTORY.md"),
        Path("runs/_summary/poison.txt"),
        Path("runs/job_iter_001/prompt.md"),
    ]


def test_candidate_owned_excludes_unrelated_untracked(tmp_path: Path) -> None:
    """phase1.5 collateral-damage fix: a stray untracked file the candidate
    never touched must NOT be classified as candidate-owned (so rollback never
    deletes it). Only allowed_path + runs/<hyp_id>/ are candidate-owned on the
    tracked surface."""
    from harness.runner import candidate_owned_statuses
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    statuses = [
        GitPathStatus(" M", Path("workspace/transcribe.py")),  # allowed (tracked)
        GitPathStatus("??", Path("runs/job_iter_001/out.json")),  # own verify out
        GitPathStatus("??", Path("stray.txt")),                # UNRELATED stray
    ]
    owned = {str(s.path) for s in candidate_owned_statuses(statuses, config)}
    assert "stray.txt" not in owned                  # ← collateral-damage guard
    assert "workspace/transcribe.py" in owned
    assert "runs/job_iter_001/out.json" in owned


def test_rollback_paths_removes_only_listed_untracked(tmp_path: Path) -> None:
    """rollback deletes exactly the passed untracked paths with os.remove/rmtree
    — never git clean — so unrelated untracked files survive."""
    from harness.runner import rollback_paths
    _init_repo(tmp_path)
    (tmp_path / "junkdir").mkdir()
    (tmp_path / "junkdir/a").write_text("a\n", encoding="utf-8")
    (tmp_path / "junkfile.txt").write_text("p\n", encoding="utf-8")
    (tmp_path / "keepme.txt").write_text("keep\n", encoding="utf-8")  # NOT passed

    rollback_paths(tmp_path, [
        GitPathStatus("??", Path("junkfile.txt")),
        GitPathStatus("??", Path("junkdir")),
    ])
    assert not (tmp_path / "junkfile.txt").exists()
    assert not (tmp_path / "junkdir").exists()
    assert (tmp_path / "keepme.txt").exists()         # ← survived (not in list)


def test_rollback_paths_restores_tracked_with_git(tmp_path: Path) -> None:
    from harness.runner import rollback_paths
    _init_repo(tmp_path)
    (tmp_path / "workspace/transcribe.py").write_text("dirty\n", encoding="utf-8")
    rollback_paths(tmp_path, [GitPathStatus(" M", Path("workspace/transcribe.py"))])
    assert (tmp_path / "workspace/transcribe.py").read_text(encoding="utf-8") == (
        "def transcribe(audio, sr):\n    return ''\n"
    )


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


def _tracked(root: Path, rel: str) -> bool:
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=root,
        capture_output=True,
    )
    return r.returncode == 0


def test_step1_decision_trace_committed_and_survives_next_iter(tmp_path: Path) -> None:
    """Step 1 통합 smoke (모델 없이): decisions.jsonl + portfolio.json 이 iter
    커밋에 포함되고, 그 덕에 다음 iter 의 ensure_worktree_ready 가 깨지지 않는다.
    (commit_iteration 안의 _persist_decision 배선을 실제 git 경로로 검증.)"""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, commit_results=True)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    def _candidate(diff_line: str):
        def candidate(_prompt: str, out_dir: Path):
            (tmp_path / "workspace/transcribe.py").write_text(
                f"def transcribe(audio, sr):\n    {diff_line}\n    return 'ok'\n",
                encoding="utf-8",
            )
            _write_valid_meta(out_dir)
            # production 의 run_candidate_command 가 쓰는 산출물 흉내
            (out_dir / "candidate.diff").write_text(
                f"@@ @@ def transcribe(audio, sr):\n+    {diff_line}\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(["fake"], 0, "", "")

        return candidate

    def _verifier(cer: float):
        def verifier(hyp_id: str) -> VerifyResult:
            report = {
                "corpus_cer": cer,
                "total_inference_time_s": 90.0,
                "error_breakdown": {"sub_ratio": 0.3, "del_ratio": 0.3},
                "hallucination_hit_rate": 0.1,
            }
            out_dir = tmp_path / "runs" / hyp_id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "score_report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            return VerifyResult(
                ok=True, hyp_id=hyp_id, out_dir=out_dir, report=report, per_file=[]
            )

        return verifier

    # iter 1 — keep
    r1 = run_iteration(
        config, state, state_path,
        _candidate("results = generate(features, beam_size=5)"), _verifier(0.40),
    )
    assert r1.status == "keep"
    assert (tmp_path / "runs/_summary/job_decisions.jsonl").is_file()
    assert (tmp_path / "runs/_summary/job_portfolio.json").is_file()
    assert not _tracked(tmp_path, "runs/_summary/job_decisions.jsonl")  # durable but untracked

    # iter 2 — worse cer → reject. ensure_worktree_ready 가 깨지지 않아야 한다
    # (decisions/portfolio 가 iter1 커밋에 들어가 working tree clean).
    r2 = run_iteration(
        config, state, state_path,
        _candidate("audio = preemphasis(channel_eq(audio))"), _verifier(0.55),
    )
    assert r2.status == "reject"

    decisions = (tmp_path / "runs/_summary/job_decisions.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(decisions) == 2
    recs = [json.loads(d) for d in decisions]
    fams = [r["harness_family_id"] for r in recs]
    # lineage-aware family: iter2 가 파생 모드(refine/ablate/repair/combine)면 부모
    # (iter1) family 를 상속하고, explore/plateau 면 시그니처로 신규 family 를 만든다
    # (decode vs audio diff 라 신규가 됨). 둘 다 유효 family_id 여야 한다.
    mode2 = recs[1]["chosen_mode"]
    if mode2 in ("refine", "ablate", "repair", "combine"):
        assert fams[1] == fams[0]  # 부모 family 상속
    else:
        assert fams[1] != fams[0]  # explore/plateau → 신규
    # scheduler 배선: chosen_mode 가 decisions.jsonl 에 채워진다(sidecar→_persist).
    assert recs[0]["chosen_mode"] is not None
    assert recs[0]["chosen_mode"] in (
        "explore", "refine", "combine", "ablate", "repair", "plateau",
    )

    # working tree clean (커밋에 다 포함됨)
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    assert porcelain == "", f"dirty tree: {porcelain}"


def test_scope_violation_summary_poison_caught_by_snapshot(tmp_path: Path) -> None:
    """phase1.5: with runs/ gitignored, git can't see a candidate write into
    runs/_summary/ — the snapshot diff must catch it, reject the iter, remove the
    poison, and NOT touch an unrelated stray file or the pre-existing archive."""
    _init_repo(tmp_path)   # .gitignore = runs/ after Task 4 Step 1
    # pre-existing ignored files (the '2584 class' in miniature)
    arch = tmp_path / "runs/_archive/old"
    arch.mkdir(parents=True)
    (arch / "a.json").write_text("a\n", encoding="utf-8")
    # An unrelated repo file that must survive the reject's rollback (collateral-
    # damage guard). Committed (tracked-clean) so the pre-candidate
    # ensure_worktree_ready gate — unchanged on the tracked surface by phase1.5 —
    # does not reject the iter for a pre-existing top-level untracked file. The
    # untracked-stray survival itself is covered precisely by the unit tests
    # test_rollback_paths_removes_only_listed_untracked / remove_ignored_poison.
    (tmp_path / "operator_scratch.txt").write_text("DO NOT DELETE\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "operator_scratch.txt"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "operator scratch"], cwd=tmp_path, check=True,
        capture_output=True,
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    def candidate(_prompt: str, out_dir: Path):
        (tmp_path / "runs/_summary/poison.txt").write_text("evil\n", encoding="utf-8")
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    result = run_iteration(config, state, state_path, candidate, lambda _h: None)
    assert result.status == "reject"
    assert not (tmp_path / "runs/_summary/poison.txt").exists()    # poison removed
    assert (tmp_path / "operator_scratch.txt").exists()           # stray survived
    assert (tmp_path / "runs/_archive/old/a.json").exists()       # archive untouched


def test_scope_violation_clean_iter_not_rejected_with_populated_archive(
    tmp_path: Path,
) -> None:
    """REGRESSION for the 2584-file bug: a CLEAN candidate iter (writes only its
    own workspace + runs/<hyp>/) must NOT be rejected even when runs/_archive/ is
    heavily populated before the candidate runs."""
    _init_repo(tmp_path)
    arch = tmp_path / "runs/_archive"
    for i in range(60):                            # many pre-existing ignored files
        d = arch / f"job_{i}"
        d.mkdir(parents=True)
        (d / "score.json").write_text(f"{i}\n", encoding="utf-8")
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    def candidate(_prompt: str, out_dir: Path):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(a, sr):\n    return 'ok'\n", encoding="utf-8")
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id: str) -> VerifyResult:
        out_dir = tmp_path / "runs" / hyp_id
        out_dir.mkdir(parents=True, exist_ok=True)
        report = {"corpus_cer": 0.40, "total_inference_time_s": 90.0}
        (out_dir / "score_report.json").write_text(json.dumps(report), encoding="utf-8")
        return VerifyResult(ok=True, hyp_id=hyp_id, out_dir=out_dir,
                            report=report, per_file=[])

    result = run_iteration(config, state, state_path, candidate, verifier)
    assert result.status in ("keep", "success")    # ← NOT a false scope reject


# ----------------------------------------------------------------------- #
# A' (proposal 2026-05-29-agent-design) — candidate metadata + format gate #
# ----------------------------------------------------------------------- #


_VALID_YAML_BLOCK = """\
preamble prose

```yaml
capability_investigated: frozen.asr_backend.generate decoding kwargs
what_i_learned: patience is accepted but was never varied
hypothesis: widen patience to let beam explore longer hypotheses
fingerprint: [beam, length_penalty, patience]
```
"""


def test_parse_meta_happy_path(tmp_path: Path) -> None:
    meta, err = parse_candidate_metadata(_VALID_YAML_BLOCK, tmp_path)
    assert err is None
    assert meta == {
        "capability_investigated": "frozen.asr_backend.generate decoding kwargs",
        "what_i_learned": "patience is accepted but was never varied",
        "hypothesis": "widen patience to let beam explore longer hypotheses",
        "fingerprint": ["beam", "length_penalty", "patience"],
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
capability_investigated: ignored example
what_i_learned: ignored
hypothesis: ignored
fingerprint: [x]
```

actual at end:

```yaml
capability_investigated: model.align cross-attention alignment
what_i_learned: load() returns the raw model, so align() is reachable
hypothesis: use word timestamps to place chunk boundaries
fingerprint: [align, timestamp, boundary]
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert err is None
    assert meta["fingerprint"] == ["align", "timestamp", "boundary"]


def test_parse_meta_lane_is_optional_free_form(tmp_path: Path) -> None:
    """lane is demoted to an optional free-form tag (discovery schema): an
    arbitrary value is accepted and preserved; absence is also fine."""
    stdout = """\
```yaml
capability_investigated: generate return_no_speech_prob
what_i_learned: generate can return a no-speech probability per segment
hypothesis: gate empty segments on no-speech prob
fingerprint: [no_speech, gate]
lane: anything-goes
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert err is None
    assert meta["lane"] == "anything-goes"


def test_parse_meta_rejects_missing_required_key(tmp_path: Path) -> None:
    stdout = """\
```yaml
capability_investigated: x
what_i_learned: y
fingerprint: [misc]
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert meta is None
    assert "hypothesis" in err


def test_parse_meta_rejects_fingerprint_too_long(tmp_path: Path) -> None:
    stdout = """\
```yaml
capability_investigated: x
what_i_learned: y
hypothesis: z
fingerprint: [a, b, c, d, e, f, g]
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert meta is None
    assert "length 7" in err


def test_parse_meta_rejects_empty_required_text(tmp_path: Path) -> None:
    stdout = """\
```yaml
capability_investigated: x
what_i_learned: y
hypothesis: "   "
fingerprint: [beam]
```
"""
    meta, err = parse_candidate_metadata(stdout, tmp_path)
    assert meta is None
    assert "hypothesis" in err


def test_profile_documents_required_fields() -> None:
    """Sanity: the discovery output fields the parser requires must be
    documented in harness/prompts/candidate.md. Drift between code and
    profile would cause silent reject of candidate responses."""
    profile_path = Path(__file__).resolve().parents[1] / "harness/prompts/candidate.md"
    body = profile_path.read_text(encoding="utf-8")
    for field in (
        "capability_investigated",
        "what_i_learned",
        "hypothesis",
        "fingerprint",
    ):
        assert field in body, f"required field {field!r} not documented in profile"


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


def test_run_job_budgets_by_evaluated_count(tmp_path: Path, monkeypatch) -> None:
    """--iters 는 evaluated(scored) 예산이다(#1): 평가된 후보가 iters 개가 될
    때까지만 돈다. 모두 평가되면 정확히 iters 번."""
    from types import SimpleNamespace

    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, iterations=3)
    calls = {"n": 0}

    def fake(cfg, state, state_path):
        calls["n"] += 1
        state.advance()
        state.record_evaluated()
        return SimpleNamespace(format_reject=False, command_failed=False)

    monkeypatch.setattr("harness.runner.run_iteration", fake)
    state = run_job(config)
    assert state.evaluated_count == 3
    assert calls["n"] == 3


def test_run_job_attempt_cap_bounds_unevaluated_loop(tmp_path: Path, monkeypatch) -> None:
    """평가가 전혀 안 되도(무한 reject) attempt cap = iters*3+10 에서 멈춘다(#1)."""
    from types import SimpleNamespace

    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, iterations=2)
    calls = {"n": 0}

    def fake(cfg, state, state_path):
        calls["n"] += 1
        state.advance()  # 평가 없음 → evaluated_count 안 오름
        return SimpleNamespace(format_reject=False, command_failed=False)

    monkeypatch.setattr("harness.runner.run_iteration", fake)
    state = run_job(config)
    assert calls["n"] == config.iterations * 3 + 10  # 16
    # cap 도달 시 status 를 명시 저장해 정상 완료와 구분(#1).
    assert state.status == "incomplete_attempt_cap"


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


def test_run_job_aborts_after_consecutive_command_failures(
    tmp_path: Path, monkeypatch
) -> None:
    """3 consecutive candidate-command failures (exit!=0, e.g. session limit)
    abort the job instead of burning the whole budget on no-op rejects
    (phase3_003 ran 79 such iters)."""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, iterations=25)

    from harness.runner import IterationResult

    def fake_iter(cfg, state, state_path):
        state.advance()
        return IterationResult(
            hyp_id=f"job_iter_{state.iteration:03d}",
            status="reject",
            decision=None,
            verify_result=None,
            reason="candidate command 실패",
            command_failed=True,
        )

    monkeypatch.setattr("harness.runner.run_iteration", fake_iter)
    state = run_job(config)

    assert state.status == "aborted_command_failure"
    assert state.iteration == 3  # aborts at the 3rd consecutive failure


def test_command_failure_streak_resets_on_success(tmp_path: Path, monkeypatch) -> None:
    """A command that runs resets the streak, so intermittent failures don't
    accumulate to a false abort."""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, iterations=6)

    from harness.runner import IterationResult

    # fail, fail, OK, fail, fail, fail  → abort only at the final 3-streak (iter6)
    pattern = [True, True, False, True, True, True]

    def fake_iter(cfg, state, state_path):
        idx = state.iteration
        state.advance()
        return IterationResult(
            hyp_id=f"job_iter_{state.iteration:03d}",
            status="reject",
            decision=None,
            verify_result=None,
            reason="x",
            command_failed=pattern[idx],
        )

    monkeypatch.setattr("harness.runner.run_iteration", fake_iter)
    state = run_job(config)

    assert state.status == "aborted_command_failure"
    assert state.iteration == 6


# ----------------------------------------------------------------------- #
# Session/token rate-limit backoff                                         #
# ----------------------------------------------------------------------- #


def test_is_rate_limited_detection() -> None:
    from harness.runner import _is_rate_limited

    assert _is_rate_limited(
        "You've hit your session limit · resets 11:30pm (Asia/Seoul)"
    )
    assert _is_rate_limited("Usage limit reached.")
    assert not _is_rate_limited("```yaml\nlane: x\n```")
    assert not _is_rate_limited("some normal candidate output")
    assert not _is_rate_limited("")


def test_run_job_aborts_on_rate_limit_exhausted(tmp_path: Path, monkeypatch) -> None:
    """backoff 사다리를 다 쓰고도 한도면 즉시 aborted_rate_limit — command-fail
    streak(3회) 를 기다리지 않고 첫 발생에서 멈춘다(예산 보존)."""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path, iterations=25)

    from harness.runner import IterationResult

    def fake_iter(cfg, state, state_path):
        state.advance()
        return IterationResult(
            hyp_id=f"job_iter_{state.iteration:03d}",
            status="reject",
            decision=None,
            verify_result=None,
            reason="세션/토큰 한도 — backoff 사다리 소진",
            command_failed=True,
            rate_limited=True,
        )

    monkeypatch.setattr("harness.runner.run_iteration", fake_iter)
    state = run_job(config)

    assert state.status == "aborted_rate_limit"
    assert state.iteration == 1


def test_run_iteration_backs_off_on_rate_limit_then_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """한도 신호가 2번 뜨면 5·10분 backoff 후 재시도, 3번째에 정상 후보가 나오면
    그 iter 는 정상 진행한다(command_failed/rate_limited 아님). sleep 은 mock."""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job")
    state_path = tmp_path / "runs/_summary/job_state.json"

    slept: list[float] = []
    monkeypatch.setattr("harness.runner._sleep_minutes", lambda m: slept.append(m))

    calls = {"n": 0}

    def candidate(_prompt: str, out_dir: Path):
        calls["n"] += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        if calls["n"] <= 2:  # 세션 한도 메시지 + 빈 diff + 비정상 종료
            (out_dir / "claude_stdout.txt").write_text(
                "You've hit your session limit · resets 11:30pm", encoding="utf-8"
            )
            (out_dir / "candidate.diff").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(["claude"], 1, "", "")
        # 3번째: 정상 후보
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(audio, sr):\n"
            "    results = generate(features, beam_size=5)\n    return 'ok'\n",
            encoding="utf-8",
        )
        _write_valid_meta(out_dir)
        (out_dir / "candidate.diff").write_text(
            "@@ @@ def transcribe(audio, sr):\n"
            "+    results = generate(features, beam_size=5)\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(["claude"], 0, _VALID_META_STDOUT, "")

    def verifier(hyp_id: str) -> VerifyResult:
        report = {
            "corpus_cer": 0.40,
            "total_inference_time_s": 90.0,
            "error_breakdown": {"sub_ratio": 0.3, "del_ratio": 0.3},
            "hallucination_hit_rate": 0.1,
        }
        out_dir = tmp_path / "runs" / hyp_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "score_report.json").write_text(json.dumps(report), encoding="utf-8")
        return VerifyResult(
            ok=True, hyp_id=hyp_id, out_dir=out_dir, report=report, per_file=[]
        )

    r = run_iteration(config, state, state_path, candidate, verifier)

    assert calls["n"] == 3      # 2회 한도 + 1회 정상
    assert slept == [5, 10]     # backoff 2회 후 풀림
    assert not r.command_failed
    assert not r.rate_limited
    assert r.status in ("keep", "reject")  # 평가까지 도달


# ----------------------------------------------------------------------- #
# Review followup fixes (F1–F5) regression guards                          #
# ----------------------------------------------------------------------- #


def test_format_iter_plan_shows_mode_and_parent() -> None:
    """터미널 iter 라인에 mode·override·parent(hyp/family/cer)가 보여야 한다
    (verify_check 만으로는 안 보이던 흐름 가시화)."""
    from harness.runner import _format_iter_plan
    from harness.scheduler import SchedulerDecision

    line = _format_iter_plan(
        12,
        SchedulerDecision("refine", "refine", "discovery_phase"),
        [{"hyp_id": "job_iter_011", "harness_family_id": "family_004", "cer": 0.1775}],
    )
    assert "mode=refine" in line
    assert "override=discovery_phase" in line
    assert "job_iter_011" in line and "family_004" in line and "0.1775" in line

    # parent 없으면 parent=none, override=scheduled 는 표기 생략
    plain = _format_iter_plan(5, SchedulerDecision("explore", "explore", "scheduled"), [])
    assert "mode=explore" in plain and "parent=none" in plain
    assert "override" not in plain

    # repair-target 은 라벨로 구분
    rep = _format_iter_plan(
        8,
        SchedulerDecision("explore", "repair", "repair_event"),
        [{"hyp_id": "job_iter_007", "is_repair_target": True}],
    )
    assert "mode=repair" in rep and "repair-target" in rep


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


def test_findings_ledger_compounds_beyond_dedup_window(tmp_path: Path) -> None:
    """F1 fix: the findings ledger is built from the whole job's durable
    candidate-meta jsonl (runs/_summary/<job>_candidate_meta.jsonl), so a fact
    learned 8 iters ago still appears even though the recent-dedup table only
    shows the last 5. Deduped by learned text."""
    _init_repo(tmp_path)
    sm = tmp_path / "runs" / "_summary"
    sm.mkdir(parents=True, exist_ok=True)
    recs = [
        {
            "iter": i,
            "hyp_id": f"job_iter_{i:03d}",
            "status": "reject",
            "capability_investigated": f"cap{i}",
            "what_i_learned": f"fact number {i}",
            "hypothesis": "h",
            "fingerprint": ["x"],
        }
        for i in range(1, 9)
    ]
    # add a duplicate of fact 1 to exercise dedup
    recs.append(
        {
            "iter": 9,
            "hyp_id": "job_iter_009",
            "status": "reject",
            "capability_investigated": "cap1",
            "what_i_learned": "fact number 1",
            "hypothesis": "h",
            "fingerprint": ["x"],
        }
    )
    (sm / "job_candidate_meta.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8"
    )

    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job", iteration=10)
    prompt = build_candidate_prompt(config, state)

    assert "fact number 1" in prompt  # outside the 5-iter dedup window
    assert "fact number 8" in prompt
    assert prompt.count("fact number 1") == 1  # deduped


def test_parse_meta_rejects_whitespace_only_fingerprint_tokens(
    tmp_path: Path,
) -> None:
    """F5: ` ` strips to `` which would inflate Jaccard equality to 100%
    between unrelated iters. normalize-then-validate must reject."""
    stdout = """\
```yaml
capability_investigated: x
what_i_learned: y
hypothesis: z
fingerprint: ["beam", "   "]
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
capability_investigated: prompt token construction
what_i_learned: the language tag token can be swapped pre-decode
hypothesis: switch language tag
fingerprint: [language]
```
"""
    meta, err = parse_candidate_metadata(yaml_inline, tmp_path)
    assert err is None
    assert meta["fingerprint"] == ["language"]


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
    parts = shlex.split(hardened)
    # Each hardening flag name should appear either bare (`--foo`) or with
    # `=value` suffix (`--foo=bar`). `_CLAUDE_HARDENING_FLAGS` holds the
    # name-only form for compat.
    for flag in _CLAUDE_HARDENING_FLAGS:
        present = any(p == flag or p.startswith(flag + "=") for p in parts)
        assert present, f'{flag} not injected: {hardened}'
    for flag in _CLAUDE_HARDENING_FLAGS:
        present_in_added = any(a == flag or a.startswith(flag + "=") for a in added)
        assert present_in_added, f'{flag} not reported in added: {added}'


def test_harden_candidate_cmd_is_idempotent() -> None:
    cmd = (
        'claude -p --disable-slash-commands --strict-mcp-config '
        '--disallowedTools=Bash,WebFetch,WebSearch,Task'
    )
    hardened, added = _harden_candidate_cmd(cmd)
    assert added == []
    assert hardened.count('--disable-slash-commands') == 1
    assert hardened.count('--strict-mcp-config') == 1
    assert hardened.count('--disallowedTools') == 1


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



def test_check_bypass_rejects_production_iters(monkeypatch) -> None:
    """F1 (codex 3차): production 잡 (iters>1) 에서 bypass 거부."""
    import pytest
    monkeypatch.setenv(_HARDEN_BYPASS_ENV, "1")
    cfg = RunnerConfig(job_id="job", iterations=50)
    with pytest.raises(RuntimeError, match=r"production job"):
        _check_bypass_in_production(cfg)


def test_check_bypass_rejects_production_commit_results(monkeypatch) -> None:
    """F1 (codex 3차): production 잡 (commit_results=True) 에서 bypass 거부."""
    import pytest
    monkeypatch.setenv(_HARDEN_BYPASS_ENV, "1")
    cfg = RunnerConfig(job_id="job", iterations=1, commit_results=True)
    with pytest.raises(RuntimeError, match=r"production job"):
        _check_bypass_in_production(cfg)


def test_check_bypass_allows_debug_single_iter(monkeypatch, capsys) -> None:
    """F1 (codex 3차): debug 모드 (single iter, no commit) 는 bypass 허용 + 경고."""
    monkeypatch.setenv(_HARDEN_BYPASS_ENV, "1")
    cfg = RunnerConfig(job_id="job", iterations=1, commit_results=False)
    _check_bypass_in_production(cfg)  # no raise
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert _HARDEN_BYPASS_ENV in captured.err


def test_check_bypass_silent_when_not_set(monkeypatch) -> None:
    """F1 (codex 3차): bypass 미설정 시 production 잡도 통과."""
    monkeypatch.delenv(_HARDEN_BYPASS_ENV, raising=False)
    cfg = RunnerConfig(job_id="job", iterations=50, commit_results=True)
    _check_bypass_in_production(cfg)  # no raise, no warning


def test_harden_candidate_cmd_warns_on_bypass(monkeypatch, capsys) -> None:
    """F1 (codex 3차): 매 invocation 마다 bypass 활성 시 경고 stderr."""
    monkeypatch.setenv(_HARDEN_BYPASS_ENV, "1")
    _harden_candidate_cmd("claude -p")
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "hardening skipped" in captured.err


def test_harden_candidate_cmd_includes_disallowed_tools() -> None:
    """codex 2차: --disallowedTools=... 가 자동 부착되어 Bash/WebFetch 등 차단.
    `=` 형식 필수 — variadic flag 가 candidate prompt 를 tool 이름으로 먹는
    버그 방지."""
    hardened, added = _harden_candidate_cmd("claude -p")
    parts = shlex.split(hardened)
    disallowed_entries = [p for p in parts if p.startswith("--disallowedTools")]
    assert len(disallowed_entries) == 1, parts
    entry = disallowed_entries[0]
    assert "=" in entry, f"must use --flag=value form, got: {entry}"
    value = entry.split("=", 1)[1]
    for tool in ("Bash", "WebFetch", "WebSearch", "Task"):
        assert tool in value, f"{tool} not in disallowedTools: {value}"
    assert any(a.startswith("--disallowedTools") for a in added)


def test_harden_candidate_cmd_disallowed_tools_idempotent() -> None:
    """codex 2차: 이미 --disallowedTools 가 있으면 (= 형식이든 공백 형식이든)
    중복 추가 X."""
    # `=` form
    cmd1 = "claude -p --disallowedTools=Bash,WebFetch,WebSearch,Task"
    hardened1, added1 = _harden_candidate_cmd(cmd1)
    parts1 = shlex.split(hardened1)
    assert sum(1 for p in parts1 if p.startswith("--disallowedTools")) == 1
    assert not any(a.startswith("--disallowedTools") for a in added1)
    # space form (user manually provided two-arg style)
    cmd2 = 'claude -p --disallowedTools "Bash,WebFetch,WebSearch,Task"'
    hardened2, added2 = _harden_candidate_cmd(cmd2)
    parts2 = shlex.split(hardened2)
    assert sum(1 for p in parts2 if p == "--disallowedTools") == 1
    assert not any(a.startswith("--disallowedTools") for a in added2)


def test_build_candidate_prompt_inlines_workspace_body(tmp_path: Path) -> None:
    """codex 2차: workspace/transcribe.py 본문이 prompt 에 inline 되어
    Bash/Read 의존 없이 candidate 가 현재 코드를 본다."""
    _init_repo(tmp_path)
    # Replace with distinctive content
    (tmp_path / "workspace/transcribe.py").write_text(
        "def transcribe(audio, sr):\n    return 'SENTINEL_CONTENT_X1Y2'\n",
        encoding="utf-8",
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    prompt = build_candidate_prompt(config, HarnessState(job_id="job", iteration=1))
    assert "SENTINEL_CONTENT_X1Y2" in prompt
    assert "Current workspace/transcribe.py" in prompt


def test_build_candidate_prompt_does_not_start_with_double_dash(tmp_path: Path) -> None:
    """Regression: prompt must NOT start with `--` or claude CLI's argv parser
    treats it as an unknown option (`error: unknown option '--- BEGIN ...'`).
    Discovered in phase3_002 first launch: 50 reject loop before any iter ran."""
    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    prompt = build_candidate_prompt(config, HarnessState(job_id="job", iteration=1))
    assert not prompt.startswith("--"), (
        f"prompt starts with {prompt[:30]!r} — claude CLI will reject."
    )
    assert "=== BEGIN CANDIDATE PROFILE" in prompt
    assert "--- BEGIN CANDIDATE PROFILE" not in prompt


# --- Step 1: diagnosis injection (error profile + recent-table signature) ---


def _write_score(dir_path: Path, **kw: object) -> None:
    """Write a minimal score_report.json for tests."""
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "score_report.json").write_text(json.dumps(kw), encoding="utf-8")


def test_dominant_axis_substitution() -> None:
    """sub dominant + healthy length_ratio → substitution axis."""
    report = {
        "error_breakdown": {"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        "length_ratio": {"mean": 0.95},
        "hallucination_hit_rate": 0.18,
    }
    out = _dominant_axis(report)
    assert "substitution" in out.lower(), out


def test_dominant_axis_coverage_deletion() -> None:
    """deletion >= sub OR low length_ratio → coverage/deletion axis."""
    report = {
        "error_breakdown": {"sub_ratio": 0.18, "del_ratio": 0.80, "ins_ratio": 0.02},
        "length_ratio": {"mean": 0.60},
        "hallucination_hit_rate": 0.09,
    }
    out = _dominant_axis(report)
    assert "coverage" in out.lower() or "deletion" in out.lower(), out


def test_dominant_axis_over_generation() -> None:
    """high hallucination/insertion → over-generation axis."""
    report = {
        "error_breakdown": {"sub_ratio": 0.30, "del_ratio": 0.20, "ins_ratio": 0.50},
        "length_ratio": {"mean": 1.20},
        "hallucination_hit_rate": 0.40,
    }
    out = _dominant_axis(report)
    assert "over-generation" in out.lower(), out


def test_dominant_axis_missing_breakdown_degrades() -> None:
    """No error_breakdown → unknown, not a crash."""
    assert "unknown" in _dominant_axis({}).lower()


def test_read_score_report_missing_returns_empty(tmp_path: Path) -> None:
    assert _read_score_report(tmp_path) == {}


def test_format_error_profile_renders_axis(tmp_path: Path) -> None:
    """build the best dir's score_report and confirm the profile block names
    the measured mix + dominant axis."""
    runs = tmp_path / "runs"
    _write_score(
        runs / "job_iter_007",
        corpus_cer=0.1574,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95, "p05": 0.93},
        hallucination_hit_rate=0.18,
        repeated_text_rate=0.09,
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job", iteration=8, best_hyp_id="job_iter_007", best_cer=0.1574)
    block = _format_error_profile(config, state)
    assert "substitution 59%" in block
    assert "DOMINANT AXIS: substitution" in block
    assert "0.1574" in block


def test_format_error_profile_no_best_yet() -> None:
    config = RunnerConfig(job_id="job", repo_root=Path("."))
    state = HarnessState(job_id="job", iteration=1)
    assert "no best yet" in _format_error_profile(config, state).lower()


def test_format_error_profile_best_hyp_without_cer_does_not_crash() -> None:
    """Resume from a malformed/migrated state.json (best_hyp_id set but best_cer
    None) must degrade gracefully, not crash build_candidate_prompt."""
    config = RunnerConfig(job_id="job", repo_root=Path("."))
    state = HarnessState(
        job_id="job", iteration=9, best_hyp_id="job_iter_001", best_cer=None
    )
    assert "no best yet" in _format_error_profile(config, state).lower()


def test_recent_table_includes_failure_signature(tmp_path: Path) -> None:
    """_recent_iters pulls sub/del/ins + len + hal from score_report, and the
    table renders them so the candidate sees *how* each attempt failed."""
    runs = tmp_path / "runs"
    d = runs / "job_iter_001"
    d.mkdir(parents=True)
    (d / "candidate_meta.json").write_text(
        json.dumps({"fingerprint": ["beam"]}), encoding="utf-8"
    )
    _write_score(
        d,
        corpus_cer=0.16,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95},
        hallucination_hit_rate=0.18,
    )
    rows = _recent_iters(tmp_path, Path("runs"), "job", n=5)
    assert rows[0]["sub_ratio"] == 0.59
    assert rows[0]["length_ratio_mean"] == 0.95
    table = _format_recent_table(rows)
    assert "sub/del/ins" in table
    assert "0.59/0.35/0.06" in table


def test_recent_table_degrades_when_no_score(tmp_path: Path) -> None:
    """An iter with meta but no score_report (format reject) renders — for the
    signature columns, not a crash."""
    runs = tmp_path / "runs"
    d = runs / "job_iter_001"
    d.mkdir(parents=True)
    (d / "candidate_meta.err").write_text("missing yaml", encoding="utf-8")
    rows = _recent_iters(tmp_path, Path("runs"), "job", n=5)
    table = _format_recent_table(rows)
    assert "job_iter_001" in table
    assert "—" in table  # signature columns degrade gracefully


def test_error_profile_injected_into_prompt(tmp_path: Path) -> None:
    """End-to-end: build_candidate_prompt surfaces the error profile block."""
    _init_repo(tmp_path)
    runs = tmp_path / "runs"
    _write_score(
        runs / "job_iter_001",
        corpus_cer=0.20,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95, "p05": 0.93},
        hallucination_hit_rate=0.18,
        repeated_text_rate=0.09,
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job", iteration=2, best_hyp_id="job_iter_001", best_cer=0.20)
    prompt = build_candidate_prompt(config, state)
    assert "Error profile (best = job_iter_001" in prompt
    assert "DOMINANT AXIS" in prompt


# --- Step 2/3: iteration-based explore/synthesize schedule + synthesis ---

from harness.runner import (  # noqa: E402
    _EXPLORE_RATIO_FLOOR,
    _axis_scores,
    _explore_ratio,
    _format_synthesis_block,
    _is_explore_iter,
    _iteration_mode,
    _promising_rejects,
)


def _write_iter(runs: Path, name: str, *, diff: str = "x", **score: object) -> None:
    d = runs / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate_meta.json").write_text(
        json.dumps({"fingerprint": ["f"]}), encoding="utf-8"
    )
    (d / "score_report.json").write_text(json.dumps(score), encoding="utf-8")
    (d / "candidate.diff").write_text(diff, encoding="utf-8")


def test_axis_scores_clamps_coverage_at_one() -> None:
    s = _axis_scores(
        {
            "corpus_cer": 0.2,
            "error_breakdown": {"sub_ratio": 0.5, "del_ratio": 0.4, "ins_ratio": 0.1},
            "length_ratio": {"mean": 1.3},
        }
    )
    assert s["coverage"] == 1.0  # over-generation not counted as more coverage


def test_promising_rejects_ranks_by_sub_coverage_not_hallucination(tmp_path: Path) -> None:
    """A reject that only got lucky on the coarse hallucination rate (no sub /
    coverage gain) must NOT outrank one that genuinely lowered substitution."""
    runs = tmp_path / "runs"
    # best: sub .59 cov .95 hal .18
    _write_iter(
        runs, "job_iter_010",
        corpus_cer=0.157,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95}, hallucination_hit_rate=0.18,
    )
    # genuine sub improver (sub .49) — should be selected & ranked first
    _write_iter(
        runs, "job_iter_011", diff="GLOSSARY",
        corpus_cer=0.170,
        error_breakdown={"sub_ratio": 0.49, "del_ratio": 0.45, "ins_ratio": 0.06},
        length_ratio={"mean": 0.93}, hallucination_hit_rate=0.18,
    )
    # hallucination-luck only (sub/cov unchanged, hal 0) — must be excluded
    _write_iter(
        runs, "job_iter_012",
        corpus_cer=0.158,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95}, hallucination_hit_rate=0.0,
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(
        job_id="job", iteration=20, best_hyp_id="job_iter_010", best_cer=0.157,
        iters_since_best_update=10,
    )
    rej = _promising_rejects(config, state)
    names = [r["iter"] for r in rej]
    assert "job_iter_011" in names
    assert "job_iter_012" not in names  # hal-luck excluded
    assert rej[0]["iter"] == "job_iter_011"
    assert "GLOSSARY" in rej[0]["diff"]


def test_promising_rejects_skips_cer_blowups(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_iter(
        runs, "job_iter_010", corpus_cer=0.157,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95}, hallucination_hit_rate=0.18,
    )
    # improved sub massively but cer blew up (0.52) — not a usable lever
    _write_iter(
        runs, "job_iter_011", corpus_cer=0.52,
        error_breakdown={"sub_ratio": 0.10, "del_ratio": 0.88, "ins_ratio": 0.02},
        length_ratio={"mean": 0.56}, hallucination_hit_rate=0.18,
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(
        job_id="job", iteration=20, best_hyp_id="job_iter_010", best_cer=0.157,
        iters_since_best_update=10,
    )
    assert _promising_rejects(config, state) == []


def test_explore_ratio_decays_to_floor() -> None:
    """High early, monotonically decaying toward (never below) the floor."""
    r1 = _explore_ratio(1)
    r_mid = _explore_ratio(20)
    r_late = _explore_ratio(200)
    assert r1 > r_mid > r_late
    assert r_late >= _EXPLORE_RATIO_FLOOR
    assert abs(r_late - _EXPLORE_RATIO_FLOOR) < 1e-3  # converged to floor


def test_explore_density_high_early_floor_late() -> None:
    """Explore fraction is high in the first iters and approaches the floor in a
    late window — deterministic, so the counts are stable."""
    early = sum(_is_explore_iter(n) for n in range(1, 11)) / 10
    late = sum(_is_explore_iter(n) for n in range(191, 211)) / 20
    assert early >= 0.6  # explore-heavy start
    assert _EXPLORE_RATIO_FLOOR - 0.1 <= late <= _EXPLORE_RATIO_FLOOR + 0.15
    assert late > 0  # floor guarantees exploration never fully stops


def test_is_explore_iter_deterministic() -> None:
    """Same iteration → same decision every call (resume-safe)."""
    assert [_is_explore_iter(n) for n in range(1, 30)] == [
        _is_explore_iter(n) for n in range(1, 30)
    ]


def test_iteration_mode_explores_until_best_exists() -> None:
    """No best yet → always explore (nothing to synthesize from)."""
    state = HarnessState(job_id="job", iteration=50)  # no best_hyp_id
    assert _iteration_mode(state) == "explore"


def test_build_prompt_injects_parent_diff_for_refine(tmp_path: Path) -> None:
    """scheduler 가 refine 모드 + parent 를 주면 prompt 에 REFINE 지시문과
    parent 의 실제 diff 가 들어간다 (Step 2/4 배선)."""
    from harness.scheduler import SchedulerDecision

    _init_repo(tmp_path)
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    state = HarnessState(job_id="job", iteration=10, best_hyp_id="job_iter_001",
                         best_cer=0.18, evaluated_count=8)
    sched = SchedulerDecision("refine", "refine", "scheduled")
    parents = [{
        "hyp_id": "job_iter_001", "harness_family_id": "family_001",
        "cer": 0.18, "public_summary": "coverage up", "diff": "PARENT_DIFF_MARKER",
    }]
    prompt = build_candidate_prompt(config, state, sched=sched, parents=parents)
    assert "REFINE MODE" in prompt
    assert "PARENT_DIFF_MARKER" in prompt


def test_plateau_mode_injects_promising_rejects(tmp_path: Path) -> None:
    """no-improvement 이 길어지면(plateau) scheduler 가 PLATEAU 모드를 켜고,
    축 개선 reject 의 실제 diff 를 synthesis 재료로 주입한다 (구 exploit 대체)."""
    _init_repo(tmp_path)
    runs = tmp_path / "runs"
    _write_iter(
        runs, "job_iter_001", corpus_cer=0.157,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95, "p05": 0.93}, hallucination_hit_rate=0.18,
        repeated_text_rate=0.09,
    )
    _write_iter(
        runs, "job_iter_002", diff="SUBFIX_DIFF",
        corpus_cer=0.170,
        error_breakdown={"sub_ratio": 0.49, "del_ratio": 0.45, "ins_ratio": 0.06},
        length_ratio={"mean": 0.93, "p05": 0.90}, hallucination_hit_rate=0.18,
        repeated_text_rate=0.09,
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    # 긴 stall → plateau override (iters_since_best >= PLATEAU_K). portfolio.json
    # 이 없어 refine/combine 은 infeasible 이고, plateau 는 synthesis 재료를 붙인다.
    state = HarnessState(
        job_id="job", iteration=40, best_hyp_id="job_iter_001",
        best_cer=0.157, iters_since_best_update=20, evaluated_count=20,
        evaluated_since_best_update=20,  # plateau 는 evaluated 기준(#2)
    )
    prompt = build_candidate_prompt(config, state)  # sched=None → 내부 결정
    assert "PLATEAU MODE" in prompt
    assert "SUBFIX_DIFF" in prompt  # promising reject's actual code injected


def test_explore_mode_prompts_for_novelty(tmp_path: Path) -> None:
    """An explore-slot iteration is in EXPLORE MODE and asks for an unused
    mechanism, not a synthesis of rejects."""
    _init_repo(tmp_path)
    runs = tmp_path / "runs"
    _write_iter(
        runs, "job_iter_001", corpus_cer=0.157,
        error_breakdown={"sub_ratio": 0.59, "del_ratio": 0.35, "ins_ratio": 0.06},
        length_ratio={"mean": 0.95, "p05": 0.93}, hallucination_hit_rate=0.18,
        repeated_text_rate=0.09,
    )
    config = RunnerConfig(job_id="job", repo_root=tmp_path)
    explore_iter = next(n for n in range(2, 40) if _is_explore_iter(n))
    state = HarnessState(
        job_id="job", iteration=explore_iter, best_hyp_id="job_iter_001",
        best_cer=0.157, iters_since_best_update=explore_iter - 1,
    )
    assert _iteration_mode(state) == "explore"
    prompt = build_candidate_prompt(config, state)
    assert "EXPLORE MODE" in prompt
    assert "EXPLOIT MODE" not in prompt


# ── Task 8: bounded lineage set (--set-budget) ─────────────────────────────


def test_set_phase_overrides_scheduler_mode(tmp_path, monkeypatch):
    """Step 2 (I-1): a running set owns the prompt mode — set_phase, not the
    scheduler, decides chosen_mode while a set is active."""
    from harness.runner import RunnerConfig, _decide_iteration
    from harness.state import HarnessState

    _init_repo(tmp_path)
    cfg_ = RunnerConfig(job_id="j", repo_root=tmp_path, set_budget=4)
    st = HarnessState(job_id="j", set_phase="refine", evaluated_count=20,
                      best_hyp_id="h", best_cer=0.2)
    sched, _parents = _decide_iteration(cfg_, st)
    assert sched.chosen_mode == "refine"
    assert sched.override == "set:refine"


def test_set_keeps_worse_than_champion_explore(tmp_path, monkeypatch):
    """C2 fix: with set_budget>1 a worse-than-champion explore survives into
    refine (its code stays on disk) instead of being rolled back."""
    _init_repo(tmp_path)
    repo = tmp_path
    cfg_ = RunnerConfig(job_id="job", repo_root=repo, set_budget=4,
                        commit_results=True, manual=False, candidate_cmd=None)
    state = HarnessState(job_id="job", best_cer=0.20, best_hyp_id="champ")
    state_path = repo / "runs/_summary/job_state.json"

    def candidate(_prompt: str, out_dir: Path):
        (repo / "workspace/transcribe.py").write_text(
            "def transcribe(a, sr):\n    return 'EXPLORE'\n", encoding="utf-8")
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id: str) -> VerifyResult:
        return VerifyResult(
            ok=True,
            hyp_id=hyp_id,
            out_dir=repo / "runs" / hyp_id,
            report={"corpus_cer": 0.30, "total_inference_time_s": 90.0},
            per_file=[],
        )

    from harness import gitops
    gitops.ensure_champion_ref(repo, "champion")
    run_iteration(cfg_, state, state_path,
                  candidate_func=candidate, verify_func=verifier)

    assert state.set_phase == "refine"
    assert state.set_best_cer == 0.30
    assert state.best_cer == 0.20
    assert "EXPLORE" in (repo / "workspace/transcribe.py").read_text(encoding="utf-8")


def test_set_promotes_when_beating_champion(tmp_path, monkeypatch):
    """set 후보가 챔피언을 이기면 promote: best_cer 갱신 + set 종료(idle) +
    champion ref 가 새 HEAD 로 전진."""
    _init_repo(tmp_path)
    repo = tmp_path
    cfg_ = RunnerConfig(job_id="job", repo_root=repo, set_budget=4,
                        commit_results=True, manual=False, candidate_cmd=None)
    state = HarnessState(job_id="job", best_cer=0.20, best_hyp_id="champ")
    state_path = repo / "runs/_summary/job_state.json"

    def candidate(_prompt: str, out_dir: Path):
        (repo / "workspace/transcribe.py").write_text(
            "def transcribe(a, sr):\n    return 'WINNER'\n", encoding="utf-8")
        _write_valid_meta(out_dir)
        return subprocess.CompletedProcess(["fake"], 0, "", "")

    def verifier(hyp_id: str) -> VerifyResult:
        return VerifyResult(
            ok=True,
            hyp_id=hyp_id,
            out_dir=repo / "runs" / hyp_id,
            report={"corpus_cer": 0.15, "total_inference_time_s": 90.0},
            per_file=[],
        )

    from harness import gitops
    gitops.ensure_champion_ref(repo, "champion")
    run_iteration(cfg_, state, state_path,
                  candidate_func=candidate, verify_func=verifier)

    assert state.best_cer == 0.15          # global best advanced
    assert state.set_phase == "idle"       # set closed after promotion
    champ = subprocess.run(["git", "rev-parse", "champion"], cwd=repo,
                           check=True, capture_output=True, text=True).stdout.strip()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()
    assert champ == head                   # champion ref advanced to promoted commit


def test_snapshot_diff_flags_only_new_summary_and_dirs(tmp_path: Path) -> None:
    """phase1.5 snapshot guard: a pre-existing populated runs/_archive/ + prior
    runs/_summary/ files are NOT flagged; only files the candidate creates/edits
    under runs/_summary/ this iter, or a new top-level runs/<dir> that isn't the
    current hyp dir, are flagged."""
    from harness.runner import snapshot_ignored_surface, diff_ignored_surface
    summary = tmp_path / "runs/_summary"
    summary.mkdir(parents=True)
    (summary / "HISTORY.md").write_text("# h\n", encoding="utf-8")
    (summary / "job_state.json").write_text("{}\n", encoding="utf-8")
    archive = tmp_path / "runs/_archive/old_job"   # 'the 2584-file class'
    archive.mkdir(parents=True)
    (archive / "a.json").write_text("a\n", encoding="utf-8")
    (tmp_path / "runs/job_iter_001").mkdir()       # current hyp dir (allowed)

    before = snapshot_ignored_surface(tmp_path)
    # candidate poison: edits HISTORY.md, drops a new file in _summary/, and
    # creates a rogue top-level runs/ dir. Also legitimately fills its hyp dir.
    (summary / "HISTORY.md").write_text("# h\nPOISON\n", encoding="utf-8")
    (summary / "poison.txt").write_text("evil\n", encoding="utf-8")
    (tmp_path / "runs/rogue").mkdir()
    (tmp_path / "runs/rogue/x").write_text("x\n", encoding="utf-8")
    (tmp_path / "runs/job_iter_001/out.json").write_text("ok\n", encoding="utf-8")
    after = snapshot_ignored_surface(tmp_path)

    flagged = {str(p) for p in diff_ignored_surface(before, after, "job_iter_001")}
    assert "runs/_summary/HISTORY.md" in flagged    # modified metadata
    assert "runs/_summary/poison.txt" in flagged    # new metadata file
    assert "runs/rogue" in flagged                  # rogue top-level runs/ dir
    assert "runs/_archive/old_job/a.json" not in flagged  # ← pre-existing ignored
    assert "runs/_summary/job_state.json" not in flagged  # ← unchanged metadata
    assert "runs/job_iter_001/out.json" not in flagged    # ← current hyp dir OK
    assert "runs/job_iter_001" not in flagged


def test_snapshot_diff_clean_iter_not_flagged(tmp_path: Path) -> None:
    """A clean candidate iter (writes only into its own runs/<hyp>/) flags
    nothing, even with a heavily populated runs/_archive/ present before."""
    from harness.runner import snapshot_ignored_surface, diff_ignored_surface
    (tmp_path / "runs/_summary").mkdir(parents=True)
    (tmp_path / "runs/_summary/HISTORY.md").write_text("# h\n", encoding="utf-8")
    archive = tmp_path / "runs/_archive"
    for i in range(50):                              # many pre-existing ignored files
        d = archive / f"job_{i}"
        d.mkdir(parents=True)
        (d / "score.json").write_text(f"{i}\n", encoding="utf-8")
    (tmp_path / "runs/job_iter_002").mkdir()

    before = snapshot_ignored_surface(tmp_path)
    (tmp_path / "runs/job_iter_002/out.json").write_text("ok\n", encoding="utf-8")
    after = snapshot_ignored_surface(tmp_path)
    assert diff_ignored_surface(before, after, "job_iter_002") == []


def test_remove_ignored_poison_precise(tmp_path: Path) -> None:
    from harness.runner import remove_ignored_poison
    (tmp_path / "runs/_summary").mkdir(parents=True)
    (tmp_path / "runs/_summary/poison.txt").write_text("p\n", encoding="utf-8")
    (tmp_path / "runs/rogue").mkdir(parents=True)
    (tmp_path / "runs/rogue/x").write_text("x\n", encoding="utf-8")
    (tmp_path / "runs/_summary/keep.json").write_text("keep\n", encoding="utf-8")
    remove_ignored_poison(tmp_path, [
        Path("runs/_summary/poison.txt"),
        Path("runs/rogue"),
    ])
    assert not (tmp_path / "runs/_summary/poison.txt").exists()
    assert not (tmp_path / "runs/rogue").exists()
    assert (tmp_path / "runs/_summary/keep.json").exists()   # not in list → survives
