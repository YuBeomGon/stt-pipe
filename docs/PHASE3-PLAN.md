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
| `harness/prompts/candidate.md` | candidate runtime profile (역할 / 정찰 / discovery-first YAML response format). runner 가 매 iter prompt 에 inline. 변경 = candidate 행동 변경 |
| `scripts/` | 사람이 실행하는 얇은 CLI와 일회성 운영 명령 |
| `judge/` | 평가자. 후보 텍스트를 점수와 diagnosis로 변환 |
| `workspace/transcribe.py` | 후보가 수정하는 유일한 STT pipeline 표면 |
| `frozen/` | CT2 + Whisper-large-v3-turbo backend 봉인 |
| `runs/<hyp_id>/` | iteration별 평가 산출물 + candidate metadata (`candidate_meta.json` / `.err`). git ignore 대상 |
| `runs/_summary/` | harness 전용 누적 로그·상태 (`<job_id>_state.json`, `HISTORY.md`, `JOB_DONE.lock`) |
| `docs/reports/` | analyze_run / evaluate_holdout 의 잡별 산출물 |

판단 기준:
- import 가능한 재사용 로직은 `harness/`에 둔다.
- 사람이 터미널에서 실행하는 entrypoint는 `scripts/`에 둔다.
- metric 산출은 `judge/`, 채택 판정은 `harness/`가 맡는다.
- candidate는 `workspace/transcribe.py` 외 파일을 수정하지 않는다. 특히
  `runs/_summary/`는 HISTORY와 state를 담는 harness 전용 영역이므로 scope 위반이다.
- candidate runtime 가이드 (역할·접근법·응답 포맷) 는 `harness/prompts/candidate.md`
  에 둔다. runner.py 의 build_candidate_prompt 가 이 파일을 읽어 매 iter prompt
  본문에 inline — candidate session 은 file 을 직접 read 하지 않는다 (prompt.md
  사이드카로 재현·디버깅 가능).

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

### 3.7 candidate 컨텍스트 감사 — `python3 scripts/audit_candidate_context.py`

`claude -p` 가 candidate session 에 *자동* 주입하는 것 (CLAUDE.md, 플러그인
SessionStart 훅, PII, git commits, skills/MCP catalog) 을 probe 로 확인.
정본은 [`CANDIDATE-CONTEXT.md`](CANDIDATE-CONTEXT.md), 추가 도입 경위는
[`proposals/2026-05-29-agent-design.md`](proposals/2026-05-29-agent-design.md) §11.

산출: `docs/reports/<YYYY-MM-DD>_context_audit.json`. **잔여 누수 (email PII,
git recent commits) 가 `_LEAK_RULES` 안에 있어 정리 시점에 audit 는 항상
exit 1** — 즉 exit code 는 *경고* 로 취급하고 잡 진입을 자동 차단하지 않는다
([`CANDIDATE-CONTEXT.md`](CANDIDATE-CONTEXT.md) §6.4 와 동일). 운영자는 직전
audit JSON 과 diff 해 *신규* 누수만 감시 (예: 새 플러그인 자동 활성, 새 훅
fire). 임계 누수 (CLAUDE.md gate 깨짐, 새 SessionStart 훅 등) 는 사람이 잡 전
정리해야 한다.

> 참고: runner 가 `candidate_cmd` 에 다음 3 flag 를 자동 부착해 skills/MCP/Tool
> 을 process-local 로 정리한다 (`harness/runner.py::_harden_candidate_cmd`,
> 정본 [`CANDIDATE-CONTEXT.md`](CANDIDATE-CONTEXT.md) §7.6 + §7.7):
> - `--disable-slash-commands` (skills 29 → 0)
> - `--strict-mcp-config` (MCP servers → NONE)
> - `--disallowedTools=Bash,WebFetch,WebSearch,Task` (cheating Tool 차단)
>
> 본 audit 를 *production runner 와 동일한 cmd* 로 돌리려면 모두 명시:
> `--candidate-cmd "claude -p --disable-slash-commands --strict-mcp-config --disallowedTools=Bash,WebFetch,WebSearch,Task"`.
> `--disallowedTools` 는 반드시 `=` 형식 — variadic flag 가 candidate prompt 를
> 추가 tool 이름으로 먹는 버그 회피. 운영자 interactive `claude` 세션은 영향
> 받지 않음 (subprocess only).

### 3.8 자체 harness dry-run 1 iter

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

