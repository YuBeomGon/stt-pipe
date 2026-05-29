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
    disallowed_candidate_paths,
    run_candidate_command,
    run_iteration,
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


def test_candidate_scope_only_allows_workspace_and_summary() -> None:
    config = RunnerConfig(job_id="job")
    statuses = [
        GitPathStatus(" M", Path("workspace/transcribe.py")),
        GitPathStatus(" M", Path("runs/_summary/HISTORY.md")),
        GitPathStatus(" M", Path("docs/PHASE3-PLAN.md")),
    ]
    bad = disallowed_candidate_paths(statuses, config, allow_summary=True)
    assert [item.path for item in bad] == [Path("docs/PHASE3-PLAN.md")]


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

