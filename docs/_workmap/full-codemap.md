# Full Code Map — Portfolio Evolution Harness (Step 1~7, TEMP)

> proposal `2026-06-01-from-scratch-discovery-harness.md` 전체 구현 지도.
> Step 1 은 **구현 완료, fix batch pending(미커밋)**. 본 문서는 **Step 2~7**.
> `docs/_workmap/` 는 임시 — 전체 완료 후 삭제.

## ⚠ 구현 전 확정해야 할 2가지 (codex 리뷰)

**A. evaluated_count ↔ attempt 분리.** `state.advance()` 가 매 attempt 마다
`state.iteration` 을 올린다(format/command reject 포함). scheduler progress 축은
**evaluated iter**(verify 가 report 를 낸 iter)여야 §4.2 가 맞다. →
`state.iteration` 은 attempt 카운터(hyp_id dir 유니크용)로 두고, scheduler 는
**evaluated_count** 를 별도로 쓴다(state 필드 추가 또는 decisions.jsonl `evaluated=true`
집계 파생). hyp_id 번호는 attempt 기준 유지.

**B. scheduler 결정 전달 = sidecar.** mode/parent 결정은 prompt 생성 전에 나오는데
`_persist_decision` 은 commit 시점 disk-reload 라 모른다. → mode 결정 직후
`runs/<hyp_id>/scheduler_decision.json`(scheduled_mode/chosen_mode/parent_shortlist/
override/deficits) 를 쓰고, `_persist_decision` 이 candidate.diff/score_report 읽듯
읽어 decisions.jsonl 에 채운다. self-contained 패턴 유지(6 호출부 불변).

## 핵심 사실: 대부분 "확장"이지 "신설"이 아니다

현재 harness 에 이미 있는 것 (grep 확인):
- `runner._iteration_mode(state)` — **explore/exploit 2모드** 반환 (L504)
- `runner._explore_ratio` / `_is_explore_iter` — 감쇠 explore 비율 + error-diffusion
  accumulator (resume-safe, RNG 없음) (L481/489)
- `runner._promising_rejects` / `_format_synthesis_block` — 축 개선 reject 를 diff 와
  함께 prompt 에 주입 (= portfolio parent 주입의 원형) (L533/605)
- `runner._axis_scores` — sub/coverage/hal 축 추출 (L516)
- `runner.build_candidate_prompt` — profile + recent + error_profile + ledger +
  **mode directive** + synthesis_block 조립 (L955)
- `_EXPLORE_DIRECTIVE` / `_EXPLOIT_DIRECTIVE` — 모드별 지시문 (L917~)
- Step 1 산출: `harness/signature.py`, `harness/portfolio.py`(적재),
  `runner._persist_decision`(decisions.jsonl), `harness/config.py` knobs

→ Step 2/3/4 = **이 2모드 기계를 5모드 + portfolio parent 로 확장.**
Step 5/6 = 새 gate 두 개. Step 7 = run.

---

## Step 2 — Portfolio 소비 (parent 선택 + prompt 주입)

**목표**: portfolio 를 적재만 하던 걸 **parent 로 꺼내 prompt 에 주입**. proposal §3.2/§4.4.

| 변경 | 위치 | 작업 |
|---|---|---|
| portfolio 로드 헬퍼 | `runner.py` 신규 `_load_portfolio(config)` | `Portfolio.load(summary/<job>_portfolio.json)` |
| parent shortlist + scoring | `runner.py` 신규 `_select_parents(portfolio, mode, state)` | §4.4 점수식(cer_gain/axis/novelty − runtime/guard/complexity). MVP 는 단순 정렬: family_best/metric_best/micro_bank 중 mode 별 후보 |
| parent diff 주입 | `build_candidate_prompt` 확장 | 기존 `synthesis_block`(=`_promising_rejects`)을 **portfolio parent 기반으로 일반화**. mode 별 1~3개 entry 의 `public_summary`+diff 주입 |
| runtime 노출 중립화 | `build_candidate_prompt` | family 는 `family_NNN`(harness id)로만, `private_label` 금지 (§3.2/§5.4) |

