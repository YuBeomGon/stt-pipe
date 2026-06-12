# phase3_016 — run 회고

## 개요

Gen7(refactor-harness) smoke 검증 run (10 iter, 전 iter 채점 성공). phase3_015 와 동일한 구조 검증이 목적이나, 이 run 은 특히:

- **combine 모드의 첫 실전 성사** (iter_005: 두 family 의 부모를 실제로 융합)
- **lineage set 의 full cycle** — set seed(iter_006) → set:refine 연쇄(iter_007~009) → reset → 새 set(iter_010)
- **portfolio 의 family_best 슬롯**이 family_001~005 까지 분화

기록 위치가 다른 run 들과 달리 `runs/_archive/phase3_016_smoke/` 하위인 것도 이 run 이 smoke 명명으로 격리 보존됐기 때문.

결과적으로 Gen7 검증 run 4개 중 최저 CER (0.160440) 을 낸 run.

## Iteration 기록

| iter | mode | 시도한 것 (한 줄) | CER | 판정 |
|---|---|---|---|---|
| 001 | explore (ovr `no_best`) | 30s 비중첩 chunking 으로 long-form 커버리지 복구 (family_001) | 0.411350 | keep — first valid |
| 002 | explore (ovr `discovery_phase`) | numpy energy-VAD: 무음 중심에 컷을 맞춘 ≤28s chunk (family_002) | 0.180611 | keep — Δcer 0.230739 |
| 003 | explore (sched refine → `discovery_phase`) | `<|startofprev|>` + 직전 transcript 로 디코더 lexical priming (family_003) | 0.162653 | keep — Δcer 0.017958 |
| 004 | explore (ovr `discovery_phase`) | 2-pass escalation: greedy + `return_scores`, avg-logprob/zlib 압축비 게이트 실패 시만 beam 재디코드 (family_004) | 0.160762 | keep — Δcer 0.001891 |
| 005 | **combine** (parents iter_004 + iter_003) | confidence-gated context carry — 게이트 통과 chunk 만 `<|startofprev|>` context 로 전파 | 0.160474 | keep — Δcer 0.000288, **champion best** |
| 006 | explore | `<|notimestamps|>` 제거, Whisper 네이티브 timestamp 로 seek 하는 sequential decode (family_005) | 0.170103 | lineage_advance — set seed |
| 007 | refine (ovr `set:refine`, parent iter_003) | family_003 priming 파이프라인에 beam_size=5 | 0.163768 | lineage_advance — Δ0.006335 |
| 008 | refine (sched explore → `set:refine`, parent iter_002) | beam=5 위에 length_penalty=1.3 (beam 의 short-sequence bias 보정) | 0.162879 | lineage_advance — Δ0.000889 |
| 009 | refine (sched ablate → `set:refine`, parent iter_001) | patience=1.5 로 beam 종료조건 완화 | 0.161799 | reset — set 종료 (near_best 등재) |
| 010 | refine (sched refine, parent iter_002 lineage) | **axis-aware selector** (하단 상세) | **0.160440** | lineage_advance — Δ0.001359, **run 최저** |

## CER 궤적

```
stub 0.4126
001  0.411350   blind 30s chunking (coverage 복구해도 stub 동률)
002  0.180611   ── silence-aligned cut: -0.2307 (한 방)
003  0.162653   ── context priming: 앵커 0.1714 첫 돌파
004  0.160762
005  0.160474   ← champion best (combine)
006  0.170103   (set 격리)
007  0.163768 → 008 0.162879 → 009 0.161799   (set 내 단조 개선)
010  0.160440   ← 수치상 run 최저 (단, lineage 판정이라 champion 미반영인 채 run 종료)
```

최종 state: `best_cer 0.160474 @ iter_005` (champion 기준). 수치 최저는 iter_010 의 0.160440.

## 무엇이 점수를 만들었나

- **iter_002 (silence-aligned VAD cut)가 개선분의 92%**: 0.4114→0.1806. 핵심 발견은 "deletion 80% 는 잘린 꼬리가 아니라 blind cut 이 발화 한가운데를 갈라 윈도우 내 repetition-collapse/조기 EOS 를 유발한 것" — 컷 **위치**가 변수였다.
- 이후는 substitution 축 싸움 (length_ratio 0.93~0.95 로 coverage 해결됨):

### iter_005 상세 — confidence-gated context carry (combine, 0.160475)

