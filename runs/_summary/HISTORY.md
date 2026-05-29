# Phase 3 HISTORY

Iteration narratives. harness 만 append. 후보가 직접 만들거나 덮어쓰면 scope 위반.

## iter 1 · phase3_002_iter_001 · cer=0.411350 (ΔNA) · keep

### 관찰
corpus_cer=0.411350, total_inference_time_s=245.4

### 분석
first valid candidate

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 2 · phase3_002_iter_002 · cer=0.337042 (Δ+0.074308) · keep

### 관찰
corpus_cer=0.337042, total_inference_time_s=325.2

### 분석
meaningful improvement: Δcer 0.074308 >= 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 3 · phase3_002_iter_003 · cer=0.394368 (Δ-0.057325) · reject

### 관찰
corpus_cer=0.394368, total_inference_time_s=278.1

### 분석
not enough improvement: Δcer -0.057325 < 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.
