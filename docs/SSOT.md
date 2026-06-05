# SSOT — 문서 정본 지도

> 목적: 문서가 늘어나도 같은 정책이 여러 곳에서 다르게 설명되지 않도록,
> 주제별 정본 위치와 상태 문서의 역할을 고정한다.

---

## 1. 정본 원칙

- 상세 정의는 한 곳에만 둔다.
- 다른 문서는 요약과 링크만 둔다.
- 계획 문서에는 진행 체크박스를 두지 않는다.
- 상태 문서에는 절차 본문을 복사하지 않고 계획 문서의 섹션만 참조한다.
- 실험 결과 수치는 `runs/` 산출물을 우선한다.

---

## 2. 주제별 정본

| 주제 | 정본 | 비고 |
|------|------|------|
| 문제 정의, 데이터, 정규화, metric, 금지사항 | [`STT-PIPELINE-SPEC.md`](STT-PIPELINE-SPEC.md) | 도메인 정본 |
| 시스템 구조, 디렉토리 책임, phase 경계 | [`DESIGN.md`](DESIGN.md) | 구조 정본 |
| Phase 1 구축 절차 | [`PHASE1-PLAN.md`](PHASE1-PLAN.md) | 완료 상태는 status 스냅샷 |
| Phase 2 평가 인프라 절차 | [`PHASE2-PLAN.md`](PHASE2-PLAN.md) | 완료 상태는 status 스냅샷 |
| Phase 3 자체 harness 운영 절차 | [`PHASE3-PLAN.md`](PHASE3-PLAN.md) | 체크박스 금지 |
| Phase 3 DoD / 현재 상태 | [`PHASE3-STATUS.md`](PHASE3-STATUS.md) | PLAN 섹션 참조만 |
| Phase 3 구조 그림 | [`PHASE3-LOOP.md`](PHASE3-LOOP.md) | 보조 문서, 정본 아님 |
| Phase 3 harness 코드 동작 레퍼런스 (다이어그램) | [`PHASE3-HARNESS-MECHANICS.md`](PHASE3-HARNESS-MECHANICS.md) | 코드 스냅샷 기반 이해용 보조 문서, 정본 아님. 정책 변경은 PHASE3-PLAN 먼저 |
| candidate runtime profile (역할·접근법·응답 포맷) | [`../harness/prompts/candidate.md`](../harness/prompts/candidate.md) | runner 가 prompt 에 inline. 변경 = candidate 행동 변경 |
| candidate 컨텍스트 (PUSH/AUTO-PUSH/PULL) + 감사 | [`CANDIDATE-CONTEXT.md`](CANDIDATE-CONTEXT.md) | `claude -p` 가 자동 로드하는 것 + 검증 스크립트 + 누수 baseline |
| harness 변경 제안 (RFC) — *결정 이력 only, 정본 X* | [`proposals/`](proposals/) | 채택되면 §2 의 해당 정본 (PHASE3-PLAN / CANDIDATE-CONTEXT / candidate.md) 에 흡수. 진행 중 목록은 §5 |
| 세션 종료 인계 메모 | [`status/`](status/) | 날짜별 스냅샷. 옛것은 `history-archive/status/` |
| 코드·설계 리뷰 (point-in-time) | [`reviews/`](reviews/) | 잡/커밋별 리뷰. 옛것은 `history-archive/reviews/` |
| 잡 회고 (좋은점·아쉬운점·개선) | [`retrospectives/`](retrospectives/) | 잡 종료 후 1개. 다음 잡 설계 토대 |
| 실제 iteration 로그 — *현재 잡 한정* | [`../runs/_summary/HISTORY.md`](../runs/_summary/HISTORY.md) | 잡 단위로 reset. 잡 종료 후 `docs/history-archive/HISTORY.<job_id>.md` 로 이동 |
| 과거 잡 narrative 아카이브 | [`history-archive/`](history-archive/) | `HISTORY.<job_id>.md` + 하위 `reports/`·`reviews/`·`status/`·`runs/` (옛 산출물·sanitized run 로그). 다음 잡 시작 시 HISTORY 누적 차단 (anchoring 방지) |
| 종료 후 종합 리포트 | [`reports/`](reports/) | `<job_id>_<KIND>_<YYYY-MM-DD>.{md,json}` |
| autoresearch 조사 기록 | [`AUTORESEARCH.md`](AUTORESEARCH.md) | historical/deprecated |
| 일반 self-evolve 참고 | [`SELF-EVOLVE-HARNESS-SPEC.md`](SELF-EVOLVE-HARNESS-SPEC.md) | 참고용, 정본 아님 |

---

## 3. 코드 책임 지도

