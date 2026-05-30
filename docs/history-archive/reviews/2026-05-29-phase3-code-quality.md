# Phase 3 — Code Quality Review (2026-05-29)

## Scope

- HEAD: `3696d88 fix(harness): N1 verify 중 scope 오염 차단 + C3 정적 guard 한계 문서화`
- Whole-codebase quality review (not diff). Prior diff reviews
  (`docs/reviews/2026-05-29-phase3-harness-review.md`,
  `docs/reviews/2026-05-29-phase3-harness-followup.md`) 의 resolved
  finding 은 회귀 점검만 하고 재나열하지 않음.
- Reviewer: subagent via superpowers:requesting-code-review.
- 코드 수정 없음.

## Method

4 lens (SSOT · 복잡도 · 중복 · dead code) 로 분리해서 `harness/*.py`,
`scripts/{evolve,verify_check,append_history}*`, `judge/`, `frozen/`,
`workspace/`, `tests/test_harness_*`, `tests/test_verify_check.py` 를 정독.
도구:

- `ruff check --select F401,F841,ARG …` — unused import / variable / argument.
- `ruff check --select C901` — cyclomatic complexity (default cap 10).
- `grep -rn` — 상수 / 경로 / 상태 문자열 / 배치 이름 / 모듈 import 추적.
- Run scope 는 (a) 4 lens (b) 사전 review 에서 *resolved* 라 표시된 항목의 회귀
  여부 확인. pytest 재실행 없음 (직전 follow-up 에서 80 통과 확인).

---

## Findings by Lens

### Lens 1 — SSOT (single source of truth)

#### L1-1 — `runs/_summary/` 가 코드 4 곳, 테스트 16 곳에 literal로 산재 (worst)

`harness/runner.py:34` 가 `summary_dir: Path = Path("runs/_summary")` 로 단일 상수를
선언했지만, 실제 코드 안에서는 그 상수를 거의 안 쓴다:

- `harness/runner.py:155` — `("runs", "_summary")` 튜플 literal (rollback 판정).
- `harness/runner.py:107-108`, `:407` — 주석/docstring.
- `harness/history.py:30`, `:57`, `:81` — `Path("runs/_summary/HISTORY.md")` 디폴트
  3 회 반복.
- `tests/test_harness_runner.py` — `"runs/_summary"` literal 9 회.

결과: `summary_dir` 를 옮기려면 (예: `.harness/summary/` 로) 위 모든 지점을
손으로 동기화해야 한다. SSOT가 *선언만* 되고 *참조*가 안 되는 패턴.

**수정 방향**: `harness/history.py` 가 `RunnerConfig.summary_dir` 를 인자로
받게 하거나 (현재는 절대경로 default), `harness/__init__.py` 에 모듈 상수
`SUMMARY_DIR = Path("runs/_summary")` 를 두고 모든 default 가 거기를 참조. 튜플
literal (`runner.py:155`) 도 `SUMMARY_DIR.parts` 로 환원.

#### L1-2 — `absolute_delta_fallback = 0.01` 이 4 곳에 중복

- `harness/policy.py:17` (`PolicyConfig.absolute_delta_fallback`).
- `harness/runner.py:40` (`RunnerConfig.absolute_delta_fallback`).
- `harness/runner.py:519` (argparse default).
- `scripts/analyze_run.py:36` (`_NOISE_DEFAULT_DELTA`).
- `scripts/evaluate_holdout.py:33` (`_NOISE_DEFAULT_DELTA`).
- `scripts/measure_sigma.py:133` (string hint).

Runner 가 policy 의 default 를 *복제* 한 뒤 다시 PolicyConfig 에 주입
(`runner.py:469`) — 한 번 한 곳에서 떨어진 값으로 박힐 위험. 더 큰 문제는
analyze_run / evaluate_holdout 이 자기만의 `_NOISE_DEFAULT_DELTA` 를 들고 있어
정책이 바뀌면 4 군데 모두 수정 필요.

**수정 방향**: `harness/policy.py` 에 `ABSOLUTE_DELTA_FALLBACK = 0.01` 모듈
상수, `PolicyConfig` default 와 analyze_run / evaluate_holdout 모두 이 상수
import. `RunnerConfig.absolute_delta_fallback` 필드 자체 제거 — policy 에 항상
default `PolicyConfig()` 를 넘기고 CLI override 만 PolicyConfig 직접 구성.

