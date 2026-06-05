# Review — Harness Lineage-Set Refactor Phase 1 Implementation Plan

- **대상 계획**: `docs/superpowers/plans/2026-06-05-harness-lineage-set-phase1.md`
- **대조 기준**: `docs/HARNESS-REDESIGN.md`(blueprint), `docs/HARNESS-MECHANICS.md`(현행 동작), 실제 코드(`harness/runner.py`, `policy.py`, `state.py`, `scheduler.py`, `portfolio.py`, `config.py`, `.gitignore`, `tests/`)
- **리뷰 기준 커밋**: 현재 working tree (branch `phase3-diagnosis-feedback`)

---

## Verdict

**Ready with fixes** — C2 핵심 수정(두 비교 함수 + bounded-set state machine)의 설계는 건전하고 코드 근거도 정확하다. 다만 (1) **Task 7 이 기존 테스트 `test_step1_decision_trace_committed_and_survives_next_iter` 를 깨뜨리는데 계획이 그 테스트를 지목·수정하지 않음**, (2) **set advance 경로가 portfolio `global_best` 를 오염시킴**(refine parent 선택이 champion 이 아닌 worse-than-champion lineage 를 best 로 착각), (3) **set 의 refine phase 와 scheduler 의 chosen_mode 가 독립적으로 충돌**(set 은 refine 인데 scheduler 는 explore 를 줄 수 있음 — repair parent/diff 가 안 붙음) 세 가지를 고치기 전엔 Task 8 이 의도대로 동작하지 않는다. 이들을 반영하면 구현 착수 가능.

---

## Strengths

- **C2 진단이 코드와 정확히 일치.** 계획이 지목한 `runner.py:2097` reject→`rollback_paths(candidate_owned_statuses…)` 가 worse-than-champion explore 를 죽이는 지점이 맞다(`harness/runner.py:2096-2097`). MECHANICS 문서(`docs/HARNESS-MECHANICS.md:92-93` "reject → rollback (on-disk = 기존 챔피언)")와도 부합. 인용한 line number 들(`2083` decide_candidate, `2048` verify-fail, `1886` command exit, `1774` commit_iteration, `345` rollback_paths, `356` candidate_owned_statuses, `214` RunnerConfig)이 전부 실제와 맞다.
- **순수 모듈 우선(Task 1–5) → runner 배선 마지막(Task 6–8) 순서**가 안전하고 reversible. `lineage.py` 가 git/IO 없는 순수 transition 이라 §117 simulator 의도(audio 없이 결정적 단위 테스트)를 충실히 따른다.
- **`decide_promotion` = `decide_candidate` alias** 는 zero-behavior-change 가 사실이다(`policy.decide_candidate` 가 `best_cer` 만 받음 → `champion_cer` 로 이름만 바꿔 위임, `policy.py:56-123`). Task 2 정확.
- **state 후방호환**: `HarnessState.load` 가 unknown key 무시 + 누락 key 는 default(`state.py:43-46`). Task 5 의 "old state file loads" 주장과 테스트가 코드와 일치.
- **`--set-budget` opt-in(default 1 = legacy)** 게이팅으로 라이브 run 무영향 — 보수적이고 옳다.
- **doc-consistency 가드 충족 방식 정확**: `test_doc_consistency.py:36-43` 은 각 harness 모듈 stem 문자열이 SSOT.md 본문 어디든 등장하면 통과. Task 9 가 `lineage`/`gitops` 를 SSOT §3(`docs/SSOT.md:54`)에 추가하면 만족. archive 인용도 없음(plan line 13 이 명시적으로 archive 회피).

---

## Issues

### Critical

#### C-1. Task 7 이 `test_step1_decision_trace_committed_and_survives_next_iter` 를 깨뜨리는데 계획이 지목·수정하지 않음

