"""Audit what `claude -p` actually loads into the candidate context.

[`docs/CANDIDATE-CONTEXT.md`](../docs/CANDIDATE-CONTEXT.md) §6 가 본 스크립트
의 정본 설명. 잡 시작 전 1 회 실행 권장 (`claude -p` 1 회 호출 ~10 s).

    python3 scripts/audit_candidate_context.py

동작:
  1. 구조화 응답을 강제하는 짧은 probe prompt 를 `claude -p` 로 호출.
  2. 응답을 fixed-key parser 로 분석 (LLM 자기 보고 — 한계는 §6 참고).
  3. 사전 정의된 LEAK 규칙 (CLAUDE.md 로드, 플러그인 inject, PII, git 노출)
     에 매칭되면 누수 리포트.
  4. 결과 JSON 사이드카 → `docs/reports/<YYYY-MM-DD>_context_audit.json`.
  5. 누수 0 → exit 0. 누수 1+ → exit 1 + stderr 에 항목별 한 줄.

본 스크립트는 *읽기 전용 probe* — workspace / runs / commit 에 영향 X.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

_DEFAULT_CMD = "claude -p"
_REPORTS_DIR = Path("docs/reports")

# Probe 가 LLM 에게 *키:값* 형태로 자기 보고하라고 요구. free-form prose 보다
# parse 안정성 ↑. 각 키는 EXPECTED_KEYS 에 그대로 등장해야 정상 응답으로 인정.
_PROBE = """\
SYSTEM CONTEXT AUDIT MODE.

Do not invoke any tool. Do not edit any file. Respond ONLY in the exact
key:value format below, one key per line, in this order:

CWD: <your effective working directory>
CLAUDE_MD_LOADED: <YES if a project CLAUDE.md file is in your context, else NO>
CLAUDE_MD_FIRST_50: <first 50 characters of that CLAUDE.md verbatim - WITHOUT the leading "# CLAUDE.md" heading - else NONE>
AGENTS_MD_LOADED: <YES/NO — agent guide auto-loaded?>
SETTINGS_JSON_VISIBLE: <YES/NO — can you see project .claude/settings.json contents?>
HOOKS_FIRED_AT_START: <YES/NO — was a SessionStart hook injected? List hook names if YES>
PLUGIN_AUTO_INJECTED: <comma-separated plugin/skill names auto-injected at session start, or NONE>
ENV_USER_EMAIL_VISIBLE: <YES/NO — is an operator email address present anywhere>
ENV_CURRENT_DATE_VISIBLE: <YES/NO>
GIT_STATUS_VISIBLE: <YES/NO>
GIT_RECENT_COMMITS_COUNT: <integer — how many recent commit subject lines you can see, 0 if none>
SKILLS_AVAILABLE_COUNT: <integer — how many user-invocable skills you can see>
MCP_SERVERS_VISIBLE: <comma-separated MCP server names, or NONE>