#### L1-3 — Batch name (`AIG_녹취반출_20250715`) 이 9 곳에 산재

`harness/runner.py:37`, `harness/verify.py:34`, `:182`,
`scripts/{verify.sh:18, verify.sh.alt:9, measure_sigma.py:159, :166,
analyze_run.py:37, evaluate_holdout.py:32, measure_baseline.py:198,
build_audio_profile.py:139, :30}`. holdout (`_20250813`) 도 5 곳에 산재
(`evaluate_holdout.py:31`, `seal_holdout.sh:13`, `tests/test_claude_phase3_settings.py:67,:197`,
`build_audio_profile.py:30`, `measure_baseline.py:39`).

코드 안 4 곳은 모두 같은 디폴트 ("eval = 20250715, holdout = 20250813"). 운영
시점에 batch ID 가 바뀔 가능성은 낮지만 (현 프로젝트 고정 데이터), 두 batch 의
*의미* (eval / holdout) 가 코드에 type-level 로 인코드 안 됨 — 누가
`AIG_녹취반출_20250813` 을 verify 에 넘기면 sealing 우회.

**수정 방향**: `harness/__init__.py` 에 `EVAL_BATCH`, `HOLDOUT_BATCH` 상수.
`build_audio_profile.py:30` / `measure_baseline.py:39` 의
`_FORBIDDEN_BATCHES = {"AIG_녹취반출_20250813"}` 도 같은 상수 참조. CLI override
받을 때 deny-list 체크 한 곳에서.

#### L1-4 — Static backend deny-list 가 AST walk + regex 두 곳에 split

`harness/verify.py:19-26` 의 `_BACKEND_RE` 는 `import|from|importlib|__import__|
from_pretrained|Whisper(` 6 가지 패턴. `:64-74` 의 AST walk 는 `Import` /
`ImportFrom` 의 root 만 `{"ctranslate2", "transformers"}` 와 비교.

문제:
- 두 deny-list 모듈 이름 (`ctranslate2`, `transformers`) 이 AST 와 regex 양쪽에
  hard-code. 추가 deny (예: `faster_whisper`) 가 필요하면 두 곳 동시 수정.
- AST 가 잡는 케이스 (`from X import Y`) 를 regex 가 *중복* 으로 잡음 → 같은
  사유로 reject. 한쪽이면 충분.
- 정작 regex 가 잡는 dynamic 케이스 (`importlib`, `__import__`) 는 substring 이라
  false positive 가 흔함 (follow-up review N2 참조 — 코멘트 / string literal /
  `importlib_metadata` 도 hit). 이 한계는 PHASE3-PLAN §128 에 문서화는 되었지만
  코드 자체의 SSOT 문제는 그대로.

**수정 방향**: deny-list 를 모듈 상수
`_DENY_IMPORT_ROOTS = frozenset({"ctranslate2", "transformers"})` 1 곳에. AST walk
는 그 상수 참조, regex 는 *dynamic* 패턴만 (`importlib|__import__|from_pretrained|Whisper\(`) —
static import 는 AST 에 위임 → AST/regex 책임이 deduplicated.

#### L1-5 — 상태 코드 문자열 `"keep"`/`"reject"`/`"success"`/`"running"` 이 typed 와 raw literal 의 혼재

`harness/policy.py:12` `DecisionStatus = Literal["keep", "reject", "success"]` 는
`Decision.status` 의 타입을 좁힌다 — 좋다. 그러나:

- `harness/runner.py` 안에서 `"reject"` literal 이 12 번 (rollback 분기 4 개 × 3
  지점: `IterationResult`, `append_event`, `commit_iteration`).
- `harness/state.py:20` `status: str = "running"` 은 `Literal` 안 씀. `run_job` 의
  `state.status == "success"` 비교 (`runner.py:504`) 가 typo 면 silent.

**수정 방향**:
```python
# harness/policy.py
DecisionStatus = Literal["keep", "reject", "success"]
KEEP: DecisionStatus = "keep"
REJECT: DecisionStatus = "reject"
SUCCESS: DecisionStatus = "success"

# harness/state.py
JobStatus = Literal["running", "success"]
status: JobStatus = "running"
```
runner.py 가 상수 참조 → mypy/ruff 가 typo 잡음.

