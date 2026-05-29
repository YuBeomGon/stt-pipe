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

## iter 8 · da568e7 · cer=0.3575 (Δ-0.0325) · kept

## 관찰
직전 iter 4-7 sweep — decode/prompt 노브 (beam, length_penalty,
condition_on_prev, repetition_penalty) 모두 plateau 0.39 근처. 카테고리
전환 필요 신호.

## 분석
chunking 구조 자체가 남은 큰 lever. 30s chunk 의 경계에서 부분 단어/문맥
손실이 del 비중을 유지시킨다는 가설. overlap 5s 로 양 chunk 가 동일 5s
구간 디코딩 → 경계 단어 양쪽에서 캡처. 단순 join 전제 시 텍스트 중복 →
length_ratio.mean 약 1.17 배 (이전 0.59 → ~0.69), p95 가드 5.0 대비 충분
여유 예상. cer 개선 + 가드 OK 면 KEEP, length_ratio.p95 > 5.0 또는 cer
악화시 REVERT.

## 다음 후보
1. 결과 따라 overlap 비율 sweep (3s / 7s / 10s) — 1 차원 sweep.
2. overlap + 단순 dedup (이전 chunk 끝 N 토큰과 다음 chunk 시작 N 토큰
   일치 시 다음 시작 제거) — 복잡, 단 length_ratio 정상화.
3. patience 1.0 → 1.5 (beam search 더 깊이).


## iter 9 · 0b2549f · cer=0.3946 (Δ+0.0371) · reverted

## 관찰
iter 8 overlap 5s 가 plateau 돌파 (0.3900 → 0.3575, Δ-0.0325). 경계 정보
보완이 효과적임 확정. overlap 더 키워도 효과 단조 증가인지 확인.

## 분석
overlap 7s = step 23s. 중복 비율 7/30 = 23%. length_ratio 약 1.30 (이전
0.59 × 1.30 ≈ 0.77), p95 가드 5.0 여유. 효과 함수가 단조 증가면 cer 추가
개선, 감소 전환점이면 plateau 또는 regression.

## 다음 후보
1. 효과 단조 → overlap 10s 추가 sweep.
2. plateau/regression → overlap 6s 또는 5s 회귀 후 chunking 외 lever.
3. 이후 patience 1.0 → 1.5, no_repeat_ngram_size 6 등 미시도 lever.


## iter 10 · 0138fa5 · cer=0.3318 (Δ-0.0257) · kept-2warn

## 관찰
overlap sweep (5 best, 7 regression) 끝. chunking 차원 sweet spot 확인.
남은 decode 노브 중 미시도: patience (length normalization 의 alpha).
length_penalty 2.0 과 결합되는지 확인.

## 분석
patience > 1.0 = length normalization 강화 → longer output 추가 favored.
length_penalty 가 score 의 보정, patience 가 빔 결정 시점의 length-aware
확장. 효과 중복 가능 (둘 다 longer 방향) → saturate 또는 미미한 진전 예상.
드물게 빔의 다양성을 살려 정확도 ↑ 가능성.

## 다음 후보
1. 진전 → patience 2.0 sweep.
2. saturate → no_repeat_ngram_size 6 (hallucination 시점 보호망).
3. regression → revert + num_hypotheses 변동.


## iter 11 · 973df2f · cer=0.3280 (Δ-0.0038) · kept-2warn

## 관찰
iter 10 patience 1.5 가 cer 0.3318 큰 진전 (Δ-0.0257) — 단 2 WARN
(repeated_text 0.364, length_ratio.mean 0.914). 단조 효과 단계 확인.

## 분석
patience 2.0 = length normalization alpha 더 강 → 더 긴 출력 favored.
length_ratio.mean 추가 상승 예상 (0.914 → 1.0+ 가능) → length_ratio
WARN 심화 또는 catastrophic (p95 > 5.0) 위험. repeated_text 도 같이 증가
가능. cer 추가 진전과 가드 임계 trade-off.

## 다음 후보
1. cer 진전 + WARN 가중 → patience 2.5 또는 length_penalty 1.5 로 균형.
2. cer plateau → patience 회귀 + 새 lever (no_repeat_ngram_size 6).
3. catastrophic → revert.


## iter 12 · 13a5bc4 · cer=0.3241 (Δ-0.0039) · kept-1warn

## 관찰
iter 10-11 patience sweep 결과 repeated_text_rate 0.364 WARN 유지. iter 7
의 repetition_penalty 1.1 은 정당한 반복까지 억제하여 net harm 였지만
no_repeat_ngram_size 는 동일 6-gram 만 차단 — 정상 어미/마커 반복은 보호.

