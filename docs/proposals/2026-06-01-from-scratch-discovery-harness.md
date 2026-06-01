# Proposal — Portfolio Evolution Harness

- 상태: **Draft — 사용자 검토 필요**
- 작성: 2026-06-01
- 목적: 50~100회 self-evolve loop 안에서 다양한 알고리즘 계열을 탐색하고,
  유망 후보를 보존·조합·튜닝해 CER 을 `target_cer=0.10` 에 최대한 가깝게 낮춘다.
- 영향 예상: `harness/config.py`, `harness/runner.py`, `harness/state.py`,
  `harness/prompts/`, `scripts/analyze_run.py`, `docs/templates/REPORT.md`,
  `docs/PHASE3-PLAN.md`
- 선행 자료:
  [`2026-05-29-agent-design.md`](2026-05-29-agent-design.md),
  [`2026-05-29-prompt-diversification.md`](2026-05-29-prompt-diversification.md),
  [`../retrospectives/2026-05-30-1230-phase3-004-retrospective.md`](../retrospectives/2026-05-30-1230-phase3-004-retrospective.md),
  [`../retrospectives/2026-05-31-phase3-005-retrospective.md`](../retrospectives/2026-05-31-phase3-005-retrospective.md)

---

## 0. 목표

이번 목표는 순수한 "from scratch discovery 능력 평가" 하나가 아니다. 목표는 두 개다.

1. **탐색 다양성** — agent 가 최대한 다양한 알고리즘 계열을 스스로 찾아내고 시도했는가.
2. **Best CER** — 그 탐색과 튜닝 결과로 CER 을 얼마나 `0.10` 에 가깝게 낮췄는가.

따라서 harness 는 single-best hill climb 이 아니라 작은 **portfolio evolution loop** 가
되어야 한다. 어떤 후보가 당장 global best 를 못 이겨도, coverage 개선, substitution
개선, hallucination 감소, runtime 단축 같은 진화 재료가 있으면 보존하고 나중에
refine/combine/ablate 에 사용한다.

---

## 1. 기존 run 해석

### phase3_004

`phase3_004` 는 50 iter 에서 `0.1574` 까지 도달했다. 핵심은 단순 beam/temperature
sweep 이 아니라 surface discovery 였다. 후보가 long-form control, confidence fallback,
alignment/rerank 류 표면을 실제로 발견했다.

한계는 single-best 구조다. iter18 이후 global best 를 이기지 못한 후보들은 대부분
rollback 되었고, 다른 축을 개선한 후보를 portfolio 재료로 충분히 보존·조합하지 못했다.

### phase3_005

`phase3_005` 는 120 iter 에서 `0.1775` 로 멈췄다. 다양성은 늘었지만 best CER 은 더
나빴다. 단, `004` 와 `005` 는 seed, iteration 수, stitched run, explore floor, LLM
분산이 모두 달라서 한 원인으로 귀인하면 안 된다.

그래도 운영상 배울 점은 있다.

- 단순히 explore 를 늘리는 것만으로는 충분하지 않다.
- 유망 reject / micro-improvement 를 진화 재료로 저장해야 한다.
- 반복되는 dead-end family 는 식혀야 한다.
- 좋은 후보를 조금 수정하거나 다른 후보와 조합하는 구조가 필요하다.

---

## 2. 설계 원칙

1. **Best 하나만 파지 않는다.**  
   global best 는 중요하지만 유일한 parent 가 아니다. family-best, metric-best,
   promising reject 도 다음 후보의 parent 가 될 수 있다.

2. **새로운 시도를 끝까지 포기하지 않는다.**  
   후반에도 explore 비율을 15~20% 남긴다. 그렇지 않으면 다시 local optimum 주변
   튜닝으로만 수렴한다.

3. **유망 후보를 보존한다.**  
   global best 를 이기지 못해도 특정 축을 개선하면 portfolio 에 남긴다.

4. **조합과 제거를 1급 작업으로 둔다.**  
   evolution 에서는 `combine` 과 `ablate` 가 중요하다. 좋은 두 후보를 합치고,
   복잡한 후보에서 불필요한 부분을 덜어낸다.

5. **mode 선택은 harness 가 한다.**  
   LLM 이 매번 mode 를 정하면 분석이 어려워진다. mode 는 deterministic schedule +
   rule-based override 로 정한다.

