# Evolve/Harness 설계 검토 — 정체(plateau) 진단과 구조 개편안

- 날짜: 2026-06-04
- 대상: Phase 3 portfolio-evolution harness (scheduler / portfolio / policy / runner / cooldown / candidate.md)
- 성격: **설계·방법론 검토** (코드 버그 검토 아님). 두 개의 독립 서브에이전트가 같은
  trace/문서를 따로 읽고 도달한 결론을 종합.
- 트리거: "버그 나오면 고치는" 패턴이 반복돼, 표면 수정이 아니라 evolve 패러다임 자체가
  이 문제(STT 파이프라인 **구조 발견**)에 맞는지, 정체/낭비 케이스는 없는지를 물음.

---

## 0. 한 줄 결론

현재 설계는 **그리디 (1+1) 언덕오르기 + 단일 스칼라 보상**이라, gain 이 *국소 정제*가 아니라
*구조 발견*에서 나오는 이 문제에는 패러다임이 어긋난다. 관측된 0.177 정체는
(a) 매 프롬프트에 현재 best 를 inline 하는 **영구 구조 앵커**와
(b) 평탄지대를 건너는 데 필요한 lateral/multi-objective move 를 거부하는 **그리디 단일
스칼라 선택 규칙** 때문이다. 6-mode/portfolio/discovery-floor 기계는 이 병을 **유발도
치료도 하지 않는다** — phase3_012 는 100% explore 압력에서도 같은 열등 basin
(~0.177, 단순 phase3_004 의 0.157 보다 나쁨)으로 수렴했다.

---

## 1. 근거 (phase3_012 "B" trace 기준, 두 에이전트 교차확인)

| 사실 | 근거 |
|---|---|
| **실제 개선분을 정책이 버림** | iter_018 = 0.17705 < best 0.17746 인데 `decisions.jsonl`: `"strict cer improvement 0.000418 < keep 0.002000"` → `micro_bank`, best 포인터 안 움직임 (`policy.py:99-116`, keep floor `config.py:30` = 0.002). iter_014 는 best 와 정확히 동률. |
| **exploit 기계가 한 번도 실행 안 됨** | 당시 잡이 `--iters 50`. `DISCOVERY_FLOOR_FRAC=0.40` (`scheduler.py:40`) → evaluated_index < 20 동안 explore 강제. 도달 19. iter 3–21 전부 `override=discovery_phase`. refine/combine/ablate/plateau = dead code. `scheduled_mode` 에 refine 이 잡힌 iter(4,8,13,17,21)도 전부 explore 로 override. |
| **정체가 순수 explore 안에서 발생** | best: 0.188(iter4) → 0.180(iter6) → 0.1775(iter11) → 이후 explore 슬롯만으로 iter12–21 갱신 실패. 즉 explore/exploit *비율*이 원인이 아님. |
| **align 실패 자석** | align/rerank/nbest/mbr/rover 계열이 9/21 iter(6,7,10,11,12,13,17,18,19). iter17 `align-alignments` → **0.698 폭망**. |
| **cooldown 이 자석을 못 막음** | `compute_cooldowns` 는 `final_decision=="reject"` 만 카운트(`cooldown.py:46-48`). align 시도들은 micro_bank/keep 이라 카운트 0. 전 구간 cooldown 경고 비어 있음. threshold(family≥5/sig≥2)에 영원히 도달 불가. |
| **objective 가 coarse + 틀림** | best 에서 corpus_cer 0.1775 인데 macro_cer 0.1933, hallucination_hit 0.182(2/11), sub_ratio 0.58. corpus 는 길이가중이라 긴 깨끗한 파일에 지배 → 최악 2–3 파일에 둔감. iter8 은 hal 0.636(7/11)인데 corpus 0.1791 로 거의 안 움직임(aggregate 가 per-file 붕괴를 가림). |

> 운영 함의: 사용자가 방금 B 를 `--iters 100` 으로 재시작 → discovery floor 경계가
> 0.40×100 = **40 iter** 로 늘어, exploit 기계는 더 오래 안 돌고 0.002 floor 가 계속
> sub-threshold gain 을 버린다. 같은 정체가 재현될 가능성이 높다.

