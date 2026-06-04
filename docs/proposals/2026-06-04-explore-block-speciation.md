# Proposal — Explore-Block Speciation (de-anchored discovery + protected refinement)

- 상태: **Draft — 사용자 합의 완료, 구현 대기**
- 작성: 2026-06-04
- 목적: explore 가 챔피언(best)에 앵커돼 사실상 refine 으로 동작하는 구조적 한계(F2)를
  없애고, 갓 태어난 새 구조를 한 번의 평가로 버리지 않도록 **보호된 육성 창**을 주어,
  `corpus_cer 0.157` basin 고착을 벗어나 더 낮은 다른 구조로 점프할 여지를 만든다.
- 영향 예상: `harness/scheduler.py`, `harness/runner.py`, `harness/state.py`,
  `harness/config.py`, `harness/portfolio.py`, `harness/prompts/candidate.md`,
  `docs/PHASE3-PLAN.md`
- 선행 자료:
  [`../reviews/2026-06-04-evolve-design-review.md`](../reviews/2026-06-04-evolve-design-review.md)
  (정체 근본원인 F1~F7),
  [`../reviews/2026-06-04-doc-code-consistency-audit.md`](../reviews/2026-06-04-doc-code-consistency-audit.md),
  [`2026-06-01-from-scratch-discovery-harness.md`](2026-06-01-from-scratch-discovery-harness.md)
  (포트폴리오/모드/패밀리 기반)
- 전제: **Phase 1 trio 이미 반영·검증** (best monotone / discovery-floor count-cap /
  dead-end 기억 — commit `2d1ee5c`). phase3_013 에서 0.1539 도달(기존 0.177 정체 돌파).
  본 제안은 그 위에 얹는 **다음 레이어**다.

---

## 0. 문제

설계 검토 F2: `build_candidate_prompt` 가 매 iter **현재 on-disk best 코드를 inline**
(`_load_workspace_body`)하고, keep/reject 가 best 로 rollback 하므로 — explore 를
포함한 **모든 모드가 best 위에서 편집**한다. 그래서:

1. **explore 가 사실상 refine 이다.** 발산(새 구조)이 아니라 incumbent 변형. 관측:
   phase3_013 에서 explore 가 한 번 좋은 걸 찾으면 그 위에서만 계속 개선(래칫) — 같은
   basin 에 갇힘. 단순 phase3_004(0.157, near-scratch 반복)보다 못한 basin 에 수렴했던
   원인.
2. **갓 태어난 구조를 1-shot 으로 죽인다.** 새 구조는 첫 평가에서 거칠어 best 를 못
   넘기 쉽고, 그러면 즉시 rollback 돼 사라진다 — 몇 번 다듬으면 빛날 구조도 폐기.

## 1. 설계 개요 — 2-phase + explore block

### 1.1 explore block (보호된 육성 창)
explore 를 **단독 1-iter 가 아니라 블록**으로 돌린다.

- 블록 = **explore + (블록크기−1) develop**, 최소 4 (Phase A). 예:
  `explore · refine · refine · refine` 또는 `explore · repair · refine · refine`.
- **explore 슬롯**: on-disk workspace 를 **stub 으로 리셋**하고 프롬프트엔 stub +
  findings ledger 만 inline(**best 숨김 = 비앵커**). 후보가 빈 구조부터 새로 설계.
- **develop 슬롯**: 기본 **refine**. 단 직전 슬롯 결과가 **에러(verify_fail)면 그 슬롯은
  repair**, 이후 다시 refine. develop 의 base 는 **글로벌 best 가 아니라 이 블록의
  lineage head**(= 블록 안에서 지금까지 가장 좋은 그 구조).
- 블록 내내 **rollback 기준 = lineage head**(챔피언 아님). 즉 첫 explore 가 best 보다
  나빠도 **버리지 않고** 그 위에서 develop 한다.

### 1.2 블록 종료 판정 (생존 게이트)
블록의 lineage 최고 후보를 가지고:
- **baseline(sealed stub `target_cer.json`, ~0.4685) 못 넘으면 → 블록 통째 폐기**,
  on-disk 챔피언 복귀. (쓰레기 구조는 parent 로도 안 남김.)
