# Phase 3 — Doc/Code Alignment Review (2026-05-29)

## Scope
- HEAD: `3696d88 fix(harness): N1 verify 중 scope 오염 차단 + C3 정적 guard 한계 문서화`
- Whole-codebase alignment check (not diff). Prior diff reviews:
  - `docs/reviews/2026-05-29-phase3-harness-review.md`
  - `docs/reviews/2026-05-29-phase3-harness-followup.md`
- Reviewer: subagent via superpowers:requesting-code-review
- 코드/문서 수정 없이 doc-vs-code 정렬만 검토.

## Method

8 개 doc surface (`docs/SSOT.md`, `docs/PHASE3-PLAN.md`, `docs/PHASE3-STATUS.md`,
`docs/PHASE3-LOOP.md`, `docs/DESIGN.md`, `AGENTS.md`, `README.md`, `CLAUDE.md`) 의
구체적 claim 을 각각 `harness/{guards,policy,runner,state,verify,history}.py`,
`scripts/{verify.sh, evolve.py, verify_check.py, append_history.sh, seal_holdout.sh,
evaluate_holdout.py, measure_baseline.py, build_audio_profile.py}`, `baseline/*.json`,
`workspace/transcribe.py`, `judge/*` 와 cross-check. 정적 guard 한계 claim 은 PLAN
§5 의 5 개 패턴을 `harness/verify.check_workspace_static` 의 AST deny-list 와 regex
양쪽에 매핑해 확인했다. 또한 holdout 이름 (`20250813`) 누출 grep, numeric threshold
3-way 일치 (코드 상수 ↔ PLAN ↔ AGENTS), PHASE3-LOOP mermaid 가 commit 3696d88 이
도입한 post-verify scope re-check 를 반영하는지를 확인했다.

---

## Findings

### Critical drift

#### CD1 — PLAN §5 Static guard 한계 단락이 코드와 어긋난 예시를 제시한다 (잘못된 안심)

- **Doc**: `docs/PHASE3-PLAN.md:128`
  > "literal `import ctranslate2`, `from transformers import X`, **`import frozen.asr_backend as f`**, `__import__("frozen.asr_backend")`, `importlib.import_module("frozen.asr_backend")` 같은 명시적 패턴은 잡지만"
- **Code**: `harness/verify.py:64-74` (AST deny-list: `{"ctranslate2", "transformers"}` 만), `:19-26` (`_BACKEND_RE` 는 `importlib`, `__import__`, `from_pretrained`, `Whisper\(`, `import ctranslate2`, `import transformers`, `from ctranslate2 import`, `from transformers import` 만)
- **무엇이 맞고 무엇이 틀리나**:
  - `import ctranslate2` / `from transformers import X` — AST + regex 양쪽이 catch. ✓
  - `__import__("frozen.asr_backend")` — `_BACKEND_RE` 의 `__import__` substring 으로 catch. ✓ (단, 인자가 `"frozen.asr_backend"` 인지 `"ctranslate2"` 인지 무관하게 `__import__` 호출 자체를 거부 — overly aggressive)
  - `importlib.import_module("frozen.asr_backend")` — 같은 이유로 catch. ✓ (overly aggressive)
  - **`import frozen.asr_backend as f`** — **NOT caught**. AST deny-list 의 root 가 `ctranslate2`/`transformers` 만이라 `frozen` 은 통과. `_BACKEND_RE` 에 `frozen` 패턴 없음. 그리고 정상 workspace stub (`workspace/transcribe.py:14`) 이 `from frozen.asr_backend import generate, load, to_storage_view` 형태로 동일 backend 를 *합법적으로* 쓰는 게 설계라서, `frozen` 자체를 deny-list 에 넣을 수도 없다.
