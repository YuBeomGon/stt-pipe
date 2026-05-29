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

## iter 6 · phase3_003_iter_006 · cer=0.175261 (Δ-0.000009) · reject

### 관찰
corpus_cer=0.175261, total_inference_time_s=242.3

### 분석
not enough improvement: Δcer -0.000009 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 7 · phase3_003_iter_007 · cer=0.167907 (Δ+0.007345) · keep

### 관찰
corpus_cer=0.167907, total_inference_time_s=298.5

### 분석
meaningful improvement: Δcer 0.007345 >= 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 8 · phase3_003_iter_008 · cer=0.173571 (Δ-0.005664) · reject

### 관찰
corpus_cer=0.173571, total_inference_time_s=326.1

### 분석
not enough improvement: Δcer -0.005664 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 9 · phase3_003_iter_009 · cer=0.173274 (Δ-0.005367) · reject

### 관찰
corpus_cer=0.173274, total_inference_time_s=327.9

### 분석
not enough improvement: Δcer -0.005367 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 10 · phase3_003_iter_010 · cer=0.168578 (Δ-0.000671) · reject

### 관찰
corpus_cer=0.168578, total_inference_time_s=331.3

### 분석
not enough improvement: Δcer -0.000671 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 11 · phase3_003_iter_011 · cer=0.259284 (Δ-0.091377) · reject

### 관찰
corpus_cer=0.259284, total_inference_time_s=328.8

### 분석
not enough improvement: Δcer -0.091377 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 12 · phase3_003_iter_012 · cer=0.167907 (Δ+0.000000) · reject

### 관찰
corpus_cer=0.167907, total_inference_time_s=332.1

### 분석
not enough improvement: Δcer 0.000000 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 13 · phase3_003_iter_013 · cer=0.193071 (Δ-0.025164) · reject

### 관찰
corpus_cer=0.193071, total_inference_time_s=269.6

### 분석
not enough improvement: Δcer -0.025164 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.
