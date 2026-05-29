# AUTORESEARCH 조사 기록

> **상태**: historical/deprecated. Phase 3 운영 정본은 이제
> [`PHASE3-PLAN.md`](PHASE3-PLAN.md)의 자체 `harness/` 절차다. 이 문서는
> 왜 외부 `autoresearch` 중심 운영에서 repository 내부 harness로 전환했는지에 대한
> 조사 근거로만 보존한다.

> **목적**: 전환 전 검토했던 `autoresearch` 의 정체·동작·가드 함의를 보존한다.
> 현재 PHASE3-PLAN / DESIGN 의 운영 가정은 이 문서가 아니라 자체 `harness/`
> 절차를 기준으로 정합되어야 한다.
>
> **1차 자료**: <https://github.com/uditgoenka/autoresearch> (master).
> 조사 시점: 2026-05-28. 본문 인용은 README / `.claude/skills/autoresearch/SKILL.md` /
> `.claude/hooks/autoresearch/` 디렉토리 구조 기준.

---

## 1. 정체

`autoresearch` 는 **Claude Code 의 스킬(skill) 묶음** 이다. CLI 도 라이브러리도
아니고, Claude Code 의 슬래시 커맨드 / 훅 / 스킬 정의 파일들의 모음이다.

- 배포 형태: GitHub 저장소 (`uditgoenka/autoresearch`, MIT). 설치 시 사용자 또는
  프로젝트의 `.claude/` 안으로 파일이 들어간다.
- 의존성: Claude Code 자체. 외부 Python/Node 라이브러리 없음 (훅 스크립트는
  Node 로 작성됨 — `.cjs`).
- 멀티 플랫폼: Claude Code 외에 OpenCode / OpenAI Codex 변종도 같은 저장소에서
  제공. 우리 프로젝트는 Claude Code 만 대상.
- 영감: Karpathy 의 *autoresearch* — "수정 → 검증 → 유지/폐기 → 반복" 의
  도메인 일반화.

---

## 2. 설치

```bash
# Claude Code, 권장
npx skills add uditgoenka/autoresearch

# 또는 플러그인 마켓
/plugin marketplace add uditgoenka/autoresearch
```

설치 후 사용자(또는 프로젝트)의 `.claude/` 트리에 다음이 들어간다:

```
.claude/
  commands/
    autoresearch.md                       # 41-line 라우팅
    autoresearch/
      plan.md, debug.md, fix.md, ...      # 12 서브커맨드
  skills/
    autoresearch/
      SKILL.md                            # 메타스킬 정의
      references/
        predict-personas.md
        reason-judge-protocol.md
        security-checklist.md
  hooks/
    autoresearch/
      hooks.json                          # 훅 등록
      node-hook-runner.sh
      scout-block.cjs
      privacy-block.cjs
      dangerous-cmd-block.cjs
      iteration-context.cjs
      subagent-context.cjs
      dev-rules-reminder.cjs
      simplify-gate.cjs
      session-init.cjs
      stop-notify.cjs
      .ckignore                           # gitignore 문법
      lib/                                # 보조 모듈
```

> **전환 근거**: 설치 위치가 글로벌 또는 프로젝트 `.claude/` 중 어디가 될지
> 외부 상태에 의존한다. 이 불확실성도 repository 내부 harness로 전환한 이유 중
> 하나다.

---

## 3. 슬래시 커맨드 (13 종)

| 커맨드 | 역할 | 기본 반복 |
|--------|------|---------|
| `/autoresearch` | 핵심 루프 (modify → verify → keep/discard) | 25 |
| `/autoresearch:plan` | Goal → validated Scope/Metric/Verify 변환 | 단일 |
| `/autoresearch:debug` | 버그 사냥 | 15 |
| `/autoresearch:fix` | 에러 제거 | 20 |
| `/autoresearch:security` | STRIDE+OWASP 감사 | 15 |
| `/autoresearch:ship` | 8 단계 배포 워크플로우 | 선형 |
| `/autoresearch:scenario` | 시나리오 시뮬레이션 | — |
| `/autoresearch:predict` | 예측 | — |
| `/autoresearch:learn` | 학습 보조 | — |
| `/autoresearch:reason` | 판단 / 비교 | — |
| `/autoresearch:probe` | 탐사 | — |
| `/autoresearch:improve` | 개선 루프 | — |
| `/autoresearch:evals` | 과거 결과 분석 (`*-results.tsv` 읽음) | — |

