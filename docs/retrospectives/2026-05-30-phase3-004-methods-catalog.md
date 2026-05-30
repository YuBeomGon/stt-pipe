# phase3_004 — 50회 시도 방법 카탈로그

| | |
|---|---|
| 작성 | 2026-05-30 KST |
| 잡 | `phase3_004` (50 iter, stub→eval 0.1574 / holdout 0.1847) |
| 출처 | `runs/phase3_004_iter_*/candidate_meta.json` (capability/what_i_learned/hypothesis) |
| 동반 | [회고](2026-05-30-1230-phase3-004-retrospective.md) |

> 목적: 발견형(discovery-first) 루프에서 **에이전트가 frozen 표면을 스스로
> 탐사해** 시도한 방법들을 분류·정리. 운영자가 메서드를 지정하지 않았고, 후보가
> `frozen.asr_backend` 표면(= `load()` 반환 객체 + `generate()` 의 입출력)에서
> 직접 발견했다. KEEP 5개(iter 2/4/6/7/18)로 0.41→0.157.

핵심 backend 표면(후보가 발견한 것): `generate(features, prompts,
**decoding_kwargs)` 가 ct2 `Whisper.generate` 로 그대로 pass-through →
`beam_size`/`temperature`/`length_penalty`/`patience`/`suppress_*`/
`no_repeat_ngram_size`/`max_initial_timestamp_index` 등 **모든 디코딩 kwarg** 도달
가능. `return_scores`/`return_no_speech_prob`/`num_hypotheses` 로 부가 정보 반환.
`load()` 가 raw `ctranslate2.models.Whisper` 를 줘서 `detect_language()` /
`align()` 메서드 직접 호출 가능. `WhisperProcessor` 는 어떤 입력도 30s 한 윈도우로
pad/truncate → 멀티분 오디오는 수동 청킹 필수.

---

## A. Long-form 청킹 / 윈도잉 (커버리지) — 최대 도약이 여기서

stub 의 치명적 결함: 30s 한 윈도우만 디코드 → 멀티분 통화의 30s 이후 전량 삭제
(length_ratio ~0.6). 이 계열이 커버리지를 0.59→0.95로 끌어올림.

| iter | 발견/메커니즘 | 결과 |
|---|---|---|
| 1 | 배치 청킹 시도 — `generate` 가 prompts 리스트(윈도우당 1개)를 받아 배치 디코드 가능 발견 | rej (배치 미스) |
| **2 KEEP** | **순차 30s 청킹 루프** — processor 가 30s 로 pad/truncate 함을 확인, 수동 pre-slice 만이 후속 오디오 도달 경로 | 0.4104 |
| **6 KEEP** | **`<\|notimestamps\|>` 제거** — 프롬프트 토큰 조합을 워크스페이스가 완전 제어함을 발견 → timestamp 모드로 디코드 (segment 마커가 early-EOT 억제) | 0.3322 |
| **7 KEEP** | **timestamp-anchored 동적 윈도잉** — 디코드 시퀀스가 per-segment end-timestamp 토큰을 반환함을 발견(iter6가 켰지만 버리던 것). 마지막 마커까지만 keep + 잘린 trailing segment 버리고 seek 을 그 시각으로 전진 = Whisper 네이티브 long-form 루프 | **0.1611 (Δ-0.171)** |
| 15/24/30 | overlap left-context / 절대-timestamp dedup / 음향 left-context — re-anchor 가 만드는 cold-onset cut 을 앞 오디오로 완충 | rej |
| 28/41 | energy-VAD / RMS-VAD — processor 의 zero-pad 덕에 윈도우가 clock-aligned 30s 일 필요 없음 → 침묵에서 자른 가변 segment | rej |
| 34/42 | overlap-stitch — 25s stride 5s 겹침 + `difflib.SequenceMatcher` 로 warm-tail 유지, cold-onset 폐기 | rej |
| 35/45 | coverage-gap — 마지막 timestamp 시각을 "얼마나 받아썼나" 척도로 재해석, 부족하면 notimestamps 재디코드 | rej |
| 16/50 | `max_initial_timestamp_index=0` — 첫 segment 를 `<\|0.00\|>` 에 고정해 leading-onset skip(최대 1.0s) 금지 | rej |
| **44** | **token/word-level timestamp** — `align().alignments` = (text_token_index, encoder_frame_index) 쌍으로 emit된 **각 토큰**을 음향 force-align해 audio 시각 부여. 토큰 간 gap으로 침묵 위치까지 탐지 → 최대 pause 에서 seek 재앵커 | rej |