## 분석
한국어 자연 발화에서 6-gram 동일 반복은 거의 0 (보험 약관 정형구 일부 외).
hallucination 반복 패턴 ("기상캐스터 배혜지", "한글자막 by ...") 은 동일
ngram 반복이므로 직접 차단 효과 기대. cer 진전 가능 + WARN 완화 동시.
risk: 정형구 (예: "동의하시는 거 맞으신 거죠 어머니") 가 6-gram 일치하면
차단되어 del 증가 가능.

## 다음 후보
1. WARN 완화 + cer 진전 → ngram_size 4 또는 5 추가 sweep.
2. WARN 완화 + cer 정체 → length_ratio 축에서 length_penalty 1.5 회귀.
3. cer 악화 → revert + 다른 lever.


## iter 13 · 7c094e4 · cer=0.3854 (Δ+0.0613) · reverted

## 관찰
누적 25% rel↓ (cer 0.3241). 디코드 노브 (beam, length_penalty, patience)
sweep 끝. chunking 차원 sweep 중 — overlap 5s sweet spot 확인. 다음 chunk
차원: chunk_samples 자체. 30s 가 Whisper receptive field max.

## 분석
25s = Whisper 30s receptive field 안에서 5s padding. 정확도 미세 하락
가능 vs 경계 capture 빈도 ↑ (step 25→20s). 긴 파일 (2596s = 130 chunks)
은 verify time 더 길어짐. trade-off 측정.

## 다음 후보
1. cer 진전 → 20s 추가 sweep down.
2. cer 정체/회귀 → 30s 회귀 + 35s 시도 (padding extension).
3. 둘 다 정체 → VAD/silence 기반 chunk 도입.


## iter 14 · 0fad958 · cer=0.3251 (Δ+0.0010) · reverted

## 관찰
iter 12 ngram=6 이 cer + WARN 동시 진전. 더 강한 반복 차단으로 추가
진전 시도. iter 13 chunk_samples 25s 는 cer net harm 으로 revert — 25s
는 context loss 가 경계 capture 빈도보다 큰 영향.

## 분석
ngram=5 = 5-gram 동일 반복 차단. 한국어 5-gram 반복 자연도 6보다 다소
있음 (특히 약관 정형구) — 정상 발화에 약간 영향 가능. hallucination
6-gram 패턴 ("기상캐스터 배혜지") 은 여전 차단. cer 추가 진전 + 정형구
del 증가 trade-off.

## 다음 후보
1. cer 진전 → ngram=4 추가 sweep down (위험).
2. cer 정체 → ngram=6 회귀 + 다른 lever (chunk_samples 35s 또는 VAD).
3. cer 회귀 → revert + 다른 방향.


## iter 15 · 493c76f · cer=0.2514 (Δ-0.0727) · kept-1warn

## 관찰
iter 6 의 condition_on_prev 단독 적용이 repeated_text 0.455 WARN 으로
revert. 이후 iter 12 의 ngram=6 이 반복 차단 효과 검증. 둘 결합 시 iter 6
의 부작용이 차단될 가능성 — 대화 context 유지 효과만 살릴 수 있는지 검증.

## 분석
prev 100 토큰 prefix 가 Whisper 에 대화 흐름 hint 제공 → 인사말/마무리/
보험 약관 등 chunk 경계 직전 context 유지로 del 추가 감소 가능. ngram=6
이 prev prefix 발생하는 token-prior 폭주 패턴 차단. iter 6 의 단점 (반복
폭주) 이 해결되면서 장점 (context 유지) 만 활용. 단 prev 토큰이 잘못된
hallucination 이면 다음 chunk 로 전파 (단 hallucination_hit_rate 현재 0).

## 다음 후보
1. cer 진전 → prev_max_tokens 100 → 200 sweep up.
2. cer 정체 → prev_max_tokens 100 → 50 sweep down.
3. WARN 심화 + cer 회귀 → revert + VAD/silence chunk 방향.


## iter 16 · ed99661 · cer=0.2436 (Δ-0.0078) · kept-1warn

## 관찰
iter 15 condition_on_prev + ngram=6 이 cer 0.2514 (Δ-0.0727, 단일 최대
진전). 더 긴 context prefix 가 추가 진전 줄지 sweep up.

