# Phase 2 백로그 — 라이브 검증 중 발견사항 (누적 기록)

> **목적:** phase1.5 착지 이후 라이브 스모크/실험을 돌리면서 발견한 개선거리·튜닝거리를
> **그때그때 한 줄이라도 기록**해 두고, **나중에 phase2(또는 별도 패치)에서 한 번에 반영**한다.
> 이 문서는 "지금 고치지 않기로 한 것"의 단일 출처다. 항목마다: 관찰 → 근거(데이터/코드 위치)
> → 제안 → 상태.
>
> 이미 다른 문서가 추적 중인 항목은 여기서 **중복하지 않고 링크만** 한다.
>
> 관련 문서:
> - 구현 플랜: [`docs/superpowers/plans/2026-06-05-phase2-3-worktree-parallel-promotion.md`](../superpowers/plans/2026-06-05-phase2-3-worktree-parallel-promotion.md)
> - set 동작지도: [`docs/HARNESS-MECHANICS.md`](../HARNESS-MECHANICS.md) §12
> - phase1.5 commit 정책: `docs/HARNESS-MECHANICS.md` §3

---

## 0. 검증 베이스라인 — phase3_016 스모크 (2026-06-05)

phase1.5 착지(커밋 `def55ce`..`710912a`, suite 284 pass) 직후, stub에서 fresh로
`--set-budget 4 --max-repairs 2 --max-refines 3`, 10 iter 라이브 실행(`phase3_016`).

**phase1.5 자체는 라이브에서 정상 작동 확인:**
- **코드 전용 커밋**: iter1~10 모든 커밋이 `workspace/transcribe.py` 단일 파일. metadata churn
  소멸 (예전엔 매 iter HISTORY/decisions/state/portfolio 동반 커밋).
- **false scope-reject 0건**: 모든 iter verify OK + 정상 커밋. phase3_015를 마비시킨 라이브
  스코프 오탐 재발 없음 (스냅샷-diff 가드가 기존 ignored 파일을 오탐하지 않음).
- **C2 육성 라이브 작동**: iter6 explore가 champion(0.16047)보다 나쁜 0.17010 → 버려지지 않고
  `lineage_advance`로 육성, set이 refine 진입. champion ref는 iter5에 고정(나쁜 lineage가
  pool-inert라 미오염). phase3_014가 못 하던 동작.

**iter별 set 전이 (decisions.jsonl 기준):**

| iter | override | final | cer | 비고 |
|---|---|---|---|---|
| 1–5 | (scheduler) | keep | 0.41135→0.18061→0.16265→0.16076→**0.16047** | 연속 promote, champion 전진 |
| 6 | scheduled | lineage_advance | 0.17010 | champion보다 나쁨 → **set 열림**(refine 진입) |
| 7 | set:refine | lineage_advance | 0.16377 | refine #1 |
| 8 | set:refine | lineage_advance | 0.16288 | refine #2 |
| 9 | set:refine | **reset** | 0.16179 | refine #3 → **max_refines=3 소진 → set 닫힘** |
| 10 | scheduled | lineage_advance | 0.16044 | set 닫힌 뒤 **스케줄러 독립 refine**, 새 set(set_id=7) 열림 |

(주의: "refine 4번"처럼 보이지만 set 내부 refine은 7/8/9 **3번**으로 budget 준수. iter10은
`override=set:refine`이 아니라 `scheduled` — set과 무관한 스케줄러 자체 모드 선택.)

---

## F1 — refine budget이 "아직 개선 중인" lineage를 끊는다

**관찰:** phase3_016에서 lineage가 **단조 개선**(iter6 0.17010 → iter7 0.16377 → iter8 0.16288
→ iter9 0.16179)으로 champion 0.16047에 점점 근접하던 중, iter9에서 `max_refines=3`을 다 써서
`reset`(워크트리를 champion으로 복원)으로 종료. 한 step만 더 갔으면 champion을 넘었을 수 있는데
budget이 끊음.

**근거:**
- `harness/config.py` `SET_MAX_REFINES=3`, `SET_MAX_REPAIRS=2`.
- `harness/lineage.py` `step_set` — refine budget 소진 시 promote 못 했으면 `reset`.
- 데이터: §0 표 iter7~9.

**제안 (택1/조합, phase2에서 결정):**
1. `--max-refines` 기본값을 4~5로 상향 (가장 싼 변경, 단 무한 refine 방지 위해 dead_end 가드
   유지).
2. **"still-improving → budget +1" 규칙**: refine이 직전 대비 의미 있는 개선(≥ keep_delta_eps)을
   계속 내는 동안에는 refine budget 카운트를 소비하지 않거나 1회 연장. 개선이 멈춘(hold) 순간만
   budget 소비. → "근접 중인데 끊김"을 구조적으로 방지.
3. (보수적) 그대로 두고 `--max-refines` 운영 파라미터로만 조절.

