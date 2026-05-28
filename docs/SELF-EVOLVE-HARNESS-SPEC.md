# Self-Evolve Harness — 일반 원리 명세 (이식용)

> **목적**: 이 문서는 어떤 프레임워크/언어/저장소에 상관없이 *자가 진화형 코딩
> 하네스* 를 처음부터 구축하는 데 필요한 원리·패턴·결정사항을 정리한 명세다.
> 특정 도메인 문제는 짝 문서(예: `STT-PIPELINE-SPEC.md`) 가 정의하고, 이 문서는
> **그 문제를 어떻게 자동으로 풀어내는 시스템을 구성할지** 를 다룬다.
>
> 두 문서 (도메인 명세 + 본 하네스 명세) 만 있으면 새 프로젝트를 처음부터 시작할
> 수 있도록 작성되었다.

---

## 0. 한 줄 요약

**(목표, 데이터셋, 평가, 수정 가능 영역)** 4종 입력이 주어지면, 외부 오케스트레이터가
코딩 에이전트를 반복 호출해 **자동으로 가설을 생성·평가·채택·롤백** 하며 목표
지표를 점진적으로 개선하는 시스템.

---

## 1. 4종 입력

이 하네스를 새 도메인에 적용하려면 다음 네 가지가 반드시 정의돼 있어야 한다.

| 입력 | 정의 | 예시 (ASR) |
|------|------|------------|
| **목표 (metric)** | deterministic하게 산출되는 단일 수치, lower/higher-is-better 명시 | `corpus_cer` (lower) |
| **데이터셋** | 평가용 + holdout 분리, 접근 경로 고정 | 0715 12 페어 + 0813 holdout |
| **평가 (judge)** | 인풋·아웃풋 둘 다에 동일 규칙 적용. 정규화 잠금 | §5.1 정규화 + corpus CER |
| **수정 가능 영역 (workspace surface)** | 에이전트가 만질 수 있는 *유일한* 표면. 최소화. | `transcribe(audio, sr) -> str` 단일 함수 |

이 네 가지가 명확하지 않으면 자동화는 작동하지 않는다 — 도메인 명세를 먼저 마치고
시작한다.

---

## 2. 4영역의 분리 — 가장 중요한 결정

하네스의 모든 파일은 다음 네 영역 중 정확히 하나에 속해야 한다.

| 영역 | 역할 | Agent 권한 | 누가 만지는가 |
|------|------|-----------|---------------|
| **Core** | 외부 루프, 게이트, 검증, agent 호출 | Read X (선택적 허용), Edit/Write X | 사람만 (드물게) |
| **Frozen** | 모델 인프라, judge, 데이터 로더, oracle 측정기 | Read O (모듈별), Edit/Write X | 사람만 |
| **Workspace** | agent 가설 surface — **유일한 편집 표면** | Read/Edit/Write O | agent + 사람 |
| **Runs/Jobs** | 가설 폴더, 결과, REPORT, MEMORY | 가설 폴더 내 Edit O, 결과 산출물 Read만 | agent (실행 중) |

이 분리가 명확하지 않으면:
- agent가 평가자(judge)를 수정해 점수 위조 가능
- agent가 데이터를 만져서 holdout 누설
- agent가 외부 루프 자체를 망가뜨림

**가장 흔한 실수**: workspace가 너무 넓음. 모델 호출 wrapper까지 workspace에 두면
agent가 "모델 자체를 바꾸는" 방향으로 빠짐. **모델 호출은 frozen**, 그 위의 *정책*
구성만 workspace.

---

## 3. Oracle (Target) — 사전 1회 측정

### 3.1 정의

도전할 *상한선* 을 사전에 한 번만 측정해 봉인.

- 기준 알고리즘(또는 기준 라이브러리)을 1회 실행 → 같은 judge로 점수 산출 → 결과
  봉인 (이후 절대 재실행 X)
