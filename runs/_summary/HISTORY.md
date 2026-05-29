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

## iter 14 · phase3_003_iter_014 · cer=0.173571 (Δ-0.005664) · reject

### 관찰
corpus_cer=0.173571, total_inference_time_s=326.1

### 분석
not enough improvement: Δcer -0.005664 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 15 · phase3_003_iter_015 · cer=0.167907 (Δ+0.000000) · reject

### 관찰
corpus_cer=0.167907, total_inference_time_s=331.3

### 분석
not enough improvement: Δcer 0.000000 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 16 · phase3_003_iter_016 · cer=0.168578 (Δ-0.000671) · reject

### 관찰
corpus_cer=0.168578, total_inference_time_s=330.9

### 분석
not enough improvement: Δcer -0.000671 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 17 · phase3_003_iter_017 · cer=0.250335 (Δ-0.082429) · reject

### 관찰
corpus_cer=0.250335, total_inference_time_s=330.3

### 분석
not enough improvement: Δcer -0.082429 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 18 · phase3_003_iter_018 · cer=0.167907 (Δ+0.000000) · reject

### 관찰
corpus_cer=0.167907, total_inference_time_s=332.1

### 분석
not enough improvement: Δcer 0.000000 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 19 · phase3_003_iter_019 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음

### 분석
judge.evaluate 종료 코드 비정상

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 20 · phase3_003_iter_020 · cer=0.173571 (Δ-0.005664) · reject

### 관찰
corpus_cer=0.173571, total_inference_time_s=326.2

### 분석
not enough improvement: Δcer -0.005664 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 21 · phase3_003_iter_021 · cer=0.333269 (Δ-0.165363) · reject

### 관찰
corpus_cer=0.333269, total_inference_time_s=253.2

### 분석
not enough improvement: Δcer -0.165363 < 0.002000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 22 · phase3_003_iter_022 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 23 · phase3_003_iter_023 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 24 · phase3_003_iter_024 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 25 · phase3_003_iter_025 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 26 · phase3_003_iter_026 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 27 · phase3_003_iter_027 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 28 · phase3_003_iter_028 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 29 · phase3_003_iter_029 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 30 · phase3_003_iter_030 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 31 · phase3_003_iter_031 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 32 · phase3_003_iter_032 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 33 · phase3_003_iter_033 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 34 · phase3_003_iter_034 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 35 · phase3_003_iter_035 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 36 · phase3_003_iter_036 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 37 · phase3_003_iter_037 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 38 · phase3_003_iter_038 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 39 · phase3_003_iter_039 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 40 · phase3_003_iter_040 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 41 · phase3_003_iter_041 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 42 · phase3_003_iter_042 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 43 · phase3_003_iter_043 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 44 · phase3_003_iter_044 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 45 · phase3_003_iter_045 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 46 · phase3_003_iter_046 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 47 · phase3_003_iter_047 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 48 · phase3_003_iter_048 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 49 · phase3_003_iter_049 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 50 · phase3_003_iter_050 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 51 · phase3_003_iter_051 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 52 · phase3_003_iter_052 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 53 · phase3_003_iter_053 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 54 · phase3_003_iter_054 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 55 · phase3_003_iter_055 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 56 · phase3_003_iter_056 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 57 · phase3_003_iter_057 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 58 · phase3_003_iter_058 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 59 · phase3_003_iter_059 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 60 · phase3_003_iter_060 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 61 · phase3_003_iter_061 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 62 · phase3_003_iter_062 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 63 · phase3_003_iter_063 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 64 · phase3_003_iter_064 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 65 · phase3_003_iter_065 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 66 · phase3_003_iter_066 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 67 · phase3_003_iter_067 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 68 · phase3_003_iter_068 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 69 · phase3_003_iter_069 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 70 · phase3_003_iter_070 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 71 · phase3_003_iter_071 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 72 · phase3_003_iter_072 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 73 · phase3_003_iter_073 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 74 · phase3_003_iter_074 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 75 · phase3_003_iter_075 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 76 · phase3_003_iter_076 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 77 · phase3_003_iter_077 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 78 · phase3_003_iter_078 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 79 · phase3_003_iter_079 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 80 · phase3_003_iter_080 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 81 · phase3_003_iter_081 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 82 · phase3_003_iter_082 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 83 · phase3_003_iter_083 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 84 · phase3_003_iter_084 · cer=NA (ΔNA) · reject

### 관찰
score_report 없음
candidate command exit=1

### 분석
candidate command 실패

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.
