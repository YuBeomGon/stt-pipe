"""harness/candidate_cli.py — self-contained candidate-CLI layer.

Lifted from harness/runner.py (hardening + diff-capture + YAML parse) so the
simple evolve loop never imports the 2772-line controller. runner.py is left
unchanged for the legacy path; this is a copy, not a move.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_CLAUDE_HARDENING_ARGS: tuple[str, ...] = (
    "--disable-slash-commands",
    "--strict-mcp-config",
    "--disallowedTools=Bash,WebFetch,WebSearch,Task",
)
_HARDEN_BYPASS_ENV = "EVOLVE_NO_HARDEN_CLAUDE"

_REQUIRED_META_KEYS = (
    "capability_investigated",
    "what_i_learned",
    "hypothesis",
    "fingerprint",
)
_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)
_FINGERPRINT_MAX_TOKENS = 6


def _bypass_active() -> bool:
    return os.environ.get(_HARDEN_BYPASS_ENV) == "1"


def check_bypass_in_production(iterations: int, commit_results: bool) -> None:
    """Refuse the hardening bypass for production jobs (--iters>1 or commit)."""
    if not _bypass_active():
        return
    production = iterations > 1 or commit_results
    if not production:
        print(
            f"WARNING: {_HARDEN_BYPASS_ENV}=1 — candidate hardening bypassed "
            "(skills/MCP exposed); allowed because single-iter no-commit.",
            file=sys.stderr,
        )
        return
    raise RuntimeError(
        f"{_HARDEN_BYPASS_ENV}=1 set but this is a production job "
        f"(iterations={iterations}, commit_results={commit_results}). "
        f"Bypass is debug-only. Unset {_HARDEN_BYPASS_ENV} and retry."
    )


def harden_candidate_cmd(candidate_cmd: str) -> tuple[str, list[str]]:
    """Inject skills/MCP/Tool hardening flags when argv[0] basename is `claude`.

    Idempotent; non-claude commands pass through unchanged.
    """
    if _bypass_active():
        print(
            f"WARNING: {_HARDEN_BYPASS_ENV}=1 — hardening skipped for this "
            "candidate invocation",
            file=sys.stderr,
        )
        return candidate_cmd, []
    parts = shlex.split(candidate_cmd)
    if not parts or Path(parts[0]).name != "claude":
        return candidate_cmd, []
    added: list[str] = []
    for arg in _CLAUDE_HARDENING_ARGS:
        flag_name = arg.split("=", 1)[0]
        present = any(p == flag_name or p.startswith(flag_name + "=") for p in parts)
        if not present:
            parts.append(arg)
            added.append(arg)
    return shlex.join(parts), added


def _run_git(repo_root: Path, args: list[str], check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=repo_root, check=check, capture_output=True, text=True
    )


def run_candidate_command(
    candidate_cmd: str,
    prompt: str,
    out_dir: Path,
    repo_root: Path,
    workspace_file: Path = Path("workspace/transcribe.py"),
) -> subprocess.CompletedProcess[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    hardened_cmd, added_flags = harden_candidate_cmd(candidate_cmd)
    if added_flags:
        (out_dir / "candidate_cmd_hardening.txt").write_text(
            f"original: {candidate_cmd}\nhardened: {hardened_cmd}\n"
            f"added:    {' '.join(added_flags)}\n",
            encoding="utf-8",
        )
    cmd = [*shlex.split(hardened_cmd), prompt]
    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    (out_dir / "claude_stdout.txt").write_text(result.stdout, encoding="utf-8")
    (out_dir / "claude_stderr.txt").write_text(result.stderr, encoding="utf-8")
    diff = _run_git(repo_root, ["diff", "--", workspace_file.as_posix()], check=False)
    (out_dir / "candidate.diff").write_text(diff.stdout, encoding="utf-8")
    return result


def parse_candidate_metadata(
    stdout_text: str, out_dir: Path | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Extract + validate the required YAML metadata block. Returns
    (meta, None) on success or (None, reason) on any failure."""
    def _fail(reason: str):
        if out_dir is not None:
            (out_dir / "candidate_meta.err").write_text(reason, encoding="utf-8")
        return None, reason

    matches = _YAML_FENCE_RE.findall(stdout_text)
    if not matches:
        return _fail("no yaml fenced block in stdout")
    try:
        parsed = yaml.safe_load(matches[-1])
    except yaml.YAMLError as exc:
        return _fail(f"yaml parse failed: {exc}")
    if not isinstance(parsed, dict):
        return _fail(f"yaml block is not a mapping ({type(parsed).__name__})")
    missing = [k for k in _REQUIRED_META_KEYS if k not in parsed]
    if missing:
        return _fail(f"missing keys: {missing}")
    fp = parsed["fingerprint"]
    if not isinstance(fp, list) or not all(isinstance(t, str) for t in fp):
        return _fail(f"fingerprint must be a list of strings, got {type(fp).__name__}")
    if not (1 <= len(fp) <= _FINGERPRINT_MAX_TOKENS):
        return _fail(f"fingerprint length {len(fp)} out of range [1, {_FINGERPRINT_MAX_TOKENS}]")
    normalized_fp = [t.strip().lower() for t in fp]
    if any(not t for t in normalized_fp):
        return _fail("fingerprint contains empty/whitespace-only tokens")
    text_fields: dict[str, str] = {}
    for key in ("capability_investigated", "what_i_learned", "hypothesis"):
        val = parsed[key]
        if not isinstance(val, str) or not val.strip():
            return _fail(f"{key} must be a non-empty string")
        text_fields[key] = val.strip()
    normalized = {**text_fields, "fingerprint": normalized_fp}
    lane = parsed.get("lane")
    if isinstance(lane, str) and lane.strip():
        normalized["lane"] = lane.strip().lower()
    if out_dir is not None:
        (out_dir / "candidate_meta.json").write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return normalized, None
