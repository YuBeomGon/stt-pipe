# Proposal — Explore DIVERGE directive (Increment 1) + protected refinement block (deferred)

- 상태: **Increment 1 구현 완료** (`_EXPLORE_DIRECTIVE` 강화). 전체
  explore-block/speciation 은 **보류 (Increment 1 결과에 contingent)**.
- 작성: 2026-06-04 · 개정: 2026-06-04 (서브에이전트 리뷰 + 구현 중 발견 반영)
- 목적: explore 가 챔피언(best)에 앵커돼 사실상 refine 으로 동작하는 구조적 한계(F2)를
  없앤다. 1차로는 **explore directive 문구만** "현재 알고리즘과 근본적으로 다른 시도를
  하라"로 강화하는 최소 변경(Increment 1)으로 가설을 검증한다(후보는 챔피언을 그대로 보되
  베끼지 말고 발산하도록). 갓 태어난 구조를 보호하는 육성 블록 등 더 큰 기계(2-phase,
  lineage rollback)는 그 결과를 보고 결정한다. **초안의 "시야 비앵커(stub-swap)"는 C1 을
  되살려 폐기**(§2.1).
- 선행 자료:
  [`../reviews/2026-06-04-evolve-design-review.md`](../reviews/2026-06-04-evolve-design-review.md)
  (정체 근본원인 F1~F7),
  [`../reviews/2026-06-04-doc-code-consistency-audit.md`](../reviews/2026-06-04-doc-code-consistency-audit.md),
  [`2026-06-01-from-scratch-discovery-harness.md`](2026-06-01-from-scratch-discovery-harness.md)
  (포트폴리오/모드/패밀리 기반)
- 전제: **Phase 1 trio 이미 반영·검증** (best monotone / discovery-floor count-cap /
  dead-end 기억 — commit `2d1ee5c`). phase3_013 에서 0.1539 도달(기존 0.177 정체 돌파).

---

## 0. 문제 (F2 — explore 앵커링)

설계 검토 F2: `build_candidate_prompt` 가 매 iter **현재 on-disk best 코드를 inline**
(`_load_workspace_body`)하고, keep/reject 가 best 로 rollback 하므로 — explore 를
포함한 **모든 모드가 best 위에서 편집**한다. 그래서:

1. **explore 가 사실상 refine 이다.** 발산(새 구조)이 아니라 incumbent 변형. 관측:
   phase3_013 에서 explore 가 한 번 좋은 걸 찾으면 그 위에서만 계속 개선(래칫) — 같은
   basin 에 갇힘.
2. (가설) 단순 phase3_004(0.157, near-scratch 반복)보다 못한 basin 에 수렴했던 한 원인.

---

## 1. 리뷰 결과 — 전체 블록 설계는 보류

서브에이전트 리뷰가 초안의 "explore = on-disk 를 stub 으로 리셋 + git diff 캡처 + 블록
내 lineage-head rollback + 2-phase" 설계에서 **치명 결함 3개**를 확인했다. 그대로 구현하면
진단 파이프라인이 오염되므로 **전체 설계는 채택하지 않는다.**

### C1 (showstopper) — stub 리셋이 diff base 를 깬다
`candidate.diff` 는 `git diff`(HEAD=커밋된 챔피언 기준)로 뽑는다. explore 슬롯에서
on-disk 를 stub 으로 리셋한 뒤 후보가 편집하면, diff 는 "stub→새 구조" 가 아니라
**"챔피언 전체 삭제 + 새 구조 추가"** 가 된다. 그러면 signature/family/cooldown 입력이
오염돼 — 서로 다른 새 구조라도 "챔피언 삭제분" 을 공유해 비슷한 family 로 묶인다(다양성
측정이 정반대로 망가짐). **diff base 를 정의하지 않은 것이 근본 구멍.**

