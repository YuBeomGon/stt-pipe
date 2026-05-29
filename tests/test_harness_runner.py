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
    GitPathStatus,
    RunnerConfig,
    build_candidate_prompt,
    candidate_owned_statuses,
    disallowed_candidate_paths,
    run_candidate_command,
    run_iteration,
    run_job,
)
from harness.state import HarnessState
from harness.verify import VerifyResult, check_workspace_static


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
        ["git", "add", "workspace/transcribe.py", "baseline", "runs/_summary/HISTORY.md"],
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

    def candidate(_prompt: str, _out_dir: Path):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(audio, sr):\n    return 'ok'\n",
            encoding="utf-8",
        )
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

    def candidate(_prompt: str, _out_dir: Path):
        (tmp_path / "runs/_summary/HISTORY.md").write_text(
            "candidate injected text\n",
            encoding="utf-8",
        )
        (tmp_path / "runs/_summary/poison.txt").write_text(
            "candidate injected text\n",
            encoding="utf-8",
        )
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

    def candidate(_prompt: str, _out_dir: Path):
        (tmp_path / "workspace/transcribe.py").write_text(
            "def transcribe(audio, sr):\n    return 'ok'\n",
            encoding="utf-8",
        )
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