- **왜 중요한가**: PLAN 이 명시적으로 "이 패턴은 잡는다" 라고 보장한 예시가 실제로는 안 잡힌다. 다음 agent 가 PLAN §5 를 읽고 "frozen.asr_backend 직접 import 도 정적 차단된다" 고 가정해 정책 빈틈을 인지 못 한다. 게다가 그 자체가 의도된 안전한 경로 (workspace stub 가 이미 사용) 라 `import frozen.asr_backend as f` 는 우회 패턴조차 아닌데, 우회 패턴 예시 자리에 끼어 있어 doc 가 자기 모순.
- **수정 방향 (docs 쪽)**: PLAN §5 단락에서 `import frozen.asr_backend as f` 예시를 삭제하거나, "frozen 은 정상 경로이므로 deny-list 에서 의도적으로 제외" 라는 한 줄 주석으로 분리. `__import__("frozen.asr_backend")` / `importlib.import_module("frozen.asr_backend")` 예시도 "인자 이름과 무관하게 `__import__` / `importlib` literal 자체를 거부한다 (substring 기반)" 로 정확히 기술. 본 예시들의 의도는 *dynamic backend bypass 시도를 잡는다* 이므로 인자를 `ctranslate2` / `transformers` 로 바꾼 예시가 사실 맞다.

---

### Important drift

#### ID1 — AGENTS.md §1 표가 가리키는 "Phase 3 진입 시점" 링크가 PLAN §1 으로 잘못됨

- **Doc**: `AGENTS.md:23`
  > "Phase 3 진입 시점 (`docs/PHASE3-PLAN.md §1`) 에 본 표의 제한이 *일괄 활성화* 된다."
- **Code/Doc 실제**: PLAN §1 은 "목표" (target_cer/baseline 정의 표) 다. 진입 시점·진입 가드는 PLAN §3 ("Phase 3 진입 가드"). AGENTS.md 가 §1 을 가리키면 "가드레일 일괄 활성화" 의 근거 절을 찾는 다음 agent 가 잘못된 섹션에 도달한다.
- **수정 방향**: `§1` → `§3`.

#### ID2 — DESIGN.md §1 디렉토리 트리에 `judge/diagnosis.py` 누락

- **Doc**: `docs/DESIGN.md:64-69` 의 judge tree 는 `__init__.py`, `normalize.py`, `metrics.py`, `pairing.py`, `evaluate.py` 5 항목.
- **Code**: `judge/diagnosis.py` 실재 (`build_diagnosis` 함수, `evaluate.py:29` 가 import). 도구 호출도 `judge/evaluate.py` 가 `from judge.diagnosis import build_diagnosis`.
- **왜 중요한가**: diagnosis 가 LOOP §3 의 "노출 모델" 다이어그램에 1 차 노드로 등장 (`docs/PHASE3-LOOP.md:81-86`) 한 핵심 산출인데 코드 책임 지도에서 보이지 않는다. 후속 agent 가 diagnosis 본문을 수정하려고 `judge/evaluate.py` 만 뒤지다 길을 잃을 수 있다.
- **수정 방향**: DESIGN.md tree 에 `diagnosis.py # 11파일 summary + focus 결합` 한 줄 추가.

#### ID3 — PHASE3-LOOP.md mermaid 가 commit 3696d88 의 post-verify scope re-check 를 표시 안 함

- **Doc**: `docs/PHASE3-LOOP.md:10-42` (flowchart), `:48-70` (sequence). flowchart 는 verify → harness.guards → policy → keep/reject 순. sequence 는 `H->>G: guard checks` → `G-->>H: pass/fail` → `H->>P: best, score, sigma 입력`.
- **Code**: `harness/runner.py:406-435` 가 verify 직후 (정책 결정·baseline 읽기 *전*) `disallowed_post_verify_paths` 로 추가 scope 재검사. 위반 시 즉시 reject+rollback. 이는 3696d88 의 핵심 보안 fix.
- **왜 중요한가**: LOOP.md 가 "harness loop 의 입력/산출물/keep-reject 흐름을 한눈에 보는 보조 문서" (line 1-4). post-verify scope 재검사가 누락되면 다음 agent 가 "verify 도중 candidate write" 시나리오의 방어층을 다이어그램에서 못 본다. followup review §"New Issues / N1" 의 수정이 코드에는 반영됐는데 그림에는 반영 안 됨.
- **수정 방향**: flowchart §1 의 G[score_report] → L[harness.guards] 사이에 "post-verify scope re-check" 노드 1 개 삽입. sequence §2 의 `H->>G: guard checks` 이전에 `H->>H: post-verify scope re-check` 1 줄 추가.