#### L1-6 — `baseline/target_cer.json` 경로가 4 곳에 default

`harness/runner.py:35`, `harness/verify.py:37`, `:185`, `harness/runner.py:181`
(prompt 읽기). 같은 파일을 verify 와 policy 가 각자 read 하는 *중복 I/O* 도
숨어 있음 (`runner.py:437`, `verify.py` 가 `guards.read_baseline` 안에서 다시
read).

**수정 방향**: `RunnerConfig.baseline_file` 만 정본, verify 가 caller 로부터
받는 형태. runner 가 한 번만 read 해서 dict 를 verify 와 policy 에 전달 → SSOT
+ I/O 1 회.

---

### Lens 2 — Complex functions

#### L2-1 — `harness/runner.py:312-498` `run_iteration` — 187 line, ruff C901 = 15

**복잡도 출처**: 9 단계의 iteration 흐름이 단일 함수에 인라인.
1. `state.advance` + `out_dir` (line 320-323)
2. `ensure_worktree_ready` (line 324)
3. candidate 실행 (line 326-338)
4. candidate rc 검사 → reject + rollback + history + state + commit (line 340-362)
5. pre-verify scope 검사 → 같은 reject 블록 반복 (line 364-388)
6. `verify_func` 실행 (line 390-404)
7. post-verify scope 검사 → 같은 reject 블록 반복 (line 406-435)
8. verify ok 검사 → 같은 reject 블록 반복 (line 437-461)
9. `decide_candidate` → keep/success/reject 분기 (line 463-497)

이 중 4 / 5 / 7 / 8 단계의 reject 블록이 *구조적으로 동일* (Lens 3 의 핵심 중복).
나머지 (1-3, 6, 9) 는 본질적 흐름.

**평가**: 절반은 **accidental complexity** (reject 블록 반복), 나머지는
**essential** (9-step 흐름 자체). 함수 길이가 문제라기보다 *중복* 이 더 강한 문제.

**수정 방향**:
1. 헬퍼 함수 `_reject_and_finalize(config, state, state_path, hyp_id, reason,
   verify_result=None, candidate_rc=None, candidate_stderr="") -> IterationResult`
   추출 — Lens 3 의 ROI 큰 후보.
2. 단계 6-8 (`verify + post-verify check + verify_ok check`) 을 별도
   `_run_and_validate_verify(config, hyp_id, verify_func) -> (VerifyResult,
   list[GitPathStatus] | None)` 로 묶기.
3. 결과: `run_iteration` 70 line 정도, 흐름이 9 step 직선으로 읽힘.

Effort: ~80 LOC 변경, 동작 동일. 회귀 위험: 적음 (테스트가 4 reject 케이스 모두
커버 — keep/scope-violation/verify-fail/verify-during-violation).

#### L2-2 — `harness/guards.py:88-120` `check_catastrophic` — ruff C901 = 12

3 필드 (`empty_output_rate`, `length_ratio.{mean,p05,p95}`) 에 대해 각각
"존재 → 숫자 → finite → 임계" 4-step 체크. 33 line. C2/I3 fix 의 결과 — 안전성을
위한 의도된 복잡도.

**평가**: **essential**. 다만 같은 4-step 이 `check_arithmetic` (corpus_cer)
+ `check_runtime` (total_inference_time_s 2 회) 에도 반복 → 5 회 같은 idiom.
function-level 복잡도가 아니라 file-level 중복 (Lens 3 L3-3) 으로 다룬다.

**수정 방향**: 함수 분해 불필요. 헬퍼만 추출하면 line 수 줄어듦.

#### L2-3 — `harness/verify.py:88-174` `run_verify` — branchy 라기보다 sequential

7 개 early-return (workspace 누락, syntax error, deny pattern, judge rc, missing
report, guard fail, ok). 각 단계가 `VerifyResult` constructor 를 다시 호출 →
12 lines × 5 회 반복 — 같은 패턴 (`return VerifyResult(ok=False, hyp_id=...,
out_dir=..., error=..., ...)`).

**평가**: 흐름은 straight-line 이라 cognitive complexity 는 낮으나, **VerifyResult
constructor 호출의 boilerplate 가 50 line 차지**. Lens 3 의 부수 결과.