- 넘으면:
  - 글로벌 best 추월 → **best 승격**(commit).
  - 아니면 → **near_best parent 로 bank**(refine/combine 재료로 보존), on-disk 챔피언 복귀.

### 1.3 Phase A / Phase B
- **Phase A — 구조 생성** (앞 `STRUCTURE_PHASE_FRAC`, 기본 0.5): explore block 반복으로
  **다양한 구조 baseline 군**을 쌓는다. 사실상 explore-block 지배.
- **Phase B — 활용** (나머지): exploit 위주 믹스 + **explore floor 유지(0 아님)**.
  대략 explore ~20% / refine ~30% / combine ~30% / ablate ~20% (knob). Phase B 의
  explore 도 비앵커 블록을 쓰되 **토큰 절약 위해 짧게**(explore+2).

explore 는 **전 구간 존재**하되 A 高 · B 低. combine/ablate 는 Phase A 에선 안 돌고
Phase B 에서만(구조가 쌓인 뒤 조합이 의미 있으므로).

## 2. 파라미터 (config knob)

| knob | 기본 | 의미 |
|---|---|---|
| `STRUCTURE_PHASE_FRAC` | 0.5 | Phase A(구조 생성) 비율. "50 은 예시, 일정 비율" |
| `EXPLORE_BLOCK_A` | 4 | Phase A 블록 크기(explore+3) |
| `EXPLORE_BLOCK_B` | 3 | Phase B 블록 크기(explore+2) |
| `BLOCK_SURVIVAL_VS_BASELINE` | True | 블록 끝 생존 게이트 = baseline 초과 |
| Phase B mix | explore .20 / refine .30 / combine .30 / ablate .12 | knob |

## 3. 구현 영향

- **scheduler.py**: Phase A 를 explore-block 생성기로 재구성(기존 확률 schedule 대체),
  Phase B 는 저-explore 믹스 + 블록. 블록 내 슬롯 진행은 **결정적**(state 의 블록 상태로
  replay 가능). discovery-floor(Phase 1 R-B)는 Phase A 블록 모델로 흡수/대체.
- **runner.py**: 블록 동안 **rollback 기준을 챔피언 → lineage head 로 전환**, explore
  슬롯에서 **on-disk 를 stub 으로 리셋 + 프롬프트 비앵커**(`_load_workspace_body` 분기),
  블록 종료 시 생존 게이트 적용 후 best 승격 / near_best bank / 폐기.
- **state.py**: **블록 lineage 상태 persist**(어느 블록·몇 번째 슬롯·lineage head hyp /
  cer) — 중단/재개 안전.
- **portfolio.py**: 생존 lineage 의 near_best admission(기존 메커니즘 재사용, 게이트만
  baseline 기준으로 명확화).
- **candidate.md**: explore 비앵커 의미 명시(“explore 는 stub 에서 새 구조를 설계, 현재
  파이프라인은 보이지 않는다”). exploit 모드는 기존대로.

## 4. 리스크 / 주의

1. **토큰**: 블록당 4(또는 3) 콜. Phase A 50% → ~12 블록×4 ≈ 48 콜. 1잡만 권장.
2. **baseline 바가 낮다**(0.47): 웬만한 장형 구조면 통과 → near_best 풀이 커짐. Phase B
   에서 약한 구조 혼입 가능 → near_best factor(1.20)로 정리, 필요시 게이트 강화.
3. **결정성/resume**: 블록 상태 persist 필수(미persist 시 재개가 블록 중간을 깸).
4. **clean A/B 아님**: Phase 1 trio + 본 변경이 겹치므로, phase3_013(trio only) vs 차기
   잡(trio+block)으로 분리 해석.

## 5. 성공 기준

- phase3_013(trio, 0.1539) 대비 **차기 잡이 더 낮은 CER 또는 더 다양한 구조(family) 도달**.
- explore 가 best 와 **구조적으로 다른** 후보를 실제로 생성(decisions.jsonl 의 explore
  슬롯 diff 가 stub-기반인지 확인).
- 블록 생존율·평균 lineage 개선폭을 analyze_run 에 노출.

## 6. 채택 시 흡수 (SSOT §5)

채택되면 PHASE3-PLAN §4(Iteration 흐름)에 block/2-phase 를 정본화, config knob 표 추가,
candidate.md explore 비앵커 반영, 본 proposal 은 결정 이력으로 남긴다.
