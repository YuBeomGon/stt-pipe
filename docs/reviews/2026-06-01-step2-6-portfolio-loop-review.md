# Step 2~6 Portfolio Loop Review — 2026-06-01

대상:

- `9411b88` — scheduler 5+1모드 + evaluated_count 코어
- `3cbc135` — core loop 배선, portfolio parent 주입
- `3ed9fa1` — signature 보강 + soft cooldown + runtime static guard
- `a2d3232` — full codemap 상태 정리

검토 기준:

1. proposal `2026-06-01-from-scratch-discovery-harness.md` 와 full codemap 이 실제
   구현과 맞는가
2. 50~100회 run 전에 막을 버그가 있는가
3. 과구현 또는 구현-문서 drift 가 있는가

## Findings

### Important

1. `--iters` 는 아직 evaluated iteration 수가 아니라 attempt 수다.

   - 위치: `harness/runner.py:1896`
   - 구현: `for _ in range(config.iterations)` 로 raw attempt 를 돈다.
   - 문서/목표: proposal §13 / full codemap Step 7 은 50~100 **evaluated iter** 를
     말한다. `HarnessState.evaluated_count` 는 추가됐지만, job loop budget 은 아직
     `state.evaluated_count` 기준이 아니다.
   - 영향: format reject / command fail / scope reject 가 많으면 `--iters 50` 이
     50개 scored candidate 를 보장하지 않는다. run 결과의 "50회" 해석이 흔들린다.
   - 권장: `start_evaluated = state.evaluated_count` 를 잡고
     `state.evaluated_count - start_evaluated < config.iterations` 동안 돌도록 변경한다.
     별도 `max_attempts` 안전장치를 두면 좋다.

2. plateau/no-improvement 판단은 아직 attempt-count 기반이다.

   - 위치: `harness/state.py:53`, `harness/state.py:55`,
     `harness/runner.py:1067`, `harness/scheduler.py:122`
   - 구현: `iters_since_best_update` 는 `advance()` 에서 매 attempt 증가한다.
   - 문제: scheduler progress 는 `evaluated_count` 를 쓰지만, plateau override 는 raw
     attempt 기반이다.
   - 영향: format reject, command fail, scope reject 만 누적돼도 no-improvement 로
     간주되어 plateau mode 가 켜질 수 있다. proposal §4 의 "evaluated iteration 기준"
     해석과 다르다.
   - 권장: `evaluated_since_best_update` 를 별도 필드로 두거나,
     decisions/state 에서 evaluated reject/keep 만 세어 plateau 를 판정한다.

3. REPORT 는 Step 3 scheduler 도입 이후 상태를 아직 반영하지 않는다.

   - 위치: `scripts/analyze_run.py:891`, `scripts/analyze_run.py:941`
   - 구현: `portfolio_evolution_section()` 이 `mode_distribution` 을
     `n/a (scheduler 미도입 — Step 3)` 으로 고정한다.
   - 문제: Step 3 이 구현됐고 `decisions.jsonl` 에 `chosen_mode` 가 들어가는데,
     REPORT 는 mode distribution / mode success / portfolio usage / combine success 를
     집계하지 않는다.
   - 영향: 이번 변경의 핵심 목표인 "다양하게 탐색했는가"를 run 직후 제대로 볼 수 없다.
   - 권장: `decisions.jsonl` 에서 `chosen_mode`, `final_decision`, `parent_shortlist`,
     `portfolio_slots_updated` 를 집계해 proposal §12 지표를 채운다.

4. decision trace 가 proposal §12.1 의 디버깅 정보를 충분히 남기지 않는다.

   - 위치: `harness/runner.py:1112`, `harness/runner.py:1121`,
     `harness/runner.py:1541`
   - 구현: sidecar/decision trace 에 `scheduled_mode`, `chosen_mode`, `override`,
     parent `hyp_id/family` 정도만 남긴다.
   - proposal: `deficits_before`, `override_candidates`, parent score/why,
     `parents_chosen`, `compatible_check` 를 예시 schema 로 둔다.
   - 영향: "왜 이 mode/parent 를 골랐나"를 나중에 재현 검산하기 어렵다. 특히
     scheduler 버그와 schedule 설계 실패를 분리하는 목적이 약해진다.
   - 권장: 최소한 `deficits_before` 또는 base schedule replay 정보, parent score/why,
     `parents_chosen`, `compatible_check` 를 추가한다. 구현을 단순화할 거라면 proposal
     schema 를 MVP schema 로 낮춰야 한다.