#### ID4 — PHASE3-PLAN §3 진입 가드 5 번 "정상 verify 1회 + 의도적 위반 smoke 1회" 가 STATUS 의 미완 체크 (`[ ]`) 와만 연결, 절차 본문 미정의

- **Doc**: `PHASE3-PLAN.md:61` ("5. 정상 verify 1회와 의도적 위반 smoke 1회"). `PHASE3-STATUS.md:48-49` 의 2 개 `[ ]` 항목 ("정상 verify 1회", "의도적 위반 smoke 1회").
- **Code**: `scripts/verify.sh` 로 정상 verify 1 회는 가능. 의도적 위반 smoke 의 정의·재현 방법은 *어디에도 명시 안 됨*. `tests/` 에 smoke 테스트가 통합돼 있지만 (`tests/test_harness_runner.py`), Phase 3 진입 가드용 "사람이 한 번 돌려 위반 경로 확인" 의 절차는 PLAN 본문에도 STATUS 본문에도 없다.
- **왜 중요한가**: STATUS 의 미완 체크가 진입 결정에 영향. 절차가 정의 안 된 미완 체크는 영원히 완료 안 됨. 다음 agent 가 "smoke 1회 어떻게 돌리지" 에서 막힌다.
- **수정 방향**: PLAN §3.5 또는 새 §3.6 으로 "의도적 위반 smoke" 구체 절차 (예: workspace 에 `import ctranslate2` 한 줄 → `bash scripts/verify.sh` → exit 1 + static backend 메시지 확인 → 원복) 를 1-2 줄로 명시.

#### ID5 — AGENTS.md §1 footer 의 "holdout 이름 참조 예외 wrapper" 목록이 실제 코드 사용처와 부분 일치

- **Doc**: `AGENTS.md:44-48` — 운영 wrapper 예외는 `scripts/seal_holdout.sh`, `scripts/evaluate_holdout.py` *둘만*.
- **Code**: 실제로 holdout name (`AIG_녹취반출_20250813`) 을 hard-code 한 운영 스크립트:
  - `scripts/seal_holdout.sh:13` ✓ (allowlisted)
  - `scripts/evaluate_holdout.py:31` ✓ (allowlisted)
  - `scripts/measure_baseline.py:39` ✗ (NOT in allowlist) — defensive `_FORBIDDEN_BATCHES` 로 사용
  - `scripts/build_audio_profile.py:30` ✗ (NOT in allowlist) — defensive `_FORBIDDEN_BATCHES` 로 사용
  - `tests/test_evaluate_holdout_smoke.py:90-91`, `tests/test_claude_phase3_settings.py:67` — `tests/` 는 doc 의 예외 범위에 명시 안 됨
  - `.claude/hooks/block_swap_and_seal.py:29` — `.claude/` 도 명시 예외 아님
- **왜 중요한가**: AGENTS.md 가 holdout 이름 참조 금지 범위를 "`workspace/`, `judge/`, prompt, 운영 wrapper 를 제외한 `scripts/`" 로 정의하는데, 실제 `scripts/` 안에서 4 개 (`measure_baseline.py`, `build_audio_profile.py`, 외 wrapper 2) 가 이름을 hard-code. 그 중 2 개는 *방어적 deny* 라 의도상 OK 지만 doc 가 인정 안 함. STT-PIPELINE-SPEC §11 의 "holdout 접근" 금지는 *read* 이지 *name reference* 가 아니라 일관성 있어도, AGENTS.md 의 자기 규칙은 깨진다.
- **수정 방향 (docs 쪽)**: AGENTS.md §1 footer 의 예외 wrapper 목록에 `scripts/measure_baseline.py`, `scripts/build_audio_profile.py` (defensive guard 목적), `tests/` (smoke fixture 목적), `.claude/hooks/` (hook deny 패턴 목적) 4 카테고리 추가. 또는 규칙을 "name reference 가 아니라 *read access* 가 금지" 로 더 정확히 재서술.