조사 당시 직접 사용 후보는 `/autoresearch` (메인) + `/autoresearch:plan`
(선택, 입력 검증) + `/autoresearch:evals` (잡 종료 후 분석) 이었다. 자체 harness
전환 뒤에는 운영 전제가 아니다.

---

## 4. 입력 4 원소 + Iterations

`/autoresearch` 호출 시 다음 5 필드가 표준 입력:

```
/autoresearch
Goal: <plain language outcome>
Scope: <glob patterns, comma-separated>
Metric: <metric name + direction (higher/lower is better)>
Verify: <shell command — last numeric stdout = metric>
Iterations: <N, 기본 25>
```

각 필드 의미:

- **Goal** — 자유 문장. 무엇을 달성하려는지. (예: "테스트 커버리지를 72% → 90%")
- **Scope** — glob 패턴 목록. 수정 가능 영역 *지시*. **enforce 안 됨 — §6 참조**.
- **Metric** — 검증 명령의 마지막 숫자 출력 + 방향. 더 높을수록 좋은지 낮을수록
  좋은지 명시.
- **Verify** — shell 한 줄. 마지막 stdout 숫자가 Metric. 종료 코드 비정상이면
  "crashed" 로 간주 → 후속 처리.
- **Iterations** — 최대 반복. 기본 25.

`--evals` (중간 체크포인트) 와 `--chain <a,b>` (커맨드 연쇄 — `handoff.json` 매개)
선택 플래그.

---

## 5. 루프 동작 (정확한 알고리즘)

매 iteration:

1. **상태 검토** — git history + 결과 로그 (`autoresearch/<sub>-<YYMMDD>-<HHMM>/`
   디렉토리의 TSV / handoff.json) + 현 코드.
2. **변경 선택** — 무엇이 효과 있었고, 뭐가 실패했고, 뭐가 미시도인지에 근거.
3. **편집** — 한 가지 포커스된 수정. Claude Code 의 Edit/Write 도구 사용.
4. **commit (검증 *전*)** — `git commit -m "experiment: <description>"`.
5. **verify 호출** — Verify 의 shell 명령을 실행. 마지막 stdout 숫자를 파싱.
6. **판정**:
   - improved → keep (아무 액션 없음)
   - worse → `git revert HEAD` (즉시·비대화식)
   - crashed (exit ≠ 0 또는 숫자 파싱 실패) → fix or skip
7. **결과 로그** — `autoresearch/<sub>-<YYMMDD>-<HHMM>/*-results.tsv` 에 append.
   컬럼: `iteration commit metric delta status`.
8. **반복** — Iterations 회까지 또는 goal 도달까지.

> **주의 1 — 커밋이 검증 *전***: revert 가 일어나도 git 히스토리에는 `experiment:`
> 커밋이 남는다. 우리 메인 브랜치를 직접 가리키게 두면 히스토리가 빠르게 오염된다.
>
> **주의 2 — Verify 의 stdout 숫자**: 우리 `verify.sh` 는 이미 마지막 줄에
> `corpus_cer` 하나만 찍도록 설계되어 있다. autoresearch 의 "마지막 숫자 파싱" 과
> 정합한다.
>
> **주의 3 — keep/revert 임계는 autoresearch 가 결정**: "improved" = 단순 부등호
> 비교 (방향에 따라). 명시적 σ 노이즈 임계 지원 여부는 본 README 에서 확인되지
> 않음 (PHASE3-PLAN §8 열린 검토 항목). verify 측에서 이전 best 대비 노이즈 임계
> 검사를 직접 두는 편이 안전.

---

## 6. Scope 강제 모델 — **prompt-only, sandbox 아님**

본 문서의 가장 중요한 사실.

> **autoresearch 의 Scope 는 enforce 되지 않는다.** Scope 는 자연어/glob 지시이고,
> 에이전트는 "이 범위만 만져라" 라는 *명세* 를 prompt 로 받는다. Claude Code 의
> Edit/Write 도구가 Scope 밖 파일을 차단하지는 *않는다*.