- **위치**: 계획 Task 7 Step 1/5 vs `tests/test_harness_runner.py:381-466`
- **문제**: 계획은 metadata 를 git tracking 에서 빼고(`runs/` gitignore + `commit_iteration` 이 code 만 stage), Step 1 에서 "grep 해서 committed 를 assert 하는 테스트를 손보라"고만 일반 지시한다. 그러나 실제로 깨지는 테스트는 단 하나이고 매우 구체적이다:
  ```python
  # tests/test_harness_runner.py:431-432
  assert _tracked(tmp_path, "runs/_summary/job_decisions.jsonl")
  assert _tracked(tmp_path, "runs/_summary/job_portfolio.json")
  ```
  `_tracked`(line 372-378)는 `git ls-files --error-unmatch` 로 **git 추적 여부**를 본다. Task 7 후 이 파일들은 더 이상 commit 되지 않으므로 두 assert 가 **확정적으로 FAIL**. 또한 line 462-466 의 `assert porcelain == ""`(clean tree) 은 — `_init_repo`(line 76-79)가 자체 `.gitignore` 를 `runs/*\n!runs/_summary/\n` 로 쓰기 때문에 — Task 7 의 production `.gitignore` 수정만으로는 테스트 repo 에 반영되지 않는다. 즉 테스트 repo 에선 `runs/_summary/*` 가 여전히 untracked-non-ignored 로 남아 `commit_iteration` 이 code 만 stage 하면 metadata 가 **untracked 로 떠서 porcelain 이 dirty** → line 466 도 FAIL.
- **수정**:
  1. Task 7 에 명시적 step 추가: `tests/test_harness_runner.py:431-432` 의 `_tracked` assert 를 "파일이 **디스크에 존재**한다"(`assert (tmp_path/"runs/_summary/job_decisions.jsonl").is_file()`)로 교체.
  2. `_init_repo` 의 `.gitignore`(line 76-79)를 production 과 동일하게 `runs/\n` 으로 바꾸고, 그에 맞춰 `runs/_summary/HISTORY.md` 를 `git add` 하는 line 100-101 도 정리(ignored 경로를 add 하면 force 필요). 이걸 안 바꾸면 모든 `commit_results=True` 통합 테스트의 clean-tree 가정이 흔들린다.
  3. `git rm -r --cached runs/_summary` (Step 4)는 **현 repo 에선 no-op** 이다 — `git ls-files runs/_summary` 가 0 건이라 추적된 summary 파일이 없다(과거 커밋엔 있었으나 현 브랜치엔 없음). `|| true` 가드가 있어 무해하나, 계획의 "이미 committed 된 summary 를 untrack" 전제는 현 상태와 어긋나므로 주석으로 "현 브랜치엔 추적 파일 없을 수 있음(무해)" 명시.

#### C-2. set `advance` 가 portfolio `global_best` 를 worse-than-champion lineage 로 오염 → refine parent 가 champion 이 아닌 열등 후보를 best 로 착각

- **위치**: 계획 Task 8 Step 3 (`advance → commit_iteration(... status="keep" ...)`) vs `harness/runner.py:1688-1712`, `harness/portfolio.py:289-291`
- **문제**: `commit_iteration`(`runner.py:1783-1786`)은 `_persist_decision` 을 호출하고, 거기서 `portfolio.update(decision_status=status, …)` 가 돈다. `Portfolio.update` 는 **`decision_status in ("keep","success")` 이면 무조건 `global_best=hyp_id`** 로 미러링한다(`portfolio.py:289-291`). 계획대로 set 의 `advance` 를 `status="keep"` 으로 commit 하면, **champion(0.154)보다 나쁜 explore(0.190)가 `global_best` 로 등록**된다. 그러면:
  - 다음 iter `_decide_iteration`(`runner.py:1196-1201`)의 refine parent 선택(`parents_for_mode("refine")` → `_ranked_pool` → `global_best_entry`)이 **열등 lineage 를 champion 으로 착각**해 prompt 의 "build on best" 재료가 오염된다.
  - `_scheduler_context.has_best`(`runner.py:1180`)는 `state.best_hyp_id` 를 보므로 set 이 `state.record_best` 를 호출하지 않으면 OK 지만, portfolio 의 `global_best`/`near_best` cutoff(`portfolio.py:342`, `_NEAR_BEST_FACTOR`)가 0.190 기준으로 재계산돼 near-best 풀 전체가 망가진다.
  계획은 set 경로의 portfolio 연동을 **전혀 언급하지 않는다**(self-review "Type consistency" 에도 없음). HARNESS-REDESIGN §74(두 비교 함수의 핵심)와 §209 의 `job_best` vs `global_best` 분리를 Phase 2 로 미뤘는데, **그 분리가 없으면 Phase 1 의 advance commit 이 곧바로 global_best 를 더럽힌다** — 즉 §209 deferral 이 안전하지 않다(아래 Important I-3 의 scope-cut 위험과 직결).
