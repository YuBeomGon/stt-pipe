# Phase 3 HISTORY

Iteration narratives. harness 만 append. 후보가 직접 만들거나 덮어쓰면 scope 위반.

## iter 1 · phase3_003_iter_001 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음

### 분석
judge.evaluate 종료 코드 비정상

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 2 · phase3_003_iter_002 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음

### 분석
format reject: yaml parse failed: mapping values are not allowed here
  in "<unicode string>", line 2, column 322:
     ...  per-item prompt list. (Negative: frozen/asr_backend.py is block ... 
                                         ^

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 3 · phase3_003_iter_003 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음

### 분석
judge.evaluate 종료 코드 비정상

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 4 · phase3_003_iter_004 · cer=0.175252 (ΔNA) · keep

### 관찰
corpus_cer=0.175252, total_inference_time_s=268.7

### 분석
first valid candidate

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 5 · phase3_003_iter_005 · cer=0.255546 (Δ-0.080294) · reject

### 관찰
corpus_cer=0.255546, total_inference_time_s=260.6

### 분석
not enough improvement: Δcer -0.080294 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.
