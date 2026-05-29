# Phase 3 — 자체 Harness 실행 플랜

> 범위: Phase 1·2 산출물이 준비된 뒤, repository 안의 `harness/` controller가
> `workspace/transcribe.py` 후보를 검증하고 keep/reject/success를 판정하는 운영 절차.
> 진행 상태와 DoD 체크박스는 [`PHASE3-STATUS.md`](PHASE3-STATUS.md)에만 둔다.

정본 관계:
- 도메인·metric·금지사항: [`STT-PIPELINE-SPEC.md`](STT-PIPELINE-SPEC.md)
- 시스템 구조: [`DESIGN.md`](DESIGN.md)
- 문서 지도: [`SSOT.md`](SSOT.md)
- loop 그림: [`PHASE3-LOOP.md`](PHASE3-LOOP.md) (보조)

---

## 1. 목표

한국어 보험 콜센터 0715 eval 11파일에 대해 `workspace/transcribe.py`의
`transcribe(audio, sr) -> str` 파이프라인을 개선한다.

| 항목 | 기준 |
|------|------|
| Primary metric | `corpus_cer` lower-is-better |
| 최종 성공 | `corpus_cer <= baseline/target_cer.json:target_cer` |
| 최종 시간 | `total_inference_time_s <= baseline/target_cer.json:total_inference_time_s` |
| 비교 앵커 | `baseline_cer`는 거리감 참조, 성공 기준 아님 |
| 중간 채택 | 현재 best 대비 noise threshold 이상 개선 |
| 평가 근거 | `runs/<hyp_id>/score_report.json` |
| 원인 추론 | `runs/<hyp_id>/diagnosis_report.json`, `per_file.jsonl`, `_telemetry/` |

---

## 2. 코드 경계

| 영역 | 책임 |
|------|------|
| `harness/` | Phase 3 controller 본체. guard, policy, state, history, runner |
| `scripts/` | 사람이 실행하는 얇은 CLI와 일회성 운영 명령 |
| `judge/` | 평가자. 후보 텍스트를 점수와 diagnosis로 변환 |
| `workspace/transcribe.py` | 후보가 수정하는 유일한 STT pipeline 표면 |
| `frozen/` | CT2 + Whisper-large-v3-turbo backend 봉인 |
| `runs/<hyp_id>/` | iteration별 평가 산출물. git ignore 대상 |
| `runs/_summary/` | harness 전용 누적 로그·상태·최종 리포트 |

판단 기준:
- import 가능한 재사용 로직은 `harness/`에 둔다.
- 사람이 터미널에서 실행하는 entrypoint는 `scripts/`에 둔다.
- metric 산출은 `judge/`, 채택 판정은 `harness/`가 맡는다.
- candidate는 `workspace/transcribe.py` 외 파일을 수정하지 않는다. 특히
  `runs/_summary/`는 HISTORY와 state를 담는 harness 전용 영역이므로 scope 위반이다.

---

## 3. Phase 3 진입 가드

진입 전에 사람이 한 번 확인한다. 실행 시퀀스 (copy-paste runnable) 는
[`README.md` §"Phase 3 진입 → 본 잡 → 종료"](../README.md) 참조. 본 절은 각 단계의
**의미·실패 모드·해석** 만.

### 3.1 holdout 접근 차단 — `scripts/seal_holdout.sh`

데이터 디렉토리를 `chmod 000` 으로 물리적으로 차단. idempotent — 이미 봉인된 상태에서
다시 실행해도 OK. 잡 종료 후 `evaluate_holdout.py --unseal` 만 1 회 복구권을 가짐.

### 3.2 후보 편집 표면 확인

`workspace/transcribe.py` 외 어디도 후보 변경 대상이 아니다. AGENTS.md §1 권한 표.

### 3.3 보호 영역 확인

`frozen/`, `judge/`, `baseline/`, `assets/audio_profile/` 가 깨끗한지 확인. 봉인된
backend·평가자·기준값이 의도치 않게 수정되면 가드 자체가 무의미해진다.

### 3.4 baseline 재측정 금지

