# Autoresearch run — autoresearch-260529-0632

Target: corpus_cer ≤ 0.10 (baseline 0.43197, num_files=11).
Source-of-truth = commit body (`PHASE3-PLAN.md §4.4`). 이 파일은 시간순
derived view + 메트릭 요약.

**Backfill 주의**: iter 1-7 은 §4.4 규약 도입 *전*에 만들어진 commit (subject
한 줄만) — 아래 단락들은 채팅 reasoning 을 직접 옮긴 retrospective narrative.
iter 8 부터는 commit body 자체에 3 단락 + `append_history.sh` 자동 합성.

---

## iter 1 · 19fd801 · cer=NA (ΔNA) · crashed-OOM

### 관찰
30s chunking 도입 + batch decode (모든 chunk 한 번에 `generate` call). 첫
파일 (audio_s=1629s → 54 chunks) 에서 `RuntimeError: CUDA failed with error
out of memory`. 11 페어 중 0 개 score.

### 분석
batch 차원 OOM — Whisper-large-v3-turbo float16 + batch=54 chunk × 80×3000
mel features 가 4080 SUPER (16GB) 메모리 초과. 30s single chunk decode 자체는
가능 (dry-run 에서 확인). batch 사이즈만 줄이면 됨.

### 다음 후보
1. **per-chunk 순차 decode** (batch=1, n_chunks 회 generate) — OOM 회피, 단순.
2. 동적 mini-batch (예: 4 chunk 씩) — 복잡, 추후.
→ (1) 채택.

---

## iter 2 · 6ca5108 · cer=0.4104 (Δ-0.0216) · kept

### 관찰
per-chunk 순차 decode. 11/11 페어 완주. corpus_cer **0.4104** (baseline
0.4320 대비 Δ-0.0216, ~5% rel↓). runtime 178s (budget 457s 충분 여유).
hallucination 0 건, repeated_text 0 (baseline 0.091).

### 분석
chunking 만으로 첫 의미 진전. 30s 윈도우 truncation 이 dry-run 0.99 의 근본
원인이었음 확정. 다만 chunk 경계 정보 손실로 del 비중 여전 큼 — 다음 lever
들이 del 축소 방향으로 가야 함.

### 다음 후보
1. **beam_size 1 → 5** — 디코딩 폭 확대, 단순 변경, 안전.
2. length_penalty 튜닝 — longer bias 로 del 직접 공략.
3. chunk overlap — 가장 발본적이지만 중복 처리 복잡.
→ (1) 안전 순서 우선.

---

## iter 3 · f280134 · cer=0.4059 (Δ-0.0046) · kept

### 관찰
beam_size 1 → 5. corpus_cer **0.4059** (Δ-0.0046). runtime 231s. 모든 가드
정상, hallucination 0.

### 분석
beam 폭 확대 효과 modest. del 비중이 본질적으로 chunk 경계 / 토큰-수준
score 가 아닌 모델의 conservative emission tendency 가 원인일 가능성 시사.
decode-only 노브로는 큰 진전 제한적.

### 다음 후보
1. **length_penalty 1.0 → 1.5** — longer bias 로 del 직접 공략 (decode 노브).
2. condition_on_prev — 큰 효과 가능, hallucination 전파 위험.
3. chunk overlap — 가장 발본적이지만 구현 복잡.
→ (1) 단순 + 직접적, decode 노브 우선 sweep.

---

## iter 4 · 367ff32 · cer=0.3929 (Δ-0.0129) · kept

### 관찰
length_penalty 1.0 → 1.5. corpus_cer **0.3929** (Δ-0.0129, iter 3 의 ~3 배
효과). runtime 230s. 가드 정상.

### 분석
del 비중 감소 가설 들어맞음. length_penalty 가 현 시점 del 의 주된 lever
임 확인. 1.5 가 sweep 의 최적인지 확인 필요 — 2.0 시도해서 saturate 점 탐색.

### 다음 후보
1. **length_penalty 1.5 → 2.0** — sweep 한 단계 더.
2. beam_size 5 → 8 — 결합 효과 시도.
3. chunk overlap — 보류 (sweep 후).
→ (1) 동일 lever sweep 마무리.

---

## iter 5 · a77658e · cer=0.3900 (Δ-0.0030) · kept

### 관찰
length_penalty 1.5 → 2.0. corpus_cer **0.3900** (Δ-0.0030, diminishing).
runtime 219s. 누적 baseline 0.4320 → 0.3900 (Δ-0.0420, 9.7% rel↓).

### 분석
length_penalty 효과 saturate 시작. 추가 sweep (2.5+) 은 ROI 낮음 — 다른
lever 필요. **이 시점이 첫 plateau** — 새로운 카테고리 (prompt / 후처리 /
chunking 구조) 시도해야.

### 다음 후보
1. **condition_on_prev** (`<|startofprev|>` + 직전 출력 토큰 100 prefix) —
   대화 context 유지로 del 추가 감소 기대. hallucination 전파 위험.
2. repetition_penalty 1.1 — 반복 약억제, 안전.
3. chunk overlap 5s — 발본, 중복 처리 필요.
→ (1) 큰 진전 잠재 + risk receivable, prompt 카테고리 첫 시도.

---

## iter 6 · 00fba60 · cer=0.3985 (Δ+0.0085) · reverted