- **수정**: set advance 의 code-checkpoint commit 시 **portfolio global_best 미러링을 막아야** 한다. 최소 침습 방안 둘 중 하나:
  - (a) set advance 는 `commit_iteration` 을 거치되, `_persist_decision`/`portfolio.update` 에 "lineage-local, not global" 를 알리는 새 status(예: `"lineage_advance"`)를 넘기고 `Portfolio.update` 의 `global_best` 분기(line 289)를 `("keep","success")` 로 한정 유지(즉 `lineage_advance` 는 global_best 를 안 건드림). family_best/near_best 적재는 `valid` 조건에 넣을지 별도 판단.
  - (b) Phase 1 에서 §209 `global_best` vs `job_best` 분리를 **최소 형태로 당겨온다** — `Portfolio` 에 set-local best 를 두고 advance 는 거기만 갱신. (계획의 "Phase 2 deferral" 을 일부 철회.)
  어느 쪽이든 **계획에 "set advance 가 portfolio global_best 를 건드리지 않음" 을 검증하는 테스트**를 Task 8 에 추가해야 한다.

### Important

#### I-1. set phase(refine) 와 scheduler chosen_mode 가 독립 — 모순 가능, repair parent/diff 가 안 붙음

- **위치**: 계획 Task 8(“set 은 scheduler 위에 얹고 decide_mode/parent 는 그대로”) + self-review line 1100 vs `harness/runner.py:1838`, `scheduler.decide_mode`(`scheduler.py:126-168`)
- **문제**: `run_iteration` 은 매 iter 맨 위에서 `sched, parents = _decide_iteration(...)`(`runner.py:1838`)로 **scheduler 가 독립적으로 mode 를 고르고 그 mode 에 맞는 parent/diff 를 prompt 에 주입**한다. set state machine 의 `phase`(explore/repair/refine)는 **decision 단계(line 2079 이후)에서만** 쓰인다. 따라서:
  - set 이 `phase="refine"` 여도 scheduler 는 `discovery_phase`/`diversity_stall`/`no_best` override(`scheduler.py:150-160`)로 `explore` 를 줄 수 있다. 그러면 prompt 는 "새 구조 탐색" 을 지시하는데 set 은 그 결과를 "refine 결과" 로 채점·전이한다 — **set 이 의도한 '직전 lineage head 를 다듬어라' 가 prompt 에 전달되지 않는다**. C2 수정의 절반(refine 으로 육성)이 prompt 레벨에서 무력화될 수 있다.
  - set 이 `phase="repair"`(verify_fail 후)인데 scheduler 가 repair 가 아닌 mode 를 주면, `_last_failure_parent`(`runner.py:1215-1218`, repair-only)가 안 붙어 **실패 diff+stderr 가 후보에게 전달되지 않는다**. 계획 Task 8 Step 3 은 "repair keeps the failed iter's artifact via existing `_last_failure_parent`" 라고 적었지만, 그 헬퍼는 `sched.chosen_mode == "repair"` 일 때만 호출된다 — set phase 가 repair 라고 scheduler chosen_mode 가 repair 가 되는 보장이 없다.
- **이건 설계 공백**(아래 Open Question 1 과 동일 축). 최소한 계획은 다음을 명시해야 한다:
  - set 이 active(`set_budget>1` & phase∈{repair,refine})일 때 **scheduler 결정을 set phase 로 강제 override** 하거나(예: `_decide_iteration` 에 set phase 를 주입해 chosen_mode 를 고정), 아니면 set phase 를 scheduler context 의 입력으로 넘겨 일관되게 만든다.
  - 그게 Phase 1 범위를 넘으면, "set 활성 시 scheduler override 는 무시되고 set phase 가 prompt mode 를 결정한다" 를 명시적 결정으로 기록.

