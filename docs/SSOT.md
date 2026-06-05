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

> **2026-06-05 docs 정리.** drift 된 plan/design/deprecated 문서는 `archive/` 로
> 이동했다(리팩토링 후 `HARNESS-REDESIGN.md` 기준으로 새로 작성 예정). 루트에는
> 아래 **현행 정본** + **시점기록 디렉토리**만 둔다. `_workmap/` 은 삭제(임시였음).

### 현행 정본 (live)

| 주제 | 정본 | 비고 |
|------|------|------|
| 문제 정의, 데이터, 정규화, metric, 금지사항 | [`STT-PIPELINE-SPEC.md`](STT-PIPELINE-SPEC.md) | 도메인 정본 (코드 무관, 유효) |
| harness 코드 동작 (다이어그램) | [`HARNESS-MECHANICS.md`](HARNESS-MECHANICS.md) | **현재 코드 기준** 동작 지도 |
| 리팩토링 설계도 (worktree/lineage 분리) | [`HARNESS-REDESIGN.md`](HARNESS-REDESIGN.md) | 진행 중 리팩토링의 중심. 채택 시 새 PLAN 으로 정본화 |
| candidate runtime profile | [`../harness/prompts/candidate.md`](../harness/prompts/candidate.md) | runner 가 prompt 에 inline. 변경 = candidate 행동 변경 |

### 시점기록 (그대로 유지)

| 주제 | 위치 | 비고 |
|------|------|------|
| harness 변경 제안 (RFC) | [`proposals/`](proposals/) | 결정 이력. 진행 목록은 §5 |
| 코드·설계 리뷰 | [`reviews/`](reviews/) | 잡/커밋별 |
| 잡 회고 | [`retrospectives/`](retrospectives/) | 잡 종료 후 1개. 다음 잡 설계 토대 |
| 세션 인계 메모 | [`status/`](status/) | 날짜별 스냅샷 |
| 종료 후 종합 리포트 | [`reports/`](reports/) | `<job_id>_<KIND>_<YYYY-MM-DD>` |
| 현재 잡 iteration 로그 | [`../runs/_summary/HISTORY.md`](../runs/_summary/HISTORY.md) | 잡 단위 reset (phase1.5 이후 git 미추적 — append-only 로 디스크에 durable) |
| 과거 잡 narrative 아카이브 | [`history-archive/`](history-archive/) | `HISTORY.<job_id>.md` 등. 다음 잡 anchoring 방지 |

> 위 폴더(proposals/reviews/reports/retrospectives/status)의 **과거 기록은
> `archive/<폴더>/` 로 이동**(2026-06-05). 폴더는 빈 채 유지 — 앞으로의 새 기록용.

---

## 3. 코드 책임 지도

| 영역 | 책임 |
|------|------|
| `harness/` | Phase 3 controller 로직: guards(수치 가드), verify(judge 실행+guard), policy(keep/reject/micro_bank + **promotion/lineage 두 비교**), **scheduler**(mode 결정), **portfolio**(후보 bank + parent 선택), **signature**(diff→family), **cooldown**(반복실패 soft 경고), **lineage**(bounded-set 상태기계), **gitops**(champion ref 헬퍼), **config**(threshold 상수), state, history, runner. **simple-evolve 라인**: `candidate_cli`(runner 에서 lift 한 독립형 candidate CLI 계층 — `claude -p` hardening + stdout/stderr/diff capture + YAML metadata parse; runner import 안 함), `archive`(flat never-pruned 후보 archive — `ArchiveRecord` schema + append-only `archive.jsonl` + `best.txt` cache + parent materialize; 절대 prune/rollback 안 함), `select_context`(flat archive 에서 parent + inspiration slate 선택 — parent policy `llm`(Boltzmann weighted_random/child-penalty)·`random`(seeded)·`best`; inspiration = top-3-by-cer + 2-diverse + recent N, fingerprint dedup; 주입된 rng 로만 결정적), `prompt_simple`(candidate prompt 조립 — `candidate_simple.md` profile + frozen surface + baseline goal + EXPLORE/EXPLOIT mode block + operator `--directive` slot + `--ban` block + parent + inspirations + 40-deep findings ledger; scheduler/family/parent-diff 머신 없음, 첫 줄은 candidate gate 마커, 구분자는 `===`), `evolve_simple`(simple-evolve 루프 glue — `SimpleConfig` + 결정적 `_coin`/`_seed_for` mode + delta 기반 `_out_of_scope`/`_scope_ok` scope 가드 + `run_iter`(select_context→materialize parent→prompt→candidate→scope→run_verify→**항상** archive row append→실제 개선 시에만 `best.txt` 전진→workspace restore + state/log 기록); 구컨트롤러 import 안 함) |
| `harness/prompts/candidate.md` | candidate runtime profile — runner 가 매 iter inline |
| `harness/prompts/candidate_simple.md` | simple-evolve candidate profile — `prompt_simple.build_simple_prompt` 가 inline (candidate.md 의 mode-scheduler/family/parent-diff 산문 제거, YAML 출력 계약 보존) |
| `scripts/` | 사람이 실행하는 thin CLI 또는 일회성 운영 명령 (`evolve.py`, `analyze_run.py`, `evaluate_holdout.py`, `audit_candidate_context.py` 등) |
| `judge/` | 평가 산출: score, per-file, diagnosis |
| `frozen/` | 고정 ASR backend |
| `workspace/` | 후보 파이프라인 표면 (`transcribe(audio, sr) -> str`) |
| `baseline/` | 봉인된 target/baseline/noise floor |
| `runs/<hyp_id>/` | iteration별 평가 산출물 + `candidate_meta.json` / `.err` (phase1.5: `runs/` 전부 gitignore) |
| `runs/_summary/` | harness 전용 누적 — `<job_id>_state.json`, `<job_id>_portfolio.json`, `<job_id>_decisions.jsonl`, `<job_id>_candidate_meta.jsonl`, `HISTORY.md` (현재 잡 한정), `JOB_DONE.lock`. phase1.5: git 미추적이며 atomic write/append 로 디스크에 durable; 후보가 여기 쓰는 poison 은 `run_iteration` 의 pre/post 스냅샷 diff 로 탐지 |
| `docs/reports/` | analyze_run / evaluate_holdout 의 잡별 산출물 |
| `docs/proposals/` | harness 변경 RFC (채택 후 정본 갱신 + historical) |
| `docs/history-archive/` | 잡 종료 후 `runs/_summary/HISTORY.md` 를 `HISTORY.<job_id>.md` 로 이동 → 다음 잡은 빈 HISTORY 부터 시작 (잡 단위 ablation 보호) |

