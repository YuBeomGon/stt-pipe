# 전체 실험 아이디어 인벤토리

작성일: 2026-06-09

## 범위

확인한 기록:

- `runs/**/candidate_meta.json`, `runs/**/score_report.json`, `runs/**/candidate.diff`
- `runs/_summary/*.jsonl`, `runs/_summary/HISTORY.md`
- `runs/_archive/**`
- `docs/history-archive/*.tar.gz` 를 풀어 둔 `/tmp/aig-doc-runs-archive/**`
- CER 15%대 후보 복원본: `temp/cer15_transcribes/**`

이 문서는 지금까지 나온 아이디어를 버리지 않기 위한 재사용 목록이다. 정확한 15%대 후보별 파라미터는 `docs/reports/cer15_method_and_parameter_catalog.md` 를 정본으로 본다.

## 상위 성능 기준점

| 구간 | 대표 후보 | CER | 핵심 아이디어 |
|---|---:|---:|---|
| 최상위 | `phase3_013_iter_022` | 0.153887 | per-window `detect_language()` 로 언어 토큰을 고신뢰 구간에서만 교체 |
| 15%대 | `phase3_004_iter_018` | 0.157355 | avg-logprob/compression gate 기반 conditional temperature fallback |
| 15%대 | `phase3_014_iter_046` | 0.158540 | glossary 조건 B를 strict greater가 아니라 0.1 logprob margin 안에서 채택 |
| 16% 초반 | `phase3_016_iter_010` | 0.160440 | 반복 붕괴 후보는 compression 우선, 깨끗한 후보는 score 우선으로 tie-break |
| 16% 초반 | `phase3_030_iter_097` | 0.162836 | VAD/segment 기반 위에 beam width 8 재시도 |
| 17%대 탐색 | `phase3_005_iter_098` | 0.176899 | CJK/Kana/foreign-script suppress token mask |

## 재사용 가치가 높은 아이디어

### 1. Long-form / seek / segmentation

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| 30초 window batched decode | `phase3_019_iter_001`, `phase3_030_iter_001` | 기본 stub가 30초 이후를 버리는 구조라 삭제 오류의 가장 큰 원인을 제거 | 모든 새 실험의 기본 backbone |
| timestamp 기반 adaptive seek | `phase3_004_iter_007`, `phase3_015_iter_003`, `phase3_030_iter_002` | blind 30초 stride보다 훨씬 개선. `phase3_030_iter_002` 0.174939 | 15%대 decode stack과 결합 |
| silence-aware cut / RMS trough | `phase3_019_iter_003`, `phase3_030_iter_094~098` | 긴 발화 중간을 자르지 않기 위한 경계 탐색. 16.6%대까지 접근 | `phase3_013` backbone에 forced fallback만 RMS trough로 교체 |
| dual grid / phase-shift witness | `phase3_014_iter_068`, `phase3_014_iter_075` | seam 근처 substitution을 반박 witness로 보정 | seam 인근 짧은 replace span에만 제한 적용 |
| penultimate-overlap / trailing segment re-decode | `phase3_014_iter_069` | 마지막 segment가 context 부족으로 틀리는 문제를 다음 window interior로 재해석 | trailing 1 segment만 중복 decode하고 difflib reconcile |
| hierarchical segment re-decode | `phase3_014_iter_081` | low-confidence window 내부 timestamp segment만 isolate decode | `lp < -0.5` band에만 적용해 비용 제한 |