#### L2-4 — `harness/policy.py:41-108` `decide_candidate` — 정상

5 분기 (non-finite reject / success / first-keep / threshold-keep / threshold-reject).
각 분기가 `Decision` 을 명시적으로 반환 — 읽기 좋음. **essential**.

#### L2-5 — `scripts/analyze_run.py:768` 의 `datetime.utcnow` deprecation

(범위 참고용 — Phase 2 잔존, 이번 review 범위 아님.) 한 줄로 수정 가능
(`datetime.now(UTC)` 로). judge/evaluate.py 는 이미 `datetime.now(UTC)` 로
갱신됨 — 일관성을 위해 같은 패턴.

---

### Lens 3 — Duplication

#### L3-1 — Runner 의 4 × `_reject_and_finalize` 블록 (가장 큰 ROI)

`harness/runner.py:340-362`, `:365-388`, `:413-435`, `:441-461` — 거의 글자단위
복제:

```python
rollback_paths(repo_root, candidate_owned_statuses(<statuses>, config))
result = IterationResult(
    hyp_id=hyp_id, status="reject", decision=None,
    verify_result=<varies>, reason=<varies>,
)
append_event(str(state.iteration), hyp_id, "NA", "NA", "reject",
             _history_body(result, <maybe candidate_rc, stderr>),
             repo_root=repo_root)
state.save(state_path)
if config.commit_results:
    commit_iteration(config, state_path, "reject", hyp_id, state.iteration)
return result
```

차이는 (a) `verify_result` 가 있는지 (b) `_history_body` 가 candidate rc/stderr 를
받는지 (c) status 가 statuses 인지 post_verify_statuses 인지 정도.

- 본문 block 당 ~22 line × 4 = **88 line 중 70+ 가 사실상 동일**.
- 더 위험한 부분: `"reject"` 문자열 literal 이 각 블록에 3 회 (`IterationResult`,
  `append_event`, `commit_iteration`) — 12 회 노출. 한 군데가 typo (예: `"rejct"`)
  면 status round-trip 깨지지만 type 시스템이 못 잡음.

**ROI**: extract 시 ~50 LOC 절약 + runner 가독성 큰 폭 개선. 위험 적음.

```python
def _reject_and_finalize(
    config, state, state_path, hyp_id, reason, *,
    verify_result=None, candidate_rc=None, candidate_stderr="",
    statuses=None,
) -> IterationResult:
    rollback_paths(config.repo_root.resolve(),
                   candidate_owned_statuses(
                       statuses or git_status(config.repo_root.resolve()),
                       config))
    result = IterationResult(hyp_id=hyp_id, status="reject",
                             decision=None, verify_result=verify_result,
                             reason=reason)
    append_event(str(state.iteration), hyp_id, "NA", "NA", "reject",
                 _history_body(result, candidate_rc, candidate_stderr),
                 repo_root=config.repo_root.resolve())
    state.save(state_path)
    if config.commit_results:
        commit_iteration(config, state_path, "reject", hyp_id, state.iteration)
    return result
```

#### L3-2 — `VerifyResult(ok=False, hyp_id=…, out_dir=…, error=…, …)` × 5

`harness/verify.py:96-102, 127-134, 136-143, 155-164` — 같은 패턴 4-5 회.
공통 매개변수 (`hyp_id`, `out_dir`) 가 모든 호출에 동일. helper
`_fail(error, *, stdout="", stderr="", report=None, per_file=None)` 로 묶으면
function body 가 절반.

**ROI**: ~25 LOC 절약. 위험 적음.

#### L3-3 — `guards.py` 의 "필드 존재 → 숫자 변환 → finite 체크" idiom × 5

`check_arithmetic` (corpus_cer), `check_catastrophic` (empty_output_rate, mean,
p05, p95), `check_runtime` (baseline_t, run_t) — 같은 6-line idiom 5 회 반복:

```python
if "<field>" not in report:
    return "<field> 누락 — score_report 스키마 위반"
try:
    val = float(report["<field>"])
except (TypeError, ValueError):
    return f"<field> 가 숫자가 아님: {report.get('<field>')!r}"
if not math.isfinite(val):
    return f"<field> non-finite: {val!r}"
```