6. **LLM 은 계획/구현을 하고, 평가는 harness 가 한다.**  
   score 계산, keep/reject, portfolio update, cooldown 은 LLM 자기보고가 아니라
   `score_report.json`, guard, diff, metadata 를 기준으로 harness 가 처리한다.

7. **runtime 힌트는 중립적으로 준다.**  
   알고리즘 family 는 `family_001` 같은 중립 ID 로 노출한다. 사람이 붙인
   `timestamp_alignment` 같은 private label 은 REPORT/회고용이며 prompt 에 넣지 않는다.

8. **다양성 측정은 harness-derived 기준으로 한다.**  
   candidate 가 써내는 `family_id` / `fingerprint` 는 자기보고 metadata 일 뿐이다.
   목표 1의 대표 지표인 `family_count`, `family_best_table` 은 diff keyword / AST /
   touched region 기반의 harness-derived family/signature 로 계산한다.

---

## 3. Portfolio

### 3.1 구조

`portfolio.json` 은 global best 하나가 아니라 여러 축의 진화 재료를 보존한다.

```text
runs/_summary/<job_id>_portfolio.json
  global_best
  family_best
    family_001
    family_002
    ...
  metric_best
    best_coverage
    best_substitution
    best_low_hallucination
    fast_runtime_variant
  micro_bank
  rejected_promising
```

### 3.2 Runtime 노출

candidate prompt 에는 private label 을 노출하지 않는다.

허용 예:

```text
Family A
- best CER: 0.1812
- strength: coverage improved
- weakness: substitution worsened
- recent fingerprints: [boundary, seek]
```

금지 예:

```text
Family A = timestamp_alignment
```

`timestamp_alignment`, `fallback_decode` 같은 이름은 해답성 힌트가 될 수 있으므로
사후 분석용 private label 로만 쓴다.

`family_001` 같은 runtime ID 는 candidate 자기선언이 아니라 harness-derived
cluster 에 부여한다. candidate 가 제출한 `family_id` 는 참고값으로 저장하되,
portfolio key 와 REPORT 다양성 지표의 기준으로 쓰지 않는다.

### 3.3 최소 schema

```json
{
  "job_id": "phase3_006",
  "updated_at_iter": 37,
  "global_best": "phase3_006_iter_021",
  "family_best": {
    "family_001": {
      "hyp_id": "phase3_006_iter_021",
      "status": "keep",
      "cer": 0.1812,
      "public_summary": "coverage improved; substitution neutral",
      "private_label": "operator-only, never prompt",
      "harness_signature": "sig_9a31",
      "self_declared_family_id": "family_003",
      "fingerprint": ["boundary", "seek"],
      "mode": "explore",
      "diff_path": "runs/phase3_006_iter_021/candidate.diff",
      "runtime_s": 412.0,
      "strengths": ["coverage"],
      "weaknesses": ["runtime"],
      "regression_summary": "runtime increased"
    }
  },
  "metric_best": {
    "best_coverage": {
      "hyp_id": "phase3_006_iter_028",
      "cer": 0.1901,
      "axis_metric": {"length_ratio_mean": 0.98, "del_ratio": 0.31},
      "why_keep": "best coverage axis",
      "why_not_global_best": "substitution regressed",
      "harness_family_id": "family_003"
    }
  },
  "micro_bank": [],
  "rejected_promising": []
}
```

필수 필드:

- `hyp_id`, `status`, `cer`
- `harness_family_id` 또는 portfolio slot
- `harness_signature`
- `self_declared_family_id` (있으면 저장, 신뢰 기준 아님)
- `public_summary`
- `fingerprint`
- `mode`
- `diff_path`
- `runtime_s`
- `strengths`, `weaknesses`
- `regression_summary`

저장은 풍부하게 하되 prompt 주입은 mode 별로 1~3개 후보로 제한한다.

---

## 4. Iteration Modes

### 4.1 Mode 정의

| mode | 목적 | parent 선택 |
|---|---|---|
| `explore` | 완전히 새 알고리즘 계열 찾기 | current workspace 또는 중립 baseline |
| `refine` | 기존 유망 후보의 파라미터/세부 정책 튜닝 | family_best 또는 global_best |
| `combine` | 서로 다른 두 후보 조합 | compatible family/metric 후보 2개 |
| `ablate` | 복잡한 후보에서 불필요한 부분 제거 | global_best 또는 느린/복잡한 family_best |
| `repair` | guard 실패, hallucination, runtime, 특정 축 악화 보정 | 문제를 만든 직전 후보 또는 best |
| `plateau` | no-improvement 탈출용 특수 explore | 별도 quota 없음. override 로만 발동 |