### 2. Decode search / fallback

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| beam search + N-best | `phase3_013_iter_011`, `phase3_014_iter_014`, `phase3_030_iter_005` | substitution 축에서 일관되게 강함 | `beam=5`, `num_hypotheses=5` 기본값 유지 |
| patience 확대 | `phase3_013_iter_012`, `phase3_004_iter_020`, `phase3_030_iter_033` | correct path가 early pruning되는 경우에 유효 | `patience=2.0` 과 `1.5` 를 backbone별 분리 측정 |
| beam width 8 | `phase3_013_iter_020`, `phase3_014_iter_019`, `phase3_030_iter_097` | 검색 폭 확대는 15~16%대에서도 반복 등장 | runtime budget 안에서 `beam=5` 대비 isolated A/B |
| conditional temperature fallback | `phase3_004_iter_018`, `phase3_030_iter_007` | 15%대 검증된 핵심. 실패 window만 재디코드 | gate는 `avg_logprob < -1.0`, `compression > 2.4` 를 기준점 |
| fallback ladder trim | `phase3_014_iter_050`, `phase3_011_iter_021` | 높은 temperature rung은 full-tail noise를 만들 수 있음 | `(0.0,0.2,0.4,0.6)` 과 `(0.0,0.4,0.8)` 비교 |
| axis-aware selector | `phase3_016_iter_010` | 반복 후보는 compression 우선, 정상 후보는 logprob 우선 | 후보 선택 함수를 failure axis별로 분리 |
| no-speech single beam gate | `phase3_014_iter_018`, `phase3_014_iter_087`, `phase3_030_iter_006` | hallucination 방어. 단독으로도 16~17%대 | temperature fallback 뒤 최종 emission gate로 결합 |

### 3. Language / prompt / glossary

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| per-window language detection | `phase3_013_iter_022`, `phase3_004_iter_049` | 전체 최고 성능. code-switch substitution 완화 | top language prob >= 0.7일 때만 `<|ko|>` override |
| ambiguous band dual-language decode | `phase3_013_iter_023` | 0.154541로 매우 강함. 애매한 언어 구간을 양쪽 decode 후 선택 | `[0.4,0.7)` band에서만 dual decode |
| static glossary via `<|startofprev|>` | `phase3_030_iter_004~005`, `phase3_014_iter_039` | rolling feedback은 악화, static prompt는 domain prior로 유효 | rolling tail 제거, 고정 glossary만 제한 token budget |
| glossary logprob margin | `phase3_014_iter_046` | strict score 비교가 domain correction을 버리는 문제 해결 | `lp_b >= lp_a - 0.1` 를 기본 후보 |
| glossary trigger decouple | `phase3_014_iter_051`, `phase3_014_iter_076` | cond B가 너무 늦게 실행되는 문제. 15.8%대 유지 | `lp_a < -0.5` band까지 glossary branch 허용 |
| glossary budget tuning | `phase3_005_iter_101` prompt ledger | token cap이 glossary tail을 잘라 prior가 약해짐 | budget 24/36/64 isolated sweep |
| confidence-gated context carry | `phase3_016_iter_005`, `phase3_014_iter_066`, `phase3_030_iter_071~073` | 이전 text를 무조건 넣으면 오류 전파. gate가 중요 | compression/logprob 통과 window만 context로 carry |
| bidirectional context | `phase3_004_iter_026`, `phase3_030_iter_105~106` | 다음 window 문맥으로 모호 domain term 보정 시도 | low-confidence window에만 2-pass future context |
| term bank / recurrence glossary | `phase3_030_iter_102~103`, `phase3_014_iter_067` | 이전에 여러 번 안정적으로 나온 단어를 bank화 | re-emission penalty와 같이 사용해 drift 방지 |

### 4. Alignment / acoustic verification

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| align posterior tail trim | `phase3_013_iter_014`, `phase3_013_iter_016`, `phase3_004_iter_037` | 15%대 검증. loop/hallucinated tail 제거 | logprob fail 또는 repeated_text일 때만 align 실행 |
| frame advance trim | `phase3_013_iter_018` | 텍스트 posterior뿐 아니라 frame 진행 정체를 loop 신호로 사용 | `prob floor` 와 `frame advance` OR 조건 |
| align-based N-best rerank | `phase3_013_iter_021`, `phase3_004_iter_032`, `phase3_014_iter_023` | score가 LM-biased일 때 acoustic posterior로 선택 | top-2 또는 top-5 disagreement window에만 |
| align-verified lexicon rewrite | `phase3_014_iter_044` | out-of-beam glossary correction을 acoustic drop 없이 검증 | jamo 후보 rewrite 후 changed span posterior 비교 |
| token prob excision | `phase3_030_iter_081~085` | language prior hallucination token 제거 목적 | isolated unsupported token만 제거, 연속 저확률 run은 보수 처리 |
| rank1 fork acoustic arbiter | `phase3_014_iter_089` | rank0/rank1의 짧은 replace span을 align으로 판정 | span 길이 <= 3 content tokens 제한 |