`_require_finite(report, field, label=None) -> tuple[float | None, str | None]`
헬퍼로 묶으면 5 회 × 6 line = 30 line → 5 회 × 1 line + 헬퍼 10 line = 15 line
절약. 메시지 문자열의 일관성도 자동 확보.

**ROI**: ~15 LOC 절약, 향후 새 필드 추가 비용 1/6.

#### L3-4 — Test `_init_repo` 가 두 파일에 중복

`tests/test_harness_runner.py:27-66` (40 line) 와 `tests/test_harness_history.py:14-37`
(24 line). 같은 git init + commit 패턴. 후자는 더 단순 (workspace 만 commit, baseline/runs
없음) — 부분 중복.

**ROI**: `tests/conftest.py` 에 fixture (`@pytest.fixture def harness_repo(tmp_path)`)
로 추출. 30 LOC 절약 + 새 테스트 작성 비용 감소. M5 (prior review) 의 conftest
요구사항도 같이 해결.

#### L3-5 — Wrapper 일관성

- `scripts/verify_check.py` (11 줄) — `from harness.guards import main`. 호환 wrapper.
- `scripts/append_history.sh` (6 줄) — `python -m harness.history "$@"`. 호환 wrapper.
- `scripts/evolve.py` (20 줄) — `from harness.runner import main` + sys.path
  injection.

세 wrapper 모두 "thin" 으로 일관. 호출 측 정리:
- `scripts/verify_check.py` 직접 호출자 0 (코드/테스트 모두 `-m harness.guards`).
- `scripts/append_history.sh` 직접 호출자 0 (`runs/_summary/HISTORY.md:9` 의 *역사*
  언급 1회 만).
- `scripts/evolve.py` — `docs/PHASE3-PLAN.md` 의 진입점 문서화. 1 차 정본.

→ `verify_check.py` 와 `append_history.sh` 는 **dead wrapper** (Lens 4 L4-3 참조).

---

### Lens 4 — Dead code / unused exports / stale shims

#### L4-1 — Unused import: `frozen/asr_backend.py:12` `import os` (ruff F401)

```python
import os  # 사용 안 함
```
한 줄 삭제. PHASE 1 잔존.

#### L4-2 — Unused test args: `tests/test_harness_runner.py:284` (ruff ARG001 × 2)

```python
def fake_run_iteration(cfg, state, state_path):  # cfg, state_path 미사용
```
시그니처 호환 위한 모킹이라 의도된 unused. `_cfg, state, _state_path` 로
prefix-underscore 처리 권장 (ruff convention).

#### L4-3 — Legacy shims — `swap_*.sh`, `verify.sh.alt`, `.claude.alt/`, `.ckignore`

`docs/PHASE3-STATUS.md:35-36` 에 "[ ] 폐기 또는 archive 여부 결정" 으로 열린 항목.
실제 활성도:

| 파일 | 운영 경로에서 호출? | 사람 호출 차단? |
|---|---|---|
| `scripts/swap_verify.sh` | 없음 | 없음 (단순 file mv) |
| `scripts/swap_claude.sh` | 없음 | `.claude/hooks/block_swap_and_seal.py` 가 PreToolUse Bash 차단 — Phase 3 활성 시만 |
| `scripts/verify.sh.alt` | swap 의 *대상* — 사람이 swap 누르면 활성 | 없음 |
| `.claude.alt/` | swap 의 대상 (1 파일 `.gitkeep` 만) | 없음 |
| `.ckignore` | (확인 안 함, ignore 파일이라 inert 가능성 큼) | — |

위험: 본 운영 경로 (`harness/`) 가 정본인 시점에 사람이 실수로
`bash scripts/swap_verify.sh` 를 누르면 `scripts/verify.sh` 가 옛 본문으로 바뀐다.
그 다음 `bash scripts/verify.sh` 호출이 일어나면 옛 `verify_check.py` 본문 로직이
다시 활성화 — 하지만 `scripts/verify_check.py` 자체가 11 줄 wrapper 가 되었으므로
이미 그 경로는 dead. **실제 위험은 swap_verify.sh 가 verify.sh 본문을 옛 것으로
바꿔도 그 본문이 호출하는 logic 이 이미 harness 로 갈아치워졌으므로 무해**.
swap_claude.sh 도 hook 차단 + `.claude.alt/.gitkeep` 만 있어 swap 해도 효과 없음.