### timestamp 두 종류 — segment-level(iter7 KEEP) vs token/word-level(iter44 rej)

운영자가 "word timestamp 구현"으로 기억하는 건 **iter44** 다. iter7 과는 시각의
**출처와 입도(granularity)** 가 다르다:

| | iter7 (KEEP) | iter44 (reject) |
|---|---|---|
| 입도 | **segment** 단위 | **token(≈word)** 단위 |
| 출처 | 디코더가 emit한 `<\|t\|>` **timestamp 토큰** (vocab id ≥ `<\|0.00\|>`, 0.02s/token) | `align()` 의 **`.alignments`** — 토큰↔encoder frame(20ms) **사후 음향 force-align** |
| 전제 | timestamp 모드 디코드(= `<\|t\|>` 토큰이 나와야 함) | **`<\|notimestamps\|>` 모드여도** 동작 — 타이밍을 디코드 후 음향에서 복원 |
| 의의 | Whisper 네이티브 long-form 루프(마지막 segment 마커로 재앵커) | **커버리지 디코드 ↔ 윈도우 전진을 분리**: notimestamps 로 잘린 tail 까지 받아쓰고, 그와 무관하게 토큰 타이밍으로 침묵에서 재앵커 |
| 결과 | **Δ-0.171, 최대 도약** | 메커니즘은 정확히 구현, 11-file eval 종합 개선엔 미달 → reject |

> iter32/36/37 은 같은 `align()` 객체의 `.text_token_probs`(음향 *신뢰도*)만 썼고,
> iter44 는 `.alignments`(음향 *타이밍*)를 따로 발견해 쓴 게 차별점. 즉 후보는
> 한 frozen 메서드의 **서로 다른 반환 필드를 독립적으로 발견·활용**했다.

---

## B. 디코딩 파라미터 (beam / temperature / penalties)

`generate()` pass-through 로 도달하는 ct2 kwarg 들. 단발 probe 가 대부분.

| iter | kwarg / 메커니즘 | 결과 |
|---|---|---|
| 3 | `no_repeat_ngram_size=3` — greedy 3-gram 루프 차단 | rej |
| **4 KEEP** | **beam_size 1→5** — 전 iter 가 순수 greedy 였음 발견. early-EOT collapse 완화 | 0.4058 |
| 5 | temperature 샘플링 fallback — collapsed chunk 를 stochastic 재디코드 | rej |
| 9 | `length_penalty=1.2` — beam length-normalization 지수(beam>1 에서만 작동) | rej |
| 17 | `repetition_penalty=1.1` — prefix 재사용 토큰 down-weight (no_repeat 와 구별) | rej |
| 20 | `patience=2.0` — 몇 개의 완성 가설을 모으고 멈출지 (length_penalty 와 별개) | rej |
| 25 | `suppress_tokens=[]` — 기본 `[-1]` non-speech 억제 해제 (token-availability 레버) | rej |
| 27 | `suppress_blank=False` — 첫 토큰 blank 허용 → cold-onset 강제 commit 완화 | rej |
| 47 | `sampling_topp=0.9` (nucleus) — top-k 와 직교, fallback 샘플 정제 | rej |
| 48 | `no_repeat_ngram_size=3` (beam 경로) — 반복을 사후 flag 아닌 원천 차단 | rej |
| 50 | `max_initial_timestamp_index=0` — leading 삭제 방지 (B/A 교차) | rej |

---

## C. 신뢰도 스코어링 & 재랭킹 (rescoring) — 가장 풍부하게 탐색

`return_scores`(avg-logprob), `return_no_speech_prob`, `num_hypotheses`(빔/샘플
리스트), `align()`(음향 신뢰도)를 발견하고 다양한 선택/게이트로 조합.