### 5. N-best / consensus / ensemble

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| beam medoid / MBR | `phase3_004_iter_031`, `phase3_030_iter_008~009` | score-free selector로 substitution 완화 시도 | margin이 충분할 때만 top beam override |
| token/word majority vote | `phase3_013_iter_015`, `phase3_004_iter_033`, `phase3_030_iter_011` | 단일 confident-wrong token을 다른 beam들이 반박하는 구조 | equal-length replace span 한정 |
| sampling MBR / independent draws | `phase3_030_iter_051~056`, `phase3_014_iter_082~083` | beam과 다른 오류 분포를 가진 witness 확보 | `temp=0.2`, `topk=8`, `N=5` 주변부터 재측정 |
| ROVER gated override | `phase3_030_iter_086~091`, `phase3_014_iter_058` | anchor가 거의 지지받지 못할 때만 override | anchor support gate + vote min 조합 |
| lexicon rerank inside epsilon | `phase3_030_iter_065~069`, `phase3_014_iter_040` | logprob 근접 후보 중 domain term coverage로 선택 | strict superset gate로 false positive 제한 |
| longest beam within score margin | `phase3_004_iter_013`, `phase3_004_iter_023`, `phase3_014_iter_073` | under-generation / deletion 보정 | compression-pass + score margin 0.10~0.15 |

### 6. Token constraints / repetition / hallucination guards

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| CJK/Kana/foreign script suppression | `phase3_013_iter_017`, `phase3_005_iter_098`, `phase3_030_iter_040~042` | Hangul 대신 kana/CJK가 나오는 substitution 방지 | Korean + allowed Latin 외 script mask를 decode 공통 적용 |
| default suppress_tokens 해제 | `phase3_004_iter_025` | token availability/length deficit 개선 시도 | deletion-heavy backbone에서만 isolated test |
| suppress_blank=False | `phase3_004_iter_027` | clipped onset에서 첫 content token 강제 방지 | max_initial_timestamp와 같이 onset A/B |
| repetition_penalty=1.1 | `phase3_013_iter_024`, `phase3_030_iter_016`, `phase3_019_iter_012` | 반복 loop에는 유효하지만 과하면 삭제 증가 | 1.05/1.1/1.15를 guard metric과 같이 측정 |
| no_repeat_ngram | `phase3_019_iter_013`, `phase3_005` ledger | exact loop만 막는 targeted guard | ngram 4/5/6을 반복 focus file 기준으로 비교 |
| seam exact dedup | `phase3_014_iter_085`, `phase3_008_iter_021` | window seam repeat를 직접 제거 | exact consecutive duplicate segment만 제거 |
| no_speech threshold tuning | `phase3_030_iter_049`, `phase3_004_iter_010~012` | hallucination 방지와 deletion 위험의 tradeoff | 0.5/0.6 + avg_logprob joint gate |

### 7. Audio frontend / input conditioning

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| high-pass / low-band removal | `phase3_013_iter_026` | 15.8%대. 전화 음성 저역 rumble 제거 | boxcar high-pass width `sr/80` 전후 |
| pre-emphasis | `phase3_014_iter_020`, `phase3_030_iter_030~034`, `phase3_030_iter_076~080` | obstruent/high-frequency cue 복원 목적. 과하면 왜곡 | raw trusted floor + margin 있는 dual front-end |
| loudness normalization / gain cap | `phase3_030_iter_019~023`, `phase3_004_iter_038` | quiet file 보정 vs noise amplification tradeoff | target RMS 0.05, max gain 2.5부터 |
| VTLN / mel/formant warp witness | `phase3_014_iter_042`, `phase3_014_iter_064` | formant-shift witness가 substitution 반박 가능 | whole pipeline이 아니라 correction witness로만 사용 |
| dual front-end score arbitration | `phase3_030_iter_076~080` | raw와 pre-emphasis 중 score/margin으로 선택 | raw score가 낮을 때만 second pass |

### 8. Post-decode lexical correction