---

## 2. 설계 수준 실패 모드 (severity 순)

- **F1 (CRITICAL) — keep floor 가 실제 진행을 삼킴.** best 기록 게이트와 commit/bank
  게이트가 같은 0.002 상수에 묶여 있다(`policy.py:100`). σ 가 provisional 이라 flat
  fallback 으로 붕괴(`policy.py:45-47`). 결정적 평가인데도 (0, 0.002) 개선은 전부
  버려지고, best 포인터가 안 움직이니 `iters_since_best`·plateau 감지·near_best
  pruning·error-profile 가 전부 stale 한 챔피언을 가리킨다. **"10 iter 정체"의 상당
  부분이 측정 artifact.**

- **F2 (CRITICAL) — 영구 구조 앵커: 매 프롬프트에 현재 best 를 inline.**
  `build_candidate_prompt` 가 mode 블록과 무관하게 현재 workspace 전체를 주입
  (`runner.py` workspace_body / error_profile). explore 디렉티브가 "ledger 가 안 다룬 걸
  조사하라" 고 말해도, 후보는 동작하는 0.177 파이프라인을 보며 *delta* 를 얹는다.
  004(0.157)는 near-scratch 반복 시도라 챔피언에 재앵커되지 않았다. **여기서 "explore"는
  restart-from-incumbent = 이웃 탐색일 뿐, 발산이 아니다.** mode 블록은 위에 주입된 구조
  앵커를 되돌릴 수 없다 → 100% explore 여도 hill-climber 처럼 수렴.

- **F3 (CRITICAL/HIGH) — exploit/discovery-floor 기계가 비실행 또는 수렴 가속.**
  fraction 기반 floor + 짧은 잡 → 6-mode 가 dead code. floor 가 풀린 뒤에도
  combine/refine 는 `_ranked_pool`(CER 정렬, `portfolio.py:142-146,196-214`)에서 최저-CER,
  즉 incumbent basin 내부 변종만 교배(near_best cutoff best×1.20, `portfolio.py:37`).
  proposal §4.4 의 novelty/axis-complement 점수는 **미구현 스텁**. 즉 출시된 것은 설계의
  *수렴 가속* 부분이고 *다양성 보존* 부분은 빠짐.

- **F4 (HIGH) — 구조적 dead-end 기억 부재.** cooldown 이 reject 만 세고, scored 폭망
  (iter17 0.698)은 failure 기억을 안 남김(`runner.py` verify_stderr 캡처는 verify_fail
  에서만). 후보는 *왜* 죽었는지 못 받아 같은 자석을 재발명. (※ A/run50 에는 2026-06-04
  crash-feedback 패치로 일부 완화됨 — verify_error.txt + recent-table 노트.)

- **F5 (HIGH) — open-loop mode 스케줄, credit assignment 0.** `base_mode` 는
  evaluated_index 의 순수함수(`scheduler.py:76-95`)라 outcome 무관. 폭망을 내는
  mode/family 도 gain 내는 것과 동일 예산.

- **F6 (MEDIUM) — plateau/cold-restart 가 여전히 portfolio 앵커.**
  `parents_for_mode("plateau")` 가 기존 family-best 2개 재조합(`portfolio.py:198-214`) +
  프롬프트는 여전히 best inline. "평탄지대 탈출"이 정의상 *현재 pool 의 재조합* → basin
  을 못 벗어남. stub 부터의 진짜 재시작이 없음.

- **F7 (LOW) — diversity_stall 사실상 발동 불가.** `recent_new_family_count==0` 조건인데
  후보가 매 iter 새 family_id 를 만듦(21 iter 에 family 15개). 정작 진짜 다양성 문제(같은
  구조 자석이 새 family 라벨로 재등장)는 family_id 가 너무 잘게 쪼개져 못 잡음.

### Net-negative 컴포넌트 (현 목표 기준)
- 매 프롬프트 best inline (F2) — 발산을 막는 핵심 앵커.
- 단일 스칼라 corpus_cer keep/reject + rollback (F1) — 정체 lock-in + 틀린 숫자 최적화.
- CER-ranked parent 선택 + near_best (F3) — incumbent basin 내 교배 = 수렴 가속을 다양성
  으로 오칭.
