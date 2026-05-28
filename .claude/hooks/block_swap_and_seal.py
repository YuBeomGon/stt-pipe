#!/usr/bin/env python3
"""Phase 3 PreToolUse hook — Bash 호출에서 사람 전용 스크립트 / holdout chmod 거부.

차단 대상:
  * `scripts/swap_verify.sh`   — verify swap (사람 전용)
  * `scripts/swap_claude.sh`   — .claude swap (사람 전용)
  * `scripts/seal_holdout.sh`  — holdout 봉인 (사람 전용)
  * `scripts/evaluate_holdout.py --unseal` — holdout unseal (사람 전용 1회)
  * holdout 디렉토리에 대한 `chmod` 직접 호출 (chmod 우회)
  * `.claude.alt`, `.claude` 디렉토리에 대한 `mv` / `rm` (swap 우회)

AGENTS.md §1 표 / PHASE3-PLAN §1.2 / §9 안티 패턴.

exit 0 : 허용
exit 2 : 거부
"""
from __future__ import annotations

import json
import re
import sys

_BLOCK_PATTERNS = [
    (re.compile(r"\bscripts/swap_verify\.sh\b"),       "swap_verify.sh — 사람 전용"),
    (re.compile(r"\bscripts/swap_claude\.sh\b"),       "swap_claude.sh — 사람 전용"),
    (re.compile(r"\bscripts/seal_holdout\.sh\b"),      "seal_holdout.sh — 사람 전용"),
    (re.compile(r"scripts/evaluate_holdout\.py.*--unseal"),
                                                       "evaluate_holdout.py --unseal — 사람 전용 1회"),
    (re.compile(r"\bchmod\b.*AIG_녹취반출_20250813"),  "holdout chmod 우회 시도"),
    (re.compile(r"\bmv\b.*\.claude(\.alt)?\b"),        ".claude swap 우회 시도"),
    (re.compile(r"\brm\b.*-rf?.*\.claude(\.alt)?\b"),  ".claude 삭제 시도"),
    (re.compile(r"\bmv\b.*scripts/verify\.sh"),        "verify.sh swap 우회 시도"),
]


def _read_input() -> dict:
    try:
        return json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return {}


def main() -> int:
    data = _read_input()
    if not data:
        return 0
    tool = data.get("tool_name") or data.get("tool") or ""
    if tool != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not cmd:
        return 0

    for pattern, reason in _BLOCK_PATTERNS:
        if pattern.search(cmd):
            print(
                f"Phase 3 가드: bash 명령 거부 — {reason}. "
                f"AGENTS.md §1 표 / PHASE3-PLAN §1.2·§9 참조.",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
