"""
harness/config.py
Operator-tunable knobs for the Phase 3 candidate-evolution loop — single SSOT.

운영자가 실제로 조정하는 값들의 단일 출처. guards / policy / runner / verify 는
여기서 import 해 dataclass 기본값·module 상수의 source 로만 쓴다. 값 변경은 이
파일 한 곳에서.

여기 두지 않는 것: 가드 내부 임계값(`guards.QB_*`, `EMPTY_OUTPUT_RATE_MAX`,
`ARITHMETIC_TOL`, length-ratio 경계)은 산술/스키마 게이트 로직과 강결합이라
`guards.py` 에 로직과 함께 남긴다.
"""

from __future__ import annotations

# ── Runtime hard cap ─────────────────────────────────────────────────
# cap = baseline_total_inference_time_s * RUNTIME_HARD_MULTIPLIER 초과 시 reject.
# baseline ~152.3s → cap ~1066s.
RUNTIME_HARD_MULTIPLIER: float = 7.0

# verify subprocess(judge.evaluate) wall-clock 타임아웃 = cap + 이 여유(초).
# cap 은 사후 측정 게이트라 candidate 가 디코딩에서 무한정 매달리면 report 가
# 안 나와 cap 체크에 도달조차 못 한다(2-3h hang). 여유는 모델 로드 + 직렬화
# 오버헤드 몫. 타임아웃 초과 candidate 는 runtime cap reject 로 처리.
VERIFY_TIMEOUT_LOAD_MARGIN_S: float = 180.0

# ── Keep / banking ───────────────────────────────────────────────────
# σ가 잠정(deterministic eval, σ~0)일 때 best 대비 개선 폭이 이 값 이상이면
# bank (review F2). genuine sub-0.01 개선도 누적되도록 0.01→0.002.
BANKING_ABSOLUTE_DELTA: float = 0.002

# ── Explore / exploit schedule ───────────────────────────────────────
# explore 비율이 iter 증가에 따라 START→FLOOR 로 지수 감쇠. 후반에도 FLOOR 보장.
EXPLORE_RATIO_START: float = 0.9    # ~90% explore at the start
# 0.5 는 phase3_005 후반에 악화 후보만 늘리고 plateau 를 못 깼다(005 회고 #3) →
# 0.3 으로 회귀. **주의(리뷰 #7)**: 이것은 visibility 가 아니라 candidate prompt mode
# 비율을 바꾸는 behavior change(Step 1b policy knob)다. 다음 run 분석에서는
# "portfolio/logging 추가 효과"와 분리된 **별도 변수**로 해석한다. (revert 하지 않는
# 이유: 0.5 는 005 가 이미 harmful 로 판정했고, portfolio loop 자체가 큰 behavior
# change라 어차피 clean A/B 가 아니다.)
EXPLORE_RATIO_FLOOR: float = 0.3    # guaranteed ≥30% explore even late
EXPLORE_RATIO_DECAY: float = 18.0   # iters; ~halves gap above floor every 12-13 iters

# ── Synthesis (promising rejects) ────────────────────────────────────
# exploit iter 에서 주입할, 한 축(sub/coverage)을 개선했으나 종합 reject 된
# 과거 후보 diff 의 선택 기준.
PROMISING_REJECT_COUNT: int = 3        # 주입할 reject 후보 수
PROMISING_DIFF_MAX_CHARS: int = 4000   # 후보당 diff 주입 char 상한
AXIS_IMPROVE_EPSILON: float = 0.02     # "축 개선" 으로 인정할 최소 폭
PROMISING_CER_MAX_FACTOR: float = 1.25 # best_cer 대비 이 배수 넘는 reject 는 제외

# ── Abort guards ─────────────────────────────────────────────────────
# 초반 format-reject 연속 / command-fail 연속 시 루프 조기 중단.
FORMAT_REJECT_PROBE_ITERS: int = 5     # 이 iter 까지만 format-reject abort 감시
FORMAT_REJECT_ABORT_COUNT: int = 4     # 연속 format-reject N 회 → abort
COMMAND_FAIL_ABORT_COUNT: int = 3      # 연속 command-fail N 회 → abort
