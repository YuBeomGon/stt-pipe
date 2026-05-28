#!/usr/bin/env bash
# Phase 1 verify — no guards yet; just runs judge and lets exit codes flow.
# Phase 3 wraps this with static guards, runtime cap, and quality budget
# (see docs/PHASE3-PLAN.md §1.2).

set -euo pipefail

HYP_ID="${HYP_ID:-manual_$(date +%s)}"
BATCH="${BATCH:-AIG_녹취반출_20250715}"
TRANSCRIBE="${TRANSCRIBE:-workspace.transcribe:transcribe}"

OUT_DIR="runs/${HYP_ID}"
mkdir -p "${OUT_DIR}"

exec python -m judge.evaluate \
    --batch "${BATCH}" \
    --transcribe "${TRANSCRIBE}" \
    --out "${OUT_DIR}/score_report.json"