- agent에게는 **숫자 한 줄** 만 노출 — 측정 코드 본문은 **읽기 금지**

### 3.2 왜 봉인하는가

- 결정론 보장 (같은 oracle을 다시 측정하면 노이즈로 흔들림)
- 측정 코드 안의 기준 알고리즘 디테일이 agent에게 누설되는 것 방지

### 3.3 봉인된 산출물 (권장 스키마)

```json
{
  "target_metric": <number>,
  "metric_name": "corpus_cer",
  "lower_is_better": true,
  "num_items": 12,
  "per_item": [{"id": "...", "metric": 0.04..}],
  "produced_at": <timestamp>,
  "frozen_revision": "<git sha of frozen/ at measurement>"
}
```

### 3.4 종결 조건과 연결

oracle 도달은 잡 **성공 종료** 조건의 하나:

```
if (lower_is_better and current_metric <= target_metric) -> SUCCESS, terminate
```

---

## 4. 외부 루프 — 한 사이클

```
for i in range(max_iterations):
    1. 새 가설 ID + 가설 브랜치 생성 (best_branch에서 분기)
    2. system prompt 빌드 (sanitized — §10)
    3. agent runner 호출 (블로킹)
         └── agent 내부:
             a. workspace 편집
             b. judge 호출 → score_report + diagnosis_report
             c. report 자기 검토 → REPORT.md / MEMORY.md 채움
    4. agent 종료
    5. harness verification (sha + schema + 일관성, §5~§6)
    6. keep/revert 게이트 (§7) → CONTINUE / ROLLBACK
    7. 2차 staging + commit (§12)
    8. CONTINUE → best_branch = hypothesis_branch
       ROLLBACK → checkout best_branch
       SUCCESS → 종료
```

**중첩(nested) 구조**: 외부 루프 1 iteration이 내부 agent multi-turn 세션을 통째로
블로킹 호출. 교대(alternating) 아님.

---

## 5. Authoritative Eval — Source of Truth

### 5.1 단일 산출 경로

평가 결과는 **딱 한 곳** 에서, **딱 한 번** 의 호출로 산출된다.

- agent가 평가를 호출 (`judge` 명령)
- judge가 결정된 경로에 결과 파일을 *기록* 한다
- harness는 **재실행하지 않음** — 검증으로만 진위 확인

### 5.2 위조 방지 — 4중 방어

| 방어선 | 메커니즘 |
|--------|----------|
| 1. 평가자 본문 보호 | `frozen/` 전체 Edit/Write 금지 |
| 2. 결과 파일 직접 쓰기 차단 | 결과 경로 Edit/Write 금지 + shell redirect/sed -i/symlink/touch 등 우회 hook 차단 |
| 3. 결과 파일은 평가 서브프로세스만 작성 가능 | judge가 결정론적 절차로 결과 파일을 생성. 그 외 경로로 쓰기 시도는 sandbox에서 차단 |
| 4. workspace 해시 검증 | 결과 파일의 `workspace_hash` 가 verification 시점의 워킹트리 해시와 일치 — 평가 *후* workspace를 더 만지면 깨짐 |

### 5.3 신뢰 모델

> "agent가 보호 경로를 sandbox에서 우회하지 않는 한, 결과 파일은 진짜다."

sandbox가 뚫리는 경우(권한 escape 등)는 *harness 외부* 의 문제 — 운영 환경에서
별도 검증 (§14).

---

## 6. 변경 증명 — Workspace Hash

### 6.1 정의

workspace 디렉토리 아래 모든 일반 파일을 정렬 후 다음 규칙으로 직렬화한 sha256(또는
동등 해시):

```
H = hash( for path in sorted(workspace files):
            relative_path + NUL + raw_bytes + NUL )
```

### 6.2 제외 패턴은 정확히 명시

와일드카드 카테고리로 두지 말 것 (우회 가능):

```
**/__pycache__/**
**/*.pyc
**/*.pyo
**/.DS_Store
```