1. **prompt 빌드** — `build_candidate_prompt` 가 다음을 inline 으로 합쳐
   `runs/<hyp_id>/prompt.md` 로도 저장 (재현용):
   - `harness/prompts/candidate.md` 프로필 (역할·정찰·응답 포맷)
   - 현재 `workspace/transcribe.py` 본문 + frozen backend 표면 (sandbox 가 frozen
     Read 를 deny 하므로 surface 를 inline — 발견형 정찰의 전제)
   - goal / state + **error profile** — best 의 sub/del/ins·length_ratio·
     hallucination + `DOMINANT AXIS` 자동 판정 (substitution / coverage /
     over-generation). 후보가 병목을 scalar cer 가 아니라 측정으로 본다
   - 최근 5 iter 표 `(fingerprint, cer, sub/del/ins, len, hal)` — 각 시도가
     *어떻게* 실패했는지 (dedup + 진단)
   - **mode directive (6-mode)** — `harness/scheduler.py` 의 `decide_mode` 가
     explore/refine/combine/ablate(+repair/plateau) 중 하나를 정하고 runner 가
     `_MODE_DIRECTIVES` 의 해당 블록을 주입 (`=== … MODE ===`). mode 는
     base schedule(evaluated index 의 순수함수, error-diffusion 가중치
     `scheduler._PHASES`) + override precedence(repair_event > no_best >
     discovery_phase > diversity_stall > plateau > base)로 결정. 초반
     `DISCOVERY_FLOOR_FRAC`(0.40) 까지는 explore 강제. derivative mode 는
     `harness/portfolio.py` 의 `parents_for_mode` 가 고른 parent 후보(global_best
     /family_best/near_best/micro_bank pool)의 실제 diff 를 주입.
     candidate.md 는 mode-agnostic — mode 정의는 runner 가 단일 출처.
   - findings ledger — 잡 전체 누적 발견 (rollback 돼도 보존)

   > **구현 현황 주의**: proposal `2026-06-01-from-scratch-discovery-harness.md`
   > §4.4 candidate_score/novelty, §4.3 opportunity override + cooldown-in-
   > precedence, §4.5 compatible-parent 휴리스틱은 **deferred(미구현)** — 현재
   > parent 선택은 CER 정렬 MVP. 정합성 감사:
   > [`reviews/2026-06-04-doc-code-consistency-audit.md`](reviews/2026-06-04-doc-code-consistency-audit.md).
2. 후보 변경 생성 (`claude -p` 를 candidate worker 로 사용)
3. **format 게이트** (discovery-first schema) — candidate stdout 의 마지막
   ```yaml fenced block 을 `yaml.safe_load` 로 파싱해 `capability_investigated` /
   `what_i_learned` / `hypothesis` (각 non-empty 문자열) + `fingerprint`
   (1–6 token) 검사. `lane` 은 optional free-form (강제 X). 누락·malformed 면
   `candidate_meta.err` 만 남기고 workspace rollback + reject (verify 호출 X).
   성공 시 `candidate_meta.json` 저장 → 이후 단계 진행
4. `workspace/transcribe.py` 정적 금지 패턴 검사 (§5)
5. `judge.evaluate` 실행 → `runs/<hyp_id>/score_report.json` 외 산출
6. `harness.guards`로 산술·catastrophic·runtime·quality budget 검사
7. verify 직후 scope 재검사 — `workspace/transcribe.py` + `runs/<hyp_id>/` 밖 변경
   발견 시 즉시 reject (candidate 의 `transcribe()` 가 verify 중 임의 파일 I/O 로
   정본 오염 방지)
8. `score_report.json`에서 `corpus_cer` 읽기
9. `harness.policy`가 keep/reject/success 판정 (§6)
10. keep이면 best 갱신과 기록, reject면 후보 변경 rollback
11. `runs/_summary/HISTORY.md` append

**잡 레벨 abort 가드**: 두 종류.
- *format reject abort*: 첫 5 iter 중 4 회 이상 format reject → 잡 중단,
  `HarnessState.status = "aborted_format_reject"`. profile 이 LLM 응답 형식과
  어긋난 신호 — 운영자가 profile 재작성 후 새 job_id 로 재실행.
- *command failure abort*: candidate command (`claude -p`) 가 연속
  `_COMMAND_FAIL_ABORT_COUNT` (3) 회 exit≠0 → 잡 중단,
  `status = "aborted_command_failure"`. 세션 한도/환경 오류로 후보가 아예 안
  도는 상태를 조기 차단 (phase3_003 에서 세션 한도로 79 iter 낭비한 뒤 추가).

### Flag semantics

- **`--candidate-cmd`** — harness 가 만든 prompt 를 마지막 argv 로 붙여 실행한다.
  따라서 `claude -p` 는 매 iteration 마다 후보를 만드는 worker 일 뿐이고,
  검증·판정·기록·rollback 은 repository 내부 `harness/` 가 결정한다.
- **`--manual`** — candidate 생성 없이 현재 `workspace/transcribe.py` 만 1 iter
  평가. dry-run / 사람 직접 편집 검증 용. dry artifacts 가 worktree 에 남으므로
  본 잡 시작 전 정리 필수 (§3.8).
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

Pre-verify format gate (discovery-first schema):
- candidate stdout 의 마지막 ```yaml fenced block 이 없거나 필수 키
  (`capability_investigated` / `what_i_learned` / `hypothesis` / `fingerprint`)
  누락 → reject **before verify** (verify 호출 X, GPU 비용 0). 실패 사유는
  `candidate_meta.err` 에 기록되어 `scripts/analyze_run.py` 의 D 축
  format_reject_pct 로 집계.
