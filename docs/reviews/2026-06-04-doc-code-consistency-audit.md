# 문서 ↔ 코드 정합성(drift) 감사

- 날짜: 2026-06-04
- 브랜치: `phase3-family-lineage` (※ 일부 문서 헤더는 아직 `phase3-diagnosis-feedback` 로 고정 — 항목 D3)
- 대상 repo: `/data/MyProject/side/evolve/aig` (family-lineage / "B" 트랙. run50base 워크트리는 별도 코드라 범위 밖)
- 방법: 두 독립 서브에이전트가 (1) proposal ↔ harness 구현, (2) SSOT/PHASE3/candidate.md ↔ 코드·경로·계약 을 각각 감사. 아래는 종합.
- 분류: MATCH / DRIFT(문서 X, 코드 Y) / UNIMPLEMENTED(문서가 명세, 코드 스텁·없음) / DOC-STALE(문서가 제거·개명된 것/구식 기술) / CODE-ONLY(문서에 없는 코드 메커니즘)

---

## 0. 한 줄 요약

**과거의 치명적 drift(candidate.md 2-mode vs harness 6-mode)는 해소됐고 가드 테스트도 녹색이다.**
지금 남은 정합성 문제는 두 갈래: **(A) proposal 이 명세한 "다양성 보존" 절반이 미구현**
(novelty/pair_score/compatible-parent/opportunity override) — 이건 설계 검토
([2026-06-04-evolve-design-review.md](2026-06-04-evolve-design-review.md))가 지목한 정체
원인과 정확히 맞물린다. **(B) 운영 정본(DESIGN/SSOT/PHASE3-PLAN/STATUS)이 현재 엔진을
기술하지 못함** — scheduler/portfolio/family/cooldown 서브시스템이 코드엔 핵심인데 정본엔
거의 없음. 즉 proposal 은 "채택 시 흡수" 라 적혀만 있고 실제로 정본에 흡수되지 않았다(SSOT
§5 절차 위반).

---

## 1. 안심해도 되는 것 (MATCH — 특히 점검 요청 항목)

- **candidate.md 는 mode-agnostic.** mode 블록은 `runner._MODE_DIRECTIVES`(runner.py:1025-1032)
  에서 주입; candidate.md:82-94 는 "harness 가 mode 를 골라 `=== … MODE ===` 블록을
  주입한다" 로만 기술, 특정 2-mode 하드코딩 없음. **2026-06-01 회귀 재발 아님.**
- **drift 가드 테스트 존재 + 통과.** `tests/test_scheduler.py:142-149`
  `test_mode_directives_match_scheduler_modes` 가 `_MODE_DIRECTIVES == _SCHEDULED_MODES ∪
  {repair,plateau}` 단언. mode 관련 스위트 68개 통과.
- **SSOT §2/§3 의 candidate.md inline 주장 일치** (runner.py:1290,1341-1343).
- **SSOT §2 의 스크립트 4종 모두 존재** (evolve/analyze_run/evaluate_holdout/audit_candidate_context).
- **계약 일치**: 편집 허용 `workspace/transcribe.py` 단일(`.claude/hooks/restrict_workspace.py`),
  signature `transcribe(audio,sr)->str`, 하드닝 플래그(`--disable-slash-commands`,
  `--strict-mcp-config`, `--disallowedTools=…`) (runner.py:125-132,177; CANDIDATE-CONTEXT 일치).
- **SSOT 경로 404 없음** — §2/§3/§5 참조 파일·디렉토리 전부 존재.

---

## 2. A. proposal ↔ harness 구현 drift

> 전제: SSOT §5(line 81)는 본 proposal 을 **"Draft / 채택 시 흡수"** 로 표기 — "완료" 가
> 아니다. Revision 2026-06-02(discovery floor + 스케줄 재조정)만 구체 코드 변경으로 반영됨.
> 따라서 "MVP 부분집합 구현" 이 기준선이고, 코드 주석들도 그렇게 self-flag 한다.

### UNIMPLEMENTED (문서 명세 ↔ 코드 없음) — 설계 검토와 직결
- **§4.4 `candidate_score`(cer_gain + axis_improvement + **novelty** − runtime/complexity/guard
  penalty) 전무.** parent 선택은 순수 CER 정렬(`portfolio.py:142-146`). novelty·axis·penalty
  항 모두 없음.
- **§4.4 combine `pair_score`(axis_complementarity, diff_overlap, same_family_penalty) 없음.**
  combine 은 최저-CER distinct-family 2개만(`portfolio.py:210-211`).
- **§4.5 compatible-parent 휴리스틱 없음.** `feasibility()` 는 `distinct_families>=2` 만
  검사(`portfolio.py:159-170,185-189` MVP 주석).