### 4.2 기본 schedule

Schedule 은 valid evaluated iteration 기준으로 계산한다. format reject / command fail 은
quota 를 소모하지 않는 편이 좋다.

| 구간 | explore | refine | combine | ablate | repair |
|---|---:|---:|---:|---:|---:|
| 0~20% | 60% | 20% | 10% | 10% | event |
| 20~70% | 35% | 35% | 15% | 15% | event |
| 70~100% | 20% | 40% | 15% | 25% | event |

초반에는 portfolio 가 빈약하므로 explore 를 강하게 둔다. 중반부터 combine 을 본격화한다.
후반에도 explore 를 남겨 새 계열 발견 가능성을 유지한다. 첫 MVP run 에서는 combine 을
15% 이하로 제한한다. `combine_success_rate` 가 충분히 나오면 다음 run 에서 30%까지
올릴 수 있다.

구현은 multi-class deterministic scheduler 로 한다. 단순 explore accumulator 를 확장해
각 mode 의 deficit 을 누적하고, 가장 deficit 이 큰 mode 를 선택하는 weighted/deficit
round-robin 방식이 적합하다. `repair` 는 quota 가 아니라 이벤트 override 로 다음
`refine` 또는 `ablate` slot 을 대체한다.

### 4.3 Rule-based override

기본 schedule 위에 다음 override 를 적용한다.

| 조건 | 동작 |
|---|---|
| compatible parent 가 2개 미만 | `combine` → `explore` 또는 `refine` |
| no improvement 8 iter 이상 | 다음 3개 중 2개를 `explore` 또는 `combine` |
| 최근 10 iter 동안 new family 0개 | 강제 `explore` |
| 같은 family 3회 reject | 해당 family cooldown |
| combine 3회 연속 실패 | combine 일시 감소, `refine`/`explore` 로 전환 |
| micro improvement 발생 | 다음 1~2회 `refine` 우선 |
| 서로 다른 axis 개선 후보 2개 이상 | `combine` 우선 |
| global best 가 복잡하거나 느림 | `ablate` 우선 |
| guard fail / hallucination 증가 / runtime 폭증 | `repair` |
| metric_best 가 새로 생김 | 해당 metric_best 를 `refine` 또는 `combine` parent 로 재사용 |

`repair` 는 고정 비율보다 이벤트 기반으로 둔다. 이벤트가 없으면 해당 slot 은
`refine` 또는 `combine` 으로 넘긴다.

여러 override 가 동시에 참이면 아래 우선순위를 적용한다.

| 우선순위 | 분류 | 예 |
|---:|---|---|
| 1 | hard repair event | guard fail, timeout, hallucination/runtime 폭증 |
| 2 | infeasible / cooldown 제거 | compatible parent 부족, combine cooldown, family cooldown |
| 3 | diversity stall | 최근 10 iter 동안 new family 0개 → 강제 `explore` |
| 4 | plateau 탈출 | no improvement 8 iter 이상 |
| 5 | opportunity override | micro improvement, axis complement, complex best, new metric_best |
| 6 | 기본 schedule | weighted/deficit round-robin |

Tie-break 는 deterministic 하게 처리한다. 먼저 불가능한 mode 를 후보에서 제거하고,
남은 mode 중 최근 덜 사용한 mode, 그 다음 deficit 이 큰 mode 를 고른다. 같은 조건이면
고정된 mode order 를 사용해 run 재현성을 유지한다.

### 4.4 Parent selection

Parent 선택은 랜덤도 아니고 LLM 자유선택도 아니다. Harness 가 portfolio / score /
metadata / diff signature 를 기준으로 deterministic shortlist 를 만든다. LLM 은 그
shortlist 안에서 계획을 세우거나, MVP 에서는 harness 가 지정한 parent 하나만 받아
구현한다.

MVP 선택 규칙:

