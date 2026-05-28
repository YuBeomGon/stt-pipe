#!/usr/bin/env bash
# Phase 3 진입 직전에 holdout 을 물리적으로 차단한다 (PHASE3-PLAN.md §1.3).
# 종료 후 evaluate_holdout.py --unseal 가 1 회만 권한을 복구하고 다시 봉인한다.
#
# 동작:
#   1. 이미 부모 디렉토리가 000 이면 idempotent 성공 (재호출 안전)
#   2. 그렇지 않으면 파일 → 서브디렉토리 → 부모 디렉토리 순으로 chmod 000
#      (`chmod -R 000` 은 부모를 먼저 000 으로 만들어 재귀가 깨지므로 사용 X)

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

seal_one() {
    local d="$1"
    local perm
    perm=$(stat -c '%a' "${d}")
    if [[ "${perm}" == "0" ]]; then
        echo "seal_holdout: already sealed (perm=000) — ${d}"
        return 0
    fi
    # 부모가 아직 traversable 할 때 파일·서브디렉토리 먼저 봉인.
    find "${d}" -mindepth 1 -type f -exec chmod 000 {} +
    find "${d}" -mindepth 1 -type d -exec chmod 000 {} +
    chmod 000 "${d}"
    echo "sealed: ${d}"
}

seal_one "${WAV_DIR}"
seal_one "${LABEL_DIR}"