(그 외 *모든* 파일을 해시에 포함 — `.txt`, `.yaml`, `.json`, prompt/config 등 확장자
무관. 파일이 들어와 있으면 코드가 읽을 가능성이 있으므로 해시에 잡혀야 함.)

### 6.3 사용

- judge가 평가 시작 시점에 해시 계산 → 결과 파일에 `workspace_hash` 필드로 기록
- harness가 verification 시점에 워킹트리에서 동일 함수로 재계산 → 비교
- 불일치 → ROLLBACK (eval 호출 후 추가 수정 흔적)

### 6.4 단일 정의 위치

해시 계산 함수는 **단 하나의 모듈** 에 둔다 (`core/utils/workspace_hash.*`).
judge와 harness verification 양쪽이 같은 함수를 import — 두 군데에서 따로 구현하면
미묘한 차이로 항상 깨지는 버그 발생.

---

## 7. Keep/Revert 게이트

### 7.1 두 층

| 층 | 종류 | 통과 못 하면 |
|----|------|--------------|
| **Hard gate** | 위조/누락/누설 검출 | 무조건 ROLLBACK (점수 무관) |
| **Soft gate** | 메트릭 비교 / 가드 임계 | ROLLBACK 또는 CONTINUE |

### 7.2 Hard Gate 체크리스트 (모두 통과해야 진행)

- 결과 파일 존재 + 스키마 OK
- diagnosis 파일 존재 + 스키마 OK
- `workspace_hash` 동등성
- 산술 일관성 (예: `Σ edits / Σ ref_chars == corpus_metric ± 1e-6`)
- 산출 데이터셋이 holdout 포함 X, eval 배치만 포함
- REPORT.md의 보고 숫자가 결과 파일의 숫자와 일치 (±tolerance)
- 보호 영역 (`core/`, `frozen/`, `data/`, ...) 변경 없음

### 7.3 Soft Gate (예시 규칙 — 도메인별 조정)

```
if current_metric <= target_metric:                   -> CONTINUE, SUCCESS
if current_metric > previous_best + tolerance:        -> ROLLBACK (regression)
if any guard threshold violated:                      -> ROLLBACK
if abs(current_metric - previous_best) < 2 * sigma:   -> ROLLBACK (noise)
if current_metric <= previous_best - 2 * sigma:       -> CONTINUE
```

### 7.4 Agent recommendation은 advisory

REPORT.md에 agent가 적은 CONTINUE/ROLLBACK은 정보 가치만. 최종 판정은 harness가
authoritative metric + hard/soft gate로 계산.

> agent가 CONTINUE라고 적어도 검증 실패면 ROLLBACK.
> agent가 ROLLBACK이라고 적어도 검증 통과 + 개선이면 CONTINUE.

---

## 8. Noise Floor — Baseline σ

### 8.1 측정

같은 workspace 코드를 **3회 이상** 반복 실행해 메트릭 분포의 표준편차 σ를 구한다.
디코딩 비결정성, GPU 라운딩, 시스템 부하 때문에 같은 코드도 매 실행 값이 다르다.

### 8.2 사용

- σ는 잡 동안 **고정**
- "의미 있는 개선"의 임계: `|Δmetric| ≥ 2σ`
- 그 이하의 차이는 노이즈로 간주 → ROLLBACK

### 8.3 측정 시점

- 가장 깨끗: **oracle 측정과 같은 절차를 3회 반복** → σ 산출
- 차선: 첫 정상 가설을 3회 반복

---

## 9. Score Report + Diagnosis Report

### 9.1 분리 이유

**Score** 는 keep/reject *판정* 에 쓰임 — machine readable, 작고 결정론적.
**Diagnosis** 는 LLM이 *다음 가설을 생각하는 데* 쓰임 — chunk/segment-level
telemetry, 풍부함.

판정 로직과 학습 신호를 한 파일에 섞으면 양쪽 다 망가짐.