## 분석
prev 150 토큰 = 약 30s 의 직전 transcription text. Whisper decoder max
context 448 토큰 — prompt 150+4 + generated typ 200 = 354 < 448 안전.
대화의 더 긴 context 유지로 long-range coherence 강화 가능. risk: 잘못된
prev token (특히 chunk 첫 reset 시점) 전파 영향 더 큼.

## 다음 후보
1. 진전 → 200 추가 sweep up.
2. saturate → prev 100 회귀 + VAD/silence 기반 chunk 시도.
3. regression → 50 sweep down + reset 주기 도입 검토.


## iter 17 · 2f29f9d · cer=0.2428 (Δ-0.0009) · kept-1warn

## 관찰
iter 16 prev=150 단조 진전 (Δ-0.0078). 더 긴 context 효과 있음. 200 으로
한 단계 더.

## 분석
prev 200 + sot 4 = 204 prompt. Whisper decoder context 448 의 절반 차지.
generated ~200 = 404 < 448 여전 안전. 더 긴 context 가 cer 추가 진전
줄지 saturate 시작인지 확인.

## 다음 후보
1. 진전 → 250 sweep up 또는 saturate 검증.
2. saturate → VAD chunk / prompt token (initial_prompt) 카테고리.
3. regression → 150 회귀 + chunk reset 도입 (예: 5 chunk 마다 reset).


## iter 18 · b6f44e1 · cer=0.2772 (Δ+0.0344) · reverted

## 관찰
iter 8 overlap=5 plateau 돌파, iter 9 overlap=7 regression. 5-7 사이
fine sweep 미수행. condition_on_prev + ngram=6 도입 후 overlap 의
효과 함수가 변했을 가능성 — 재 sweep.

## 분석
overlap 6 = step 24s. 중복 비율 20%. condition_on_prev 가 경계의 context
이미 유지하므로 overlap 의 marginal 가치 감소 가능 — 단 5/6 의 차이는
작아 효과 가늠 어려움. cer 변화 ±0.005 안이면 noise 가능, 그 이상이면
real signal.

## 다음 후보
1. 5 best 재확인 → overlap=5 회귀 + initial_prompt 시도.
2. 6 진전 → 7 재시도 또는 stop.
3. noise 수준 변화 → 5 회귀 + 다른 차원 (VAD).


## iter 19 · cb16708 · cer=0.2412 (Δ-0.0016) · kept-1warn

## 관찰
prev sweep saturate. 첫 chunk 는 prev 없어 도메인 hint 없는 cold start.
보험 콜센터 vocab 을 first-chunk prev 자리에 넣어 cold start 정확도 ↑
시도. 두 번째 chunk 부터는 prev tokens 자연 흐름.

## 분석
"보험 약관 청구 가입 보장 동의 고객 어머니 선생님 계약" 약 10-15 토큰
hint. Whisper 가 도메인 단어를 첫 chunk 부터 인지 → 흔한 mishear (예:
"동의" → "동이", "약관" → "약환") 감소 가능. 단 hint 가 너무 specific 면
처음에 hint 단어 over-generation 위험. 짧게 + 자주 등장 단어만 선택.

## 다음 후보
1. cer 진전 → hint 확장 (더 많은 도메인 단어).
2. saturate → VAD/silence chunk 경계 (chunking 차원 새 lever).
3. regression → revert + 빈 첫 chunk prompt.


## iter 20 · 091c6a4 · cer=0.2205 (Δ-0.0207) · kept-1warn

## 관찰
condition_on_prev + initial_prompt + ngram=6 stack 위에서 overlap fine
sweep. iter 18 의 6s 가 regression 으로 5s sweet spot 재확정 — 4s 도
검증 가치.

## 분석
overlap 4s = step 26s. 중복 비율 13%. condition_on_prev 가 경계 context
유지하므로 overlap 의 필요성 ↓ 가능 — 4s 가 sufficient 일 수도. 효과
없으면 5s 로 되돌리고 다른 차원.

## 다음 후보
1. cer 진전 → 3s 추가 sweep down.
2. saturate → 5s 회귀 + temperature_fallback 도입.
3. regression → 5s 회귀 + VAD chunk 시도.


## iter 21 · 54a9be5 · cer=0.2051 (Δ-0.0154) · kept-1warn

## 관찰
iter 20 overlap=4 가 cer 0.2205 (Δ-0.0207, 큰 진전). condition_on_prev
도입 후 sweet spot 이 5→4 로 이동했음 확인. 3s 추가 sweep 단조성 확인.