**상태:** 미구현. phase2 후보. (지금은 `--max-refines` 올려서 운영으로 우회 가능.)

---

## F2 — 근접(near-champion) lineage가 명시적으로 parent로 보존되지 않는다

**관찰 (사용자 제기):** champion 근방까지 간 lineage를 `reset`으로 버리면, 그 진화 경로가 이후
combine/refine의 parent 후보로 재사용되지 못한다. "근방까지 갔으면 버리지 말고 parent pool에
남겨서 나중에 조합 등에 불릴 수 있어야 한다."

**현재 동작 (문서 §12 + 코드 확인):**
- `lineage_advance`(set 내부 체크포인트)는 **의도적으로 portfolio pool-inert** — `portfolio.py`
  `update()`에서 `decision_status != "lineage_advance"` 가드로 어떤 풀(global_best/family/
  near_best)에도 안 들어감. 이유: champion보다 나쁜 explore가 portfolio를 오염시키는 것 방지
  (C2/C1 가드, review C-2). → **중간 lineage head(iter6/7/8)는 parent 후보가 안 되고, 코드도
  reset 시 champion으로 롤백되어 사라짐.**
- **그러나 `reset` iter는 `lineage_advance`가 아니므로** near_best 평가를 받는다. champion ×
  factor 이내면 `near_best`에 보존되고, `near_best`는 `_ranked_pool`(combine/refine parent 풀,
  `portfolio.py` `candidate_pool`/`parents_for_mode`)의 일부다.
- 실측 (phase3_016 portfolio.json): `near_best = [iter5 0.16047, iter4 0.16076, **iter9
  0.16179**, iter3 0.16265, iter2 0.18061]` — **iter9(=reset 시점, 이 lineage의 best)는 실제로
  near_best에 들어가 있어 향후 combine parent로 쓰일 수 있다** (그 diff가 `runs/iter9/`에 잔존).

**갭 (= 사용자 우려의 핵심):**
- near_best 보존은 **"reset 시점 후보 한 방"만** 받는다. 이번엔 lineage가 단조개선이라 reset
  시점(iter9)이 곧 lineage best여서 운 좋게 잡혔다. **lineage best가 중간(예: iter8)이었고 이후
  살짝 후퇴한 뒤 reset됐다면, 그 중간 best는 `lineage_advance`=pool-inert라 유실**된다.
- "근접한 lineage를 **명시적으로** parent로 승격/보존"하는 규칙은 문서·코드에 **없다**. 지금은
  reset 시점이 우연히 near_best 컷에 들면 살아남는 식 (incidental, not by design).

**제안 (phase2):**
1. **set 종료 시 lineage best를 parent pool에 명시적으로 등록.** set이 promote 없이 닫힐 때
   (`reset`), 그 set의 `set_best_hyp_id`/`set_best_cer`가 champion × factor 이내면 near_best
   (또는 전용 `lineage_survivors` 풀)에 **lineage best 자체를** 등록 — reset 시점이 아니라 set이
   기록해 둔 lineage best를 쓴다 (`state.set_best_*` 이미 존재). → 중간 best 유실 방지.
2. pool 오염 우려 대응: 등록 대상을 "champion 근방(× factor 이내)"으로 **한정**하면, C2/C1이
   막으려던 "champion보다 한참 나쁜 explore 오염"은 여전히 차단된다 (근접한 것만 살림).
3. (선택) 전용 풀 분리: `near_best`와 섞지 않고 `lineage_survivors`로 별도 관리 → diff base/family
   계산에 미치는 영향을 격리하고, combine parent 후보로만 노출.

**연계 주의:** F2를 구현하면 phase2-3 플랜의 **refine-parent wart 수정**(lineage head를 refine
parent로 주입; 플랜 "Decision: the Phase-1 refine-parent wart" 절 + Task 2 Step 4)과 풀 의미가
겹친다. 두 변경을 같은 패스에서 일관되게 설계할 것 (lineage head/lineage best가 parent로
노출되는 경로가 한 군데로 모이도록).

**상태:** 미구현. phase2 후보. **사용자가 "나중에 한 번에 수정" 요청한 항목.**

---

## F3 — lost-race → reset/repair 가 HEAD 와 워크트리를 어긋나게 둔다 (dirty-tree 크래시) **[버그]**

**관찰:** phase3_017 라이브(단일 lane, set_budget 4)에서 iter5 가 시작하자마자
`ensure_worktree_ready: workspace/transcribe.py is already dirty before candidate generation`
로 크래시. 조사 결과 `workspace/transcribe.py == stub(champion)` 인데 `HEAD == iter4 "keep" 커밋`
→ 둘이 어긋나(dirty) 다음 iter 진입 가드가 거부.

**근거 (코드 경로):**
- promote 경로는 gate 가 splice 할 `source_commit` 이 필요해서 **gate 실행 전에** candidate 를
  `commit_iteration(commit_status="keep")` 로 커밋한다 → HEAD 가 candidate 로 전진.