| mode | 후보 | 선택 기준 |
|---|---|---|
| `explore` | 없음 또는 current workspace/global_best | 새 family 발견이 목적이므로 parent 영향 최소화 |
| `refine` | `global_best`, 최근 `micro_bank`, 해당 `family_best` | CER 낮음, 최근 개선 있음, runtime 정상, guard 통과 |
| `combine` | compatible 후보 pair | 서로 다른 axis 개선, diff 충돌 적음, 둘 다 guard 통과 |
| `ablate` | global_best 또는 느린/복잡한 family_best | CER 은 좋지만 runtime/복잡도 비용이 큰 후보 |
| `repair` | 직전 guard fail, hallucination 증가, runtime 폭증, axis regression 후보 | 실패 원인이 가장 명확하고 최근인 후보 |

`micro_bank` 를 parent 로 쓸 때는 `target_axis` match, guard pass, recency, runtime,
CER proximity 순으로 정렬한다. 단순히 가장 최근 후보를 쓰지 않는다.

Scoring 은 단순한 정렬 규칙으로 시작한다.

```text
candidate_score =
  cer_gain
  + axis_improvement
  + novelty
  - runtime_penalty
  - guard_or_regression_penalty
  - complexity_penalty
```

`combine` 은 pair score 로 고른다.

```text
pair_score =
  axis_complementarity
  + both_promising
  - diff_overlap
  - same_family_penalty
  - runtime_risk
```

처음 MVP 에서는 점수식을 과하게 튜닝하지 않는다. `cer`, axis 개선, guard 통과,
runtime, diff overlap 정도의 명확한 규칙으로 시작하고 REPORT 를 보고 조정한다.
`novelty` 는 기존 harness family 와의 최대 feature similarity 가 낮을수록 높게 본다.
`axis_improvement` 는 현재 mode 의 `target_axis` 에서 parent 또는 global_best 대비
개선된 정도로 계산한다.

### 4.5 Compatible parent

`combine` parent 는 LLM 자기판단이 아니라 harness-derived 휴리스틱으로 1차 필터링한다.

호환 가능 후보:

- diff touched region 이 거의 겹치지 않는다.
- 서로 다른 pipeline stage 를 건드린다. 예: audio/frontend vs postprocess, segmentation vs rerank.
- 한 후보의 strength 가 다른 후보의 weakness 를 보완한다.
- 두 후보 모두 static guard / runtime cap 을 통과한 이력이 있다.

호환 불가 후보:

- 같은 함수/상수/분기에서 서로 다른 값을 주장한다.
- 둘 다 decode call 의 동일 kwarg set 을 바꾼다.
- 한 후보가 고친 axis 를 다른 후보가 강하게 망가뜨린다.
- 둘 중 하나가 guard fail / timeout 계열이다.

LLM 은 combine 후보를 제안할 수 있지만, scheduler 의 `compatible parent` 판정은
harness 가 한다. 구현 후에는 항상 static check, smoke/eval, guard 로 검증한다.
`combine` 이 global keep 또는 micro_bank 로 성공하면 후속 `ablate` 를 enqueue 해서
두 parent 중 어떤 요소가 실제 기여했는지 줄이는 검증을 수행한다.

---

## 5. Harness-derived Family Grouping

Candidate 가 제출하는 `family_id` 는 참고값일 뿐이다. 다양성 측정, cooldown, portfolio
key 는 harness-derived family/signature 기준으로 계산한다.

### 5.1 Signature vs family

| 항목 | 의미 | 용도 |
|---|---|---|
| `harness_signature` | 거의 같은 변경인지 보는 세부 지문 | exact-repeat 감지, hard cooldown |
| `harness_family_id` | 비슷한 알고리즘 계열을 묶는 상위 그룹 | diversity, family_best, repeated family |
| `private_label` | 사람이 나중에 붙이는 설명 | REPORT/회고 전용. candidate prompt 금지 |

### 5.2 MVP feature extraction

Diff 와 metadata 에서 다음 feature 를 추출한다.

| feature | 예 |
|---|---|
| touched regions | 수정된 함수/블록, helper 이름 |
| backend/API keywords | `return_scores`, `num_hypotheses`, `align`, `detect_language` 등 |
| changed params | decode kwargs, threshold/constant 이름 |
| pipeline stage tokens | segmentation, audio_frontend, confidence_gate, rerank, postprocess 등 |
| structural markers | 새 helper 함수, fallback loop, merge/split 로직, normalization table |

예:

```json
{
  "harness_signature": "sig_9a31",
  "harness_family_id": "family_004",
  "features": {
    "touched_regions": ["decode_window", "merge_segments"],
    "api_keywords": ["return_scores"],
    "changed_params": ["temperature"],
    "stage_tokens": ["confidence_gate", "fallback"]
  },
  "self_declared_family_id": "candidate_family_x"
}
```