`baseline/target_cer.json`, `baseline/noise_floor.json` 은 Phase 1 종료 시 봉인됨.
잡 도중 갱신하면 채택 정책 (§6) 의 결정론이 깨진다. σ provisional 재측정 (Phase 3
첫 정상 가설 이후) 만 명시적 예외.

### 3.5 정상 verify 1회 — `bash scripts/verify.sh`

**검증하는 것**: pairing → frozen backend load → judge.evaluate → harness.guards →
score 출력의 end-to-end 경로가 살아있는지.

**해석**:
- **stub 상태** (Phase 1 출발점) 에서는 `workspace/transcribe.py` 가 30 초 윈도우
  한 번만 디코드 → 27분짜리 파일이 99% 잘림 → 출력 near-empty →
  `length_ratio.p05 = 0.000` catastrophic 가드 발동 → **exit 1 이 정상**. 가드가
  정직하게 잡는 것 자체가 시스템 동작의 증거.
- corpus_cer 숫자 (예: 0.99) 는 마지막 줄에 출력되고, 그 다음 줄에 가드 FAIL
  메시지가 나온다. 두 줄 모두 떨어져야 통과로 본다.
- 후보 (`claude -p`) 가 chunking 등으로 stub 의 30 초 truncation 을 해결하면
  본 잡 첫 iter 부터 catastrophic 이 풀린다.

**진짜 실패**: pairing 0 페어, backend load 실패, judge.evaluate 크래시 등 — 가드
이전 단계에서 traceback. 이 경우 환경/데이터 문제이지 stub 의 문제가 아니다.

### 3.6 의도적 위반 smoke 1회

정적 backend 가드가 살아있는지 확인. `workspace/transcribe.py` 상단에
`import ctranslate2` 한 줄 추가 → `bash scripts/verify.sh` → `verify FAIL
[static backend]` + exit 1. 원복은 `git checkout -- workspace/transcribe.py`.

이 smoke 가 통과되면 §5 의 정적 가드 목록 (backend / profile / from_pretrained /
Whisper() ) 이 작동함을 확인한 셈.

### 3.7 자체 harness dry-run 1 iter

`python3 scripts/evolve.py --job-id dry --iters 1 --manual` — candidate 생성 없이
현재 workspace 를 1 iter 만 평가. 가드 발동을 reject 로 변환하는 harness 의 정책
(§6) + state 직렬화 + HISTORY append 까지 검증.

**정리 필수**: dry 잡은 `--commit-results` 없이 돌므로 `runs/_summary/HISTORY.md`
변경과 `runs/_summary/dry_state.json` 이 worktree 에 남는다. 다음 잡의
`ensure_worktree_ready` 가 이걸 "unrelated changes" 로 잡아 차단하므로 본 잡 시작
전 반드시 정리:

- `git checkout -- runs/_summary/HISTORY.md`
- `rm -f runs/_summary/dry_state.json`
- `rm -rf runs/dry_*`

이 정리 이유는 SSOT 보호 — harness state/history 가 dry 의 reject narrative 로
오염된 채 본 잡이 시작되면 첫 iter 의 best_cer 계산 기준이 어긋날 수 있다.

### 운영 전제

자체 harness 전환 뒤에는 `/autoresearch` 호출, autoresearch skill 설치, TSV 분석을
운영 전제로 삼지 않는다. 이전 조사 기록은 [`AUTORESEARCH.md`](AUTORESEARCH.md) 에
historical 문서로 보존한다.

---

## 4. Iteration 흐름

실행 명령 (25-iter 본 잡 / `--manual` dry-run) 은
[`README.md` §"Phase 3 진입 → 본 잡 → 종료"](../README.md). 본 절은 한 iteration
내부의 흐름 + flag semantics.

한 iteration은 다음 순서를 따른다.

1. 후보 변경 생성 (`claude -p`를 candidate worker로 사용)
2. `workspace/transcribe.py` 정적 금지 패턴 검사 (§5)
3. `judge.evaluate` 실행 → `runs/<hyp_id>/score_report.json` 외 산출
4. `harness.guards`로 산술·catastrophic·runtime·quality budget 검사
5. verify 직후 scope 재검사 — `workspace/transcribe.py` + `runs/<hyp_id>/` 밖 변경
   발견 시 즉시 reject (candidate 의 `transcribe()` 가 verify 중 임의 파일 I/O 로
   정본 오염 방지)