### 9.2 Score Report 권장 스키마

```json
{
  "job_id": "...", "hypothesis_id": "...",
  "workspace_hash": "...",
  "started_at": <ts>, "completed_at": <ts>,
  "num_items": <int>,
  "dataset_batches": [...],
  "holdout_excluded": [...],
  "per_item": [{"id": "...", "metric": <num>, ... item-level fields ...}],
  "totals": { ... aggregate sums needed for consistency check ... },
  "primary_metric": <num>,        // 의사결정 기준
  "secondary_metrics": { ... },   // 진단·서브 지표
  "guards": { ... corpus-level guard aggregates ... }
}
```

### 9.3 Diagnosis Report 권장 스키마 (JSONL — 라인당 한 단위)

```jsonl
{"item_id": "...", "unit_idx": 0, "<unit telemetry fields...>"}
{"item_id": "...", "unit_idx": 1, ...}
```

"단위(unit)"는 도메인이 정의 (ASR이면 chunk, 코드 생성이면 함수/모듈, 텍스트
분류면 sentence 등).

### 9.4 Diagnosis 필드 선정 원칙

- **결과를 설명할 수 있는 telemetry** 만 포함 — 어떤 알고리즘이든 채울 수 있는
  값 (예: 처리 시간, 출력 길이, 재시도 여부)
- 알고리즘 특정 필드는 두지 말 것 (특정 정책을 강요하게 됨)
- agent는 이 파일을 *Read* 만 — 다음 가설을 위한 학습 신호 ("the agent's eyes")

### 9.5 Diagnosis가 판정에 직접 쓰이지 않음

판정은 `score_report` 의 `guards` 만 본다. diagnosis는 풍부할수록 좋지만, 풍부함이
keep/reject에 영향 X — agent의 자율 분석 자료.

---

## 10. Sanitization — Zero-base 원칙

### 10.1 층위

| 층위 | 입장 | 노출 |
|------|------|------|
| 알고리즘 zero-base | **O — 핵심** | 정책 어휘 (chunking/fallback/postprocess 등) 어디에도 X |
| 인프라 zero-base | X | mel/tokenizer/loader/raw model API 제공 |
| 모델 정체성 zero-base | X | "어떤 모델을 쓸 것인가" 는 명시 (다른 모델로 갈아치우는 시도 방지) |
| 평가 정체성 zero-base | X | judge 정의는 명시 (정규화 + 집계 규칙) |

### 10.2 무엇을 숨기고 무엇을 노출하는가

| 노출 | 숨김 |
|------|------|
| 모델/판정 정체성 | 기준 알고리즘 구현 코드 (oracle 측정기 본문) |
| 데이터셋 형식/접근 방법 | holdout 데이터 자체 |
| 인프라 API 시그너처 + 타입/shape docstring | 정책 어휘를 담은 docstring/주석 |
| 자기 결과 (eval JSON) Read | agent 자기 stdout/stream 로그 Read (context bleed) |
| 자기 diagnosis Read | 잘못된 시도가 들킬 정보 채널 |

### 10.3 금지어 가드

prompt/JOB/DOMAIN/seed code 모든 텍스트 파일에 대해 도메인-특정 금지어 목록을
substring 검사하고, 잡 시작 전 위반 발견 시 abort.

ASR 도메인 금지어 예시:
```
chunk, chunking, overlap, fallback, VAD, silence,
postprocess, condition_on_prev, hallucination, repetition,
temperature, beam, stitching, merge, windowing,
faster-whisper(특정 라이브러리명)
```

각 도메인에 맞게 목록을 다시 작성.

### 10.4 정직한 한계 인정

- 모델 이름을 노출하는 순간 외부 자료(논문/위키/검색)에서 모델 본질적 사실을
  학습 가능 — 완전 차단 불가
- WebFetch/WebSearch 차단 옵션이 있어도 100% 막을 수 없음
- "알고리즘 zero-base"가 실용적으로 달성 가능한 최선