---

## 4. 충돌 시 우선순위

1. `STT-PIPELINE-SPEC.md` — 문제·평가·금지사항
2. `HARNESS-MECHANICS.md` — 현재 코드 동작
3. **코드 자체** — MECHANICS 와 코드가 다르면 코드가 정본(MECHANICS 를 갱신)
4. `HARNESS-REDESIGN.md` — 리팩토링 목표 구조
5. `README.md`, `AGENTS.md`, `CLAUDE.md` — 요약·진입점

> `archive/` 의 DESIGN·PHASE*-PLAN·STATUS 는 drift 가능 — 리팩토링 후 재작성 전까지 참고만.

리팩토링 과도기에는 코드를 정본으로 보고 MECHANICS 를 맞춘다.
실험 결과 충돌은 `score_report.json`과 `runs/_summary/HISTORY.md`를 우선한다.

---

## 5. 결정 이력 (proposals)

> proposal 원문은 `archive/proposals/` 에 보존(2026-06-05 이동). 아래는 **링크 없는
> 요약 인덱스** — 결정 흐름만. 새 proposal 은 `proposals/` 에 추가하고 채택 시 §2 정본
> 갱신. (archive 는 drift 가능하니 의존 말고, 필요한 내용은 코드 기준으로 재유도.)

- **2026-05-29 agent-design** (Accepted, 부분 대체) — A' candidate runtime. 5-lane 스키마는 이후 discovery-first → explore/exploit 로 대체.
- **2026-05-29 skills-and-prompt-eval** (Draft) — 내부 skill/agent + prompt-eval.
- **2026-05-29 prompt-diversification** (흡수/대체) — cold-restart/HISTORY 망각/metric reframe → discovery-first 로 흡수.
- **2026-06-01 from-scratch-discovery-harness** (Draft) — portfolio/mode scheduler/micro-bank/cooldown 기반. Rev 2026-06-02: discovery floor + explore-heavy.
- **2026-06-04 Phase 1 trio** (구현 완료, commit 2d1ee5c) — best monotone / discovery-floor count-cap / dead-end 기억. phase3_013 0.177→0.1539 돌파.
- **2026-06-04 explore-block-speciation** (Increment 1 구현 · 전체 블록 보류) — explore DIVERGE directive. 전체 블록은 C1/C2 로 보류. phase3_014 결과(explore 단독 한계)로 **worktree 리팩토링**(HARNESS-REDESIGN)으로 방향 전환.

지금 진행 중인 대규모 리팩토링 설계 = `HARNESS-REDESIGN.md` (§2 현행 정본).
proposal 은 *결정 이력* 이지 운영 정본이 아니다.

- **2026-06-05 lineage-set phase1** (구현 중, `refactor-harness` 브랜치) — `decide_promotion`/
  `decide_lineage_progress` 두 비교 + bounded-set 상태기계(`lineage.py`) + `champion` ref
  (`gitops.py`), `--set-budget` opt-in. C2(explore 1-shot 사망) 해소. 계획·리뷰:
  `superpowers/plans/2026-06-05-harness-lineage-set-phase1.md`,
  `reviews/2026-06-05-lineage-set-phase1-plan-review.md`. metadata-off-git 은 phase1.5 로 보류.
  동작 지도 = `HARNESS-MECHANICS.md` §12.
- **2026-06-05 metadata-off-git phase1.5** (구현 완료, `refactor-harness` 브랜치) — `runs/`
  전부 gitignore; metadata(state/portfolio/decisions/candidate_meta/HISTORY)는 atomic
  write/append 로 디스크에 durable, git commit 은 code checkpoint 전용
  (`keep`/`success`/`lineage_advance`/`reset`). ignored `runs/` 의 scope-violation 탐지는
  per-iter pre/post 파일시스템 스냅샷 diff(`git status --ignored` 아님), rollback 은 정밀
  파일 삭제(`git clean` 아님). per-iter metadata-commit churn + git-clean collateral 제거.
  계획 = `superpowers/plans/2026-06-05-phase1.5-metadata-off-git.md`, 근거 = `HARNESS-REDESIGN.md`
  §80. 동작 지도 = `HARNESS-MECHANICS.md` §3.