→ **inert** 로 판단. 폐기/archive 결정만 STATUS 에서 처리하면 충분. Phase 3 진입
차단 사유 아님.

**수정 방향**: STATUS §3 의 2 열린 항목을 PHASE 3 entry 후 cleanup commit 으로
처리 (`scripts/swap_*.sh` + `scripts/verify.sh.alt` + `.claude.alt/` → `.archive/`).

#### L4-4 — `harness/verify.py` 의 `main()` (line 177-209) — 1 차 caller 없음

코드/테스트/문서 검색 결과 `python -m harness.verify` 직접 호출자 0:

```
$ grep -rn "harness.verify" . --include="*.py" --include="*.sh"
harness/runner.py:19:from harness.verify import VerifyConfig, VerifyResult, run_verify
tests/test_harness_runner.py:24:from harness.verify import VerifyResult, check_workspace_static
docs/PHASE3-PLAN.md:128:  (단순 mention)
```

함수 정의 (line 177-209) 만 33 line 차지. 운영 경로 (`harness.runner`) 가 직접
`run_verify(VerifyConfig(...))` 호출. `main()` 은 manual debug 용도로 *만*
의미 있고, 별도 entry point 가 없어 다른 wrapper 가 안 가리킴.

**평가**: 유지해도 무해 (33 line). 단, "verify 가 단독 CLI 로 호출 가능" 라는 잘못된
기대를 주므로 차라리 docstring 에 "harness.runner 가 유일 caller, manual debug 에만
사용" 명시 권장. 삭제는 보수적으로 비권장 (운영 디버깅 시 한 줄 entry).

#### L4-5 — `harness/history.py:24-47` `append_history` — 코드 caller 0, 테스트만

```
$ grep -rn "append_history\b"
harness/history.py:24:def append_history(...)
harness/history.py:85:    append_history(...)  # CLI main 내부
tests/test_harness_history.py:11,:43
runs/_summary/HISTORY.md:9: (역사 언급)
```

`harness.history.main` (CLI) 만 `append_history` 를 부르고, runner 는 더 저수준의
`append_event` 만 사용 (`runner.py:16`). `append_history` 의 가치는:
- git commit hash 를 받아 short_hash + body 를 *조회* 한 뒤 HISTORY 에 append.
- runner 가 자체 `_history_body` 를 만들기 때문에 이 경로 안 씀.

→ `scripts/append_history.sh` (compatibility wrapper) 의 *유일한* purpose. wrapper
가 inert 면 `append_history` + `harness.history.main` + `_git_output` 도 dead.
직접 호출 없음 확인됨.

**평가**: backfill 시 사람이 손으로 `bash scripts/append_history.sh <iter> <commit>
<metric> <delta> <status>` 를 부를 가능성은 있다 (HISTORY 손으로 보충). 운영 자동
경로에는 없음. wrapper + CLI + helper 3-layer 가 단일 사람-호출 시나리오만 위해
존재 — 명시화 가치.

**수정 방향**: docstring 에 "사람 backfill 전용. runner 는 `append_event` 직접
호출" 한 줄. 향후 자동 backfill 도구가 안 생기면 폐기 후보.

#### L4-6 — `scripts/verify_check.py` — caller 0

코드 / 테스트 / 문서 모두 `-m harness.guards` 로 갈아탔음. wrapper 의 유일한
purpose 는 "옛 PLAN 문서가 `scripts/verify_check.py` 를 참조" 하던 시점의 호환.
prior review 가 inert 라고 판정.

**평가**: 11 line 이라 유지 비용 0. 단, `docs/PHASE3-STATUS.md` 의 "compat wrapper
유지" 결정 사유를 SSOT/STATUS 에 한 줄 박는 것이 좋다 — 미래 cleanup 시 근거.

#### L4-7 — Empty `__init__.py` 들

`harness/__init__.py`, `judge/__init__.py`, `frozen/__init__.py`, `workspace/__init__.py`
모두 빈 파일 (혹은 docstring 만). 패키지 import 만 활성화. 의도된 비움 — 재
export 도 없고 모듈명이 짧아 caller 가 `from harness.policy import …` 직접 import.

**평가**: dead 아님. 단, `harness/__init__.py` 에 Lens 1 의 SUMMARY_DIR /
EVAL_BATCH / HOLDOUT_BATCH / ABSOLUTE_DELTA_FALLBACK 상수를 두면 SSOT 와 연결.