#### ID6 — README.md 의 "사전 조건: 14 페어" 가 실제 11 페어와 불일치

- **Doc**: `README.md:26-28` — "14 페어:" 다음에 `data/raw/wav/AIG_녹취반출_20250715/*_l.wav` + label.
- **Code/Data**: `baseline/target_cer.json:5` (`num_files: 11`), `judge/evaluate.py` 가 11 페어 평가, PLAN §1 + STT-PIPELINE-SPEC §3 표가 일관되게 11 페어 명시.
- **왜 중요한가**: README 가 사람용 진입점. 14 vs 11 의 데이터 페어 수 차이는 후속 agent / 사람이 누락된 페어를 찾으러 헛수고할 가능성.
- **수정 방향**: `README.md:26` 의 "14 페어:" → "11 페어:".

---

### Minor drift

#### MD1 — `scripts/verify.sh` 헤더 주석이 stale 한 swap 활성화 절차를 안내

- **Doc**: `scripts/verify.sh:4-6`
  > "활성화: 사람이 `bash scripts/swap_verify.sh` 한 번 실행 → scripts/verify.sh <-> scripts/verify.sh.alt 교환."
- **Code/Doc 실제**: PHASE3-STATUS §3 의 4 개 미해결 항목 (line 34-37) 이 swap 구조 폐기 검토 중. AGENTS.md §6 (line 105-110) 가 "swap 스크립트는 자체 harness 전환 과정의 정리 대상" 으로 격하. 그러나 `scripts/verify.sh` 본문 헤더는 여전히 swap 활성화 절차를 진행 가능한 운영 절차처럼 안내.
- **수정 방향**: 헤더 주석에 "legacy swap 구조 — PHASE3-STATUS §3 에서 폐기 검토 중. 현재는 본 파일이 정본 verify entrypoint" 한 줄 추가하거나, swap 안내 자체를 archive 표시.

#### MD2 — `scripts/verify.sh` "5. 마지막 줄에 corpus_cer (autoresearch 가 읽음)" 주석이 stale reader 호칭

- **Doc**: `scripts/verify.sh:13`, `:68`.
- **Code**: 자체 harness 전환 후 reader 는 harness (`runner._format_delta` / `verify.run_verify`) 또는 사람.
- **수정 방향**: "(harness reader 또는 사람이 읽음)" 로 갱신. (followup review §M1 이 같은 지적, 미해결 잔존.)

#### MD3 — `scripts/build_audio_profile.py:104` 및 `scripts/evaluate_holdout.py:326` 의 `PHASE3-PLAN.md §6.3` / `Phase 3 §6.3` 참조가 stale

- **Doc**: 두 파일이 `§6.3` 을 가리킴.
- **Doc 실제**: 현재 PLAN §6 ("채택 정책") 에 §6.3 sub-section 없음. §6 본문 5 줄. holdout chmod / audio profile generation 정책은 §3 (진입 가드) + §8 (Holdout 평가) 로 이동.
- **수정 방향**: 두 참조 모두 § 번호 갱신 (audio profile → §3 또는 §8; chmod 복구 → §8).

#### MD4 — `scripts/seal_holdout.sh:2` 의 `(PHASE3-PLAN.md §1.3)` 참조 stale

- **Code**: 현재 holdout 봉인은 PLAN §3 "Phase 3 진입 가드" 1 번 항목 (line 57).
- **수정 방향**: `§1.3` → `§3`.

#### MD5 — `scripts/verify.sh:2` 의 `(PHASE3-PLAN.md §1.2)` 참조 stale

- **Code**: 현재 verify 정책 본체는 PLAN §5 ("Guard 정책") + §4 ("Iteration 흐름") 2 번.
- **수정 방향**: `§1.2` → `§5` (또는 `§5, §4`).

#### MD6 — DESIGN.md §2.6 의 `(PHASE3 §1.2)` 참조 stale

- **Doc**: `docs/DESIGN.md:224`.
- **수정 방향**: `(PHASE3 §1.2)` → `(PHASE3-PLAN §5)`.