### 5.3 Grouping rule

MVP grouping:

1. `harness_signature` 는 normalized feature tuple 을 hash 해서 만든다.
2. 기존 family 와 feature Jaccard similarity 가 높으면 같은 family 로 배정한다.
3. similarity 가 낮으면 새 `family_NNN` 을 만든다.
4. exact signature 가 같으면 같은 family 안의 repeat/variant 로 본다.

초기 threshold 는 보수적으로 둔다. false merge 보다 false split 이 낫다. family 가
조금 많이 생겨도 REPORT 에서 사람이 private label 을 붙이며 정리할 수 있지만, 서로
다른 계열을 하나로 합치면 diversity 평가가 망가진다.
이 때문에 `family_count` 는 다양성의 상한 추정치로 해석한다. 실제 결론은
`family_best_table`, mode 성공률, private retrospective label 과 함께 본다.

### 5.4 Runtime prompt 노출

Candidate 에게는 다음만 노출한다.

```text
Family A
- public_summary: coverage improved; runtime high
- best CER: 0.1812
- strengths: coverage
- weaknesses: runtime
- recent fingerprints: [boundary, confidence]
```

다음은 노출하지 않는다.

- `private_label`
- known-answer 계열명
- 사람이 붙인 retrospective taxonomy
- eval label 에서 추출한 correction 정보

---

## 6. Prompt Steps

> **구현 현황(2026-06-01)**: 현재 구현은 **single-call MVP** 다 — mode directive 와
> parent diff 를 기존 단일 candidate prompt 에 주입한다(추가 LLM 호출 0). 아래 6.2/6.3
> 의 2-call(`Ideate+Plan -> Implement`) 분리와 plan/idea/implementation-notes 별도 저장은
> **후속**(첫 run 의 mode_success 데이터를 본 뒤 도입). 본 절은 목표 설계를 기술한다.

### 6.1 전체 흐름

```text
Harness scheduler
  -> mode 결정
  -> mode별 prompt step 실행
  -> candidate 구현
  -> harness evaluate
  -> portfolio / cooldown / report update
```

Evaluate 이후 판단은 harness-only 다. LLM 이 keep/reject 또는 portfolio update 를
결정하지 않는다.

### 6.2 Mode 별 단계

| mode | prompt 단계 | 설명 |
|---|---|---|
| `explore` | `Ideate+Plan -> Implement` | 새 family 후보를 만들고 하나를 구현 |
| `plateau` | `Ideate+Plan -> Implement` | no-improvement 탈출용 특수 explore |
| `combine` | `Plan -> Implement` | portfolio 후보 2개를 선택해 조합 |
| `refine` | `Short Plan+Implement` 또는 `Plan -> Implement` | 한 후보의 세부 튜닝 |
| `ablate` | `Plan -> Implement` | 복잡한 후보의 불필요한 부분 제거 |
| `repair` | `Short Plan+Implement` 또는 `Plan -> Implement` | guard/axis regression 보정 |

MVP 에서는 `Diagnose` 와 `Ideate` 를 분리하지 않고 `Ideate+Plan` 한 호출로 시작한다.
필요할 때만 나중에 `Diagnose -> Ideate -> Plan -> Implement` 로 확장한다.

### 6.3 Step 계약

#### Ideate+Plan

코드 수정 금지.

출력:

```json
{
  "mode": "explore",
  "target_axis": "coverage_without_substitution_regression",
  "seed_candidates": ["family_002", "metric_best.best_coverage"],
  "avoid": ["family_004"],
  "ideas": [
    {
      "idea_id": "idea_001",
      "summary": "new control strategy for under-covered windows",
      "novelty": "medium",
      "risk": "medium",
      "expected_impact": "coverage up, substitution neutral"
    }
  ],
  "selected_idea_id": "idea_001"
}
```

#### Implement

여기서만 `workspace/transcribe.py` 수정 허용. 입력은 반드시 selected idea 하나다.
여러 아이디어를 동시에 구현하면 attribution 이 깨진다.

출력:

- `workspace/transcribe.py` diff
- `candidate_meta.json`
- `implementation_notes.md`
- `self_declared_family_summary`
- `expected_metric_impact`