#### I-2. promote 시 champion ref 가 받는 commit 이 모호 — "<HEAD after commit>" 의 순서 의존성

- **위치**: 계획 Task 8 Step 3 promote 분기 (`gitops.advance_champion_ref(repo_root, state.champion_ref, <HEAD after commit>)`)
- **문제**: 단일 lane 에서 HEAD = lineage head. promote 시 후보 코드는 이미 working tree 에 있고, `commit_iteration(status="keep"…)`(`runner.py:1802-1809`)가 `git add -- transcribe.py` → `commit` 으로 HEAD 를 전진시킨다. champion 을 advance 하려면 **그 commit 직후의 HEAD SHA** 를 받아야 한다. 계획의 "<HEAD after commit>" placeholder 는 의도는 맞지만 **순서가 명세되지 않았다**:
  - `advance_champion_ref` 를 commit **전에** 호출하면 champion 이 직전 lineage commit(후보 코드 미포함)을 가리켜 promote 가 무의미.
  - `commit_iteration` 이 내부에서 commit 하므로, 호출부는 commit 후 `git rev-parse HEAD` 로 SHA 를 다시 읽어 `advance_champion_ref` 에 넘겨야 한다. 그런데 `commit_iteration` 은 **diff 가 없으면 commit 을 건너뛴다**(`runner.py:1803-1805`). promote 후보가 직전 advance 와 동일 코드(예: refine 가 best 를 재확인만)면 commit 이 안 생겨 HEAD 가 안 움직이고, 그래도 champion advance 는 해야 한다(이전 lineage commit 으로). 이 edge 가 명세에 없다.
- **수정**: Task 8 promote 분기를 다음 순서로 고정 명세:
  1. `commit_iteration(status="keep"…)` 호출(코드 checkpoint).
  2. `head = _run_git(repo_root, ["rev-parse","HEAD"]).stdout.strip()`.
  3. `gitops.advance_champion_ref(repo_root, state.champion_ref, head)`.
  4. `state.record_best(hyp_id, cand_cer)`; `state.set_phase="idle"`; success 면 `state.status="success"`.
  그리고 `gitops.advance_champion_ref` 가 받는 commit 이 "방금 만든 HEAD" 임을 plan 문구에 못박는다.

#### I-3. scope-cut "scheduler/portfolio job_best 분리 deferral" 이 안전하지 않다(C-2 근거)

- **위치**: 계획 self-review line 1107, 1110 ("scheduler/portfolio split (`job_best` vs `global_best`) is deferred to Phase 2 … NOT a gap")
- **문제**: C-2 에서 보였듯, **Phase 1 의 set advance commit 이 곧장 portfolio `global_best` 를 갱신**(`portfolio.py:289-291`)하므로, `job_best`/`global_best` 분리 없이는 set 이 portfolio 를 오염시킨다. 계획은 이 deferral 을 "explicit scope cut, not a gap" 으로 단정했지만 **실제로는 Phase 1 이 이 분리에 의존**한다. HARNESS-REDESIGN §209 가 정확히 이 분리를 요구하는 이유.
- **수정**: deferral 을 철회하거나(최소 set-local best 도입), 또는 set advance 가 portfolio 를 우회하도록 명세(C-2 수정안 (a)). 어느 쪽이든 self-review 의 "NOT a gap" 단정을 정정.

#### I-4. Task 8 Step 2 통합 테스트 본문이 stub — 핵심 회귀 방어선이 비어 있음

- **위치**: 계획 Task 8 Step 2 (`...` 본문)
- **문제**: 계획 스스로 "behavioral assertions 는 완전 명세" 라고 하지만, C2 수정의 **유일한 end-to-end 증거**가 이 테스트다(worse-than-champion explore 가 rollback 안 되고 refine 으로 살아남음). pure state-machine 테스트(Task 4)는 transition 만 검증하지 **runner 가 실제로 rollback 을 건너뛰는지**는 검증 못 한다. stub 으로 두면 C2 가 진짜 고쳐졌는지 CI 가 보장하지 못한다.
- **수정**: Task 8 Step 2 를 concrete 테스트로 채운다. 기존 `test_run_iteration_rolls_back_workspace_on_verify_failure`(`tests/test_harness_runner.py:340-369`)와 `_init_repo`/`candidate`/`verifier` injection 패턴이 그대로 재사용 가능하다 — `set_budget=4`, champion 0.20, explore verifier 0.30, 그리고 `assert (tmp_path/"workspace/transcribe.py").read_text() != original`(rollback 안 됨) + `state.set_phase=="refine"` + `state.set_best_cer==0.30`. 스타일 근거가 이미 파일에 있으니 stub 일 이유가 없다.

