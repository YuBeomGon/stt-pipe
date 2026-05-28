#!/usr/bin/env bash
# Phase 3 진입 직전에 holdout 을 물리적으로 차단한다 (PHASE3-PLAN.md §1.1).
# 종료 후 evaluate_holdout.py --unseal 가 1 회만 권한을 복구하고 다시 봉인한다.

set -euo pipefail

ROOT="${ASR_RAW_DATA_ROOT:-data/raw}"
BATCH="${BATCH:-AIG_녹취반출_20250813}"

WAV_DIR="${ROOT}/wav/${BATCH}"
LABEL_DIR="${ROOT}/label/${BATCH}"

for d in "${WAV_DIR}" "${LABEL_DIR}"; do
    if [[ ! -e "${d}" ]]; then
        echo "seal_holdout: missing ${d}" >&2
        exit 1
    fi
done

chmod -R 000 "${WAV_DIR}" "${LABEL_DIR}"
echo "sealed: ${WAV_DIR}"
echo "sealed: ${LABEL_DIR}"