family_004 의 confidence 신호(`generate(return_scores=True)` 의 avg log-prob + zlib 압축비)와 family_003 의 `<|startofprev|>` priming 채널을 **융합**한 첫 combine 성공 사례. incumbent 는 직전 chunk 의 텍스트를 **무조건** 다음 chunk 의 priming context 로 넘겼는데, 이 패치는 `_decode_chunk` 가 `(text, confident)` 를 반환하게 바꾸고, **두 게이트(avg-logprob ≥ `_LOGPROB_THRESH`, 압축비 ≤ `_CR_THRESH`)를 통과한 chunk 만** `prev_context_ids` 를 갱신한다. 불신 chunk 는 "마지막으로 신뢰된 context 를 유지" — substitution 오류가 priming 채널을 타고 후속 chunk 로 전파되는 것을 차단. escalation(Pass B)까지 간 chunk 도 선택된 후보가 게이트를 재통과해야만 confident 로 인정. Δcer 0.000288 로 작지만 champion 으로 승격됐고, 두 독립 채널의 wiring 이라는 combine 모드의 설계 의도에 정확히 부합한다.

### iter_010 상세 — axis-aware selector (refine, 0.160440)

iter_004 가 만든 2-pass escalation 의 후보 선택 규칙이 대상. 기존 선택은 greedy(Pass A) vs beam(Pass B) 두 후보를 **무조건 compression-first** (`rank = (cr, -score)`) 로 비교했는데, 압축비는 repetition 축만 판별하므로 **둘 다 깨끗할 때는 사실상 동률**이 되어, 정작 dominant 축(substitution 59%)을 풀어주는 beam 의 승리가 "greedy 텍스트가 우연히 약간 덜 압축된다"는 이유로 버려지고 있었다. 패치:

- **둘 중 하나라도 repetitive** (`cr > _CR_THRESH`): 기존대로 compression 우선 (repetition collapse = 최악 실패이므로)
- **둘 다 clean**: avg log-prob 이 높은 쪽 선택 (남은 판별축은 confidence 뿐)

즉 실패축에 따라 selector 의 우선순위를 바꾸는 axis-aware 선택. CER 0.160440 으로 run 수치 최저. 단 판정은 set lineage 의 `lineage_advance` 였고 run 이 iter 10 에서 끝나 **champion 에는 미반영** — state 의 best 는 여전히 iter_005.

## 한계와 보완점

하네스 검증 관점:

- **확인된 것**: combine 스케줄→성사→승격의 전 과정(iter_005), set 의 seed→refine 예산 3회 소진→reset(iter_006~009) full cycle, family 5개 분화와 family_best 슬롯 운용, scheduled_mode 와 chosen_mode 의 override 매트릭스(discovery_phase / set:refine / scheduled)가 모두 실데이터로 발화. **10/10 iter 채점 성공** — verify/scope 사고 0건.
- **남은 것 / 한계**:
  - iter_010 이 보여준 구조적 빈틈: **수치상 최저 후보가 lineage 판정에 갇힌 채 run 이 끝나면 champion 으로 흡수할 경로가 없다** (0.160440 vs champion 0.160474). set 졸업(승격) 시점과 run 종료의 경계 처리 문제.
  - iter_005 부터 5 iter 동안 champion 정체 (`evaluated_since_best_update: 5`) — Δ들이 noise floor 근처 (0.0003~0.0014). 이 영역에서 keep/advance 판정의 통계적 의미는 미검증.
  - set 내 refine(007~009)이 각각 다른 family 의 부모를 받아 사실상 "set" 이라기보다 family 순회에 가깝게 동작 — set 의 의미론이 이 run 에서 다소 흐려짐.

## 아티팩트

- iter 디렉토리: `runs/phase3_016_iter_001` ~ `runs/phase3_016_iter_010`
- 판정/상태: `runs/_archive/phase3_016_smoke/phase3_016_decisions.jsonl`, `.../phase3_016_state.json`, `.../phase3_016_portfolio.json`, `.../phase3_016_candidate_meta.jsonl`
- 상세 diff: `runs/phase3_016_iter_005/candidate.diff` (confidence-gated carry), `runs/phase3_016_iter_010/candidate.diff` (axis-aware selector)
- 하네스 맥락: `docs/HARNESS-EVOLUTION.md` Gen7 절