#### I-5. `decide_lineage_progress` 의 `success` 처리 누락 — target 도달 explore 가 dead_end/advance 로 오분류

- **위치**: 계획 Task 3 `decide_lineage_progress` vs `policy.decide_candidate` success 분기(`policy.py:80-92`)
- **문제**: `decide_lineage_progress` 는 `corpus_cer` 만 보고 advance/hold/dead_end 를 낸다 — target_cer 도달(success) 개념이 없다. set 경로에서 promotion 은 별도 `decide_promotion` 으로 잡으니 `beats_champion` 으로 promote 되긴 한다(`step_set` line 527: promotion always wins). 따라서 기능적 누락은 아니다. 다만 `LINEAGE_DEAD_END_FACTOR=1.5` 가 **lineage_best 기준**이라, lineage 가 이미 매우 낮은데(예 0.05) 후보가 0.08 이면 `0.08 > 0.05*1.5=0.075` 라 **target 근처의 정상 후보가 dead_end 로 닫힐 수 있다**. set 초반(lineage_best 가 높을 때)은 무해하나 후반엔 과민. 
- **수정**: 경미하나, Task 3 에 "dead_end_factor 는 lineage_best 가 낮아질수록 보수적으로 작동 — 절대 하한(예: `max(lineage_best*factor, lineage_best+abs_margin)`) 고려" 를 note 로 남기거나, Phase 1 은 그대로 두되 Open Question 으로 기록.

### Minor

- **M-1. `decide_lineage_progress` 의 `Any` import 확인 지시가 불필요하게 방어적.** 계획 Task 3 Step 3 note 가 "`Any` 가 import 됐는지 확인하라" 는데, `policy.py:10` 이 `from typing import Any, Literal` 로 이미 둘 다 import 함. 또 `math`(line 8), `cfg`(line 12) 도 이미 있어 `math.isfinite`/`cfg.LINEAGE_DEAD_END_FACTOR` 사용 가능. 확인 지시는 맞지만 결과는 "이미 됨" — 구현자가 헷갈리지 않게 "이미 import 됨, 추가 불필요" 로 단정해도 됨.
- **M-2. `RunnerConfig` 가 frozen dataclass.** `harness/runner.py:214` `@dataclass(frozen=True)`. 계획이 `set_budget`/`max_repairs`/`max_refines` 를 default 필드로 추가하는 건 문제없으나(frozen 은 인스턴스 변경만 막음), Task 8 의 `main` 에서 `RunnerConfig(... set_budget=args.set_budget ...)` 로 생성자에 넘겨야 함(post-init 대입 불가). 계획 Step 4 "thread them into the RunnerConfig(...) construction" 가 이를 함의하나 명시 권장.
- **M-3. `--iters>1 requires --commit-results` 가드와 set 경로.** `runner.py:2238-2239` 가 `iters>1` 일 때 `--commit-results` 를 강제한다. set 경로는 code checkpoint commit 에 의존하므로 `--set-budget>1` 도 `--commit-results` 를 요구해야 일관적이다(advance 가 commit 못 하면 promote 시 champion advance 할 HEAD 가 없음). Task 8 Step 4 에 `if args.set_budget>1 and not args.commit_results: parser.error(...)` 가드 추가 권장.
- **M-4. resume 결정성 — `_set_state_from` 의 set_id 증가.** 계획 helper `_set_state_from`(plan line 1006-1018)는 phase∈{idle,closed} 면 `SetState.new(set_id=state.set_id+1)`. mid-set crash(phase=refine) resume 은 기존 set_id 로 복원되어 OK. 다만 **closed 직후 crash**(persist 전) 시 재개하면 같은 closed state 로 또 `+1` — set_id 가 한 번 더 증가할 뿐 기능상 무해(중복 없음). 비결정성 없음. resume 안전성은 합격. 단 `state.save` 가 advance/repair/promote/reset **모든 분기 끝에서 한 번** 불리는지 Task 8 에서 확실히(현 `run_iteration` 은 각 return 직전 `state.save`, line 2115 — set 분기도 동일 패턴 유지 필요).
- **M-5. `test_refine_no_gain_holds_until_budget` self-수정 지시(Task 4 Step 4)가 TDD 흐름과 어긋남.** 계획이 먼저 `assert action in {advance, hold}` 로 테스트를 쓴 뒤 구현 후 `assert action == "repair"` 로 고치라고 한다. "failing test 먼저" 원칙상 처음부터 `"repair"` 로 쓰는 게 깔끔(구현 동작이 이미 확정이므로). 경미.
- **M-6. `decide_lineage_progress` 시그니처에 미사용 `config` 파라미터.** 계획 구현(plan line 253-256)이 `config: PolicyConfig|None` 과 `dead_end_factor` 를 둘 다 받지만 `cfg_.keep_delta_eps` 만 config 에서 씀 — OK. 다만 `dead_end_factor=cfg.LINEAGE_DEAD_END_FACTOR` 를 default 인자로 두면 **import 시점 바인딩**이라 테스트에서 monkeypatch 로 config 값을 바꿔도 반영 안 됨. config 일관성 위해 `dead_end_factor` 도 `PolicyConfig` 필드로 넣는 편이 SSOT 원칙(`config.py` 주석)에 맞음. 경미.

