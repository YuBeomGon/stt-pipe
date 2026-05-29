#!/usr/bin/env bash
# Phase 3 verify entrypoint — 사람이 직접 실행 가능. 가드 정본은 PHASE3-PLAN §5.
#
# 자체 harness 전환 후 본 파일이 정본 verify 경로다. 과거 `verify.sh.alt` /
# `swap_verify.sh` 는 PHASE3-STATUS §3 에서 폐기 검토 중이며 현재 호출되지 않는다.
#
# 가드:
#   1. 정적 backend 직접 import 차단 (ctranslate2 / transformers / from_pretrained / Whisper()
#   2. 정적 profile/asset 직접 참조 차단 (assets / audio_profile / silero)
#   3. judge.evaluate 실행 (실행 무효 = exit 1)
#   4. harness.guards: 산술 무결성 / catastrophic / runtime cap / quality budget
#   5. 마지막 줄에 corpus_cer 한 숫자 (harness reader 또는 사람이 읽음)

set -euo pipefail

HYP_ID="${HYP_ID:-manual_$(date +%s)}"
BATCH="${BATCH:-AIG_녹취반출_20250715}"
TRANSCRIBE="${TRANSCRIBE:-workspace.transcribe:transcribe}"
WORKSPACE_FILE="${WORKSPACE_FILE:-workspace/transcribe.py}"
BASELINE_FILE="${BASELINE_FILE:-baseline/target_cer.json}"
RUNTIME_HARD_MULTIPLIER="${RUNTIME_HARD_MULTIPLIER:-3.0}"

OUT_DIR="runs/${HYP_ID}"
mkdir -p "${OUT_DIR}"

# --- 1. 정적 backend 보호 ----------------------------------------------------
# ctranslate2 / transformers 직접 import 또는 from_pretrained / Whisper(  사용 금지.
if [[ ! -f "${WORKSPACE_FILE}" ]]; then
    echo "verify: workspace 파일 누락 — ${WORKSPACE_FILE}" >&2
    exit 1
fi

if grep -E -q '(^|[[:space:]])import[[:space:]]+ctranslate2([[:space:]]|$)|(^|[[:space:]])import[[:space:]]+transformers([[:space:]]|$)|from_pretrained|Whisper\(' \
    "${WORKSPACE_FILE}"; then
    echo "verify FAIL [static backend]: ${WORKSPACE_FILE} 에 ctranslate2/transformers/from_pretrained/Whisper( 패턴 검출 — STT-PIPELINE-SPEC §2/§7/§11 위반" >&2
    exit 1
fi

# --- 2. 정적 profile/asset 직접참조 차단 -------------------------------------
# workspace 는 assets/audio_profile/silero 본문을 직접 읽으면 안 됨 (PHASE3-PLAN §3).
if grep -E -i -q '(assets|audio_profile|silero)' "${WORKSPACE_FILE}"; then
    echo "verify FAIL [static profile]: ${WORKSPACE_FILE} 에 assets/audio_profile/silero 직접 참조 검출 — diagnosis 만 노출 정책" >&2
    exit 1
fi

# --- 3. 실행 -----------------------------------------------------------------
if ! python -m judge.evaluate \
        --batch "${BATCH}" \
        --transcribe "${TRANSCRIBE}" \
        --out "${OUT_DIR}/score_report.json"; then
    echo "verify FAIL [run]: judge.evaluate 종료 코드 비정상" >&2
    exit 1
fi

if [[ ! -s "${OUT_DIR}/score_report.json" ]]; then
    echo "verify FAIL [run]: score_report.json 누락 또는 빈 파일" >&2
    exit 1
fi

# --- 4. 수치 가드 ------------------------------------------------------------
python -m harness.guards \
    --report "${OUT_DIR}/score_report.json" \
    --per-file "${OUT_DIR}/per_file.jsonl" \
    --baseline "${BASELINE_FILE}" \
    --runtime-hard-multiplier "${RUNTIME_HARD_MULTIPLIER}"

# --- 5. corpus_cer 한 숫자 (harness reader 또는 사람) ------------------------
python -c "import json,sys; print(json.load(open('${OUT_DIR}/score_report.json'))['corpus_cer'])"