### C2 — lineage-head rollback 이 핵심 불변식을 깬다
"on-disk == 커밋된 챔피언" 불변식에 reject 경로 ~10곳 + `ensure_worktree_ready` +
`commit_iteration` 이 의존한다. 블록 중 갓난 구조를 챔피언으로 되돌리지 않으면 다음 iter 가
dirty worktree 로 abort 되거나 비챔피언이 몰래 커밋된다. 블록 중간 상태의 중단/재개
재구성도 미정이었다.

### C3 — 생존 게이트 전제가 사실오류
초안의 baseline 0.4685 는 보관된 옛값(stale)이다. 실제 `baseline/target_cer.json` =
**`baseline_cer` 0.17143**, 챔피언은 0.1539 로 그 아래. 게다가 near_best 는 **이미
champion-relative (`best × 1.20`, cap 24)** 라, 초안 §4 가 걱정한 "near_best 풀 bloat"
은 이미 해결돼 있다.

### 부차 (I)
- I2: Phase A 의 50-iter explore-block 이 Phase 1 trio 의 discovery-floor count-cap(8)을
  무력화한다(두 정책 충돌).
- I3: cooldown 이 보호 중인 갓난 후보의 비개선까지 벌점으로 집계한다.
- I4: 4-슬롯 무조건 보호는 토큰 낭비(개선이 멈춘 lineage 도 끝까지 돌림).

---

## 2. Increment 1 — 채택 (explore directive 강화, "DIVERGE")

**가설:** explore 가 앵커돼 있다(F2). **검증 방법:** invariant 도 진단 파이프라인도
하나도 건드리지 않고, **explore directive 문구만** "현재 알고리즘과 근본적으로 다른 시도를
하라"로 강하게 바꾼다.

### 2.1 왜 stub-swap(시야 비앵커)을 버렸나
초안의 "explore 슬롯에서 챔피언을 숨기고 stub 을 보여준다"는 **C1 을 다른 경로로 되살린다.**
de-anchor 된 후보는 자연히 파일을 **통째로 새로 쓰고**(Write), 그러면
`candidate.diff = git diff(HEAD=챔피언)` 가 "챔피언 전체 삭제 + 새 구조 추가"가 된다.
실측: 현 챔피언 기준 **removed-side 토큰 51개**(모든 explore 가 공유) vs added-side ~12개
→ 서로 완전히 다른 구조라도 Jaccard ≈ 0.72 ≫ 0.5 로 **전부 한 family 로 false-merge.**
이건 (a) Increment 1 의 측정("다른 family 가 나오나?")을 항상 "아니오"로 만들고,
(b) cooldown(family 5회 비개선 → explore 전체 회피 경고)과 diversity_stall 을 오작동시킨다.
**stub-swap 은 prompt-only 라도 diff-base 오염을 피할 수 없다** → 폐기.

### 2.2 채택안 — directive 만 강화
`_EXPLORE_DIRECTIVE` 에 **DIVERGE** 지시를 추가한다:
- "현재 파이프라인을 *개선*하지 마라. 위에 보이는 챔피언 코드는 **무엇을 반복하지 말지**
  보라고 보여줄 뿐. 디코딩 전략/세그멘테이션/백엔드 반환 채널 사용/공략 error-axis 중
  **근본적으로 다른 접근**을 골라라. '같은 파이프라인 + 노브 하나'면 이 슬롯엔 틀린 수다."
- "one focused change/no refactor 규칙은 explore 에서 **정지** — 파이프라인의 큰 부분을
  교체하는 구조적 새 메커니즘이 바로 이 슬롯이 원하는 것."

### 2.3 핵심 — 후보는 챔피언을 그대로 본다 (C1 회피)
챔피언 코드는 **계속 inline 으로 보인다.** 후보는 그것을 Edit 하므로 `git diff` 는
정상적인 "챔피언→후보 변경"이고, 변경이 크든 작든 **실제 한 그 변경**을 반영한다(인위적
챔피언-삭제 noise 없음). signature/family/cooldown/diversity 전부 온전. on-disk·diff
base·rollback·commit·state·scheduler·portfolio·keep/reject **전부 불변.** 새 파일도
state 도 0.