---

## Open design questions (운영자 결정 필요)

1. **set phase vs scheduler chosen_mode 의 권위 (I-1 핵심).** set 이 active 일 때 prompt 의 mode/parent 를 누가 정하는가?
   - (A) set phase 가 scheduler 를 override(set=refine → prompt 도 refine, set=repair → `_last_failure_parent` 강제 주입). C2 의도에 충실하나 scheduler 의 discovery floor/diversity 로직과 경합.
   - (B) scheduler 가 계속 독립적으로 mode 를 고르고 set 은 채점/전이만. 구현 단순하나 "refine 으로 육성" 이 prompt 에 안 실릴 위험.
   현재 계획은 암묵적으로 (B) 인데, 그러면 C2 수정이 prompt 레벨에서 약화된다. **권장: (A)**, 단 Phase 1 은 "set active 시 scheduler override 무시" 만으로 최소 구현.
2. **portfolio global_best 오염 방지 방식 (C-2).** set-local best 를 portfolio 에 새로 두나(§209 일부 선반영), 아니면 advance commit 시 portfolio 미러링을 우회하나. 후자가 Phase 1 범위에 더 맞지만 near_best 풀 갱신 일관성을 별도로 봐야 함.
3. **promote 후 reseed 의미.** 계획은 promote 시 `set_phase="idle"` 로 닫고 "next set reseeds from new champion"(plan line 320). 단일 lane 에서 champion advance 후 working tree 는 이미 promote 코드(=새 champion)와 동일하므로 reseed = no-op rollback. 맞다. 다만 `dead_end`/budget reset 시 `restore_file_from_ref(champion)` 로 working tree 를 champion 으로 되돌리는데, 그 직후 다음 explore 가 다시 champion 위에서 시작 — **이게 C2 가 고치려던 "explore 가 champion 위에서만 시작" 앵커링을 단일 lane 에서 부분적으로 유지**한다(reset 이후엔 어쩔 수 없음). 의도된 동작인지 확인 필요(redesign §86 은 seed=champion 기본을 허용하므로 OK 로 보임).

---

## Coverage check vs HARNESS-REDESIGN §195 change-points table