| 영역 | 책임 |
|------|------|
| `harness/` | Phase 3 controller 로직: guards(수치 가드), verify(judge 실행+guard), policy(keep/reject/micro_bank), **scheduler**(mode 결정), **portfolio**(후보 bank + parent 선택), **signature**(diff→family), **cooldown**(반복실패 soft 경고), **config**(threshold 상수), state, history, runner |
| `harness/prompts/candidate.md` | candidate runtime profile — runner 가 매 iter inline |
| `scripts/` | 사람이 실행하는 thin CLI 또는 일회성 운영 명령 (`evolve.py`, `analyze_run.py`, `evaluate_holdout.py`, `audit_candidate_context.py` 등) |
| `judge/` | 평가 산출: score, per-file, diagnosis |
| `frozen/` | 고정 ASR backend |
| `workspace/` | 후보 파이프라인 표면 (`transcribe(audio, sr) -> str`) |
| `baseline/` | 봉인된 target/baseline/noise floor |
| `runs/<hyp_id>/` | iteration별 평가 산출물 + `candidate_meta.json` / `.err` |
| `runs/_summary/` | harness 전용 누적 — `<job_id>_state.json`, `<job_id>_portfolio.json`, `<job_id>_decisions.jsonl`, `<job_id>_candidate_meta.jsonl`, `HISTORY.md` (현재 잡 한정), `JOB_DONE.lock` |
| `docs/reports/` | analyze_run / evaluate_holdout 의 잡별 산출물 |
| `docs/proposals/` | harness 변경 RFC (채택 후 정본 갱신 + historical) |
| `docs/history-archive/` | 잡 종료 후 `runs/_summary/HISTORY.md` 를 `HISTORY.<job_id>.md` 로 이동 → 다음 잡은 빈 HISTORY 부터 시작 (잡 단위 ablation 보호) |

---

## 4. 충돌 시 우선순위

1. `STT-PIPELINE-SPEC.md` — 문제·평가·금지사항
2. `DESIGN.md` — 시스템 구조
3. `PHASE*-PLAN.md` — phase별 절차
4. `PHASE3-STATUS.md` / `docs/status/*` — 현재 상태
5. `README.md`, `AGENTS.md`, `CLAUDE.md` — 요약·진입점

문서와 코드가 충돌하면, 정책 충돌은 정본 문서에서 먼저 고치고 구현을 맞춘다.
실험 결과 충돌은 `score_report.json`과 `runs/_summary/HISTORY.md`를 우선한다.

---

## 5. 결정 이력 (proposals)

| 상태 | proposal | 흡수 위치 |
|---|---|---|
| Accepted (부분 대체) | [`proposals/2026-05-29-agent-design.md`](proposals/2026-05-29-agent-design.md) — A' candidate runtime | PHASE3-PLAN §2/§4/§5/§7, harness/prompts/candidate.md, scripts/analyze_run.py D 축. **단 A' 의 5-lane round-robin·`lane`/`diff_fingerprint`/`why_different` 스키마는 이후 discovery-first → explore/exploit 로 대체** (PHASE3-STATUS §9). §11 Addendum 에 audit 인프라 회고 |
| Draft | [`proposals/2026-05-29-skills-and-prompt-eval.md`](proposals/2026-05-29-skills-and-prompt-eval.md) — 내부 skill/agent + prompt-eval | 채택 시 .claude/skills/aig/, runner.py default cmd, README workflow. 본문은 phase3_002 결과 후 |
| Draft | [`proposals/2026-06-01-from-scratch-discovery-harness.md`](proposals/2026-06-01-from-scratch-discovery-harness.md) — portfolio evolution harness | 채택 시 portfolio, mode scheduler, prompt steps, micro-bank, cooldown, evolution metrics 를 PHASE3-PLAN, harness/runner, harness/state, harness/prompts, analyze_run 으로 흡수. **Revision 2026-06-02**: phase3_011 조기수렴 회귀 → discovery floor(첫 40% explore-only) + explore-heavy 스케줄 재조정 (proposal Revision 절, harness/scheduler.py) |
| 흡수/대체 | [`proposals/2026-05-29-prompt-diversification.md`](proposals/2026-05-29-prompt-diversification.md) — Cold-restart wildcard / HISTORY 망각 / metric reframe | discovery-first 재작성으로 흡수 (cold-restart → 이후 explore/exploit 감쇠 스케줄로 대체). 운영 정본: PHASE3-PLAN §4/§5 + candidate.md. 진화 이력: PHASE3-STATUS §9 |
| Accepted (구현 완료) | 2026-06-04 Phase 1 trio (best monotone / discovery-floor count-cap / dead-end 기억) | reviews/2026-06-04-evolve-design-review.md 의 R-A/R-B/R-C. harness/policy·config·scheduler·cooldown·signature (commit 2d1ee5c). phase3_013 에서 0.177 정체 돌파(0.1539) |
| Increment 1 구현 완료 · 전체 블록 보류 | [`proposals/2026-06-04-explore-block-speciation.md`](proposals/2026-06-04-explore-block-speciation.md) — explore DIVERGE directive. **서브에이전트 리뷰로 전체 블록 설계 보류**(C1 stub 리셋이 git-diff base 오염, C2 lineage rollback 이 on-disk==커밋 불변식 깸, C3 baseline 0.4685 stale→실제 0.17143·near_best 이미 champion-relative). **초안의 stub-swap(시야 비앵커)도 C1 재발로 폐기**: de-anchor 후보가 파일을 통째로 다시 써 챔피언-삭제 토큰 51개를 모든 explore 가 공유→family false-merge. Increment 1 = **`_EXPLORE_DIRECTIVE` 문구만 강화**("현재 알고리즘과 근본적으로 다른 시도를 하라") — 후보는 챔피언을 그대로 Edit 하므로 diff/signature/family 온전, invariant 0 변경. 전체 블록은 Increment 1 결과(다른 family 가 생기나 1-shot 사망)에 contingent |

proposal 은 *결정 이력* 이지 운영 정본이 아니다. 운영 정본은 §2 표 (특히
PHASE3-PLAN, CANDIDATE-CONTEXT, `harness/prompts/candidate.md`) 에만 둔다.
