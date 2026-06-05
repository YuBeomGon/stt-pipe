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

## (참고) 이미 다른 문서가 추적 중 — 여기서 중복 안 함

- **refine-parent wart** (refine 프롬프트 parent 힌트가 lineage head가 아니라 champion-family를
  보여줌): phase2-3 플랜의 "Decision: the Phase-1 refine-parent wart" 절 + Task 2 Step 4에서
  수정 예정. phase3_016 iter7~9에서 `parent=iter3/iter2/iter1`로 관측됨(= 증상 재확인). F2와
  함께 일관 설계 필요(위 연계 주의 참조).
- **lost-race 시 `record_best` 처리**: phase2-3 플랜 Task 3 Note(open question).
- **worktree·병렬·gated promotion·markers·packaging**: phase2-3 플랜 본문.
