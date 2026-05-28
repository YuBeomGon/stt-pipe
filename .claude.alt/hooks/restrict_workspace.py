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

# 허용되는 *디렉토리 prefix* — 그 안의 임의 파일 작성 가능.
ALLOWED_PREFIXES = (
    "runs/",        # autoresearch / judge 산출. agent 도 자유 작성 가능 (telemetry 등)
    "autoresearch/" # autoresearch 자체 결과 TSV / handoff.json
)


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

    print(
        f"Phase 3 가드: '{rel}' 편집 거부. workspace/transcribe.py 만 편집 가능 "
        f"(허용 산출 디렉토리: {', '.join(ALLOWED_PREFIXES)}). "
        f"AGENTS.md §1 표 / PHASE3-PLAN §1.2 참조.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