#### MD7 — DESIGN.md `frozen/` tree 가 `__init__.py` 누락

- **Doc**: `docs/DESIGN.md:62-63` — frozen tree 에 `asr_backend.py` 만.
- **Code**: `frozen/__init__.py` 실재 (untracked 였지만 commit 됨 — git ls-files 확인).
- **왜 minor 인가**: 다른 패키지 (`judge/`, `harness/`, `workspace/`) tree 는 `__init__.py` 명시. frozen 만 비대칭.

#### MD8 — README.md 가 `scripts/evolve.py` 의 `--commit-results` 강제성을 doc 화하지 않음

- **Doc**: `README.md:44-46` — `python3 scripts/evolve.py --job-id phase3_001 --iters 25 --candidate-cmd "claude -p" --commit-results` 예시.
- **Code**: `harness/runner.py:524-525` 가 `--iters > 1` 인데 `--commit-results` 없으면 `parser.error("--iters >1 은 --commit-results 가 필요합니다")` 로 거부.
- **왜 minor 인가**: README 예시가 우연히 `--commit-results` 포함이라 사용자가 카피하면 안전. 단 PLAN §4 (line 102-104) 만 강제성을 명시하고 README 는 "왜 필수인지" 무언급.
- **수정 방향**: README 예시 아래 "iters>1 은 `--commit-results` 필수 (PLAN §4)" 한 줄 추가.

#### MD9 — `harness/__init__.py` 이 namespace 만 — 책임 doc 화 없음

- **Doc**: SSOT §3 와 PLAN §2 가 `harness/` 를 "Phase 3 controller 본체. guard/policy/state/history/runner" 로 정의.
- **Code**: `harness/__init__.py` 빈 docstring 3 줄. 다음 reader 는 어느 모듈이 어디 책임인지 알려면 docs 로 우회 필요.
- **수정 방향 (없어도 무방)**: `harness/__init__.py` 에 모듈별 1-줄 요약 추가 또는 doc-only 변경 권장. P3 진입 차단 사유 아님.

#### MD10 — `scripts/verify_check.py` / `scripts/append_history.sh` 호환 wrapper 가 STATUS 외 어디에도 "live entrypoint 아님" 명시 안 됨

- **Doc**: `docs/PHASE3-STATUS.md:32-33` 이 "compatibility wrapper 로 축소" `[x]` 표시. 다른 doc (AGENTS / README / CLAUDE / DESIGN / PLAN) 어디도 "이 두 파일은 backward-compat 만, 신규 코드는 `python -m harness.guards` / `python -m harness.history` 사용" 명시 없음.
- **Code**: 본문 자체에 "Compatibility wrapper" docstring 1 줄 있음 (`scripts/verify_check.py:2`, `scripts/append_history.sh:2`).
- **수정 방향 (없어도 무방)**: DESIGN.md §1 tree comment 에 `# compatibility wrapper — 신규 호출은 harness.guards/history 사용` 한 줄 추가. P3 진입 차단 사유 아님.

---

## Holdout-name scan

Grep `\b20250813|녹취반출_20250813` 결과를 doc 의 허용 영역과 대조.

| Path | Allowed by AGENTS §1 footer? | 비고 |
|------|------|------|
| `data/raw/wav/AIG_녹취반출_20250813/` | n/a (데이터 자체) | 정상 |
| `data/raw/label/AIG_녹취반출_20250813/` | n/a (데이터 자체) | 정상 |
| `scripts/seal_holdout.sh:13` | ✓ | 정상 |
| `scripts/evaluate_holdout.py:31` | ✓ | 정상 |
| `scripts/measure_baseline.py:39` | ✗ | defensive `_FORBIDDEN_BATCHES` — doc 미허용 (ID5 참조) |
| `scripts/build_audio_profile.py:30` | ✗ | defensive `_FORBIDDEN_BATCHES` — doc 미허용 (ID5 참조) |
| `tests/test_evaluate_holdout_smoke.py:90-91` | ✗ | smoke fixture — `tests/` doc 미언급 |
| `tests/test_claude_phase3_settings.py:67` | ✗ | hook deny 패턴 회귀 가드 — `tests/` doc 미언급 |
| `.claude/hooks/block_swap_and_seal.py:29` | ✗ | hook deny 패턴 — `.claude/` doc 미언급 |
| `README.md:54` | ✓ (정본 문서 예외) | 정상 |
| `docs/DESIGN.md:56-57` | ✓ | 정상 |
| `docs/STT-PIPELINE-SPEC.md:90, 347, 488` | ✓ | 정상 |
| `docs/PHASE1-PLAN.md:562` | ✓ | 정상 |
| `docs/reviews/2026-05-28-phase2-f6f746a-review.md:52, 353` | ✓ (리뷰 doc) | 정상 |