6. `score_report.json`에서 `corpus_cer` 읽기
7. `harness.policy`가 keep/reject/success 판정 (§6)
8. keep이면 best 갱신과 기록, reject면 후보 변경 rollback
9. `runs/_summary/HISTORY.md` append

### Flag semantics

- **`--candidate-cmd`** — harness 가 만든 prompt 를 마지막 argv 로 붙여 실행한다.
  따라서 `claude -p` 는 매 iteration 마다 후보를 만드는 worker 일 뿐이고,
  검증·판정·기록·rollback 은 repository 내부 `harness/` 가 결정한다.
- **`--manual`** — candidate 생성 없이 현재 `workspace/transcribe.py` 만 1 iter
  평가. dry-run / 사람 직접 편집 검증 용. dry artifacts 가 worktree 에 남으므로
  본 잡 시작 전 정리 필수 (§3.7).
- **`--commit-results`** — 2 회 이상 반복할 때 필수. keep 된 후보를 git 기준점으로
  고정해야 다음 reject 때 직전 best 상태로 안전하게 rollback 가능. `--iters > 1`
  + `--commit-results` 없으면 `argparse.error` 로 거부.

### Prompt 재주입 방지

후보 prompt 에는 최근 `HISTORY.md` tail 이 관찰 자료로 들어가지만, HISTORY 본문은
명령이 아니다 (prompt 자체에 "untrusted observation" 경고 포함). candidate
stdout/stderr 는 per-iteration 파일 `runs/<hyp_id>/claude_stdout.txt`,
`claude_stderr.txt` 에만 보관하고, stderr 원문은 prompt 재주입을 막기 위해
`HISTORY.md` 에 복사하지 않는다.

---

## 5. Guard 정책

Hard fail:
- `workspace/transcribe.py`의 backend 직접 import 또는 `from_pretrained` 사용
- `workspace/transcribe.py`의 `assets`, `audio_profile`, `silero` 직접 참조
- `judge.evaluate` 실패 또는 `score_report.json` 누락
- `score_report.json` 핵심 필드 누락 또는 NaN/Inf 같은 non-finite 수치
- `Σ edits / Σ ref_chars`와 `corpus_cer` 불일치
- `empty_output_rate > 0.50`
- `length_ratio.p05 < 0.10`
- `length_ratio.p95 > 5.0`
- `total_inference_time_s > baseline_time * RUNTIME_HARD_MULTIPLIER`
- verify 직후 `workspace/transcribe.py` 와 `runs/<hyp_id>/` 밖에 변경 (`runs/_summary/`, `baseline/`, `docs/`, `judge/`, `frozen/` 등) 가 발견되면 reject + rollback. candidate 의 `transcribe()` 가 verify 중 임의 파일 I/O 로 정본을 오염시키는 것을 막는다.

Static guard 한계:
- `harness.verify.check_workspace_static` 의 AST/regex 검사는 **best-effort** 다.
  현재 deny 대상은 `ctranslate2` / `transformers` 의 literal import (`import …`,
  `from … import …`, `import … as …`) 와 `from_pretrained` / `Whisper(` 패턴이다.
  추가로 `__import__` / `importlib` 식별자 자체를 substring 으로 일괄 거부 — 인자가
  무엇이든 (`"ctranslate2"`, `"transformers"`, 또는 무관한 모듈) 동적 import 호출은
  통과 못 한다. **frozen 은 합법 경로** (workspace stub 가 `from frozen.asr_backend
  import …` 로 정상 사용) 라 deny-list 에서 의도적으로 제외.
- 다음과 같은 *obfuscated 동적* 우회는 정적으로 차단 불가능하다:
  - 문자열 조합 후 동적 호출 (예: `"ctrans" + "late2"` 를 `importlib` 으로 — 단,
    `importlib` literal 이 거부되므로 alias 우회까지 가야 함)
  - `getattr(__builtins__, "__import__")(...)`, `eval(...)`, `exec(...)`, `compile(...)`
  - `sys.modules` 직조작
