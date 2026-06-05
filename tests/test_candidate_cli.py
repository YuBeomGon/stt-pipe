"""Tests for the lifted candidate-CLI layer (harness/candidate_cli.py)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import candidate_cli as cc


def test_harden_injects_flags_for_claude():
    hardened, added = cc.harden_candidate_cmd("claude -p")
    assert "--disallowedTools=Bash,WebFetch,WebSearch,Task" in hardened
    assert "--disable-slash-commands" in hardened
    assert "--strict-mcp-config" in hardened
    assert added  # non-empty list of what was added


def test_harden_is_idempotent():
    once, _ = cc.harden_candidate_cmd("claude -p")
    twice, added2 = cc.harden_candidate_cmd(once)
    assert twice == once
    assert added2 == []


def test_harden_passes_through_non_claude():
    cmd = "python my_stub.py"
    hardened, added = cc.harden_candidate_cmd(cmd)
    assert hardened == cmd
    assert added == []


def test_parse_metadata_happy_path(tmp_path):
    stdout = (
        "blah blah\n```yaml\n"
        "capability_investigated: |\n  studied generate kwargs\n"
        "what_i_learned: |\n  beam returns logprobs\n"
        "hypothesis: |\n  use logprobs to gate\n"
        "fingerprint: [decode, gate]\n```\n"
    )
    meta, reason = cc.parse_candidate_metadata(stdout, out_dir=tmp_path)
    assert reason is None
    assert meta["fingerprint"] == ["decode", "gate"]
    assert (tmp_path / "candidate_meta.json").is_file()


def test_parse_metadata_rejects_missing_block(tmp_path):
    meta, reason = cc.parse_candidate_metadata("no yaml here", out_dir=tmp_path)
    assert meta is None
    assert "yaml" in reason.lower()
    assert (tmp_path / "candidate_meta.err").is_file()


def test_run_candidate_command_captures_diff(tmp_path, monkeypatch):
    # Build a tiny git repo with a workspace file a stub command will edit.
    import subprocess
    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    ws = repo / "workspace"
    ws.mkdir()
    (ws / "transcribe.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    # Stub candidate: a python script that appends a line to the workspace file.
    stub = repo / "stub.py"
    stub.write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path('workspace/transcribe.py')\n"
        "p.write_text(p.read_text() + 'y = 2\\n')\n"
        "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
        "hypothesis: c\\nfingerprint: [t]\\n```')\n"
    )
    out_dir = repo / "runs" / "iter1"
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")  # stub isn't claude anyway
    res = cc.run_candidate_command(
        candidate_cmd=f"{sys.executable} {stub}",
        prompt="hello",
        out_dir=out_dir,
        repo_root=repo,
    )
    assert res.returncode == 0
    assert (out_dir / "prompt.md").read_text() == "hello"
    assert (out_dir / "claude_stdout.txt").is_file()
    diff = (out_dir / "candidate.diff").read_text()
    assert "y = 2" in diff