근거: README 의 명시적 문구 — *"violations aren't technically prevented — the
agent is instructed to respect boundaries"*. `/autoresearch:debug` 의 `--scope
<glob>` 도 입력 지시일 뿐 sandbox 가 아니다.

함의:

- **이 프로젝트의 Phase 3 가드 핵심**: workspace/transcribe.py 외 영역 보호는
  autoresearch 가 *해주지 않는다*. 우리가 별도로 책임져야 한다.
- 후보 보호 메커니즘 (Phase 3 진입 직전 재논의 대상):
  1. 우리 저장소 `.claude/settings.json` 의 `permissions.deny` / `allow` 규칙으로
     Edit/Write 대상 파일 제한.
  2. 우리 저장소 `.claude/hooks/` 의 **PreToolUse** 훅으로 judge/ frozen/
     baseline/ assets/ scripts/swap_verify.sh 등 편집 시도 거부.
  3. holdout 디렉토리는 OS chmod 000 (이미 §9 의 `scripts/seal_holdout.sh`).
  4. backend/profile 직접참조 차단은 verify.sh 의 정적 grep (이미 `verify.sh.alt`).
- 위 1·2 는 autoresearch 의 9 가지 훅과는 별개로 우리가 직접 작성해야 한다.

---

## 7. 안전 훅 (9 종) — *일반 안전, 프로젝트 scope 아님*

autoresearch 가 함께 설치하는 훅. PreToolUse / UserPromptSubmit / SessionStart /
SessionEnd 이벤트에 등록.

| 훅 | 이벤트 | 역할 |
|----|--------|------|
| `scout-block` | PreToolUse | `node_modules/`, `.git/`, `__pycache__/` 등 컨텍스트 채우기 차단 |
| `privacy-block` | PreToolUse | `.env`, SSH 키, 자격증명 차단 |
| `dangerous-cmd-block` | PreToolUse | `force-push`, `rm -rf`, `git reset --hard` 등 |
| `iteration-context` | UserPromptSubmit | TSV iteration 데이터 주입 |
| `subagent-context` | SubagentStart | 루프 상태 공유 |
| `dev-rules-reminder` | UserPromptSubmit | compaction 이후 룰 재주입 |
| `simplify-gate` | UserPromptSubmit | 400 LOC 경고, 800 LOC 차단 |
| `session-init` | SessionStart | 프로젝트 컨텍스트 셋업 |
| `stop-notify` | SessionEnd | 터미널 알림 + 선택적 webhook |

**비활성화** — 각 훅마다 ENV 토글:

```bash
export AR_DISABLE_SCOUT_BLOCK=1
export AR_DISABLE_PRIVACY_BLOCK=1
export AR_DISABLE_DANGEROUS_CMD_BLOCK=1
export AR_DISABLE_ITERATION_CONTEXT=1
export AR_DISABLE_SUBAGENT_CONTEXT=1
export AR_DISABLE_DEV_RULES_REMINDER=1
export AR_DISABLE_SIMPLIFY_GATE=1
export AR_DISABLE_SESSION_INIT=1
export AR_DISABLE_STOP_NOTIFY=1
```

선택 webhook: `export AR_NOTIFY_WEBHOOK=<URL>`.

> **이 훅들은 *일반적 안전* 만 다룬다** — privacy, danger, context bloat. **우리
> 프로젝트의 scope (workspace/transcribe.py 만 편집 가능) 는 막아주지 않는다.**
> 또한 ENV 한 줄로 모두 우회 가능하므로 *프로젝트 가드의 1차 layer 로 의존하면
> 안 된다*. 보조 layer 로만 활용.

---

## 8. `.ckignore` (gitignore 문법)

프로젝트 루트의 `.ckignore` 가 `scout-block` 의 차단 목록을 *확장* 한다.
gitignore 와 동일 문법.

예시 (본 프로젝트가 채택하면 추가할 패턴):

```
data/raw/wav/AIG_녹취반출_20250813/**
data/raw/label/AIG_녹취반출_20250813/**
assets/audio_profile/**
baseline/**
judge/**
frozen/**
scripts/swap_verify.sh
scripts/seal_holdout.sh
scripts/evaluate_holdout.py
```

다만 `.ckignore` 는 **읽기만 막는다 (scout-block)** — 편집 차단이 아니다. 편집
차단은 별도 PreToolUse 훅이 필요.

---

## 9. 결과 저장 위치

autoresearch 가 생성하는 산출물:

```
autoresearch/<subcommand>-<YYMMDD>-<HHMM>/
  *-results.tsv        # iteration | commit | metric | delta | status
  handoff.json         # 체인용 Goal/Scope/Metric/Verify config
  <기타 로그>
```

> **본 프로젝트의 `runs/<hyp_id>/` 와 다른 트리**. 두 산출 체계가 *공존* 한다:
>
> - autoresearch 자체 로그: `autoresearch/...` (autoresearch 가 씀)
> - 우리 evaluation 산출: `runs/<hyp_id>/score_report.json`, `per_file.jsonl`,
>   `diagnosis_report.json`, `_telemetry/` (verify.sh / judge.evaluate 가 씀)
>
> autoresearch 의 TSV 와 우리 `score_report.json` 은 *서로 모른다*. 종료 후
> 분석은 우리 `scripts/analyze_run.py` 가 `runs/` 만 본다. autoresearch 의 TSV 는
> 디버깅 용으로 보조 참조 가능하나 분석 정본 아님.

---

## 10. Git 동작

- 매 iteration 마다 *검증 전* `git commit -m "experiment: <desc>"` 1 회.
- 메트릭 악화 시 `git revert HEAD` 즉시. 사용자 확인 없음.
- 결과: 메인 브랜치에 `experiment:` 와 `Revert "experiment:..."` 가 빠르게 누적.

함의:

- Phase 3 잡은 **별도 작업 브랜치** 에서 돌리는 편이 합리적. (지금 우리는 이미
  `phase3` 브랜치에 있다.)
- 잡 종료 후 사람이 squash/rebase 또는 `experiment:` 만 골라서 PR 으로 정리.

---

## 11. 본 프로젝트에 대한 함의 (요약)

| 항목 | 결론 |
|------|------|
| **Scope 강제** | autoresearch 가 안 함. 우리가 PreToolUse 훅 / settings.json 으로 별도 구현 필요 |
| **verify.sh** | 마지막 줄 숫자 1개 (corpus_cer) — 정합 ✓ |
| **σ 노이즈 임계** | autoresearch 가 자체 지원하는지 미확인 — verify 가 직접 책임지는 게 안전 |
| **9 가지 훅** | 일반 안전만. 우리 scope 보호 아님. ENV 우회 가능 → 1차 layer 아님 |
| **`.ckignore`** | 읽기 차단만 — 편집 차단은 별도 훅 필요 |
| **결과 저장 위치** | `autoresearch/...` 와 `runs/<hyp_id>/` 가 공존. 분석 정본은 후자 |
| **git 동작** | 메인 브랜치에 commit/revert 누적 — 잡은 작업 브랜치에서 |
| **commit 가 verify *전*** | verify 가 catastrophic 잡아도 commit 은 이미 존재 → revert 발생. 히스토리 노이즈 감수 |
| **AR_DISABLE_\*** | 우리 가드는 ENV 토글로 끄지 *못하게* 구현해야 함 |

PHASE3-PLAN §1 ~ §3 의 가드 구조와 §8 의 열린 검토 항목은 본 문서를 정본으로
삼아 재정합되어야 한다. 본 문서는 정합 작업을 시작하기 *전* 의 1 차 자료
스냅샷이다.

---

## 12. 출처

- [GitHub: uditgoenka/autoresearch](https://github.com/uditgoenka/autoresearch)
- [README.md](https://github.com/uditgoenka/autoresearch/blob/master/README.md)
- [.claude/skills/autoresearch/SKILL.md](https://github.com/uditgoenka/autoresearch/blob/master/.claude/skills/autoresearch/SKILL.md)
- [.claude/skills/autoresearch/references/](https://github.com/uditgoenka/autoresearch/tree/master/.claude/skills/autoresearch/references)
- [.claude/hooks/autoresearch/](https://github.com/uditgoenka/autoresearch/tree/master/.claude/hooks/autoresearch)
- [프로젝트 페이지](https://udit.co/projects/autoresearch)

조사 일자: 2026-05-28. 본 문서는 정적 스냅샷이며 autoresearch 본 저장소의 향후
버전과 어긋날 수 있다. Phase 3 진입 전 최신 README 와 본 문서 §6·§7·§9·§10 의
사실을 사람이 재확인할 것.