5. scheduler override precedence 가 proposal 보다 축소되어 있다.

   - 위치: `harness/scheduler.py:105`
   - proposal: repair > infeasible/cooldown > diversity stall > plateau > opportunity >
     base (`docs/proposals/...:257`)
   - 구현: no_best > repair > diversity_stall > plateau > feasible base > fallback.
   - 빠진 축: cooldown 제거, combine 실패 감소, micro improvement refine, axis complement
     combine, complex best ablate, metric_best reuse.
   - 영향: codemap의 "override precedence 완료" 표현과 실제 구현 사이에 차이가 있다.
     동작 자체는 MVP 로 수용 가능하지만, proposal-level Step 3 완료라고 보기에는 과장이다.
   - 권장: 문서를 "MVP precedence" 로 낮추거나 opportunity/cooldown override 를 실제
     구현한다.

### Medium

6. `combine` parent 호환성이 너무 약하다.

   - 위치: `harness/portfolio.py:152`, `harness/portfolio.py:159`
   - 구현: 서로 다른 `harness_family_id` 에서 CER 낮은 후보 2개를 고른다.
   - proposal: diff touched-region overlap, stage complement, axis complement,
     guard/runtime 통과를 compatible parent 조건으로 둔다.
   - 영향: 서로 다른 family 라도 같은 decode kwarg 를 충돌되게 바꾼 후보를 combine 할 수
     있다.
   - 권장: MVP 로 유지해도 되지만 "compatible 구현 완료"가 아니라 "distinct family
     MVP" 로 문서화한다. Step 7 전에 최소 diff overlap / same changed param 충돌 검사는
     넣는 편이 낫다.

7. parent prompt 가 parent 의 strength/weakness/axis 정보를 거의 주지 않는다.

   - 위치: `harness/runner.py:1096`, `harness/runner.py:1104`
   - 구현: family id, CER, `public_summary`, diff 를 넣는다. 그러나 current
     portfolio entry 에는 `public_summary` 가 기본적으로 없어 빈 문자열이 된다.
   - 영향: combine/refine directive 는 "다른 axis 의 strong parent"를 전제로 하지만,
     candidate 는 어떤 parent 가 어떤 축을 개선했는지 diff 를 읽어 추론해야 한다.
   - 권장: parent block 에 `axis_metric`, `portfolio_slots_updated` 또는
     `metric_best` slot reason 을 짧게 넣는다.

8. repair event 가 proposal 보다 좁다.

   - 위치: `harness/runner.py:1052`, `harness/runner.py:1069`
   - 구현: 직전 `attempt_status == "verify_fail"` 일 때만 repair.
   - proposal: guard fail, hallucination 증가, runtime 폭증, 특정 축 악화 보정.
   - 영향: evaluated reject 중 hallucination/runtime/axis regression 은 repair 로 이어지지
     않는다.
   - 권장: decisions record 의 `axis_metrics` / guard failure reason 을 이용해
     `repair_event` 를 넓힌다. 어렵다면 MVP 한계로 문서화.

9. proposal Step 4 는 multi-step prompt처럼 남아 있지만 구현은 single-call MVP 다.

   - 위치: `docs/proposals/2026-06-01-from-scratch-discovery-harness.md:438`,
     `docs/proposals/2026-06-01-from-scratch-discovery-harness.md:748`
   - 구현: mode directive 를 single prompt 안에 주입한다.
   - 판단: single-call MVP 선택은 타당하다. 다만 proposal 운영 섹션이 아직
     `Ideate+Plan -> Implement`, `plan/idea/implementation notes 저장`을 말해 문서 drift 가
     있다.
   - 권장: proposal 에 "현재 구현은 single-call MVP, 2-call은 후속"이라고 명시.

10. static runtime guard 는 accidental guard 로는 적절하지만, false positive 여지가 있다.

    - 위치: `harness/verify.py:36`
    - 구현: `open`, `read_text`, `write_text`, `data/`, `runs/`, `baseline/`, `judge/`,
      `holdout`, `.git` 등을 정규식으로 차단한다.
    - 판단: proposal 이 "accidental/explicit contamination guard" 로 정의하면 적절하다.
      다만 문자열/주석 내 forbidden path 도 차단하므로 candidate 가 설명 주석에
      `runs/` 같은 단어를 쓰면 reject 될 수 있다.
    - 권장: 50회 run 중 static guard false positive 가 나오면 tokenize 기반으로 낮춘다.

### Minor / Cleanup