- `_promising_rejects` 는 이미 "축 개선 reject + diff" 를 하므로 **`rejected_promising`
  슬롯의 원형**. portfolio.rejected_promising / micro_bank 를 함께 쓰도록 source 확장.
- **MVP 단순화**: parent 는 harness 가 1개(또는 combine 시 2개) 지정해 prompt 에 박는다.
  LLM 자유 선택 아님 (§4.4 / §2.5).

## Step 3 — Scheduler (2모드 → 5모드 + override)

**목표**: explore/exploit → explore/refine/combine/ablate/repair(+plateau). proposal §4.

| 변경 | 위치 | 작업 |
|---|---|---|
| 모드 enum 확장 | `runner._iteration_mode` 재작성 → `_decide_mode(state, portfolio)` | 5+1 모드 반환 |
| evaluated_count | `state.py` + `run_iteration` (resolution A) | scheduler progress 축. attempt 와 분리 |
| multi-class schedule | 신규 `_mode_schedule(progress)` + deficit round-robin | `_is_explore_iter` 의 accumulator 를 **다중 클래스로 일반화**(§4.2). progress=evaluated_count/iters. 구간 0~20/20~70/70~100% 비율 |
| scheduler sidecar | `run_iteration` mode 결정 직후 (resolution B) | `runs/<hyp_id>/scheduler_decision.json` 기록 → `_persist_decision` 가 읽음 |
| override 우선순위 | 신규 `_apply_overrides(base_mode, state, portfolio)` | **§4.3 표 + precedence(리뷰 fix)**: 1 repair > 2 infeasible/cooldown > 3 diversity stall > 4 plateau > 5 opportunity > 6 base. tie-break: infeasible 제거 → 최근 덜 쓴 → deficit 큰 → 고정 order |
| combine 호환성 | 신규 `_compatible_parents(portfolio)` (§4.5) | diff touched-region 겹침/같은 kwarg/ guard 통과 여부. `signature.Features` 재사용 |
| decisions 기록 | `_persist_decision` | `scheduled_mode`/`chosen_mode`/`override_applied` 채움(현재 None) |
| budget 단위 | scheduler 는 **valid evaluated iter 기준**(§4.2) | format/command reject 는 quota 미소모 |

- **첫 run combine ≤15%** (§4.2). progress 는 `state.iteration / config.iterations`.

## Step 4 — Prompt steps (MVP: single-call)

**목표**: mode 별 prompt. proposal §5/§6.

- **MVP 결정**: §5.2 의 2-call(Ideate+Plan→Implement) **보류**. 현재 single-call
  구조 유지하고 mode directive 만 5종으로 확장 → 추가 LLM 호출 0, 과금/복잡도 최소.
- `_EXPLORE_DIRECTIVE`/`_EXPLOIT_DIRECTIVE` 옆에 `_REFINE_/_COMBINE_/_ABLATE_/_REPAIR_/
  _PLATEAU_DIRECTIVE` 추가. `build_candidate_prompt` 가 mode→directive 매핑.
- combine directive 는 주입된 **parent 2개를 합치라**고 지시. ablate 는 best 에서
  불필요부 제거. repair 는 직전 실패 축 보정.
- candidate.md profile 에 mode 개념 한 줄 + 출력계약은 기존 YAML 유지(필드 호환).
- 2-call 분리는 별도 후속(§5.2) — REPORT 의 mode_success 데이터 본 뒤.

## Step 5 — Cooldown (hard gate)

**목표**: 반복 dead-end 차단. proposal §8/§5.

