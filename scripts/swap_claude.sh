#!/usr/bin/env bash
# scripts/swap_claude.sh — legacy/autoresearch 운영 잔재. 사람 전용.
# AGENTS.md §1 표 / PHASE3-PLAN §1.2·§9 참조. `.claude/hooks/block_swap_and_seal.py`
# 가 Phase 3 활성 상태에서 본 스크립트의 bash 호출 자체를 거부한다.
#
# 동작: `.claude/` <-> `.claude.alt/` 1:1 교환.
# 한 번 호출하면 swap (Phase 3 본문 활성), 한 번 더 호출하면 원복.
# 결과는 `ls .claude/hooks/` 와 `ls .claude/settings.json` 으로 확인.
#
# 자체 harness 전환 뒤 이 스크립트는 새 운영 경로가 아니다.
# 폐기 또는 archive 여부는 docs/PHASE3-STATUS.md 에서 추적한다.

set -euo pipefail

A=".claude"
B=".claude.alt"
TMP=".claude.swap.tmp"

if [[ ! -d "${A}" ]]; then
    echo "swap_claude: ${A} 누락" >&2
    exit 1
fi
if [[ ! -d "${B}" ]]; then
    echo "swap_claude: ${B} 누락" >&2
    exit 1
fi
if [[ -e "${TMP}" ]]; then
    echo "swap_claude: ${TMP} 가 이미 존재 — 이전 swap 이 중단됐을 가능성. 수동 점검 후 삭제하세요." >&2
    exit 1
fi

mv "${A}" "${TMP}"
mv "${B}" "${A}"
mv "${TMP}" "${B}"

echo "swap_claude: ${A} <-> ${B} 교환 완료"
echo "--- 현재 활성 (ls ${A}) ---"
ls -la "${A}"
echo "--- hooks (있으면 Phase 3 본문 활성) ---"
ls "${A}/hooks/" 2>/dev/null || echo "  (없음 — Phase 1·2 빈 본문)"