이 한계는 *문서로 명시* 하고 받아들인다.

---

## 11. Holdout 규율

### 11.1 다층 방어

| 층 | 메커니즘 |
|----|----------|
| 1 | 데이터셋 로더 자체에서 holdout 배치를 allowlist 외 거부 |
| 2 | sandbox path deny — holdout 디렉토리 Read/Edit/Write 모두 금지 |
| 3 | sandbox 패턴 hook — holdout 이름이 어떤 도구의 어떤 인자에 등장해도 차단 |
| 4 | hard gate — 결과 파일에 holdout 배치가 등장하면 ROLLBACK |

### 11.2 평가 시점

- 잡 동안: 절대 접근 X
- 잡 종료 후: 사용자가 **수동으로 1회만** 평가 (자동화하지 않음)

### 11.3 우회 차단

- shell redirect, symlink, mv/cp -f, sed -i, git checkout/restore 등 모든 우회 패턴을
  hook에서 차단
- 절대 경로 / 상대 경로 / basename 패턴 다 등록

---

## 12. Branch / Commit 규약

### 12.1 분기 모델

```
best_branch (main 또는 trunk 역할)
   ├── hypothesis_branch_001 (분기 → 평가 → CONTINUE → best 전진)
   ├── hypothesis_branch_002 (분기 → 평가 → ROLLBACK → 그대로 두고 best는 001)
   └── ...
```

### 12.2 Two-stage Staging

Eval 산출물 (`score_report`, `diagnosis`) 과 REPORT/MEMORY 변경이 **반드시 같은
commit에 묶여야** 한다. 그러려면:

```
1차 staging:  git add -A                    # agent 편집 → index
agent: judge 호출 (결과 파일 산출)
verification + 게이트 판정
2차 staging:  git add -A                    # eval 산출물 + REPORT + MEMORY
commit:        하나의 commit으로 묶기
```

1차 staging만 하고 commit하면 결과 파일이 commit에 포함되지 않아 분석 시 추적 불가.

### 12.3 Commit 메시지 규약

```
feat(experiment): <hyp_id> - <CONTINUE|ROLLBACK>

primary_metric: <num>
previous_best:  <num>
guards: ok|fail(...)
hard_gate: ok|fail(...)
```

---

## 13. Agent Runner 추상

### 13.1 인터페이스 (구체 도구 무관)

```
runner.run(
    system_prompt:  str,        # sanitized
    working_dir:    path,       # 워크스페이스 루트
    permissions:    PermissionPolicy,  # deny/hook
    timeout_s:      int,
    on_log:         callable,   # 실시간 로그 콜백
) -> RunResult { return_code, log_path, stream_path, meta_path }
```

### 13.2 구체 옵션 (예: Claude Code CLI)

```
spawn("claude", [
  "--print",
  "--output-format", "stream-json",
  "--system-prompt", system_prompt,
  "--add-dir", working_dir,
  "--dangerously-skip-permissions",   # ← deny+hook이 작동하는지 사전 smoke test 필수
  "-p", user_prompt,
])
```

다른 에이전트 런너(OpenAI Agents, Aider, 자체 구축 등)도 동일 인터페이스로 추상화.

### 13.3 산출물

매 호출당 다음 파일을 **agent 폴더 안에** 저장 (agent 본인은 Read 금지 — context
bleed 방지):

- `agent.log` — stdout
- `agent.stream.jsonl` — 구조화 이벤트 (turn, tool call, ...)
- `agent.meta.json` — return_code, timestamp, runner 메타

---

## 14. 안전 경계 (Sandbox)

### 14.1 권한 정책 — 두 메커니즘 결합

| 메커니즘 | 역할 |
|----------|------|
| **Deny list (정적)** | 보호 경로에 대한 Edit/Write/Read 거부 — runner의 권한 시스템 사용 |
| **Hook (동적)** | 도구 호출 직전 페이로드 검사 — shell 우회·심볼릭 링크·redirect 등 차단 |