| 아이디어 | 출처 예 | 관찰 | 다시 테스트할 형태 |
|---|---|---|---|
| jamo consonant correction | `phase3_014_iter_041`, `phase3_014_iter_067` | 보험 domain term의 자음 혼동 보정 | 동일 음절수, 동일 모음, 첫 음절/조사 guard 유지 |
| recurrence canonicalization | `phase3_014_iter_037`, `phase3_014_iter_067` | 여러 번 나온 표기를 anchor로 singleton 수정 | corpus 내부 반복 count threshold 필요 |
| align-verified glossary rewrite | `phase3_014_iter_044` | post-correction의 위험을 acoustic posterior로 제한 | changed span만 posterior 비교 |
| logit glossary snap | `phase3_014_iter_086` | 선택 token 근처의 glossary token으로 직접 snap | `return_logits_vocab` 가능성 확인 후 별도 test |

## 버리기 아까운 조합 후보

| 조합 | 이유 | 주의점 |
|---|---|---|
| `phase3_013_iter_022` language override + `phase3_014_iter_046` glossary margin | 최고 성능 축과 domain prior 축이 서로 다름 | glossary branch가 code-switch를 다시 한글화하지 않도록 language token 분리 |
| conditional fallback + no_speech final gate | fallback은 substitution, no_speech는 hallucination을 담당 | no_speech gate가 실제 speech window를 지우지 않도록 joint gate 필요 |
| beam=5/8 + align top-2 acoustic rerank | LM-biased top beam을 acoustic posterior로 반박 | align 비용과 token trim 부작용 제한 |
| VAD/RMS trough segmentation + 15%대 decode stack | segmentation 개선은 deletion/seam 축, decode stack은 substitution 축 | segmentation만 바꾸면 기존 seek/overlap 계약 깨질 수 있음 |
| static glossary + confidence-gated carry + term bank | domain prior를 주되 오류 feedback을 막음 | prompt token budget과 re-emission penalty 필요 |
| CJK suppress mask + language detect override | 잘못된 script를 막고, 실제 foreign term은 language detector로 살림 | Latin/English code-switch는 suppress 대상에서 제외 |
| longest-within-margin + length_penalty | under-generation 보정 두 축 | hallucination/반복 guard와 같이 봐야 함 |
| dual front-end witness + ROVER/MBR gate | acoustic view 차이를 correction evidence로 사용 | whole-pipeline substitution보다 high-precision correction으로 제한 |

## 부정적이지만 기록할 교훈

- Rolling transcript를 무조건 `<|startofprev|>` 로 넣으면 오류가 다음 window로 전파된다. static glossary 또는 confidence-gated carry가 낫다.
- 단순 loudness/pre-emphasis는 조용한 파일에서 noise도 같이 키워 substitution을 악화시킬 수 있다. gain cap 또는 score arbitration이 필요하다.
- 반복 penalty를 크게 주면 loop는 줄어도 정상 반복 한국어 토큰까지 눌러 deletion을 늘릴 수 있다.
- 높은 temperature ladder는 low-confidence window에서 rare-token tail과 hallucination을 만들 수 있다. ladder trim 또는 best-of/MBR gate가 필요하다.
- glossary/post-correction은 strict하게 적용하면 false positive 위험이 크다. logprob margin, align posterior, jamo guard, superset gate 같은 안전장치가 필요하다.
- `suppress_tokens` script mask는 Korean-only 상황에서는 유리하지만 실제 code-switch/Latin acronym을 막지 않도록 범위를 명확히 해야 한다.
- whole-hypothesis MBR/ROVER는 좋은 후보를 고르는 대신 평균적인 이상한 문장을 만들 수 있다. anchor-support나 margin gate가 필요하다.

## 다음 실험 우선순위

1. 최고 성능 `phase3_013_iter_022` 를 기준으로 `glossary-margin=0.1` 만 결합한다.
2. 같은 기준에서 no_speech final gate를 joint gate 형태로 추가해 hallucination guard만 확인한다.
3. `beam=5`와 `beam=8`, `patience=2.0`을 각각 isolated로 재측정한다.
4. top-2 align acoustic rerank를 disagreement window에만 붙인다.
5. RMS trough segmentation을 fallback cut에만 적용해 기존 seek 계약을 유지한 채 비교한다.
6. CJK/Kana suppress mask를 language override와 함께 적용하되 Latin은 허용한다.
7. jamo/glossary post-correction은 align-verified span만 대상으로 별도 branch에서 테스트한다.