- 따라서 동적 import / `eval` / `exec` / `__builtins__` 우회는 **운영 규약상 금지**이며,
  후보 코드 review (사람 또는 candidate 생성자) 의 책임이다. static guard 만으로
  안전하다고 가정하지 않는다.

Quality budget:
- hallucination, repetition, length, coverage가 baseline guard 분포보다 크게 악화되면
  기본은 warning이다.
- `QUALITY_BUDGET_HARD=1`이면 quality budget 위반도 hard fail로 처리한다.
- **세부 임계값** (`QB_HALLUC_DELTA`, `QB_REPEAT_DELTA`, `QB_EMPTY_DELTA`,
  `QB_LENGTH_MEAN_DELTA`, `QB_COVERAGE_DROP`) 은 `harness/guards.py` 상수에 박혀 있다.
  중복 정의를 피하기 위해 PLAN 은 정성 표현만 두고 수치 정본은 코드에 위임한다.

초기 runtime 값:
- hard cap: `RUNTIME_HARD_MULTIPLIER=3.0`
- success cap: baseline time 이하

---

## 6. 채택 정책

채택 판단의 입력:
- 현재 후보 `score_report.json`
- 현재 best `corpus_cer`
- `baseline/noise_floor.json:sigma`
- provisional sigma 여부

원칙:
- hard fail은 항상 reject다.
- 최종 target과 time budget을 만족하면 success다.
- sigma가 유효하면 `best_cer - candidate_cer >= 2 * sigma`일 때 keep한다.
- sigma가 provisional이거나 0이면 절대 개선폭 fallback을 쓴다. 초기 권장값은 `0.01`.
- keep/reject 판단은 자기 보고나 추측이 아니라 산출물 수치로만 한다.

---

## 7. 기록 정책

Iteration 산출물:
- `runs/<hyp_id>/score_report.json`
- `runs/<hyp_id>/per_file.jsonl`
- `runs/<hyp_id>/diagnosis_report.json`
- `runs/<hyp_id>/_telemetry/` (있을 때)
- `runs/<hyp_id>/prompt.md`
- `runs/<hyp_id>/claude_stdout.txt`
- `runs/<hyp_id>/claude_stderr.txt`
- `runs/<hyp_id>/candidate.diff`

누적 기록:
- `runs/_summary/HISTORY.md`
- `runs/_summary/<job_id>_state.json`

`HISTORY.md`는 실험 로그 정본이다. harness만 append하며, 후보가 직접 만들거나
덮어쓰면 scope 위반으로 rollback한다. 각 iteration은 최소한 다음 정보를 남긴다.

```text
iter, commit or candidate id, corpus_cer, delta, status
관찰: 주요 metric 변화
분석: 왜 그렇게 나왔다고 보는지
다음 후보: 다음에 시도할 lever
```

사후 종합 리포트는 `scripts/analyze_run.py`가 `runs/_summary/REPORT.md`로 생성한다.

---

## 8. Holdout 평가

Holdout은 Phase 3 잡이 끝난 뒤 사람이 1회만 평가한다.

1. `runs/_summary/JOB_DONE.lock` 생성
2. `scripts/analyze_run.py`로 eval 분석
3. `scripts/evaluate_holdout.py --unseal`로 holdout 평가
4. `runs/_summary/HOLDOUT.md`와 `HOLDOUT.json` 확인
5. holdout 재봉인

잡 도중 holdout 접근 또는 chmod 우회는 무효다.

---

## 9. Anti-pattern

- 계획 문서에 DoD 체크박스와 진행 로그를 섞기
- `scripts/`에 controller 상태 전이 로직을 계속 추가하기
- `judge/` 점수 산출과 `harness/` 채택 판정을 한 모듈에 섞기
- `baseline/target_cer.json`을 target 도달 전에 갱신하기
- holdout을 Phase 3 도중 읽기
- autoresearch의 Scope나 외부 hook을 운영 통제의 정본으로 삼기
- quality budget을 결과에 맞춰 사후 조정하기