Refine/repair 의 `Short Plan+Implement` 도 최소한 `selected_change`, `target_axis`,
`expected_impact` 는 남긴다. 완전한 implement-only 는 사후 분석이 약해지므로 피한다.

---

## 7. Candidate Metadata

기존 discovery metadata 에 mode/evolution 필드를 추가한다.

```yaml
mode: combine
target_axis: coverage_without_substitution_regression
parent_hyp_ids:
  - phase3_006_iter_021
  - phase3_006_iter_028
family_id: family_003
fingerprint: [boundary, confidence, merge]
what_is_new: |
  Combines one parent that improved coverage with another that reduced
  hallucination; it is not another threshold-only sweep.
expected_metric_impact: |
  Coverage should improve while substitution remains neutral.
```

`family_id` 는 runtime prompt 에서 중립 ID 로만 사용한다. 사람이 붙인 private label 은
candidate prompt 에 넣지 않는다.
`family_id` 는 후보 자기보고이므로 hard gate 나 REPORT 다양성 지표의 정본이 아니다.
harness 는 diff 기반 `harness_signature` 와 `harness_family_id` 를 별도로 계산한다.

---

## 8. Banking / Micro-bank

CER 개선이 작아도 진화 재료로 가치가 있을 수 있다. 다만 11파일 eval 에서 작은 개선을
무조건 global keep 하면 overfit path-dependence 가 커진다.

따라서 두 레벨로 분리한다.

| 레벨 | 의미 |
|---|---|
| `keep` | global best 를 교체. guard/runtime/per-file non-regression 조건 필요 |
| `micro_bank` | global best 는 아니지만 strict improvement 또는 한 축 개선이 있어 portfolio 재료로 저장 |

권고:

- `BANKING_ABSOLUTE_DELTA` 를 무조건 `0.0005` 로 낮춰 global keep 하는 방식은 보류한다.
- 먼저 `best_raw` / `micro_bank` 를 저장한다.
- global keep threshold 완화는 `macro/per-file non-regression`, guard non-regression,
  runtime cap 을 같이 만족할 때만 적용한다.

---

## 9. Cooldown

Self-declared fingerprint 는 관측용으로는 유용하지만 hard gate 로는 약하다. 후보가
토큰 이름을 바꾸면 우회할 수 있다.

따라서 두 계층을 둔다.

| 계층 | 용도 |
|---|---|
| self-declared `fingerprint` / `family_id` | prompt guidance, report, soft cooldown |
| harness-derived signature | hard cooldown 후보. diff keyword / AST / touched constants 기반 |

MVP:

- exact self-declared fingerprint 2회 reject → prompt warning
- harness-derived exact signature 2회 reject → cooldown
- family 3회 reject → warning
- family 5회 reject → `what_is_new` 없으면 reject

Family clustering hard gate 는 사후 분석으로 false positive 를 확인한 뒤 강화한다.
REPORT 의 `family_count`, `family_best_table`, `repeated_failed_family_count` 는
self-declared family 가 아니라 harness-derived family 기준으로 계산한다.

---

## 10. Runtime Purity

이번 목표는 production 최적화에 가깝지만, cheating/accidental leakage 는 여전히 막아야
한다. `.claude` read-deny 는 Claude Tool 채널만 막고, `workspace/transcribe.py` 가
평가 중 파일을 읽는 문제는 별도다.

1차는 static guard 로 시작한다.

금지 후보:

- `open`, `Path.open`, `Path.read_text`, `Path.read_bytes`
- `os.listdir`, `os.scandir`, `glob`, `iglob`
- `subprocess`, `socket`, `requests`, `urllib`
- `importlib`, `__import__`, `eval`, `exec`, `compile`
- 문자열 `data/`, `docs/`, `runs/`, `.git`, `baseline/`, `judge/`, `assets/`

단, 전역 runtime monkeypatch 는 즉시 적용하지 않는다. `frozen.load()` / tokenizer /
model cache 의 정상 파일 읽기를 깨뜨릴 수 있기 때문이다. runtime allowlist 는 후보 코드
I/O 와 모델 내부 I/O 를 구분할 수 있을 때 후속으로 도입한다.

---

## 11. Holdout / Overfit Boundary

Portfolio 는 eval 11파일에서 나온 다양한 재료를 저장하므로 overfit 표면을 넓힐 수 있다.
따라서 holdout 사용 경계를 명확히 둔다.

- 기본은 job-end holdout 1회다.
- in-loop holdout 결과를 candidate prompt, scheduler, keep/reject, portfolio update 에
  사용하지 않는다.