## 분석
overlap 3s = step 27s. 중복 비율 10%. condition_on_prev 가 context 거의
완전 제공하므로 overlap 의 main 역할 (경계 정보) 이 minimal 로도 충분
가능. 단 너무 작으면 (예: 0-1s) 경계 단어 절단 위험.

## 다음 후보
1. 진전 → 2s 추가 sweep (limit 가까이).
2. saturate → 4s 회귀 + temperature_fallback / VAD.
3. regression → 4s 회귀 + initial_prompt 확장.


## iter 22 · dc7cf5b · cer=0.1922 (Δ-0.0128) · kept-1warn

## 관찰
iter 20-21 overlap 단조 감소 효과 (5→4→3 모두 cer 진전). condition_on_prev
가 context 거의 완전 제공 가설 확인. 2s 추가 sweep — limit 근접.

## 분석
overlap 2s = step 28s. 중복 비율 7%. 거의 no-overlap 에 가까움 — 경계
2s 만 양쪽 capture. condition_on_prev 효과로 더 작은 overlap 도 충분
가설. 0s 한계까지 진전 가능성. 단 첫 chunk 와 두번째 chunk 사이 prev
context 가 형성되기 전 (first 2-chunk) 의 경계는 condition_on_prev 효과
없어 risk 있을 수 있음.

## 다음 후보
1. 진전 → 1s 추가 sweep (limit).
2. saturate → 3s 회귀 + temperature_fallback / VAD.
3. regression → 3s 회귀 + initial_prompt 확장.


## iter 23 · f04afa3 · cer=0.2024 (Δ+0.0102) · reverted

## 관찰
overlap 단조 감소가 단조 cer 진전 (5→4→3→2 all kept). 1s 가 거의 zero-
overlap. 단조 검증 끝점.

## 분석
overlap 1s = step 29s. 중복 3%. 거의 disjoint chunking. condition_on_prev
가 전부 대체 가설의 한계 검증. 1s 도 더 좋으면 0s 시도 가치, 회귀면 2s
sweet spot 확정 + 다른 lever.

## 다음 후보
1. 진전 → overlap 0 시도.
2. saturate → 2s 회귀 + temperature_fallback / VAD chunk.
3. regression → 2s 확정 + initial_prompt 확장 또는 chunk_samples 35s 시도.


## iter 24 · edbb4b9 · cer=0.1930 (Δ-0.0008) · kept-1warn

## 관찰
iter 19 첫 도메인 hint 10개 단어. 효과 미미 (Δ-0.0016). 확장으로 추가
진전 가능성 — 한국어 보험 콜센터 자주 등장 단어 ~40개. first chunk 만에
한정 효과지만 long-form 의 lead-in 정확도 ↑ 가능.

## 분석
prev 자리 ~50-70 토큰 (한국어 byte-level). first chunk decode 시 hint
다음에 transcription 생성. Whisper 가 hint 단어를 본문에 더 잘 매칭.
risk: hint 자체가 본문에 over-emit (prev 의 token-prior 영향).

## 다음 후보
1. cer 진전 → 도메인 단어 더 (콜센터 정형구 추가).
2. saturate → 다른 차원 — VAD / temperature_fallback / num_hypotheses.
3. regression → hint 짧게 또는 제거.


## iter 25 · 84b3a07 · cer=0.1662 (Δ-0.0260) · kept-1warn

## 관찰
overlap+condition_on_prev+ngram+prompt stack 으로 cer 0.1922 (best, iter
22). uniform 30s chunking 의 boundary 가 발화 중간 절단 가능. silence 기반
chunk 경계 = 자연 발화 boundary 와 align → 단어/문장 절단 0.

## 분석
librosa.effects.split top_db=30 (default 60 보다 strict) 으로 silent
intervals 검출, 30s 까지 합쳐 chunk 생성. 발화 boundary 에서 chunk 경계
→ del 추가 감소 + overlap 의존 ↓. 단 long-form 의 silence 가 적은 구간은
강제 30s 절단 (uniform 과 동일 effect). 짧은 silence chunk 다수 면 prev
context 빈도 ↑ — 효과 누적.

## 다음 후보
1. 진전 → top_db sweep (30→25, 30→40) 으로 sensitivity 튜닝.
2. saturate → max_seconds 변경 (30→25) 또는 silence margin.
3. regression → revert + 다른 차원.


## iter 26 · dbb7d5b · cer=0.1657 (Δ-0.0005) · kept-1warn