#### L4-8 — `RunnerConfig.runs_dir = Path("runs")` (line 33) — 의도된 디폴트, 거의 안 쓰임

`config.runs_dir` 참조는 runner 안에서 `_best_diagnosis` (`runner.py:174`),
`runs/<hyp_id>` 출력 (`:322`), 그리고 `VerifyConfig(runs_dir=config.runs_dir)`
전달. runner ↔ verify 양쪽 default 가 같아 정렬은 OK. 활용은 적지만 dead 아님.

---

## Top fixes (prioritized)

| Rank | Issue | File:line | Fix sketch | Effort (LOC) | Risk |
|------|-------|-----------|------------|--------------|------|
| 1 | `run_iteration` 의 4 × reject 블록 중복 | `harness/runner.py:340-461` | `_reject_and_finalize` 헬퍼 추출, 4 블록 호출 1 줄로 환원 | -50 (388→338) | 낮음 (테스트 4 reject 케이스 모두 커버) |
| 2 | `runs/_summary/` literal 산재 + `absolute_delta_fallback=0.01` 4 중복 | `harness/{runner,history}.py`, `scripts/{analyze_run,evaluate_holdout}.py` | `harness/__init__.py` 에 `SUMMARY_DIR`, `ABSOLUTE_DELTA_FALLBACK` 상수, 모든 default 가 참조 | +5 / -10 | 매우 낮음 |
| 3 | `guards.py` 의 "필드 존재 → 숫자 → finite" idiom 5 회 | `harness/guards.py:65-152` | `_require_finite_field(report, name)` 헬퍼, 5 회 인라인 → 5 회 호출 | -15 | 낮음 (테스트 5 종 모두 커버) |
| 4 | Test `_init_repo` 두 파일에 복제 | `tests/test_harness_runner.py:27`, `tests/test_harness_history.py:14` | `tests/conftest.py` fixture `harness_repo(tmp_path)` | -30 + 새 fixture | 낮음 |
| 5 | Static deny-list split (AST + regex 두 곳) | `harness/verify.py:19-26, 64-74` | `_DENY_IMPORT_ROOTS` 상수, regex 는 dynamic 패턴만 | -5 | 매우 낮음 |
| 6 | `VerifyResult(ok=False, …)` × 5 중복 | `harness/verify.py:96-164` | `_fail(error, **k)` 헬퍼 | -25 | 매우 낮음 |
| 7 | Batch name 9 곳 산재 | repo-wide | `EVAL_BATCH`, `HOLDOUT_BATCH` 상수 + `_FORBIDDEN_BATCHES = {HOLDOUT_BATCH}` 1 곳에 | +3 / -10 | 낮음 |
| 8 | 상태 코드 typo 보호 (`"reject"` 12 회, `"success"` 의 비교는 `state.status`) | `harness/{policy,runner,state}.py` | `REJECT/KEEP/SUCCESS` 상수 + `JobStatus` Literal | +5 LOC | 매우 낮음 |
| 9 | Legacy `swap_*.sh`, `verify.sh.alt`, `.claude.alt/` cleanup | `scripts/`, `/` | `.archive/` 로 mv + STATUS 갱신 | 0 | 낮음 |
| 10 | `frozen/asr_backend.py:12` unused `os` import | (단일 라인) | 한 줄 삭제 | -1 | 0 |

---

## Assessment

**Ready to lock-in / proceed to Phase 3 entry?** **Yes** (cleanups recommended but
not blocking).

**Reasoning**: 직전 두 리뷰의 Critical/Important 항목이 코드에서 모두 닫혔고
회귀 가드도 박혀 있다. 본 quality review 에서 새로 본 결함은 모두 *유지보수
부담* (rank 1-8) 과 *legacy artifact* (rank 9) 영역으로, Phase 3 잡의 정확성을
깨지 않는다. Rank 1 (run_iteration reject 중복) 과 rank 2 (SSOT 상수) 만 진입
전에 처리하면 25-iter 잡 디버깅 시 "어느 reject 블록에서 떨어졌나" 추적이
훨씬 쉽다 — 합쳐도 100 LOC 미만, 위험 매우 낮음. Critical 한 quality blocker
없음.