- **§4.3 opportunity override(micro→refine, axis-complement→combine, complex→ablate,
  metric_best reuse) + cooldown-in-precedence 미구현** (`scheduler.py:135-138` MVP 주석).
- **§4.4/§4.5 micro_bank target-axis 정렬, combine 성공→ablate enqueue 없음.**
- **§3.3 schema 필드 `public_summary`/`strengths`/`weaknesses`/`regression_summary` 미기록.**
  `_format_parents_block` 가 `public_summary` 를 읽지만(runner.py:1241) 채워지는 곳이 없어
  항상 axis_metric 로 fallback.

> ⇒ 미구현분이 전부 **"다양성 보존/축 보완/dead-end 억제"** 쪽이다. 출시된 건 설계의
> *수렴 가속* 부분(CER 정렬 교배)뿐 → 설계 검토의 F3/F4 와 동일 결론.

### DRIFT (문서 X ↔ 코드 Y)
- **§3.1 `metric_best.best_coverage` → 코드는 `best_deletion`** (`portfolio.py:25-30`; composite
  coverage 슬롯은 Step 2 로 유예 주석).
- **§4.3 override 우선순위 순서 다름.** 문서: 1 hard repair, 2 infeasible/cooldown, 3 diversity,
  4 plateau, 5 opportunity, 6 base. 코드(`scheduler.py:120-159`): 1 repair_event, 2 no_best,
  3 discovery_phase, 4 diversity_stall, 5 plateau, 6 base, 7 infeasible-fallback. → no_best·
  discovery_phase 는 코드 전용 단계, infeasible 은 rank2→최후 fallback 으로 강등, opportunity
  부재.
- **§9 cooldown 이 soft-only.** 문서 "family 5회 reject→reject", "signature 2회→cooldown" 인데
  코드는 **프롬프트 경고만**, 하드 reject/모드 변경 없음(`cooldown.py:60-75`).
- **§9 cooldown 의 reject 집합이 더 좁다.** `compute_cooldowns` 는 `final_decision=="reject"`
  카운트(`cooldown.py:47`)인데, 축 개선 reject 는 결정 기록 전에 `micro_bank` 로 재분류
  (`runner.py:1657-1662`)돼 카운트에서 빠짐 → **자석 억제가 더 약함**(설계 검토 F4 와 동일).
- **§8/§16 "keep threshold 유지" 권고 ↔ 코드 keep/banking 0.002 로 하향**
  (`policy.py:18-28`, `config.py:30`). 경고했던 0.0005 까진 아니지만 0.01→0.002 하향됨.

### CODE-ONLY (문서에 없는 핵심 메커니즘) — load-bearing
- **`near_best` 6번째 bank** (factor 1.20 / cap 24, `portfolio.py:36-38,227,326-359`). proposal
  §3.1 은 5개 bank 만 명시. 그런데 이 bank 가 **parent pool 전체를 구동**한다(설계 검토 F3/S7).
- **derived-mode family 상속**: refine/ablate/repair/combine/plateau 가 signature 재계산 대신
  `parents[0].harness_family_id` 상속(`runner.py:1619-1639`). §5.3 Jaccard 그룹핑엔 이 예외
  없음. (근거 주석: "36 iter 에 23 family → 스퓨리어스".)
- **`PLATEAU_EVERY=3` 주기 burst** + **plateau 2-parent 회전 합성**(`scheduler.py:47,152-154`;
  `portfolio.py:198-214`). §4.3/§6.2 엔 없음(phase3_008 회귀 대응 코드 전용 개선).

---

## 3. B. 운영 정본 ↔ 코드 drift

- **D1 (HIGH) DESIGN.md harness/ 트리 + SSOT §3 코드 책임 지도가 5개 모듈 누락.**
  DESIGN.md:72-78 / SSOT.md:47 은 guard/policy/state/history/runner 만 나열. 실제 harness/ 엔
  **scheduler.py, portfolio.py, signature.py, cooldown.py, config.py** 가 추가로 있고 이게 현재
  진화 엔진의 핵심. SSOT §4 우선순위 2(구조 정본)를 따라 구조 지도를 보는 사람이 틀린 모델을 얻음.
- **D2 (HIGH) PHASE3-PLAN §4 가 아직 "explore/exploit … `_iteration_mode`" 로 기술**
  (PHASE3-PLAN.md:184-186). 라이브 경로는 `_decide_iteration`→`scheduler.decide_mode` 6-mode
  (runner.py:1180-1188). refine/combine/ablate/repair/plateau·portfolio·family 언급 0. **DOC-STALE.**