deny만 두면 우회 가능. hook만 두면 정적 검사를 못 함. 둘 다 필요.

### 14.2 Smoke Test — Runner 권한이 실제로 적용되는지 사전 검증

Runner가 권한을 **무시할** 수 있는 옵션(예: `--dangerously-skip-permissions`)을 켤
경우, 정말로 deny가 hard-block되는지 직접 검증:

| 시도 | 기대 |
|------|------|
| Read holdout path | BLOCKED |
| Edit protected file | BLOCKED |
| Bash `cat protected_file` | BLOCKED |
| Bash `echo ... > protected_file` | BLOCKED |
| Bash `ln -s protected /tmp/alias && cat /tmp/alias` | BLOCKED |
| Bash `sed -i ... protected_file` | BLOCKED |

하나라도 통과하면 **잡 시작 금지** — 권한 정책 수정 또는 fallback (예: 권한 우회
옵션 비활성화) 필수.

### 14.3 우회 패턴 카탈로그 (hook에서 차단)

- shell redirect: `>`, `>>`, `tee`
- 파일 조작: `touch`, `mv`, `cp -f`, `ln -s`
- in-place 편집: `sed -i`
- VCS 우회: `git checkout/restore` 보호 경로
- 동적 dispatch: Python `open(..., 'w')` 같은 ID 우회

각 패턴을 정규식으로 등록하고 PreToolUse hook에서 매칭.

---

## 15. 실패 모드와 대응

| 실패 모드 | 증상 | 대응 |
|-----------|------|------|
| Agent가 결과 위조 | score_report 숫자가 실제 데이터와 안 맞음 | 산술 일관성 hard gate에서 catch |
| Agent가 평가 안 돌리고 숫자만 적음 | 결과 파일 없음 | hard gate에서 catch |
| Agent가 평가 후 workspace 추가 수정 | `workspace_hash` 불일치 | hard gate에서 catch |
| Agent가 holdout 누설 | 결과 파일에 holdout 배치 포함 또는 hyp에 라벨 본문 등장 | 결과 hard gate + sandbox + leakage 가드 |
| Non-determinism으로 노이즈 채택 | metric 차이가 σ 이하인데 CONTINUE | noise floor 임계 (Δ ≥ 2σ) |
| Sanitization 누락 | prompt에 알고리즘 어휘 남아 있음 | 금지어 substring 가드 (잡 시작 전 abort) |
| Permission system 우회 | smoke test에서 사전 catch | runner 옵션 변경 또는 물리적 격리 |
| 무한 회귀 (NotImplementedError 시드) | corpus_metric ↔ baseline 비교 불가 | oracle을 baseline으로 사용 또는 첫 정상 가설 후 σ 측정 |
| Agent가 결과 파일 Read만 가능해서 분석 못 함 | diagnosis 활용 안 됨 | diagnosis 파일 Read 명시 허용 |

---

## 16. 진화 단계 (Roadmap)

### 16.1 Stage 1 — AutoResearch single-best (시작점)

- `best` 하나만 유지
- 매 iter: best에서 분기 → 가설 → 채택 시 best 전진, 아니면 분기 폐기
- 가장 단순. 처음 구현은 여기까지만.

### 16.2 Stage 2 — Archive

- CONTINUE된 좋은 가설들을 `archive/` 에 저장 (점수, 변경 내용, 시점)
- 다음 가설 시 archive를 참조 자료로 제공
- "방금 잘 된 가설을 잊지 않고 다시 활용"

### 16.3 Stage 3 — Population

- 여러 분기를 병렬로 유지 (각각 다른 전략)
- 자원 허용 시 multi-process

### 16.4 Stage 4 — Selection / Recombination