### 관찰
condition_on_prev 도입 (직전 chunk 출력 토큰 100 prefix + `<|startofprev|>`).
corpus_cer **0.3985** (regression +0.0085). `repeated_text_rate 0.455 >
baseline 0.091 + 0.2` WARN 발생.

### 분석
context 유지의 부작용 — Whisper 가 직전 토큰을 strong prior 로 받아 반복
패턴 증폭. 대화체 (전화 상담) 의 자연스러운 반복 ("네", "고맙습니다") 가
hallucination-수준 repetition 으로 변질. **단순 prefix 는 부적합** —
detection-and-reset 또는 condition + repetition_penalty 결합 필요.

### 다음 후보
1. **repetition_penalty 1.1 단독** (iter 5 위에서) — 반복 영향 단독 측정.
2. condition_on_prev + repetition_penalty 결합 — 부작용 상쇄 시도.
3. chunk overlap — 미시도.
→ (1) 단일 변수 측정 우선.

---

## iter 7 · 0ed3bda · cer=0.4129 (Δ+0.0229) · reverted

### 관찰
repetition_penalty 1.1 (iter 5 위에 적용). corpus_cer **0.4129** (큰
regression +0.0229 from 0.3900). 가드 추가 WARN 없음, hallucination 여전 0.

### 분석
repetition_penalty 가 정당한 반복 (한국어 어미, 대화 마커, 보험 약관 정형구)
까지 억제 → 정확 토큰 score 가 강제로 낮아져 잘못된 alternative 선택.
hallucination 0 상태에선 repetition_penalty 가 **net harm**. iter 6 의
condition_on_prev 가 만든 반복은 token-prior 폭주 였으므로 둘은 별개 문제 —
repetition_penalty 가 condition_on_prev 의 해결책이 아니었음.

**Plateau 확인**: iter 4-5-6-7 동안 길이/디코드 노브 sweep 모두 0.39 근처
또는 위로. local minima 신호. 카테고리 전환 필요.

### 다음 후보
1. **chunk overlap 5s** + 단순 join — 가장 큰 미시도 lever, 경계 정보
   가장 발본적 해결. length_ratio 약간 상승 예상.
2. patience > 1.0 (beam search 깊이) — decode 노브 미시도지만 효과 작을 듯.
3. no_repeat_ngram_size 6 — hallucination 발생 시점만 효과.
→ (1) 카테고리 전환 (chunking 구조), 가장 큰 잠재.

---

## iter 1 · phase3_001_iter_001 · cer=0.411350 (ΔNA) · keep

### 관찰
corpus_cer=0.411350, total_inference_time_s=110.1

### 분석
first valid candidate

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 2 · phase3_001_iter_002 · cer=0.403517 (Δ+0.007833) · reject

### 관찰
corpus_cer=0.403517, total_inference_time_s=124.6

### 분석
not enough improvement: Δcer 0.007833 < 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 3 · phase3_001_iter_003 · cer=0.390987 (Δ+0.020363) · keep

### 관찰
corpus_cer=0.390987, total_inference_time_s=124.8

### 분석
meaningful improvement: Δcer 0.020363 >= 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 4 · phase3_001_iter_004 · cer=0.363766 (Δ+0.027221) · keep

### 관찰
corpus_cer=0.363766, total_inference_time_s=137.2

### 분석
meaningful improvement: Δcer 0.027221 >= 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 5 · phase3_001_iter_005 · cer=0.339778 (Δ+0.023988) · keep

### 관찰
corpus_cer=0.339778, total_inference_time_s=149.7

### 분석
meaningful improvement: Δcer 0.023988 >= 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 6 · phase3_001_iter_006 · cer=0.339003 (Δ+0.000775) · reject

### 관찰
corpus_cer=0.339003, total_inference_time_s=149.5

### 분석
not enough improvement: Δcer 0.000775 < 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 7 · phase3_001_iter_007 · cer=0.290103 (Δ+0.049675) · keep

### 관찰
corpus_cer=0.290103, total_inference_time_s=156.0

### 분석
meaningful improvement: Δcer 0.049675 >= 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 8 · phase3_001_iter_008 · cer=0.288875 (Δ+0.001229) · reject

### 관찰
corpus_cer=0.288875, total_inference_time_s=155.9

### 분석
not enough improvement: Δcer 0.001229 < 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 9 · phase3_001_iter_009 · cer=0.254500 (Δ+0.035603) · keep

### 관찰
corpus_cer=0.254500, total_inference_time_s=168.9

### 분석
meaningful improvement: Δcer 0.035603 >= 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 10 · phase3_001_iter_010 · cer=0.255311 (Δ-0.000810) · reject

### 관찰
corpus_cer=0.255311, total_inference_time_s=168.9

### 분석
not enough improvement: Δcer -0.000810 < 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 11 · phase3_001_iter_011 · cer=0.266420 (Δ-0.011920) · reject

### 관찰
corpus_cer=0.266420, total_inference_time_s=168.1

### 분석
not enough improvement: Δcer -0.011920 < 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.

## iter 12 · phase3_001_iter_012 · cer=0.266420 (Δ-0.011920) · reject

### 관찰
corpus_cer=0.266420, total_inference_time_s=167.9

### 분석
not enough improvement: Δcer -0.011920 < 0.010000

### 다음 후보
직전 결과와 diagnosis를 보고 다음 단일 변경을 선택한다.