- **D3 (MEDIUM) PHASE3-STATUS §9 narrative 가 한 단계 일찍 끝나고 헤더 브랜치가 구식.**
  "discovery-first → explore/exploit" 에서 멈춤(PHASE3-STATUS.md:95,107-109), 헤더는 여전히
  `phase3-diagnosis-feedback`(실제 `phase3-family-lineage`).
- **D4 (HIGH) portfolio/family/scheduler 서브시스템이 운영 SSOT 세트에 미문서화.**
  families 는 candidate.md(런타임 프로필)에만 등장. PHASE3-PLAN/STATUS/CANDIDATE-CONTEXT/SSOT
  어디에도 portfolio.py·scheduler 설명 없음 → proposal 이 **코드엔 들어갔으나 정본엔 흡수 안 됨**
  (SSOT §5 "채택 시 정본 흡수" 미이행).
- **D5 (LOW) 코드 residue.** `_EXPLOIT_DIRECTIVE` 정의됨(runner.py:947-967)이나 `_MODE_DIRECTIVES`
  에 없어 한 번도 emit 안 됨. `_iteration_mode`/`_is_explore_iter`(runner.py:495-519)도 라이브
  경로 밖인데 테스트(test_harness_runner.py:1401,1500-1587)가 구식 동작을 가드 중.
- **D6 (LOW) SSOT §3 가 `runs/_summary/` 산출물 일부 누락** — `<job>_portfolio.json`,
  `<job>_decisions.jsonl`, `<job>_candidate_meta.jsonl` 미기재.

---

## 4. 가장 중대한 drift (순위)

1. **proposal 의 다양성 보존 절반(novelty/pair_score/compatible-parent/opportunity override)
   전부 미구현.** 설계 검토가 지목한 "수렴 가속만 출시" 와 동일. 정체의 구조적 뿌리.
2. **운영 정본(DESIGN/SSOT §3/PHASE3-PLAN §4)이 현재 엔진을 기술 못 함** (D1/D2/D4) —
   누구든 정본으로 시스템을 이해하면 2-mode·5-module 의 틀린 그림을 얻음. **다음 drift 회귀의
   온상**(이전 candidate.md 사고와 같은 계열).
3. **cooldown soft-only + reject 카운트 누락** — 문서가 약속한 dead-end 억제가 코드엔 사실상
   없음(자석 무한 재시도, 설계 검토 F4).
4. **near_best / family 상속 / plateau-burst 가 load-bearing 인데 미문서화** — 동작은 하지만
   정본에 없어 추론·디버깅 시 보이지 않음.

---

## 5. 권고 (SSOT §4: 정책 충돌은 정본에서 먼저, 그 외는 사실에 맞게 갱신)

- **즉시(문서 갱신, 코드 변경 0):**
  - DESIGN.md 트리 + SSOT §3 에 scheduler/portfolio/signature/cooldown/config 추가 (D1).
  - PHASE3-PLAN §4 / PHASE3-STATUS §9 를 6-mode 포트폴리오/패밀리로 갱신, 헤더 브랜치 정정 (D2/D3).
  - proposal 을 SSOT §5 절차대로 정본(PHASE3-PLAN)에 흡수하고 **"구현 현황(MVP/미구현)" 표를
    proposal 에 명시** — 미구현분(§4.3/§4.4/§4.5)을 "deferred" 로 박아 향후 drift 오해 차단 (D4).
  - near_best·family 상속·plateau-burst 를 정본에 1단락씩 문서화 (CODE-ONLY 항목).
- **정책 결정 필요(문서 vs 코드 어느 쪽을 정본으로?):**
  - keep/banking threshold 0.002 (§8 권고와 충돌) → 설계 검토 R-A(monotone best) 결정과 함께 처리.
  - cooldown soft→hard 여부, opportunity override 구현 여부 → 설계 검토 우선순위와 묶어 결정.
- **코드 정리(저위험):** `_EXPLOIT_DIRECTIVE`·`_iteration_mode` residue 제거 또는 "legacy"
  명시(D5). 단 테스트 의존 있으니 함께 정리.
- **재발 방지:** 본 감사를 반복 가능한 체크로 — 최소한 (a) `_MODE_DIRECTIVES==scheduler modes`
  가드(이미 있음)에 더해 (b) "SSOT §3 에 나열된 harness 모듈 집합 == 실제 harness/*.py" 같은
  경량 정합 테스트 추가 검토.

> 본 감사 + 설계 검토 두 보고서는 상호보완: **미구현 = 정체 원인**(설계 검토), **미문서화 =
> drift 재발 위험**(본 감사). 채택 결정은 설계 검토 §3 우선순위와 함께.