| iter | 메커니즘 | 결과 |
|---|---|---|
| 10 | `no_speech_prob` 게이트(>0.6) — 인코더의 무음 확률로 환각 윈도우 drop | rej |
| 11 | `return_scores` avg-logprob → temperature-fallback (Whisper 표준) | rej |
| 12 | joint silence gate — `no_speech_prob>0.6 AND avg_logprob<-1.0` 한 pass 로 | rej |
| 13 | `num_hypotheses=beam_size` 전체 빔 리스트 — score margin 내 최장 빔 선택 | rej |
| **18 KEEP** | **품질 게이트 재디코드** — `scores[0]`(avg-logprob) + zlib `compression_ratio` 로 윈도우 판정, 실패 시 temperature 0→0.4→0.8 escalate, 최고 score 유지 | **0.1574 (banking)** |
| 19 | best-of 샘플링 — beam=1+temp 로 N 독립 샘플, max-score | rej |
| 23 | longest-beam — score 가 length-정규화라 top 빔이 더 짧음 → margin 내 최장 | rej |
| 31 | consensus medoid — scores 는 *정확도* 아닌 *확신도* 랭킹(유창한 환각이 더 높음) → 샘플 medoid | rej |
| 32 | `align().text_token_probs` — per-token **음향** 신뢰도(decoder score 와 다른 축)로 가설 선택 | rej |
| 33 | ROVER 토큰 다수결 — 선택이 아닌 *합성*(한 가설의 치환 오류 상속 회피) | rej |
| 36 | longest-beam + align 게이트 — iter23·32 가 상보적으로 상쇄됨을 관찰해 결합 | rej |
| 37 | no-speech + align trim — 가설 전체가 아닌 trailing 환각 토큰만 국소 절단 | rej |
| 46 | align-judge rerank — fallback 후보들을 음향 prob 로 재판정 | rej |

> **주목**: iter31 이 "avg-logprob 는 정확도가 아닌 확신도 — 유창한 치환이 정답보다
> 높게 점수"라는 핵심 통찰을 스스로 도출. iter36 은 "iter23 길이↑·iter32 음향↑ 이
> 단독으론 상보적으로 상쇄"를 관찰 — 이게 새 harness 의 **synthesis 모드** 동기.

---

## D. 컨텍스트 조건화 / 프롬프팅 (prefix / glossary)

`<|startofprev|>` soft-prompt 슬롯과 decoder text-prefix 슬롯을 발견. 치환 잔차
공략 시도(커버리지 해결 후 병목이 치환으로 이동).

| iter | 메커니즘 | 결과 |
|---|---|---|
| 8 | `<\|startofprev\|>` + 직전 디코드 transcript (faster-whisper 의 condition_on_previous_text) | rej |
| 14 | **static glossary** — 고정 한국 보험 용어 문자열을 prev-text 로 prime (도메인 치환 직격) | rej (sub +0.04 개선) |
| 21 | gated running-transcript — iter18 게이트를 reset 트리거로 결합 | rej |
| 26 | bidirectional 2-pass — 양쪽 이웃 윈도우 텍스트를 컨텍스트로 | rej |
| 29 | trailing-segment carry — re-anchor 가 버리는 잘린 segment 를 다음 윈도우 prev 로 | rej |
| 39 | decoder text-prefix — 직전 윈도우 끝 단어를 강제 prefix(de-dup 불필요) | rej |
| 43 | batched dual-prompt — `[plain, glossary-primed]` 를 한 배치 GPU pass 로, coverage 로 선택 | rej |
| 22/49 | per-window `detect_language()` — code-switch(영문 상품명) 대응 언어 토큰을 윈도우별 설정 | rej |

---

## E. 오디오 frontend / 입력

| iter | 메커니즘 | 결과 |
|---|---|---|
| 38 | peak-normalize — mel front-end 가 loudness 정규화를 안 함 발견 → 조용한 통화 under-driven 보정 | rej |
| 40 | `<\|notimestamps\|>` 교차 디코드 fallback — 프롬프트 id 리스트만으로 디코드 모드 선택 | rej |

---

## 종합 — 무엇을 보여주나

- **자가발견 입증**: 운영자가 rescoring·seek·prefix·VAD 를 지정하지 않았는데
  후보가 표면에서 전부 발견(C/A/D 계열). frozen 표면을 "읽고" ct2 API 를 떠올려
  실제로 호출(`align()`, `detect_language()`, `num_hypotheses`, `return_scores`).
- **큰 이득은 구조 발견(A)에서, 미세 이득은 게이트(C)에서**: iter7 timestamp 윈도잉
  Δ0.171, iter18 품질게이트 Δ0.0037(banking). 디코딩 파라미터 단발(B)은 거의 무효.
- **병목 이동의 자각**: iter8 부터 "deletion 해결됨, 이제 substitution 이 지배"를
  반복 인지. C/D 계열 대부분이 치환 공략이었으나 frozen 표면만으론 못 깸 → 회고 §3
  의 "도메인 치환은 표면 밖" 결론과 정합.
- **상보적 상쇄 패턴**: 단발 lever 가 한 축 고치고 다른 축 깸(iter23·32·45 등).
  이 관찰이 새 harness 의 **synthesis(유망 reject 조합)** 모드로 이어짐.