> stub-swap 은 "시야"를 떼려다 기록을 깼다. 이번 안은 시야는 두고 **지시 강도**만 올린다.
> diff 가 정상이라 측정이 살아있다.

### 2.4 측정 (성공/실패 판정)
- explore 슬롯의 `decisions.jsonl` family/signature 가 **챔피언과 구조적으로 다른
  family** 를 실제로 더 만들어내는가? (anchored 평소보다 new-family 율 ↑)
- CER 또는 family 다양성이 phase3_013(trio, 0.1539) 대비 개선되는가?
- 분기:
  - **다른 family 가 나오는데 첫 평가에서 죽더라** → 그때 보호 블록(§3)을 C1/C2 풀어서
    구현할 근거가 생긴다.
  - **이미 CER↓ 또는 family 넓어짐** → 블록 기계 불필요.
  - **변화 없음(여전히 같은 basin)** → 지시 강화로는 부족 → 보호 블록(§3) 검토.

### 2.5 영향·리스크
- 영향: `harness/runner.py` 의 `_EXPLORE_DIRECTIVE` 문구뿐. candidate.md 는
  mode-agnostic(SSOT §2)대로 불변.
- 새 파일 0, 새 state 0, invariant·diff 파이프라인 변경 0. 리스크 ≈ 0.
- keep/reject 도 그대로 — best 추월 → 승격, `best×1.20` 이내 → near_best parent 보존,
  그 밖 → reject. **C3 대로 near_best 가 champion-relative 생존 게이트를 이미 수행**하므로
  새 게이트를 추가하지 않는다.

---

## 3. (보류) 전체 explore-block speciation — Increment 1 결과에 contingent

아래는 **Increment 1 이 "다른 family 가 생기지만 첫 평가에서 죽는다" 를 보였을 때만**
착수한다. 착수 시 위 C1/C2/I2~I4 를 먼저 해소해야 한다.

### 3.1 보호된 육성 블록
explore 를 단독 1-iter 가 아니라 **블록**(explore + develop ×N, 최소 4)으로 돌려 갓난
구조를 1-shot 으로 죽이지 않는다. develop 은 직전 결과가 verify_fail 이면 repair, 아니면
refine. base/rollback 기준은 **블록 lineage head**.

### 3.2 미해결 설계 과제 (착수 전 필수)
- **C1**: explore 의 diff base 를 정의한다. (옵션: stub 을 임시 커밋해 HEAD 로 만들고
  블록 종료 시 정리 / 별도 base ref 로 `git diff --no-index` / diff base 를 명시 인자로.)
- **C2**: lineage-head 운영 동안 "on-disk == 커밋" 불변식을 어떻게 유지·재구성할지
  (블록 상태 state persist + 재개 시 lineage head 재materialize) 명세.
- **I2**: discovery-floor(trio R-B)와 Phase A 블록 모델을 하나로 통합(둘 다 explore 강제
  → 충돌). Phase A 를 floor 의 상위 개념으로 흡수하거나 floor 를 끈다.
- **I3**: 블록 내 보호 중 newborn 의 비개선은 cooldown 집계에서 제외.
- **I4**: 블록 보호를 조건부로(연속 개선 없으면 조기 종료) — 무조건 N-슬롯 금지.
- 생존 게이트: 별도 baseline 게이트 대신 **near_best(champion-relative) 재사용**으로 통일
  (C3). 0.4685 같은 stale 절대값 쓰지 않는다.

### 3.3 2-phase / config knob (보류)
Phase A(구조 생성, `STRUCTURE_PHASE_FRAC`) vs Phase B(활용 + explore floor). knob 표는
착수 시 I2 통합 결과에 맞춰 재작성한다(초안의 절대 baseline·고정 블록크기 값은 폐기).

---

## 4. 채택 시 흡수 (SSOT §5)

Increment 1 이 검증되면(또는 §3 착수 시) PHASE3-PLAN §4 와 candidate.md(explore 비앵커)
에 정본화하고 본 proposal 은 결정 이력으로 남긴다.
