"""Smoke for `.claude.alt/` Phase 3 본문 — settings.json + PreToolUse hooks.

검증:
  * settings.json 이 valid JSON, permissions.deny 가 모든 보호 경로 포함
  * hooks 가 등록됨 (Edit/Write/MultiEdit, Bash)
  * restrict_workspace.py — workspace 만 허용, judge/baseline/frozen/assets 거부
  * block_swap_and_seal.py — swap/seal/unseal/holdout-chmod 거부

본 테스트는 Claude Code 자체를 띄우지 않고 hook 스크립트만 직접 호출한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALT = ROOT / ".claude.alt"


# ---------------------------------------------------------------------------
# settings.json 스키마
# ---------------------------------------------------------------------------

def test_settings_json_valid() -> None:
    assert (ALT / "settings.json").is_file()
    data = json.loads((ALT / "settings.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "permissions" in data
    assert "hooks" in data


def test_deny_covers_protected_paths() -> None:
    data = json.loads((ALT / "settings.json").read_text(encoding="utf-8"))
    deny = data["permissions"]["deny"]
    assert isinstance(deny, list) and len(deny) > 0

    must_include = [
        "judge/",
        "frozen/",
        "baseline/",
        "assets/",
        "scripts/swap_verify.sh",
        "scripts/swap_claude.sh",
        "scripts/seal_holdout.sh",
        "scripts/evaluate_holdout.py",
        "scripts/verify.sh",
        "scripts/verify_check.py",
        "AIG_녹취반출_20250813",
    ]
    joined = "\n".join(deny)
    for needle in must_include:
        assert needle in joined, f"deny 누락: {needle}"


def test_hooks_registered() -> None:
    data = json.loads((ALT / "settings.json").read_text(encoding="utf-8"))
    hooks = data["hooks"]
    assert "PreToolUse" in hooks
    pre = hooks["PreToolUse"]
    assert isinstance(pre, list) and len(pre) >= 2

    matchers = {entry["matcher"] for entry in pre}
    # Edit/Write/MultiEdit 조합 + Bash 둘 다 매처에 등장해야 함
    assert any("Edit" in m or "Write" in m or "MultiEdit" in m for m in matchers)
    assert "Bash" in matchers


# ---------------------------------------------------------------------------
# restrict_workspace.py 동작
# ---------------------------------------------------------------------------

def _run_hook(script: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_restrict_workspace_allows_transcribe() -> None:
    r = _run_hook(
        ALT / "hooks" / "restrict_workspace.py",
        {"tool_name": "Edit", "tool_input": {"file_path": "workspace/transcribe.py"}},
    )
    assert r.returncode == 0, r.stderr


def test_restrict_workspace_allows_runs_dir() -> None:
    r = _run_hook(
        ALT / "hooks" / "restrict_workspace.py",
        {"tool_name": "Write", "tool_input": {"file_path": "runs/iter_01/note.md"}},
    )
    assert r.returncode == 0, r.stderr


def test_restrict_workspace_denies_judge() -> None:
    r = _run_hook(
        ALT / "hooks" / "restrict_workspace.py",
        {"tool_name": "Edit", "tool_input": {"file_path": "judge/metrics.py"}},
    )
    assert r.returncode == 2
    assert "judge" in r.stderr


def test_restrict_workspace_denies_baseline() -> None:
    r = _run_hook(
        ALT / "hooks" / "restrict_workspace.py",
        {"tool_name": "Write", "tool_input": {"file_path": "baseline/target_cer.json"}},
    )
    assert r.returncode == 2
    assert "baseline" in r.stderr


def test_restrict_workspace_denies_frozen() -> None:
    r = _run_hook(
        ALT / "hooks" / "restrict_workspace.py",
        {"tool_name": "MultiEdit", "tool_input": {"file_path": "frozen/asr_backend.py"}},
    )
    assert r.returncode == 2


def test_restrict_workspace_ignores_non_edit_tools() -> None:
    """Read 같은 도구는 본 훅 책임 밖 (matcher 자체가 Edit/Write/MultiEdit)."""
    r = _run_hook(
        ALT / "hooks" / "restrict_workspace.py",
        {"tool_name": "Read", "tool_input": {"file_path": "judge/metrics.py"}},
    )
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# block_swap_and_seal.py 동작
# ---------------------------------------------------------------------------

def test_block_swap_verify() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Bash", "tool_input": {"command": "bash scripts/swap_verify.sh"}},
    )
    assert r.returncode == 2
    assert "swap_verify" in r.stderr


def test_block_swap_claude() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Bash", "tool_input": {"command": "bash scripts/swap_claude.sh"}},
    )
    assert r.returncode == 2
    assert "swap_claude" in r.stderr


def test_block_seal_holdout() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Bash", "tool_input": {"command": "bash scripts/seal_holdout.sh"}},
    )
    assert r.returncode == 2
    assert "seal_holdout" in r.stderr


def test_block_evaluate_holdout_unseal() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Bash",
         "tool_input": {"command": "python scripts/evaluate_holdout.py --unseal"}},
    )
    assert r.returncode == 2
    assert "unseal" in r.stderr


def test_block_holdout_chmod_bypass() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Bash",
         "tool_input": {"command": "chmod -R u+rwX data/raw/wav/AIG_녹취반출_20250813"}},
    )
    assert r.returncode == 2
    assert "holdout chmod" in r.stderr


def test_block_claude_dir_mv_bypass() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Bash", "tool_input": {"command": "mv .claude .claude.bak"}},
    )
    assert r.returncode == 2


def test_block_swap_allows_normal_bash() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Bash", "tool_input": {"command": "ls workspace/"}},
    )
    assert r.returncode == 0


def test_block_swap_ignores_non_bash_tools() -> None:
    r = _run_hook(
        ALT / "hooks" / "block_swap_and_seal.py",
        {"tool_name": "Edit",
         "tool_input": {"file_path": "scripts/swap_verify.sh"}},
    )
    # Edit 은 본 훅 책임 밖 (matcher 가 Bash 임). Edit 거부는 restrict_workspace 책임.
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# swap_claude.sh 3-way mv 무결성 (실제 swap 실행 — tmp dir 에서)
# ---------------------------------------------------------------------------

def test_swap_claude_3way_mv(tmp_path: Path) -> None:
    """swap_claude.sh 가 두 디렉토리를 1:1 교환하고, 한 번 더 호출하면 원복."""
    import shutil

    # tmp 에 가짜 프로젝트 구조 만들기
    project = tmp_path / "proj"
    project.mkdir()
    a = project / ".claude"
    b = project / ".claude.alt"
    a.mkdir()
    b.mkdir()
    (a / "marker_A").write_text("A")
    (b / "marker_B").write_text("B")

    # swap_claude.sh 복사 (cwd 기반이므로 project 안에)
    shutil.copy(ROOT / "scripts" / "swap_claude.sh", project / "swap_claude.sh")

    # 1차 swap
    r = subprocess.run(
        ["bash", "swap_claude.sh"],
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (a / "marker_B").is_file(), "marker_B 가 .claude 로 이동했어야 함"
    assert (b / "marker_A").is_file(), "marker_A 가 .claude.alt 로 이동했어야 함"

    # 2차 swap → 원복
    r = subprocess.run(
        ["bash", "swap_claude.sh"],
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (a / "marker_A").is_file(), "원복 후 marker_A 가 .claude 에 있어야 함"
    assert (b / "marker_B").is_file(), "원복 후 marker_B 가 .claude.alt 에 있어야 함"


def test_swap_claude_refuses_missing_dir(tmp_path: Path) -> None:
    """`.claude.alt` 누락 시 거부."""
    import shutil
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".claude").mkdir()
    # .claude.alt 의도적으로 누락
    shutil.copy(ROOT / "scripts" / "swap_claude.sh", project / "swap_claude.sh")
    r = subprocess.run(
        ["bash", "swap_claude.sh"],
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert ".claude.alt" in r.stderr