## 관찰
iter 25 VAD top_db=30 큰 진전 (Δ-0.0260). 더 sensitive 임계로 silence
boundary 정밀도 ↑ 가능 — 더 짧은 chunks, 더 정확한 word boundary alignment.

## 분석
top_db=25 = 더 작은 dB drop 도 silence 로 인정 → silence segment 수 ↑ →
chunk 수 ↑, 평균 chunk 길이 ↓. condition_on_prev 가 short chunk 사이
context bridge — 정상적으로 동작 시 진전. risk: 자연 발화의 작은 pause 도
silence 로 잘못 분류 → 단어 중간 절단.

## 다음 후보
1. 진전 → 20 추가 sweep down.
2. saturate → top_db=30 회귀 + max_seconds sweep.
3. regression → 30 회귀 + top_db=40 sweep up.


## iter 27 · 21aa568 · cer=0.1839 (Δ+0.0182) · reverted

## 관찰
iter 26 top_db=25 가 saturate (Δ-0.0005). 반대 방향 sweep — 큰 dB drop
만 silence 로 → silence segment 적음 → 긴 chunk → 자연 발화 절단 적음.

## 분석
top_db=40 = silence threshold loose → chunk 가 30s 한계까지 자주 도달
(uniform 30s 와 유사 회귀). 단 진짜 긴 silence (대화 사이) 만 boundary.
intermediate sweet spot 찾기. risk: 너무 loose 면 VAD 효과 사라짐.

## 다음 후보
1. top_db 40 sweet spot → 더 sweep (35).
2. saturate → top_db=30 회귀 + 다른 lever (max_seconds sweep 또는
   temperature_fallback 구현).
3. regression → 25 회귀.


## iter 28 · 1566d84 · cer=0.1635 (Δ-0.0022) · kept-1warn

## 관찰
top_db=25 sweet spot 확정. uniform chunk 의 chunk_samples 25s (iter 13) 는
regression 였지만 VAD 의 max_seconds 25 는 forced 25s 가 아니라 silence
boundary 우선 → 25s 가 cap 만 역할. 자연 boundary 면 더 짧을 수도.

## 분석
max_seconds=25 = silence 없으면 25s 까지만. condition_on_prev 가 매 chunk
context 유지하므로 짧은 chunk 도 OK. uniform 25s (iter 13) 의 문제 (context
loss + 더 많은 overlap 비율) 가 여기선 적용 안 됨 — VAD + condition_on_prev
combo 효과.

## 다음 후보
1. 진전 → 20 추가 sweep.
2. saturate → 30 회귀 + max_seconds 35 sweep up (긴 발화 capture).
3. regression → 30 회귀 + 다른 차원 (max_chunk_silence_margin).


## iter 29 · 6679cf0 · cer=0.1663 (Δ+0.0028) · reverted

## 관찰
iter 28 max=25 단조 진전 (Δ-0.0022). 더 짧게 sweep.

## 분석
max=20 = 더 짧은 chunks → chunk 수 ↑ → condition_on_prev 빈도 ↑ → 누적
context 효과 더. 단 verify time 증가 (chunk 수 ↑) — budget 여유 확인.

## 다음 후보
1. 진전 → 15 추가 sweep.
2. saturate → 25 회귀 + 다른 lever (chunk margin 또는 num_hypotheses).
3. regression → 25 확정.


## iter 30 · 6ddf1f1 · cer=0.1633 (Δ-0.0002) · kept-1warn

## 관찰
VAD chunks 가 평균 짧음 → prev 가 더 적은 audio 표현. prev 250 으로 늘려
오디오 시간 기준 동일한 context window 유지 시도.

## 분석
VAD max=25 + prev=250 → context window 약 50s 음성 (chunk 25s + prev 25s
worth). Whisper decoder 448 한도 — prompt 254 + generated ~150 = 404 < 448
안전. context 더 길면 long-range coherence ↑.

## 다음 후보
1. 진전 → 300 sweep up.
2. saturate → 200 회귀.
3. regression → 200 확정 + 다른 lever.


## iter 31 · a787c30 · cer=0.1593 (Δ-0.0040) · kept-1warn

## 관찰
VAD top_db=25 가 silence 를 strict 검출 — 단어 경계의 짧은 pause 도 silence
로 분류 가능 → chunk start/end 가 단어 중간 절단 위험. 0.2s margin 으로
약한 silence (consonant onset/offset, vowel decay) 보호.