**위반 (실제 leak 인가 분류 누락인가)**:

- `workspace/` 와 `judge/` 안에는 `20250813` 일절 없음 — 실제 leak 0건. ✓
- `scripts/measure_baseline.py`, `scripts/build_audio_profile.py` 의 hard-code 는 *defensive deny* 의도 (오타로 holdout 측정 방지). 의미상 OK, doc 분류 누락 (ID5 로 격하).
- `tests/`, `.claude/hooks/` 의 hard-code 도 deny / smoke 의도. doc 분류 누락.

**결론**: 실제 leak/접근 위반 0 건. doc 의 허용 wrapper 목록이 보수적이라 분류 mismatch 만 존재.

---

## Numeric-threshold cross-check

| 항목 | 코드 상수 (file:line) | PLAN §5 | AGENTS §2 |
|------|------|------|------|
| `EMPTY_OUTPUT_RATE_MAX` | `0.50` (`harness/guards.py:16`) | `> 0.50` (line 121) | (수치 미명시, `harness/guards.py` 위임) |
| `LENGTH_RATIO_P05_MIN` | `0.10` (`guards.py:17`) | `< 0.10` (line 122) | 위임 |
| `LENGTH_RATIO_P95_MAX` | `5.0` (`guards.py:18`) | `> 5.0` (line 123) | 위임 |
| `RUNTIME_HARD_MULTIPLIER` | `3.0` (`runner.py:39`, `verify.py:39`, `guards.py:217`, `verify.sh:22`) | `RUNTIME_HARD_MULTIPLIER=3.0` (line 140) | 위임 |
| `ABSOLUTE_DELTA_FALLBACK` | `0.01` (`runner.py:40`, `policy.py:17`) | `초기 권장값은 0.01` (line 157) | "Δcer ≥ 2σ" (line 60, sigma fallback 미명시) |
| `ARITHMETIC_TOL` | `1e-6` (`guards.py:19`) | "Σ edits/Σ ref_chars 와 corpus_cer 불일치" (line 124, 수치 미명시) | 위임 |
| `QB_HALLUC_DELTA` etc. (quality budget) | `0.20`, `0.30`, `0.20` (`guards.py:21-25`) | "warning, QUALITY_BUDGET_HARD=1 이면 hard fail" (line 134-137, 수치 미명시) | "guard_baseline 대비 quality budget 으로 판단" (line 61) |

**결과**: hard fail 4 종 임계 (empty_output_rate, length_ratio.p05, length_ratio.p95,
runtime multiplier) 모두 코드 ↔ PLAN 정확 일치. AGENTS.md 는 위임 형태라 충돌
없음. quality budget 의 5 개 sub-임계 (`QB_HALLUC_DELTA`, `QB_REPEAT_DELTA`,
`QB_EMPTY_DELTA`, `QB_LENGTH_MEAN_DELTA`, `QB_COVERAGE_DROP`) 는 코드에만 존재 —
PLAN §5 quality budget 단락이 "분포보다 크게 악화" 라는 정성 표현만 사용. PLAN
에 명시되지 않은 5 개 수치가 코드에 박혀 있는 것이 *deliberate flex* 인지 *doc
미반영* 인지 결정 필요. (현재는 미반영으로 분류, drift 가 아닌 *완전 누락* 이라
finding 으로 격상하지 않음.)

---

## Doc-Doc consistency