| §195 row (파일/함수) | 계획 처리 | 평가 |
|---|---|---|
| `runner.py` `RunnerConfig` 필드 추가 | Task 8 Step 1 | **Addressed** (M-2 frozen 주의) |
| `runner.py` `ensure_worktree_ready` champion/job dirty 규칙 분리 | — | **Deferred(safe)** — 단일 lane, worktree 없음. 현 `ensure_worktree_ready`(`runner.py:331-342`)는 metadata-off-git 후에도 OK: `disallowed_candidate_paths`(line 295-304)가 `allowed_path` 외 변경을 막는데, gitignored 된 `runs/` 는 `git status --porcelain --untracked-files=all`(line 282)에서 ignore 되어 안 뜸 → unrelated dirty 오탐 없음. **단 C-1 의 `_init_repo` gitignore 수정 전제.** |
| `runner.py` `rollback_paths` → lineage head 일반화 | Task 8(단일 lane: lineage head==HEAD) | **Addressed** — `rollback_paths`(line 345-353)가 `git restore` 로 HEAD 복원이라 lineage head 와 동일. champion 복원만 새 `gitops.restore_file_from_ref`. 정확. |
| `runner.py` `_decide_iteration` set phase | self-review line 1102 "set layer above scheduler" | **Partial/Gap** — I-1 참조. set phase 가 `_decide_iteration` 에 안 들어가 mode 충돌. |
| `runner.py` `run_iteration` dual decision | Task 8 Step 3 | **Addressed**(설계), 단 C-2 portfolio 오염 미해결 |
| `runner.py` `commit_iteration` code/metadata 분리 | Task 7 | **Addressed**(설계), 단 C-1 테스트 회귀 |
| `state.py` `HarnessState` lineage 필드 | Task 5 | **Addressed** — `champion_ref`, `set_*` 추가. `lineage_ref` 는 단일 lane 이라 불필요(HEAD 가 lineage). OK. |
| `scheduler.py` `decide_mode` set phase 표현 | self-review line 1107 deferred | **Deferred — 위험(I-1/I-3)**. set/scheduler 경합이 prompt 에 영향. |
| `portfolio.py` `global_best` vs `job_best` 분리 | deferred to Phase 2 | **Deferred — 안전하지 않음(C-2/I-3)**. Phase 1 advance 가 global_best 오염. |
| `policy.py` `decide_candidate` → `decide_lineage_progress`+`decide_promotion` | Task 2/3 | **Addressed** (I-5 success 처리 note) |
| 신규 `harness/gitops.py` | Task 6 | **Addressed** — ensure/restore/advance 헬퍼. (계획 §195 의 `prepare_job_worktree`/`promote_to_champion`/`cleanup_worktree` 는 Phase 2.) |
| `cooldown.py` `api:`/`rmapi:` normalize | deferred | **Deferred(safe)** — C2 critical path 밖, 독립 버그. 동의. |
| `scripts/evolve.py` CLI flags | Task 8 Step 4(`runner.main`) | **Addressed** — thin CLI 불변, flag 는 `runner.main` argparse(`runner.py:2216+`). M-3 commit-results 가드 권장. |
| `scripts/verify.sh` runtime multiplier | — | **Missed(out of scope, OK)** — C2 무관. |

**Deferred-and-truly-safe**: worktree 실행, parallel jobs, branch protection, `requires_data`/`integration` marker, `git archive` packaging, archive/islands, cooldown normalize, verify.sh. (HARNESS-REDESIGN §172 timeline 의 "안정화 → 실행경로 → 거버넌스 → 확장" 순서와 정합 — Phase 1 = "상태 분리" 단계.)

**Deferred-but-Phase-1-depends-on-it (재검토 필요)**: portfolio `global_best`/`job_best` 분리(C-2/I-3), scheduler set-phase 인지(I-1).

---

## 요약

설계 골격(두 비교 함수 + 순수 state machine + opt-in 게이팅)은 코드 근거가 정확하고 C2 를 올바른 층위에서 공략한다. 그러나 **Phase 1 이 "Phase 2 로 미룬다" 고 단정한 portfolio/scheduler 분리에 실제로 의존**(C-2, I-1, I-3)하고, **metadata-off-git 이 기존 통합 테스트 한 건을 확정적으로 깨뜨리는데 계획이 그걸 지목·수정하지 않는다**(C-1). 이 셋과 promote 순서 명세(I-2), 통합 테스트 stub(I-4)을 보강하면 구현 착수 가능.