## 분석
margin 0.2s = 한 음절 길이 정도. chunk 양쪽 +0.4s 추가 → context 보호 +
초성/종성 정확도 ↑ 가능. chunks 사이 overlap 효과 (인접 chunk 가 같은 0.4s
영역) — overlap 도입과 유사하지만 silence 보호 우선 alignment.

## 다음 후보
1. 진전 → margin 0.3s sweep.
2. saturate → margin 0 회귀 + 다른 lever.
3. regression → margin 0 또는 0.1s.


## iter 32 · 6ef91a9 · cer=0.1733 (Δ+0.0140) · reverted

## 관찰
iter 31 margin=0.2 진전 (Δ-0.0040). 단조 가설 — margin 더 키우면 더 진전?

## 분석
margin 0.5s = 한 단어 길이 정도. chunk 양쪽 +1.0s = 인접 chunk overlap
1.0s. condition_on_prev 이미 context 공급하므로 추가 overlap 가치 marginal.
risk: 너무 길면 중복 텍스트 → length_ratio 상승.

## 다음 후보
1. 진전 → 1.0s 더 sweep.
2. saturate → 0.2 회귀 + 다른 lever.
3. regression → 0.2 또는 0.3 sweet spot.


## iter 33 · 2956299 · cer=0.1637 (Δ+0.0044) · reverted

## 관찰
0.2 best, 0.5 regression. 0.3 fine sweep — sweet spot 정밀화.

## 분석
0.3s = 한 짧은 단어 또는 한 음절 + voice onset. 0.2 와 0.5 의 중간점.
0.2 가 진짜 best 면 0.3 도 regression, 0.3 이 sweet spot 이면 진전.

## 다음 후보
1. 진전 → 0.4 sweep up.
2. saturate/regression → 0.2 확정.


## iter 34 · 1563777 · cer=0.1584 (Δ-0.0009) · kept-1warn

## 관찰
top_db 25 vs 40 (iter 26 vs 27) — 25 best. 30 (iter 25) vs 25 (iter 26)
— 25 가 약간 better. 25-30 사이 fine sweep 미수행. 28 시도.

## 분석
top_db=28 = 25 보다 살짝 loose → silence segment 약간 적음 → 약간 긴
chunks. condition_on_prev + margin 와 결합 시 sweet spot 이동 가능.

## 다음 후보
1. 진전 → 30 시도 (이전 값 재검증).
2. 회귀 → 25 확정.


## iter 35 · 03c42ae · cer=0.1622 (Δ+0.0038) · reverted

## 관찰
28 진전 (Δ-0.0009). 단조 가설 — 30 까지 사용한 적 있지만 margin 도입 전.
지금 stack 에서 재검증.

## 분석
top_db=30 + margin=0.2 + max_seconds=25 + prev=250 — 모든 lever 조합된
최신 stack 에서 측정.

## 다음 후보
1. 진전 → 32 추가 sweep.
2. 회귀 → 28 확정.


## iter 36 · d9f7e52 · cer=0.1588 (Δ+0.0004) · reverted

## 관찰
length_penalty 2.0 은 uniform chunking 시점 (iter 5) 의 sweet spot. VAD +
condition_on_prev + margin stack 에서 length 분포가 달라졌을 수 있음 —
length_ratio.mean 이 이미 0.95-0.96 (ref 가까이). 2.0 의 longer bias 가
이제 over 일 수 있음. 1.5 회귀 시도.

## 다음 후보
1. 진전 → 1.0 추가 sweep (default).
2. 회귀 → 2.0 확정.


## iter 37 · ca886c0 · cer=0.1577 (Δ-0.0007) · kept-1warn

## 관찰
beam=5 은 uniform 시점 sweep. VAD stack 에서 재검증.

## 분석
더 큰 beam = 더 다양한 hypothesis 탐색 → 정확도 ↑ 가능. runtime ↑ (예
30%) 단 budget 여유. memory 도 ↑ — OOM 가능성 낮지만 모니터.

## 다음 후보
1. 진전 → 10 sweep.
2. 회귀 → 5 확정.


## iter 38 · ecc46b4 · cer=0.1559 (Δ-0.0018) · kept-1warn

## 관찰
ngram=6 sweet spot 였지만 condition_on_prev 도입 후 hallucination 패턴
("기상캐스터 배혜지") 영향 줄어들었음 (prev context 가 자연 흐름 제공).
ngram=7 으로 차단 완화 — 정형구 7-gram 까지 허용해서 약관 fidelity ↑
가능.

