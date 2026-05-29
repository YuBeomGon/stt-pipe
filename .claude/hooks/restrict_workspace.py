#!/usr/bin/env python3
"""Phase 3 PreToolUse hook — Edit/Write/MultiEdit 대상을 workspace/transcribe.py
로만 제한.

AUTORESEARCH.md §6: autoresearch 의 Scope 는 prompt-only 이므로 우리가 강제한다.
본 훅은 ENV 우회 불가 — autoresearch 의 `AR_DISABLE_*` 와 무관.

stdin: Claude Code 가 PreToolUse 시 JSON 전달.
       {"tool_name": "Edit", "tool_input": {"file_path": "...", ...}}
exit 0 : 허용
exit 2 : 거부 (stderr 가 모델에게 노출됨)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 허용되는 편집 대상 — 워크스페이스의 단일 파일만.
ALLOWED_FILES = {
    "workspace/transcribe.py",
}

# Phase 3 자체 harness 기준: candidate 가 직접 Edit/Write 해야 하는 산출
# 디렉토리는 *없음*. judge 평가 산출 (runs/<hyp_id>/...) 은 harness 가 띄우는
# judge.evaluate 가 만드는 것이며 candidate 의 Tool 호출이 아니다. autoresearch
# 는 deprecated. ALLOWED_PREFIXES 가 비면 candidate 의 모든 Edit/Write 대상이
# ALLOWED_FILES 1 개 (workspace/transcribe.py) 로 좁혀진다.
#
# codex 2차 hardening (2026-05-29): 과거에 허용했던 `runs/`, `autoresearch/`
# 는 cheating 경로로 활용 가능 (예: 과거 score_report.json 덮어쓰기). 제거.
ALLOWED_PREFIXES: tuple[str, ...] = ()


def _read_input() -> dict:
    try:
        return json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return {}


def _project_root() -> Path:
    """Hook 가 어디서 호출되든 프로젝트 루트 기준으로 비교."""
    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR", "")).resolve() or Path.cwd().resolve()
    return cwd


def _normalize(target: str, root: Path) -> str:
    """절대/상대 경로를 프로젝트 루트 기준 상대 경로로."""
    p = Path(target)
    if not p.is_absolute():
        p = (root / p).resolve()
    else:
        p = p.resolve()
    try:
        return str(p.relative_to(root)).replace(os.sep, "/")
    except ValueError:
        return str(p)


def main() -> int:
    data = _read_input()
    if not data:
        return 0
    tool = data.get("tool_name") or data.get("tool") or ""
    if tool not in ("Edit", "Write", "MultiEdit"):
        return 0
    target = (data.get("tool_input") or {}).get("file_path", "")
    if not target:
        return 0

    root = _project_root()
    rel = _normalize(target, root)

    if rel in ALLOWED_FILES:
        return 0
    if any(rel.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return 0

    allowed_summary = (
        "workspace/transcribe.py 만 편집 가능"
        if not ALLOWED_PREFIXES
        else f"workspace/transcribe.py 또는 ({', '.join(ALLOWED_PREFIXES)})"
    )
    print(
        f"Phase 3 가드: '{rel}' 편집 거부. {allowed_summary}. "
        f"AGENTS.md §1 표 / PHASE3-PLAN §1.2 참조.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