- 그 다음 `promotion.try_promote` 가 LOST → lost-race 재결정(`harness/runner.py` 의
  lost-race 분기, gate 호출 직후): `Outcome(beats_champion=False)` 로 `step_set` 재호출 →
  iter4 는 refine 3번째라 `t2.action == "reset"`(set 닫힘) → `restore_file_from_ref(champion)`
  로 **워크트리만 champion(stub)으로 되돌리고 commit 하지 않음**.
- 결과: `HEAD = iter4(candidate)`, `workspace = champion(stub)` → dirty → 다음 iter 크래시.

**놓친 이유:** 단위테스트는 lost-race → **advance**(워크트리 == HEAD 유지) 만 커버했고,
lost-race → **reset/repair**(이미 커밋된 candidate 를 롤백) 는 안 봄.

**심각도:** 튜닝 아님, **실제 버그**. promote 가 gate 에서 지고(=A-vs-B 동시 race, 또는 F4 처럼
시드 불일치) 그 set 이 reset 으로 닫히는 모든 경우에 발생. 병렬(phase2-3 본래 목적)에서 정상적으로
일어나는 lost-race 에서도 터짐.

**제안 (Fix A):**
- lost-race 의 `reset` 분기: champion 복원 후 `commit_iteration("reset", …)` 추가 →
  HEAD == workspace (phase1.5 의 "reset = code checkpoint" 규칙과 일치).
- lost-race 의 `repair` 분기: 이 경우 candidate 가 이미 HEAD 라 `restore_lineage_head` 가
  HEAD 로 복원(=candidate 유지)되어 트리는 깨끗하나 semantics 가 애매 — lost-race 에서
  `repair` 가 실제로 나오는지(보통 advance/reset) 확인하고, 나오면 HEAD 를 직전 lineage head
  로 맞추도록 처리.
- 회귀 테스트: lost-race → reset 후 `git status` 깨끗 + 다음 iter `ensure_worktree_ready`
  통과 assert.

**상태:** 미구현. **버그(우선).**

---

## F4 — gated promotion 시드가 "champion ≈ baseline 파이프라인" 을 가정 (stub-start 에서 거짓)

**관찰:** champion 을 stub(실측 CER ~0.41)으로 리셋한 뒤 fresh 실행했는데, `run_job` 부트스트랩이
`seed_champion_cer(baseline_cer=0.1714)` 로 `promotion_map.jsonl` 을 시드 → gate 의 기준
CER(0.1714)이 champion **코드**(stub 0.41)와 불일치. 결과: 모든 후보(0.39~0.45)가 gate 에서
패배 → champion 영원히 못 전진 + 개선되는 iter 마다 promote→gate→LOST 깔때기 → **F3 를 매 iter
유발**.

**근거:** `harness/promotion.py:seed_champion_cer` 는 부트스트랩 champion 의 CER 이
`baseline/target_cer.json:baseline_cer` 와 같다고 가정(I3 fix). stub 챔피언에선 거짓 —
stub 의 실측 CER 은 ~0.41(phase3_017 iter1 이 측정한 값)이지 0.1714 가 아님. 또한 in-process
gate(`decide_promotion(champion_cer=state.best_cer)`, `record_best` 로 드리프트)와 splice
gate(`live_champion_cer`, 시드 0.1714 고정)가 **서로 다른 기준**을 써서 단일 lane 에서 이중 gate
불일치.

**제안 (Fix B, 택1/조합):**
- stub-start 면 `promotion_map` 을 **champion 의 실측 CER**(stub 이면 ~0.41)로 시드, 또는
  bootstrap 시 시드 생략(첫 후보가 promote 되도록 — 레거시 동작).
- `state.best_cer` 도 동일 값으로 시드해 in-process gate 와 splice gate 기준을 일치.
- 일반화: 시드 출처를 "champion ref 가 실제로 내는 CER"로 (필요시 champion 1회 측정), baseline_cer
  하드코딩 대신.

**상태:** 미구현. 설계 결정. (당장 라이브 재현만 하려면 운영자가 `promotion_map` 을 champion 실측
CER 로 수동 시드.)

---

## (참고) 이미 다른 문서가 추적 중 — 여기서 중복 안 함

- **refine-parent wart** (refine 프롬프트 parent 힌트가 lineage head가 아니라 champion-family를
  보여줌): phase2-3 플랜의 "Decision: the Phase-1 refine-parent wart" 절 + Task 2 Step 4에서
  수정 예정. phase3_016 iter7~9에서 `parent=iter3/iter2/iter1`로 관측됨(= 증상 재확인). F2와
  함께 일관 설계 필요(위 연계 주의 참조).
- **lost-race 시 `record_best` 처리**: phase2-3 플랜 Task 3 Note(open question).
- **worktree·병렬·gated promotion·markers·packaging**: phase2-3 플랜 본문.