## 다음 후보
1. 진전 → 8 sweep.
2. 회귀 → 6 확정.


## iter 39 · c2bcb74 · cer=0.1565 (Δ+0.0006) · reverted

## 관찰
ngram 7 진전. 8 추가 sweep — 8-gram 정형구 (예: "심사 통과되시면 오늘부터
바로 보장")까지 허용. 단 8-gram hallucination 패턴 ("한글자막 by 박진희
한의원...") 도 일부 통과 가능.

## 다음 후보
1. 진전 → 10 sweep.
2. 회귀 → 7 확정 + 다른 lever.


## iter 40 · 1cf266b · cer=0.1558 (Δ-0.0001) · kept-1warn

## 관찰
beam=8 + ngram=7 + VAD stack 에서 patience 추가 sweep — length
normalization 더 강.

## 다음 후보
1. 진전 → 3.0 sweep.
2. 회귀 → 2.0 확정.


## iter 41 · 484aedf · cer=0.1573 (Δ+0.0015) · reverted

## 관찰
iter 28 (25 진전), iter 29 (20 regression). 22 fine sweep — 25 sweet
spot 인지 22 가 더 좋은지.

## 다음 후보
1. 진전 → 23 또는 21 fine.
2. 회귀 → 25 확정.


## iter 42 · d0303e1 · cer=0.1558 (Δ+0.0000) · kept-noop

## 관찰
chunk 텍스트들 " ".join 시 chunk 내부 multiple spaces / leading/trailing
whitespace 잔존 가능. CER 은 char 단위라 공백 mismatch 도 비용. 단순
정리로 small gain 기대.

## 분석
re.sub(r"\s+", " ", ...).strip() — 연속 공백 → 단일 공백, 양끝 strip.
chunk 별 strip 추가 — chunk 끝 공백 제거 후 join. 의미 변화 없음 (단순
정리). risk 없음.

## 다음 후보
1. 진전 → 더 강한 후처리 (chunk 경계 단어 중복 제거).
2. saturate → 다른 lever (temperature_fallback, INITIAL_PROMPT 변경).


## iter 43 · 6d237ba · cer=0.1566 (Δ+0.0008) · reverted

## 관찰
0.2 best (vs 0.3, 0.5 regression). 0.15 도 시도 — 더 짧은 margin 이
중복 최소화하면서 단어 보호 충분할 수 있음.

## 다음 후보
1. 진전 → 0.1 sweep.
2. 회귀 → 0.2 확정.


## iter 44 · ba0176f · cer=0.1558 (Δ+0.0000) · kept-noop

## 관찰
prev 250 micro 진전. 300 추가 sweep. Whisper decoder context 448 — prompt
304 + generated 150 = 454, 약간 한계 초과 가능. 모니터.

## 다음 후보
1. 진전 → 350 또는 다른 lever.
2. 회귀 → 250 확정.
3. crash (context overflow) → revert + 250 확정.


## iter 45 · 62e3ba9 · cer=0.1559 (Δ+0.0001) · reverted

## 관찰
도메인 vocab hint 단어 위주. 실제 데이터에 자주 나오는 구문 ("동의하시는
거 맞으신 거죠", "심사 통과", "주민번호 말씀해 주시면") 도 hint 로 추가.
Whisper 가 구문 단위 prior 인지 → 첫 chunk 의 첫 발화 정확도 ↑ 가능.

## 다음 후보
1. 진전 → 더 많은 정형구 또는 hint 길이 sweep.
2. 회귀 → 이전 vocab-only 회귀.


## iter 46 · fea902c · cer=0.1548 (Δ-0.0010) · kept-1warn

## 관찰
margin 0.2s × 2 = 0.4s 인접 chunk 중복 → 같은 단어/구문 출력 가능.
length_ratio.mean 0.95 (ref 와 거의 같음) 인데 chunk 중복 substring 이
ref 대비 ins 증가 시키는 부분.

## 분석
인접 chunk 의 prev 끝 ↔ curr 시작 substring 일치 (max 20 chars) 검출 시
curr 시작에서 제거. 한국어 어절 길이 고려 — 20 chars ≈ 한 짧은 문장
정도. 너무 길게 매칭하면 정상 텍스트도 손실, 너무 짧으면 효과 X.

## 다음 후보
1. 진전 → max_overlap sweep (10 vs 30).
2. 회귀 → revert.


## iter 47 · 601299c · cer=0.1548 (Δ+0.0000) · kept-noop

## 관찰
20 진전. 더 긴 overlap 매칭 → 더 긴 중복 substring 도 dedup. 한 문장
완전 중복 (~30 chars) 도 검출.

## 다음 후보
1. 진전 → 60 sweep.
2. 회귀 → 20 확정.


## iter 48 · a00c111 · cer=0.1591 (Δ+0.0043) · reverted

## 관찰
0.2 best, 0.3 micro regression, 0.15 micro regression — 0.2 sweet spot
confirmed within ±0.1. 0.25 fine 시도 — dedup 도입 후 약간 큰 margin OK
일 가능성 (중복 제거되니).

## 다음 후보
1. 진전 → 다른 fine sweep 또는 정착.
2. 회귀 → 0.2 확정 + 다른 lever (iter 49-50 남음).


## iter 49 · bb1afdc · cer=NA (ΔNA) · crashed-runtime

## 관찰
beam=8 진전. 10 추가 — runtime ↑ 가능 (이미 410s, budget 457s 남은 여유
작음). budget 위반 시 verify catastrophic.

## 다음 후보
1. 진전 + budget OK → 50 마지막 fine 조합 (예: ngram=7 + 다른 patience).
2. 회귀 또는 budget 위반 → revert + 8 확정 + iter 50 마지막 안전 sweep.


## iter 50 · 924342b · cer=0.1548 (Δ-0.0000) · kept-1warn

## 관찰
50회 잡 마지막. patience 2.5 와 2.0 (이전 sweep) 사이 fine sweep —
optimal in-between 시도. best cer 0.1548 (iter 46) 유지 중.

## 다음 후보
- (50회 종료, 다음 jobs 없음.)


---

# Run summary (50 iter complete)

**Best**: cer **0.1548** (iter 46 `dedup chunk boundary`, also iter 50 tie).
**Baseline**: 0.43197 → **0.1548** (Δ-0.2772, **64.2% rel↓**). target 0.10 미달.

## 채택된 lever stack (final state)
- `librosa.effects.split(top_db=28)` + `max_seconds=25` + silence margin 0.2s
- `condition_on_prev` (직전 chunk 출력 토큰 prefix, prev_max=300 cap by ctx 448)
- `initial_prompt`: 보험 도메인 vocab 약 35 단어 (첫 chunk 만)
- `<|startofprev|>` + prev + sot tokens + `<|ko|>` + `<|transcribe|>` + `<|notimestamps|>`
- decode: `beam=8 length_penalty=2.0 patience=2.2 no_repeat_ngram_size=7`
- post-process: chunk boundary dedup (max_overlap=40) + whitespace normalize

## 주요 변곡점
1. **iter 2** chunking 도입 — 0.4320 → 0.4104 (30s window truncation 해소)
2. **iter 8** chunk overlap 5s — 0.3900 → 0.3575 (1차 plateau 돌파)
3. **iter 15** condition_on_prev + ngram=6 결합 — 0.3241 → 0.2514 (단일 최대
   Δ-0.0727). iter 6 의 condition_on_prev 부작용 (반복 폭주) 을 ngram=6 으로
   차단 + context 효과만 살림. 가장 중요한 인과 학습.
4. **iter 20-22** overlap 4→3→2s sweep — 0.2412 → 0.1922 (condition_on_prev 후
   overlap 의 marginal value 음의 함수 확정)
5. **iter 25** VAD (librosa) — 0.1922 → 0.1662 (2차 plateau 돌파)
6. **iter 46** chunk boundary dedup — 0.1593 → 0.1548 (마지막 진전)

## 미달 분석
target 0.10 미달 — Whisper-large-v3-turbo 의 11 페어 noisy 콜센터 오디오 자연
한도 가능성. 다음 단계로 가능한 lever:
- VAD library 교체 (silero-vad, webrtcvad) — librosa.effects.split 보다 정밀
- temperature_fallback (CT2 sequences_scores 기반 low-confidence retry)
- chunk batching 일부 (per-chunk batch 4 → memory ↑ but 같은 stack)
- model 자체 fine-tuning (frozen 봉인 외, scope 외)

## 가드 status
- 50 iter 중 1 crashed (OOM iter 1), 1 crashed-runtime (iter 49 beam=10)
- length_ratio.mean ~0.95 (baseline 0.60) — 가드 WARN 지속, 단 ref 와 거의
  같은 길이라 quality 신호 (not problem)
- hallucination_hit_rate 0 유지 (ngram=7 효과)
- repeated_text_rate 0 (ngram=7 + condition_on_prev 결합)