11. full codemap 자체에 상태 drift 가 있다.

    - 위치: `docs/_workmap/full-codemap.md:3`, `docs/_workmap/full-codemap.md:10`,
      `docs/_workmap/full-codemap.md:94`, `docs/_workmap/full-codemap.md:151`
    - 문제: 상단은 Step 1~6 완료라고 쓰면서 바로 아래는 "Step 1 fix batch pending" 이라고
      쓴다. Step 5 heading 은 "hard gate" 지만 상태는 soft cooldown / hard gate 후속이다.
    - 권장: "Step 5 = soft cooldown complete, hard gate deferred" 로 일관화.

12. legacy explore/exploit 코드와 새 scheduler 가 공존한다.

    - 위치: `harness/runner.py:481`, `harness/runner.py:504`, `harness/runner.py:932`
    - 판단: 현재 runtime path 는 새 scheduler 를 쓰지만, legacy `_iteration_mode` /
      `_EXPLOIT_DIRECTIVE` / 관련 tests 가 남아 있다. 당장 버그는 아니지만 다음 작업자가
      어느 scheduler 가 정본인지 헷갈릴 수 있다.
    - 권장: 50회 run 전 최소 주석으로 legacy 표시하거나, run 후 cleanup.

13. `docs/_workmap/` 는 TEMP 라고 되어 있으므로 final cleanup 대상이다.

    - 위치: `docs/_workmap/full-codemap.md:1`
    - 판단: 현재 구현 중에는 유지 가능. Step 7 회고 후 SSOT/proposal/STATUS 에 흡수하고
      삭제하는 편이 좋다.

## Alignment Summary

| Step | 리뷰 판단 |
|---|---|
| 1 visibility | 대체로 구현됨. 이전 리뷰 fix 도 대부분 반영됨 |
| 2 portfolio parent | 기본 배선됨. parent scoring/axis reason/compatibility 는 MVP 수준 |
| 3 scheduler | 5+1 mode 와 evaluated_count 필드/sidecar 는 있음. job budget/plateau/report 는 아직 evaluated 기준과 불일치 |
| 4 mode directive | single-call MVP 로 구현됨. proposal 의 multi-step 표현과 drift 있음 |
| 5 cooldown | soft warning 구현됨. hard gate 는 후속으로 봐야 함 |
| 6 static guard | accidental runtime purity guard 로 구현됨 |
| 7 run | 가능은 하나, 50 evaluated iter 로 해석하려면 #1/#2/#3 보강 권장 |

## Overengineering Check

과구현으로 보이는 큰 구조는 없다. `scheduler.py`, `cooldown.py`, `portfolio.py` 분리는
각각 순수함수 테스트가 가능해서 적절하다. 다만 `decisions.jsonl` schema 는 proposal 이
요구한 것보다 구현이 작고, legacy explore/exploit 코드가 남아 있어 복잡도가 중복되어
보인다. 현재 과구현보다 **부분 구현을 완료/문서화하지 않은 drift** 가 더 큰 리스크다.

## Verification

직접 실행:

```bash
git diff --check
python3 -m py_compile harness/runner.py harness/scheduler.py harness/portfolio.py harness/cooldown.py harness/signature.py harness/verify.py scripts/analyze_run.py
pytest -q tests/test_scheduler.py tests/test_portfolio.py tests/test_cooldown.py tests/test_runtime_purity.py tests/test_signature.py tests/test_runner_decision_trace.py tests/test_harness_runner.py -k 'scheduler or portfolio or cooldown or runtime_purity or signature or decision_trace or step1 or prompt_injects or plateau_mode or explore_mode or format_reject_when_no_yaml'
```

결과:

- diff check 통과
- py_compile 통과
- focused tests: `67 passed, 59 deselected`

전체 `pytest -q` 는 현재 shell 환경에 `rapidfuzz` 가 없어 `tests/test_metrics.py` import 에서
중단됐다. 사용자가 보고한 `210 green` 은 의존성이 갖춰진 환경의 결과로 보이며, 이
세션에서는 전체 suite 를 재현하지 못했다.

## Recommended Before 50~100 Run

최소 권장:

1. `run_job()` 을 evaluated budget 기준으로 바꾸거나, CLI/문서에서 `--iters` 가 attempt
   budget 임을 명시한다.
2. plateau 판단을 evaluated no-improvement 기준으로 바꾼다.
3. REPORT 에 실제 `mode_distribution`, `mode_success_rate`, `portfolio_usage`,
   `combine_success_rate` 를 집계한다.
4. codemap/proposal 을 single-call MVP, soft cooldown, distinct-family combine MVP 로
   정정한다.

위 4개를 처리하면 50회 run 결과를 해석할 수 있는 최소 관측성이 확보된다.
