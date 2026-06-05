# Step 1 Visibility Review — 2026-06-01

대상: Step 1 (Visibility + low-risk policy) 구현 커밋

- `c1f1e08` — harness-derived family/signature
- `43ee9b5` — portfolio + micro_bank + decision trace
- `21e60be` — REPORT 지표 + explore floor 0.3
- `8157313` — 통합 smoke

방법: signature / portfolio-policy / runner decision trace / report-config 영역을
분리해 읽기 전용 병렬 리뷰하고, 본문 코드를 직접 spot-check 했다.

## Findings

### Important

1. `REPORT.md` 가 self-declared lane/fingerprint 를 아직 "정본"이라고 부른다.

   - 위치: `docs/templates/REPORT.md:108`, `docs/templates/REPORT.md:111`
   - 문제: proposal §12/§12.1 에서는 `decisions.jsonl` 의
     harness-derived `harness_family_id` / `harness_signature` 가 다양성 정본이다.
     그런데 D 섹션은 `candidate_meta.json` 기반 lane/fingerprint 를 "정본 다양성
     신호"라고 설명한다.
   - 영향: REPORT 독자가 candidate 자기보고를 다시 정본으로 해석할 수 있다.
   - 권장: D 섹션 제목/본문을 "self-declared comparison" 으로 낮추고, 정본은
     Portfolio Evolution 섹션이라고 명시.

2. `best_coverage` 축이 coverage 의미와 어긋난다.

   - 위치: `harness/portfolio.py:21`
   - 문제: `best_coverage` 를 `error_breakdown.del_ratio` 단일 lower-is-better 축으로
     정의한다. 그러나 proposal §14 는 `length_ratio.*`, `audio_coverage_rate` 도
     coverage 계열 metric 으로 둔다. 기존 runner 도 coverage 를 length/coverage
     관점에서 다룬다.
   - 영향: deletion ratio 만 좋아지고 실제 길이/coverage 가 나빠진 후보가 coverage
     재료로 bank 될 수 있다.
   - 권장: `best_deletion` 으로 이름을 바꾸거나, coverage score 를
     `length_ratio.mean` 의 1.0 근접도 + `del_ratio` + `audio_coverage_rate` 조합으로
     별도 정의.

3. `Portfolio.global_best` 비교가 잘못된 key 를 조회한다.

   - 위치: `harness/portfolio.py:179`
   - 문제: `self.family_best.get(self.global_best)` 로 현재 global best CER 를 찾지만,
     `family_best` 는 `harness_family_id` keyed map 이다. `global_best` 는 `hyp_id` 라서
     보통 lookup 이 실패한다.
   - 영향: 현재 정책상 keep 은 개선 후보라 큰 문제로 드러나지 않지만, portfolio 의
     "global_best = 최저 CER" 불변식은 우연히 정책에 의존한다. `success`/resume/replay
     케이스에서 혼란이 생긴다.
   - 권장: Step 1 의 의도가 "policy keep mirror" 라면 비교를 제거하고 최신 keep 을
     미러링한다고 명시/테스트한다. "최저 CER" 이 의도면 별도 `global_best_entry` 또는
     hyp_id keyed index 로 비교한다.

4. `decisions.jsonl` 이 early reject 를 기록하지만 reject 원인을 잃는다.

   - 위치: `harness/runner.py:1188`, `harness/runner.py:1318`
   - 문제: `_persist_decision()` 이 `status` 만 받아 `decision_reason` 을 `reject` 로
     저장한다. candidate command failure, format reject, scope violation, post-verify
     scope violation, verify failure 가 모두 같은 값으로 접힌다.
   - 영향: decision trace 의 핵심 목적이던 "scheduler/guard/LLM 실패 분리"가 early
     reject 에서 약해진다.
   - 권장: `commit_iteration(..., reason=..., attempt_status=...)` 또는 lightweight
     sidecar 를 추가해 `format_reject`, `scope_violation`, `verify_fail`,
     `candidate_command_fail` 을 기록한다.

5. signature hard grouping 이 comment/string text 에 영향을 받는다.

   - 위치: `harness/signature.py:118`, `tests/test_signature.py:31`
   - 문제: added text 전체를 lower-case substring match 하므로 주석/문자열의 키워드도
     `api_keywords` / `stage_tokens` 에 들어간다. 현재 테스트도 `# temperature fallback`
     comment 를 feature 로 인정한다.
   - 영향: candidate 가 주석 텍스트만 바꿔 family/signature 를 흔들 수 있어
     self-declared metadata 를 hard gate 에 쓰지 않는 설계 취지가 약해진다.
   - 권장: Python AST/tokenize 기반으로 comment 제거, 문자열 literal 은 별도 낮은
     신뢰도 feature 로 분리.

6. deletion-only / ablate diff 가 unrelated family 로 false-merge 될 수 있다.

   - 위치: `harness/signature.py:95`
   - 문제: feature extraction 이 `+` added lines 중심이다. 순수 삭제 또는 삭제-heavy
     ablate diff 는 region token 정도만 남아 서로 다른 ablation 이 같은 signature/family
     로 합쳐질 수 있다.
   - 영향: false-split 선호라는 proposal §5.3 원칙과 충돌한다. 특히 Step 3 이후
     `ablate` mode 평가가 흐려질 수 있다.
   - 권장: removed lines 에서도 keyword/param/structural marker 를 추출하되
     `removed:*` prefix 로 added 와 분리한다.