- reject-only soft cooldown (F4) — 설계 목적인 자석 억제에 무력.
- 6-mode/floor 장치 (F3/F5) — 잘 만들어졌으나 trajectory 를 지배하는 그리디 선택은
  안 건드림. 012 에서 기여 0, floor 풀리면 오히려 국소최적으로 당김.

---

## 3. 구조 개편안 (영향/노력 순)

### 우선순위 1 — 즉효, 저비용
1. **best 포인터를 monotone 으로 (F1).** "best 기록"과 "commit/bank" 게이트를 분리:
   `delta>0` 이면 best 전진, 0.002 floor 는 commit/알림에만. 결정적 평가이므로 작은 고정 σ
   (~1e-4) 도입해 threshold 가 0.002 로 붕괴하지 않게. → 모든 하류 stall 신호 해동.
   과적합 위험은 잡 종료 holdout 으로 게이트(기존 정책 docstring 우려).
2. **discovery floor 를 count 기반으로 + plateau preempt (F3).** `min(8, 0.4·total)` 같은
   절대 상한, 그리고 `iters_since_best>=K` 면 discovery 중에도 refine/combine 허용.
   012 는 19 explore iter 로도 이미 정체 — 부족했던 건 explore 가 아니라 *consolidation*.
3. **dead-end 기억 강화 (F4).** cooldown 이 micro_bank + scored 폭망(`cer>best·factor`)도
   카운트. family_id 보다 거친 구조 키(touched API surface: align/rerank/nbest 해시)로
   토큰만 바꾼 재시도가 충돌하게. 폭망 사유를 다음 후보에 forward. (A 의 crash-feedback
   패턴을 정상 평가 케이스로 확장.)

### 우선순위 2 — 패러다임 교정 (정체 재발 방지 / 0.157 돌파)
4. **explore/plateau 슬롯의 앵커 제거 (F2).** 발산 슬롯에는 best 대신 **stub(또는 아무것도
   없이)** + findings ledger 만 inline. exploit 슬롯에서만 incumbent 노출.
5. **per-file / worst-k 다목적 (F1/F5).** 프롬프트·recent-table 에 per-file CER 노출,
   worst-k 목적 추가(corpus 비퇴행 가드 하에 lexicographic/scalarized). 최악 2–3 파일을
   1급 타깃으로.
6. **깊은 정체 시 stub 부터 진짜 cold-restart (F6).** `iters_since_best>=2K` 면 stub +
   ledger 만으로 한 iter — 004 식 순수 발산 강제.

### 우선순위 3 — 장기 (발산 지속)
7. **island / multi-lineage 모델 (F6/F7).** 단일 global best 대신 축별(coverage/substitution
   /hallucination) k 개 독립 lineage, 각자 자기 incumbent 만 inline, combine 슬롯에서만
   migration. 단일 basin orbit 차단.
8. **mode 예산 bandit credit assignment (F5).** 순수함수 quota 대신 realized Δcer(또는
   Δworst-k)로 보상하는 UCB/Thompson. resume 안전은 seed 로깅으로.

> 방법론적으로는 단일 스칼라 그리디 keep/reject 를 **다목적 아카이브 기반 quality-diversity
> (예: 에러축 behavior space 위 MAP-Elites)** + **발산 슬롯의 incumbent 비앵커**로 바꾸는
> 것이 004(0.157) 를 따라잡거나 넘는 정공법. explore/exploit 비율 재튜닝이 아니다.

---

## 4. 다음 행동 제안
- 최소 변경으로 가설 검증: **우선순위 1 (1·2·3)** 부터. 각각 작은 diff, 기존 테스트 위에
  가드 추가. F1 monotone best 는 단독으로도 "정체" 신호의 artifact 부분을 걷어낼 것.
- 그 후 한 잡으로 A/B: (현행) vs (우선순위 1 적용) — 같은 budget·eval 로 plateau 도달
  iter 와 최저 CER 비교.
- 채택 시 본 검토의 결론을 `docs/proposals/2026-06-01-from-scratch-discovery-harness.md`
  의 다음 Revision + PHASE3-PLAN 정본에 흡수(SSOT §5 절차).
