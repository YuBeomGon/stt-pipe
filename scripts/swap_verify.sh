#!/usr/bin/env bash
# scripts/swap_verify.sh — 사람 전용. 에이전트(Claude / autoresearch / 기타) 호출 금지.
# AGENTS.md §1 표 참조 — judge/ 와 동급 보호 대상.
#
# 동작: scripts/verify.sh <-> scripts/verify.sh.alt 1:1 교환.
# 한 번 호출하면 swap, 한 번 더 호출하면 원복. 결과는 head -3 으로 확인.
#
# Phase 3 진입 시퀀스 (PHASE3-PLAN.md §1):
#   1. bash scripts/swap_verify.sh        # Phase 1 본문 <-> Phase 3 본문 swap
#   2. head -3 scripts/verify.sh          # 현재 활성 본문 사람 확인
#   3. (smoke: 의도적 위반 1회 → exit 1 확인 → 원복)
#   4. bash scripts/seal_holdout.sh       # holdout chmod 000
#   5. /autoresearch 호출

set -euo pipefail

A="scripts/verify.sh"
B="scripts/verify.sh.alt"
TMP="scripts/verify.sh.swap.tmp"

if [[ ! -f "${A}" ]]; then
    echo "swap_verify: ${A} 누락" >&2
    exit 1
fi
if [[ ! -f "${B}" ]]; then
    echo "swap_verify: ${B} 누락" >&2
    exit 1
fi
if [[ -e "${TMP}" ]]; then
    echo "swap_verify: ${TMP} 가 이미 존재 — 이전 swap 이 중단됐을 가능성. 수동 점검 후 삭제하세요." >&2
    exit 1
fi

mv "${A}" "${TMP}"
mv "${B}" "${A}"
mv "${TMP}" "${B}"

chmod +x "${A}" 2>/dev/null || true
chmod +x "${B}" 2>/dev/null || true

echo "swap_verify: ${A} <-> ${B} 교환 완료"
echo "--- 현재 활성 (head -3 ${A}) ---"
head -3 "${A}"