- 세 prose 필드 (`capability_investigated` / `what_i_learned` / `hypothesis`)
  중 빈 문자열 → reject.
- `fingerprint` 가 list of strings 아님 또는 길이 1–6 범위 밖 → reject
  (7+ token = "one focused change" 위반). 빈/공백 token 포함 → reject.
- `lane` 은 optional free-form tag — 검증·강제하지 않는다 (round-robin 폐지).
- fingerprint *중복* (직전 N iter 과 동일) 은 **reject 대상이 아님** — 기록만
  (dedup 테이블로 후보에게 노출, novelty 압력은 explore directive 가 담당).

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

운영자 튜닝 노브(런타임 배수·뱅킹 임계값·explore 스케줄·synthesis·abort 카운트)는
`harness/config.py` 가 단일 정본(SSOT)이다. guards/policy/runner/verify 는 이 모듈을
참조만 한다 — 값 변경은 `config.py` 한 곳에서. (가드 내부 게이트 `QB_*` 등은
로직과 강결합이라 `guards.py` 에 남는다.)

초기 runtime 값:
- hard cap: `config.RUNTIME_HARD_MULTIPLIER=7.0` (baseline ~152s → cap ~1066s)
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
- sigma가 provisional이거나 0이면 절대 개선폭 fallback을 쓴다. 현재값 `0.002`
  (`PolicyConfig.absolute_delta_fallback`, review F2 banking — 작은 실질 개선도
  채택해 compound. 옛 `0.01` 에서 하향).
- keep/reject 판단은 자기 보고나 추측이 아니라 산출물 수치로만 한다.

---

## 7. 기록 정책

Iteration 산출물:
- `runs/<hyp_id>/score_report.json` — format reject 시 부재
- `runs/<hyp_id>/per_file.jsonl` — 같음
- `runs/<hyp_id>/diagnosis_report.json` — 같음
- `runs/<hyp_id>/_telemetry/` (있을 때)
- `runs/<hyp_id>/prompt.md` — runner 가 build_candidate_prompt 결과 그대로 저장 (재현·디버깅)
- `runs/<hyp_id>/claude_stdout.txt`
- `runs/<hyp_id>/claude_stderr.txt`
- `runs/<hyp_id>/candidate.diff`
- `runs/<hyp_id>/candidate_meta.json` — discovery-first YAML 메타데이터
  (`capability_investigated` / `what_i_learned` / `hypothesis` / `fingerprint`,
  optional `lane`). 파싱 성공 시
- `runs/<hyp_id>/candidate_meta.err` — format reject 사유. 파싱 실패 시 (둘 중 정확히 하나만 존재)

누적 기록:
- `runs/_summary/HISTORY.md` — **현재 잡 한정**. 잡 종료 후
  `docs/history-archive/HISTORY.<job_id>.md` 로 이동, 새 빈 HISTORY 로 다음
  잡 시작. 이유: candidate 가 매 iter prompt 에서 HISTORY tail 을 받는데,
  과거 잡 narrative 가 anchoring 으로 작용 → mechanism 선택 / fingerprint 회피
  판단 흐려짐. 잡 단위 ablation 정합성 보호.
- `runs/_summary/<job_id>_state.json` — `HarnessState.status` 가
  `"aborted_format_reject"` 또는 `"aborted_command_failure"` 이면 §4 의 잡 abort
  가드가 작동한 것

`HISTORY.md`는 실험 로그 정본이다. harness만 append하며, 후보가 직접 만들거나
덮어쓰면 scope 위반으로 rollback한다. 각 iteration은 최소한 다음 정보를 남긴다.

```text
iter, commit or candidate id, corpus_cer, delta, status
관찰: 주요 metric 변화
분석: 왜 그렇게 나왔다고 보는지
다음 후보: 다음에 시도할 lever
```

사후 종합 리포트는 `scripts/analyze_run.py`가 `docs/reports/<job_id>_REPORT_<YYYY-MM-DD>.md`로 생성한다 (명명 규칙·디렉토리 정본은 [`PHASE2-PLAN.md §2`](PHASE2-PLAN.md)).

---

## 8. Holdout 평가

Holdout은 Phase 3 잡이 끝난 뒤 사람이 1회만 평가한다.

1. `runs/_summary/JOB_DONE.lock` 생성
2. `scripts/analyze_run.py`로 eval 분석
3. `scripts/evaluate_holdout.py --unseal`로 holdout 평가
4. `docs/reports/<job_id>_HOLDOUT_<YYYY-MM-DD>.{md,json}` 확인
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