AUDIT-DONE
"""

# 필수 키 — 응답에 누락되면 probe 실패로 간주 (정상 응답 X).
_EXPECTED_KEYS = (
    "CWD",
    "CLAUDE_MD_LOADED",
    "CLAUDE_MD_FIRST_50",
    "AGENTS_MD_LOADED",
    "SETTINGS_JSON_VISIBLE",
    "HOOKS_FIRED_AT_START",
    "PLUGIN_AUTO_INJECTED",
    "ENV_USER_EMAIL_VISIBLE",
    "ENV_CURRENT_DATE_VISIBLE",
    "GIT_STATUS_VISIBLE",
    "GIT_RECENT_COMMITS_COUNT",
    "SKILLS_AVAILABLE_COUNT",
    "MCP_SERVERS_VISIBLE",
)


@dataclass
class LeakRule:
    key: str
    label: str
    predicate: Callable[[str], bool]
    rationale: str


def _is_yes(v: str) -> bool:
    return v.strip().upper().startswith("YES")


def _not_none(v: str) -> bool:
    return v.strip().upper() not in ("NONE", "NO", "0", "")


def _positive_int(v: str) -> bool:
    try:
        return int(v.strip()) > 0
    except ValueError:
        return False


# Marker placed at the top of CLAUDE.md so the file is auto-loaded but the
# content gates itself out of candidate sessions (see CLAUDE.md and
# CANDIDATE-CONTEXT.md §8). When the first-50 capture contains the marker we
# treat CLAUDE_MD_LOADED as "loaded but candidate-safe" (warn, not leak).
_CANDIDATE_GATE_MARKER = "Candidate session gate"


# Each rule represents an undesirable context source for a candidate session.
# `predicate(value)` returns True when the rule is violated (= leak detected).
_LEAK_RULES: tuple[LeakRule, ...] = (
    LeakRule(
        "CLAUDE_MD_LOADED",
        "Project CLAUDE.md auto-loaded",
        _is_yes,
        "Operator-only guidance leaks into candidate (rules, harness internals).",
    ),
    LeakRule(
        "AGENTS_MD_LOADED",
        "AGENTS.md auto-loaded",
        _is_yes,
        "Permission tables / holdout protocol leak.",
    ),
    LeakRule(
        "HOOKS_FIRED_AT_START",
        "SessionStart hook injection",
        _is_yes,
        "Plugin SessionStart hooks (e.g. superpowers) force pre-response skill invocation, "
        "may derail focused YAML emission.",
    ),
    LeakRule(
        "PLUGIN_AUTO_INJECTED",
        "Plugin/skill auto-injection",
        _not_none,
        "Auto-loaded skills bias candidate toward generic workflows.",
    ),
    LeakRule(
        "ENV_USER_EMAIL_VISIBLE",
        "Operator email PII",
        _is_yes,
        "Minor PII leak into candidate session.",
    ),
    LeakRule(
        "GIT_RECENT_COMMITS_COUNT",
        "Git recent commits visible",
        _positive_int,
        "Past iter commit subjects (e.g. 'iter9: keep ...') reveal harness verdicts and "
        "previous attempt outcomes — undermines A' recent-iter table compression.",
    ),
)


def run_probe(cmd: str, cwd: Path, timeout: int) -> tuple[str, str, int]:
    """Run `<cmd> <probe>` from cwd. Returns (stdout, stderr, returncode)."""
    argv = [*shlex.split(cmd), _PROBE]
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    return proc.stdout, proc.stderr, proc.returncode


def parse_probe(output: str) -> dict[str, str]:
    """Extract KEY: VALUE lines for known keys. Tolerates extra prose before
    or after — only exact-key lines count."""
    fields: dict[str, str] = {}
    for line in output.splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key in _EXPECTED_KEYS and key not in fields:
            fields[key] = value.strip()
    return fields


def detect_leaks(fields: dict[str, str]) -> list[dict[str, str]]:
    leaks: list[dict[str, str]] = []
    # Gate exemption: CLAUDE.md loaded *with the candidate gate marker at the
    # top* is by design — the file is auto-loaded but its body tells the LLM
    # to ignore itself in candidate sessions. Treat as known-safe, omit from
    # the leak list. Without the marker (gate missing or some other CLAUDE.md
    # variant), the leak fires as usual.
    claude_md_first_50 = fields.get("CLAUDE_MD_FIRST_50", "")
    claude_md_gated = _CANDIDATE_GATE_MARKER in claude_md_first_50

    for rule in _LEAK_RULES:
        value = fields.get(rule.key, "")
        if not value:
            continue
        if rule.key == "CLAUDE_MD_LOADED" and claude_md_gated and _is_yes(value):
            continue
        if rule.predicate(value):
            leaks.append(
                {
                    "key": rule.key,
                    "label": rule.label,
                    "observed": value,
                    "rationale": rule.rationale,
                }
            )
    return leaks


def write_report(
    out_path: Path,
    cmd: str,
    cwd: Path,
    fields: dict[str, str],
    missing_keys: list[str],
    leaks: list[dict[str, str]],
    raw_stdout: str,
    raw_stderr: str,
    returncode: int,
) -> None:
    payload: dict[str, Any] = {
        "produced_at": datetime.now(UTC).isoformat(),
        "probe_cmd": cmd,
        "cwd": str(cwd),
        "candidate_returncode": returncode,
        "parsed_fields": fields,
        "missing_keys": missing_keys,
        "leaks_detected": leaks,
        "raw_stderr": raw_stderr.strip(),
        # raw_stdout 은 길어질 수 있어 마지막에. CLAUDE.md 본문 일부 등 PII 우려
        # 있는 필드는 parsed_fields 에 이미 캡처됨.
        "raw_stdout": raw_stdout,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe what claude -p loads into the candidate context."
    )
    parser.add_argument(
        "--candidate-cmd", default=_DEFAULT_CMD,
        help='Candidate command (default: "claude -p"). Last argv is the probe prompt.',
    )
    parser.add_argument(
        "--cwd", default=".",
        help="Working directory for the probe (default: project root).",
    )
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="Seconds before killing the probe (default 120).",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output JSON path. Default: docs/reports/<YYYY-MM-DD>_context_audit.json",
    )
    args = parser.parse_args(argv)

    cwd = Path(args.cwd).resolve()
    if args.out is None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        out_path = _REPORTS_DIR / f"{date}_context_audit.json"
    else:
        out_path = Path(args.out)

    try:
        stdout, stderr, rc = run_probe(args.candidate_cmd, cwd, args.timeout)
    except subprocess.TimeoutExpired:
        print(f"probe timed out after {args.timeout}s", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"candidate command not found: {exc}", file=sys.stderr)
        return 3

    fields = parse_probe(stdout)
    missing_keys = [k for k in _EXPECTED_KEYS if k not in fields]
    leaks = detect_leaks(fields)

    write_report(
        out_path, args.candidate_cmd, cwd, fields, missing_keys, leaks,
        stdout, stderr, rc,
    )
    print(f"wrote {out_path}")

    if missing_keys:
        print(
            f"WARN: probe response missing {len(missing_keys)} expected keys: "
            f"{missing_keys[:5]}{'...' if len(missing_keys) > 5 else ''}",
            file=sys.stderr,
        )

    if not leaks:
        print(f"OK — no context leaks detected ({len(fields)}/{len(_EXPECTED_KEYS)} keys parsed)")
        return 0

    print(f"LEAKS DETECTED: {len(leaks)}", file=sys.stderr)
    for leak in leaks:
        print(f"  - {leak['label']}: observed={leak['observed']!r}", file=sys.stderr)
        print(f"    why: {leak['rationale']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