- 필요하면 operator-only shadow validation 으로 중간 holdout spot-check 를 실행할 수
  있다. 이 경우 결과는 사람이 run 중단 여부를 판단하는 참고값일 뿐, candidate 에게
  노출하거나 harness policy 에 주입하지 않는다.
- holdout 을 반복적으로 policy 에 사용하면 holdout 도 validation set 으로 오염된다.

---

## 12. Reporting

Best CER 만으로는 목표 1(다양한 탐색)을 평가할 수 없다. REPORT 에 다음을 추가한다.

| 지표 | 의미 |
|---|---|
| `best_cer` | 최종 성능 |
| `family_count` | 시도한 알고리즘 family 수 |
| `family_best_table` | family 별 best CER / strengths / weaknesses |
| `mode_distribution` | explore/refine/combine/ablate/repair 비율 |
| `mode_success_rate` | mode 별 keep/micro_bank/promising reject 비율 |
| `combine_success_rate` | combine 이 global/micro 개선으로 이어진 비율 |
| `micro_bank_count` | 작은 개선 또는 axis 개선 후보 수 |
| `repeated_failed_family_count` | 반복 실패 family |
| `portfolio_usage` | parent 로 재사용된 후보 수 |
| `iter_to_0.20/0.18/0.16` | 성능 구간 도달 속도 |

이 지표는 candidate runtime reward 로 보여주지 않는다. operator/report 용이다.
`family_count` 와 `family_best_table` 은 self-declared family 가 아니라
harness-derived family/signature 기준으로 계산한다.
`iter_to_0.20/0.18/0.16` 은 in-loop eval progression 지표이며, generalization 지표가
아니다. 일반화 판단은 job-end holdout 및 별도 operator 분석으로만 한다.

### 12.1 Decision trace