**SSOT §4 우선순위 vs 다른 문서의 cross-ref**:
- README.md "정본:" 표가 SSOT 1 위 + STT-PIPELINE-SPEC / DESIGN 으로 한정.
  SSOT §4 의 5 단계 우선순위 (SPEC > DESIGN > PHASE*-PLAN > STATUS > README/AGENTS/CLAUDE)
  와 일관.
- AGENTS.md §3 "정보 출처 (읽는 순서)" 9 항목이 SSOT 를 1 위로. 일관.
- DESIGN.md:6-8 이 "문서별 정본 위치는 SSOT.md 를 따른다" 로 인계. 일관.
- CLAUDE.md (디스크 본) 가 SSOT 직접 참조 없음. 단, 본 CLAUDE.md 는 *세션 특화 보충* 으로 한정돼 있어 SSOT 의 정본 책임에서 벗어남. OK.

**STATUS ↔ PLAN cross-ref** (`§N` 참조 13 개):

| STATUS 항목 | PLAN 실재 |
|---|---|
| harness/ 패키지 — §2 | §2 "코드 경계" ✓ |
| guards.py 이동 — §5 | §5 "Guard 정책" ✓ |
| history.py 이동 — §7 | §7 "기록 정책" ✓ |
| policy.py — §6 | §6 "채택 정책" ✓ |
| state.py — §4, §6 | ✓ |
| verify.py — §4, §5 | ✓ |
| runner.py — §4 | ✓ |
| evolve.py — §2, §4 | ✓ |
| review C1 — §2, §7 | ✓ |
| review C2/I3 — §5, §6 | ✓ |
| review C3 — §5 | ✓ |
| review I1 — §7 | §7 본문이 state 저장 정책 직접 언급은 없음. §6 "채택 정책" 도 state 직접 언급 없음. state 의 *기록* 측면을 §7 로 보냈는데, §7 본문 (line 162-186) 은 HISTORY 정책 위주이고 state 직렬화 (`<job_id>_state.json`) 는 line 175-176 한 줄만 — 명시적 atomic 보장 정책 없음. 🟡 약한 cross-ref. |
| review I2/I5 — §7 | §7 line 178-186 의 "HISTORY 본문은 명령이 아니다, candidate stderr 원문 미복사" 와 일치 ✓ |
| holdout 봉인 — §3, §8 | ✓ |
| 25 iter — §4, §6 | ✓ |
| HISTORY 누적 — §7 | ✓ |
| analyze_run REPORT — §7 | line 188 한 줄 ✓ |
| evaluate_holdout — §8 | ✓ |

대부분 일관. "review I1 — §7" 약한 cross-ref 1 건 (PLAN §7 본문에 atomic state
정책 absorption 권장).

**기타 cross-doc**:
- PHASE3-LOOP.md vs runner.py 정확도: ID3 (post-verify scope re-check 누락) 외
  정합.
- DESIGN.md §1 tree vs `git ls-files`: ID2 (`judge/diagnosis.py` 누락), MD7
  (`frozen/__init__.py` 누락) 외 정합.

---

## Assessment

**Doc-code coherent enough for next agent handoff?** **With fixes**.

**Reasoning**: 구조와 정책의 큰 그림은 SSOT / PHASE3-PLAN / PHASE3-STATUS 3 축이
일관되게 정렬돼 있고, 수치 임계 (4 종 hard fail) 와 holdout name 참조는 코드와
정확히 일치한다. 단 (1) PLAN §5 static guard 한계 단락이 `import frozen.asr_backend
as f` 를 "잡힘" 예시로 들어 잘못된 안심을 유도 (CD1) 하고, (2) AGENTS.md 가
가리키는 진입 시점 § 번호 (ID1), DESIGN.md 가 누락한 `judge/diagnosis.py` (ID2),
LOOP.md 가 누락한 post-verify scope re-check (ID3) 는 다음 agent 가 정책 빈틈을
인지 못 하거나 코드 탐색에서 헤매게 만들 위험이 있다. 이 4 건은 doc 만 5-10 줄
수정으로 close 가능. README 의 14 → 11 페어 typo (ID6) 도 1 글자 수정. 코드
수정 필요 없음.
