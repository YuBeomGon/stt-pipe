#!/usr/bin/env bash
# scripts/swap_verify.sh — legacy/autoresearch 운영 잔재. 사람 전용.
# AGENTS.md §1 표 참조 — judge/ 와 동급 보호 대상.
#
# 동작: scripts/verify.sh <-> scripts/verify.sh.alt 1:1 교환.
# 한 번 호출하면 swap, 한 번 더 호출하면 원복. 결과는 head -3 으로 확인.
#
# 자체 harness 전환 뒤 이 스크립트는 새 운영 경로가 아니다.
# 폐기 또는 archive 여부는 docs/PHASE3-STATUS.md 에서 추적한다.

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