REPORT 집계의 정본 입력은 `runs/_summary/<job_id>_decisions.jsonl` 로 둔다.
이 파일은 append-only 이며 **candidate iteration 당 한 줄**을 남긴다 — score_report
가 나오기 전 early reject(command fail / format reject / scope violation /
verify fail)도 기록하되 `evaluated=false` 와 `attempt_status` 로 구분한다(리뷰 #4/#9).

목적은 candidate 자기보고와 harness 실제 결정을 분리하는 것이다. `candidate_meta.json`
은 LLM 이 주장한 family/fingerprint/intent 를 저장하고, `decisions.jsonl` 은 harness 가
실제로 사용한 mode, parent, cooldown, signature, keep/reject 판단을 저장한다.

최소 schema:

```json
{
  "iter": 37,
  "hyp_id": "phase3_006_iter_037",
  "policy_version": "portfolio_v1",
  "config_hash": "sha256:...",
  "scheduled_mode": "refine",
  "override_candidates": ["two_axis_candidates_available"],
  "override_applied": "combine",
  "chosen_mode": "combine",
  "deficits_before": {"explore": 0.2, "refine": -0.1, "combine": 0.4},
  "active_cooldowns": ["family_004"],
  "parent_shortlist": [
    {"hyp_id": "phase3_006_iter_021", "score": 0.42, "why": ["best_coverage"]},
    {"hyp_id": "phase3_006_iter_028", "score": 0.31, "why": ["best_substitution"]}
  ],
  "parents_chosen": ["phase3_006_iter_021", "phase3_006_iter_028"],
  "compatible_check": {"passed": true, "diff_overlap": 0.05},
  "harness_signature": "sig_9a31",
  "harness_family_id": "family_004",
  "final_decision": "micro_bank",
  "decision_reason": "coverage improved; cer delta below keep threshold",
  "cer": 0.1771
}
```

`scheduled_mode`, `override_*`, `deficits_before`, `parent_shortlist`,
`active_cooldowns` 는 iter 시작 시점의 scheduler 판단이다. `harness_signature`,
`harness_family_id`, `final_decision`, `cer` 는 candidate diff/eval 이후에 채워지는
post-eval field 다. 한 줄에 같이 저장하되, replay 할 때는 pre-decision field 와
post-eval field 를 구분해서 읽는다.

이 trace 가 있어야 "의도와 다른 mode 가 돌았다"를 scheduler 버그, schedule 설계,
portfolio 상태, LLM 구현 실패 중 어디로 볼지 분리할 수 있다. `mode_distribution`,
`mode_success_rate`, `combine_success_rate`, `portfolio_usage` 는 이 파일을 집계해
계산한다.

---

## 13. Budget Unit

본 proposal 에서 `1 iter` 는 **평가된 candidate 1개** 를 뜻한다. LLM 호출 수와 같지
않다.

예:

- `refine` / `repair` 의 `Short Plan+Implement`: 보통 1 LLM call
- `combine` 의 `Plan -> Implement`: 보통 2 LLM calls
- `explore` / `plateau` 의 `Ideate+Plan -> Implement`: 보통 2 LLM calls

따라서 50 evaluated iterations 는 50회 평가지만, LLM call 은 약 70~100회가 될 수 있다.
REPORT 에는 `evaluated_iter_count` 와 `llm_call_count` 를 둘 다 기록한다.

---

## 14. Judge Metric Preconditions

`metric_best` 는 현재 judge 가 다음 corpus-level metric 을 제공한다는 전제에서 동작한다.

- `error_breakdown.sub_ratio`
- `error_breakdown.del_ratio`
- `error_breakdown.ins_ratio`
- `length_ratio.mean/p05/p95`
- `hallucination_hit_rate`
- `repeated_text_rate`
- `audio_coverage_rate`
- `total_inference_time_s`

이 metric 들은 현재 `judge/metrics.py` 와 `score_report.json` 경로에서 사용 중인 값이다.
새 metric slot 을 추가할 때는 먼저 judge output 존재 여부를 확인한다.

---

## 15. 구현 순서

### Step 1 — Visibility + low-risk policy

- mode metadata 저장
- `runs/_summary/<job_id>_decisions.jsonl` append-only decision trace 추가
  - `scheduled_mode` / `chosen_mode`
  - `override_candidates` / `override_applied`
  - `deficits_before`
  - `parent_shortlist` / `parents_chosen`
  - `active_cooldowns`
  - `harness_signature` / `harness_family_id`
  - `final_decision` / `decision_reason`
- `best_raw` / `micro_bank` 저장
- harness-derived signature/family MVP
- REPORT 에 family/mode/micro_bank 지표 추가
- explore floor 를 보수적으로 조정하되, 새 scheduler 전에는 최소 변경만 적용

### Step 2 — Portfolio MVP

- `portfolio.json` 생성
- `global_best`, `family_best`, `metric_best`, `micro_bank`, `rejected_promising`
  최소 저장
- prompt 에 mode 관련 portfolio 후보만 제한 주입

### Step 3 — Scheduler MVP

- valid evaluated iteration 기준 schedule
- 0~20 / 20~70 / 70~100% 비율 적용
- repair event override
- combine parent 부족 시 fallback
- compatible parent 휴리스틱

### Step 4 — Prompt steps

- 기본 `Plan -> Implement`
- explore/plateau 는 `Ideate+Plan -> Implement`
- refine/repair 는 `Short Plan+Implement`
- plan/idea/implementation notes 저장

### Step 5 — Cooldown

- self-declared fingerprint soft warning
- harness-derived signature MVP
- repeated family report

### Step 6 — Runtime purity static guard

- `workspace/transcribe.py` static guard 확장
- runtime allowlist 는 후속

### Step 7 — Run 50~100 evaluated iter

- same harness 조건으로 1회 실행
- 결과를 REPORT/retrospective 로 분석
- 필요하면 같은 조건 반복으로 variance 확인

---

## 16. 미결 결정

1. 첫 run 을 50으로 할지 100으로 할지
2. global keep threshold 를 기존대로 둘지, non-regression 조건부 완화를 넣을지
3. plan/implement 를 같은 `claude -p` 세션에서 이어갈지, 별도 호출로 분리할지
4. harness-derived signature 를 diff keyword 로 시작할지 AST 로 시작할지
5. portfolio prompt 주입 상한을 몇 개로 둘지
6. 중간 holdout shadow validation 을 할지, job-end holdout 만 할지

추천 기본값:

- 첫 run: 50 iter 로 MVP 검증 후 필요시 100
- global keep: 기존 threshold 유지 + `micro_bank` 먼저
- prompt steps: 별도 호출, 단 `refine/repair` 는 Short Plan+Implement 한 호출
- signature: diff keyword MVP
- portfolio prompt 주입: 1~3개
- holdout: job-end 기본, 중간 결과는 operator-only shadow 로만 허용