- 좋은 가설들의 *결합* 시도 (AlphaEvolve 스타일)
- LLM에 두 가설의 diff를 함께 보여주고 "둘의 장점을 합쳐라" 요청

**경고**: 처음부터 Stage 4를 시도하지 말 것. Stage 1이 안정적으로 동작해야
의미가 있음.

---

## 17. 도메인 적용 체크리스트

새 도메인에 본 하네스를 적용할 때 다음 항목 모두 정의:

### 17.1 도메인 정의 (도메인 명세 문서)

- [ ] 목표 metric 이름 + lower/higher-is-better
- [ ] 데이터셋 위치 + 페어링 규칙 + 라벨 포맷
- [ ] 평가 정규화 단계 (정확한 순서)
- [ ] 메트릭 집계 방식 (corpus-level / macro / etc.)
- [ ] holdout 정의
- [ ] backend / 모델 / 인프라 고정 (변경 금지 항목 명시)
- [ ] 파이프라인 I/O 계약 (단일 함수 시그너처)

### 17.2 하네스 설계

- [ ] 4영역 디렉토리 매핑 (core / frozen / workspace / runs)
- [ ] workspace를 정확히 어디까지 좁힐지 결정
- [ ] frozen에서 노출할 모듈과 차단할 모듈 분리
- [ ] oracle 측정 절차 + 봉인 위치
- [ ] score_report + diagnosis_report 스키마
- [ ] noise floor 측정 절차
- [ ] hard gate / soft gate 룰
- [ ] guards 정의 (corpus + per-item)
- [ ] 도메인 금지어 목록 (sanitization)

### 17.3 운영 인프라

- [ ] sandbox path deny + hook 패턴
- [ ] holdout 다층 방어
- [ ] permission smoke test matrix
- [ ] commit/branch 규약 (two-stage staging)
- [ ] agent runner 추상화
- [ ] failure mode mapping

---

## 18. 안티 패턴 (피해야 할 것)

| 안티 패턴 | 대신 |
|-----------|------|
| Workspace에 모델 호출 코드 포함 | 모델은 frozen, *정책* 만 workspace |
| Score와 diagnosis를 한 파일에 섞음 | 두 파일로 분리 |
| Agent recommendation을 의사결정 채택 | harness 검증 + gate가 최종 판정 |
| 노이즈 검증 없이 단조 개선 가정 | σ 측정 후 2σ 임계 |
| Sanitization을 prompt만 검사 | seed code/JOB/DOMAIN/template 전부 검사 |
| Deny list만으로 보안 신뢰 | deny + hook + smoke test 3중 |
| 한 commit에 agent 편집과 eval 결과를 분리 | two-stage staging으로 묶기 |
| Holdout을 자동화에 한 번이라도 노출 | 자동화에서 완전 격리, 종료 후 수동만 |
| 첫 시도부터 population/archive | single-best부터 |
| Workspace 해시 함수를 두 모듈에 따로 구현 | 단일 모듈 정의, 양쪽이 import |
| Frozen 코드 본문 전체를 Read 허용 | 알고리즘 누설 위험 모듈만 선별 차단 (예: oracle 측정기) |

---

## 19. 한 줄 정리

> **"4종 입력(목표·데이터·평가·workspace) + 4영역 분리(core/frozen/workspace/runs)
> + 봉인된 oracle + authoritative eval + workspace hash + hard/soft gate + score+diagnosis
> 분리 + sanitization + holdout 다층 방어 + two-stage commit + smoke-tested sandbox"**

이 11가지가 어떤 도메인에서든 자가 진화형 코딩 하네스의 뼈대다. 도메인 명세 문서가
4종 입력을 정의하면, 본 문서의 나머지 7가지를 도메인에 맞춰 구체화하면 된다.

---

**문서 종결**. 본 명세 + 도메인 명세 (예: `STT-PIPELINE-SPEC.md`) 만 있으면 어떤
프레임워크/언어로든 이 하네스를 처음부터 구축할 수 있다.