7. `EXPLORE_RATIO_FLOOR = 0.3` 은 visibility-only 변경이 아니다.

   - 위치: `harness/config.py:37`
   - 문제: Step 1 설명은 additive visibility 를 강조하지만 explore floor 변경은 실제
     candidate prompt mode 비율을 바꾸는 behavior change 다.
   - 영향: 다음 run 결과를 "logging 추가 효과"와 "policy knob 변경 효과"로 분리하기
     어렵다.
   - 권장: 문서/REPORT 에 `Step 1b policy knob` 로 명시하거나, run 비교에서는 이
     변경을 별도 변수로 취급한다.

### Medium

8. `portfolio_evolution_section()` 의 proposal §12 지표가 아직 부분 구현이다.

   - 위치: `scripts/analyze_run.py:891`
   - 구현됨: `family_count`, `repeated_failed_family_count`, decision distribution,
     `iter_to_*`, family table, metric_best summary.
   - 미구현/대기: `mode_distribution`, `mode_success_rate`, `combine_success_rate`,
     `portfolio_usage`.
   - 판단: Step 3 scheduler 전이라 일부 `n/a` 는 합리적이지만, Step 1 완료 범위의
     "mode metadata 저장"과는 차이가 있다. REPORT 에 pending 상태를 더 명확히 표시하는
     편이 낫다.

9. `decisions.jsonl` 문서와 구현의 iteration 범위가 다르다.

   - 위치: `docs/proposals/2026-06-01-from-scratch-discovery-harness.md:631`
   - 문제: 문서는 "evaluated iteration 당 한 줄"이라고 쓰지만, runner 는 format reject
     등 non-evaluated early reject 도 기록하려 한다.
   - 권장: "candidate iteration 당 한 줄, `evaluated=false` 가능"으로 수정하고,
     `attempt_status` / `evaluated` 필드를 schema 에 추가.

10. portfolio file 이 없을 때 REPORT 가 일부 값을 0처럼 보이게 만든다.

    - 위치: `scripts/analyze_run.py:926`, `scripts/analyze_run.py:934`
    - 문제: `decisions.jsonl` 은 있는데 portfolio json 이 없거나 깨진 경우
      `micro_bank_count=0` 으로 렌더링된다.
    - 권장: portfolio missing/invalid 를 명시하고, `micro_bank_count=n/a` 로 둔다.

11. signature 의 structural marker 구현이 proposal 보다 좁다.

    - 위치: `harness/signature.py:77`
    - 문제: proposal 은 fallback loop, merge/split logic, normalization table 등을
      언급하지만 현재 schema 는 region/api/param/stage 중심이다.
    - 판단: MVP 로는 수용 가능. Step 3 scheduler/cooldown 전에는 추가 테스트와 함께
      보강하는 편이 좋다.

### Minor

12. stale comment: runner 의 explore floor 설명이 `≥20%` 로 남아 있다.

    - 위치: `harness/runner.py:70`
    - 실제 값: `harness/config.py:37` 에서 `0.3`.

13. `docs/_workmap/step1-codemap.md` 가 TEMP 라고 쓰였지만 아직 남아 있다.

    - 위치: `docs/_workmap/step1-codemap.md:1`
    - 판단: review/implementation 작업 중이면 괜찮지만, Step 1 완료 커밋 전에는 삭제
      또는 archive 여부를 결정해야 한다.

## Verified Strengths

- `decisions.jsonl` 은 append mode 로 작성된다.
- decision trace 와 portfolio 는 같은 `commit_iteration()` 경로에서 stage 된다.
- `_persist_decision()` 은 best-effort 로 실패해도 commit path 를 깨지 않게 되어 있다.
- `micro_bank` 는 runner status/state 를 바꾸지 않고 post-analysis 로만 작동한다.
- keep/reject 의 primary decision 은 여전히 `policy.decide_candidate()` 에서 나온다.
- signature hashing 은 sorted feature token 기반이라 결정적이다.
- family assignment 는 self-declared metadata 가 아니라 harness-derived feature token 을
  사용한다.

## Tests Reported During Review

Subagents ran the following focused checks:

```bash
pytest tests/test_signature.py tests/test_runner_decision_trace.py -q
pytest -q tests/test_portfolio.py tests/test_runner_decision_trace.py tests/test_harness_policy.py tests/test_harness_runner.py::test_step1_decision_trace_committed_and_survives_next_iter
pytest -q tests/test_analyze_smoke.py tests/test_runner_decision_trace.py tests/test_portfolio.py tests/test_signature.py
pytest -q tests/test_runner_decision_trace.py tests/test_harness_runner.py -k 'decision_trace or decisions_jsonl or format_reject_when_no_yaml'
pytest -q tests/test_runner_decision_trace.py tests/test_harness_runner.py::test_step1_decision_trace_committed_and_survives_next_iter tests/test_harness_runner.py::test_run_iteration_format_reject_when_no_yaml
```

Reported results were passing.

## Recommendation

50회 run 전에 최소한 다음은 고치는 편이 낫다.

1. REPORT 의 self-declared "정본" 표현 수정.
2. `Portfolio.global_best` key mismatch 수정 또는 mirror semantics 로 단순화.
3. `decisions.jsonl` 에 `attempt_status` / actual reject reason / `evaluated` 추가.
4. `best_coverage` 축 이름 또는 계산식 정정.
5. signature comment/string spoofing 과 deletion-only diff 테스트 추가.

나머지 mode success / combine success / portfolio usage 는 Step 3 scheduler 도입 시
완성해도 된다.