| 변경 | 위치 | 작업 |
|---|---|---|
| signature cooldown 집계 | 신규 `runner._cooldown_state(config)` | decisions.jsonl 에서 `harness_signature`/`harness_family_id` 별 reject 횟수 집계(이미 저장됨) |
| hard gate | `run_iteration` **format-check 단계 근처**(L1462~) | harness-derived signature 가 2회 reject면 verify **전** reject. self-declared fingerprint 2회는 prompt warning(soft). family 5회는 `what_is_new` 요구 |
| prompt warning | `build_candidate_prompt` | cooldown 중 family/signature 를 "avoid" 로 노출 |
| 기록 | `_persist_decision` | `active_cooldowns` 채움 |

- 단, signature 가 hard gate 가 되므로 **리뷰 #5/#6(주석/deletion 취약)을 여기 전에 보강**
  (현재 xfail 테스트로 고정됨 → AST/tokenize + removed 라인 추출).

## Step 6 — Runtime purity (static guard, *accidental* guard)

**목표**: candidate 코드의 평가 중 파일 I/O **실수/명시적 오염 방지**. proposal §9.
정적 검사라 결정적 우회는 가능 — anti-cheat 완전방어가 아님(holdout 은 어차피
chmod 000). "accidental/explicit contamination guard" 로 문서화.

- `verify.check_workspace_static` (verify.py:57) **확장**: 기존 backend/profile 정규식에
  `open/Path.read_text/os.listdir/glob/subprocess/socket/importlib/eval/exec` +
  문자열 `data//docs//runs//baseline//judge//assets/` deny 추가.
- **주의**: stub 자신이 `from frozen.asr_backend import ...` 하므로 frozen import 는
  allowlist. monkeypatch/런타임 allowlist 는 후속(§9). 테스트로 false-positive 고정.

## Step 7 — Run 50~100 evaluated iter

- `scripts/evolve.py --job-id phase3_006 --iters 50 --candidate-cmd "claude -p"
  --commit-results` (operator, GPU + 과금). swap_claude.sh 로 .claude active body 정렬.
- 결과 → `analyze_run` REPORT(portfolio 섹션 자동) → 회고. variance 필요시 반복.

---

## 구현 순서 / 의존

```
Step 2 (parent 주입)  ─┐
Step 3 (scheduler)    ─┴→ 둘이 강결합 (mode 가 parent 선택 좌우) → 한 묶음으로
Step 4 (mode directive) ─→ 2/3 위에 directive만 (single-call MVP)
Step 5 (cooldown)     ─→ #5/#6 signature 보강 먼저 → 그 다음 hard gate
Step 6 (static guard) ─→ 독립, 아무 때나 (verify.py 단일 함수)
Step 7 (run)          ─→ 2~6 후
```

권장: **2+3+4 한 묶음(core loop) → smoke → 5 → 6 → 7.**
core loop 까지만 돼도 "다양 탐색 + parent 조합" 행동이 나오므로, 거기서 한 번 smoke
하고 cooldown/guard 를 얹는다.

## 미결/MVP 결정 (구현 중 확정)

> **확정됨(구현 전 결정, 위 ⚠ 블록)**: A. evaluated_count↔attempt 분리,
> B. scheduler 결정 sidecar. 아래는 남은 MVP 튜닝 노브.

1. parent scoring 가중치 — MVP 는 단순 정렬, REPORT 보고 튜닝.
2. combine 2-call 분리 여부 — 보류(single-call).
3. compatible 판정을 signature.Features 재사용으로 — touched-region Jaccard.
4. cooldown hard gate 임계(signature 2회) — 첫 run 후 false-positive 보고 조정.
5. #5/#6 AST 보강을 Step 5 직전에 — xfail 해제와 함께.

## 검증 원칙 (Step 1 과 동일)
- 각 step: 순수함수 먼저 TDD(scheduler/parent/cooldown 집계는 전부 순수함수화 가능).
- keep/reject **정책 불변** 유지 — scheduler 는 "무엇을 시도할지"만 바꾸고
  "무엇을 채택할지"(`policy.decide_candidate`)는 그대로.
- 전체 스위트 green + 모드 분배/override precedence 단위테스트.
- decisions.jsonl 로 "내 의도대로 갔나" 사후 검증.
