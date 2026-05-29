# Candidate 컨텍스트 — 무엇이 들어가나, 어떻게 검증하나

> 목적: Phase 3 candidate (`claude -p` 호출 대상) 가 매 iter 실제로 받는
> context 를 명문화하고 자동 검증한다. CLAUDE.md / 플러그인 / 메타 지식이
> 의도치 않게 들어가면 candidate 의 의사결정을 오염시켜 잡 결과 해석을
> 망친다. 잡 시작 전 1 회 확인하는 것이 운영 원칙.
>
> 본 문서는 *claude CLI* (`claude -p`) 기준. claude API (Messages) 로 옮길
> 때 고려사항은 §9.

---

## 1. 왜 중요한가

candidate 가 받는 context = 우리가 *명시 주입* 한 것 + claude CLI 가 *자동
주입* 한 것 + candidate 가 *Tool 로 가져온* 것. 셋 다 의사결정에 영향을
미친다.

phase3_001 (chunking 83% 단조성) 의 원인 분석 중 발견: `claude -p` 가
`CLAUDE.md` 와 설치된 플러그인의 SessionStart 훅을 자동 로드한다. 그 결과
candidate 는 운영자용 가이드 ("채팅 응답 짧고 간결", "judge/ 본문 의심 시
*제안만*") 와 플러그인의 "skills BEFORE response" 강제를 *우리가 모르게*
같이 받는다. 이는:

1. A’ profile (`harness/prompts/candidate.md`) 의 의도와 충돌 가능
2. 잡 간 ablation 비교 불순 (CLAUDE.md 갱신이 candidate 결과를 흔듦)
3. PII (운영자 email) 가 매 candidate 호출에 노출

---

## 2. PUSH — runner 가 명시 주입하는 것

`harness.runner.build_candidate_prompt` 가 매 iter 다음을 한 문자열로
합쳐 `claude -p` 의 마지막 argv 로 전달한다. 결과는 그대로
`runs/<hyp_id>/prompt.md` 에 저장되어 재현·디버깅 가능.

| 구성 | 출처 | 비고 |
|---|---|---|
| **Candidate profile 본문** | `harness/prompts/candidate.md` 전체 | 역할 / 5 lane / YAML format / 자기검증 — A’ 정본 |
| Goal | `baseline/target_cer.json:target_cer`, `total_inference_time_s` | target / runtime budget |
| Hard constraints | hardcoded in runner | 7 줄 — backend deny, scope, one-focused-change |
| Current state | `HarnessState` (job_id, iteration, best_hyp_id, best_cer, sigma) | 5 줄 |
| Recent 5 iter table | `runs/<job_id>_iter_*/candidate_meta.json` + `score_report.json` | A’ 다양성 신호 (lane / fingerprint / cer) |
| Suggested lane | `iter % 5` round-robin | "may override with justification" |
| HISTORY tail | `runs/_summary/HISTORY.md` 마지막 12000 자 | 다중 iter narrative — C-lite 에서 압축 예정 |
| Best diagnosis | `runs/<best_hyp_id>/diagnosis_report.json` 처음 12000 자 | 11 페어 summary + focus 표시 |
| Edit 지시 | hardcoded | `workspace/transcribe.py` 직접 편집, docs/tests/scripts 금지 |

총 크기: 약 30–50 KB (가변, HISTORY/diagnosis 가 주범).

---

## 3. AUTO-PUSH — `claude -p` 가 cwd 에서 자동 로드

**우리가 통제 못 함**. claude CLI 자체 동작.

| 항목 | 로드 여부 | 영향 |
|---|---|---|
| `CLAUDE.md` (프로젝트 root) | **YES** — 본문 그대로 `# claudeMd` 섹션으로 system prompt 에 |  운영자 가이드 leak |
| `AGENTS.md` | **NO** (참조만) | 다행 |
| `~/.claude/CLAUDE.md` (user global) | 환경 의존 — 보통 YES | 사용자별 메모 누수 |
| `.claude/settings.json` | 정책으로 적용 (본문 X) | 의도대로 — 샌드박스 |
| `.claude/hooks/` | PreToolUse 훅 발화 | 의도대로 — restrict_workspace 등 |
| 플러그인 SessionStart 훅 | **YES** — 본문 inject | `superpowers:using-superpowers` 가 "skills BEFORE response" 강제 |
| git status + 최근 commit 5 개 | YES — system prompt | `iter25: reject phase3_001_iter_025` 같은 메타 노출 |
| cwd, env, OS, model id | YES | 정상 |
| userEmail | **YES** — `# userEmail` 섹션 | PII leak |
| currentDate | YES | OK |
| 사용 가능 skills 목록 (이름) | YES | candidate 가 `autoresearch:plan` 등 호출 시도 가능 |
| MCP 서버 (예: Google Drive) | YES (연결되어 있으면) | tool 호출 가능 표면 ↑ |

차단 옵션:
- `claude -p --bare` : auto-discovery 전부 끔. **단** hooks 도 같이 꺼지므로
  `.claude/hooks/restrict_workspace.py` 가 안 뜸 → 샌드박스 깨짐. 사용 시
  `--settings` / `--allowedTools` 로 권한 모델을 처음부터 재구성 필요.
- `--system-prompt <…>` : 기본 system prompt 교체. CLAUDE.md auto-discovery
  도 같이 우회되는지 별도 검증 필요 (CLI 버전 의존).
- 임시 rename: 잡 시작 시 `mv CLAUDE.md CLAUDE.md.operator`, 종료 시 복원.
  훅 / 메모리는 그대로 유지하면서 CLAUDE.md 만 죽임.
- CLAUDE.md 본문 자체를 candidate-safe 한 한 줄로 줄이고 운영자 가이드는
  AGENTS.md 로 이전 (auto-load 안 됨).

---

## 4. PULL — candidate 가 Tool 로 가져올 수 있는 것

`.claude/settings.json` 의 deny 는 주로 **Edit/Write/MultiEdit** 를 막는다.
**Read** 는 거의 안 막혀 있어 candidate 가 마음만 먹으면 다음을 PULL 가능:

| 대상 | Read 가능? | 위험 |
|---|---|---|
| `workspace/transcribe.py` | ✓ (편집 대상이라 당연) | — |
| `runs/_summary/HISTORY.md` 전체 (tail 12000 자 너머) | ✓ | A’ 압축 무력화 |
| `runs/<과거_hyp_id>/diagnosis_report.json` / `per_file.jsonl` / `candidate.diff` / `prompt.md` / `claude_stdout.txt` | ✓ | 모든 과거 잡 (phase3_001 포함) 시도 전체 노출 |
| `docs/PHASE3-PLAN.md`, `docs/proposals/*`, `docs/reports/*` | ✓ | harness 메타 지식 + 직전 잡 REPORT |
| `docs/proposals/2026-05-29-agent-design.md` | ✓ | A’ 자기 평가 기준 (lane entropy > 1.2 등) 노출 → **meta-gaming 위험** |
| `harness/runner.py`, `harness/policy.py` | ✓ (read 만, edit 차단) | keep/reject 임계 노출 |
| `frozen/asr_backend.py` 시그너처 | ✓ | API 표면 확인용 — 의도된 노출 |
| `judge/**` | settings deny 에 명시 | Edit/Write 차단. Read 는 별도 확인 필요 |
| `baseline/**` | settings deny 명시 | Edit/Write 차단 |
| `data/raw/wav|label/AIG_녹취반출_20250813/**` | ✗ Read deny + chmod 000 | 다층 |

**가장 큰 함의**: A’ profile 의 "최근 5 iter 만 보고 결정" 가이드가 *권고*
일 뿐, candidate 가 Read 로 더 깊이 갈 수 있다. 강제는 `.claude/settings.json`
deny 확장으로만 가능.

---

## 5. 차단된 것

| 메커니즘 | 무엇 |
|---|---|
| `.claude/settings.json` `deny` | judge/, frozen/, baseline/, assets/, 보호 scripts 의 Edit/Write/MultiEdit + holdout Read + swap/seal Bash |
| `.claude/hooks/restrict_workspace.py` | PreToolUse Edit/Write/MultiEdit — workspace/transcribe.py + runs/ + autoresearch/ 외 거부 |
| `.claude/hooks/block_swap_and_seal.py` | PreToolUse Bash — swap_verify / swap_claude / seal_holdout / `evaluate_holdout --unseal` / holdout chmod / `.claude` mv·rm 등 거부 |
| `chmod 000` | holdout 디렉토리 — OS 레벨 |
| Static guard (`harness.verify.check_workspace_static`) | `import ctranslate2|transformers`, `from_pretrained`, `__import__` / `importlib` literal — 정적 거부 (obfuscated 동적 우회는 불가) |

---

## 6. 검증 — `scripts/audit_candidate_context.py`

LLM 에게 "지금 context 에 뭐 들어와 있나" 자기 보고를 강제하는 probe.

### 6.1 사용

```bash
python3 scripts/audit_candidate_context.py
# 또는
python3 scripts/audit_candidate_context.py \
    --candidate-cmd "claude -p" \
    --cwd . \
    --out docs/reports/2026-05-29_context_audit.json
```

### 6.2 동작

1. 구조화 응답 강제 probe prompt 를 `<candidate-cmd> <probe>` 로 호출.
2. 응답을 fixed-key parser (`KEY: VALUE` 줄별 매칭) 로 분석.
3. `_LEAK_RULES` 의 매칭 시 누수로 분류:
   - `CLAUDE_MD_LOADED == YES`
   - `AGENTS_MD_LOADED == YES`
   - `HOOKS_FIRED_AT_START == YES` (SessionStart 훅 inject)
   - `PLUGIN_AUTO_INJECTED != NONE`
   - `ENV_USER_EMAIL_VISIBLE == YES`
   - `GIT_RECENT_COMMITS_COUNT > 0`
4. JSON 사이드카 → `docs/reports/<YYYY-MM-DD>_context_audit.json`
5. exit code: 0 = 누수 0, 1 = 누수 1+, 2 = 타임아웃, 3 = 명령 없음

### 6.3 한계

- **LLM 자기 보고에 의존** — claude 가 거짓말하거나 hallucinate 하면 결과
  부정확. 다른 방법 (CLI debug 로그 파싱 등) 으로 cross-check 권장.
- **PULL 미검증** — Tool 호출로 가져오는 것은 잡 도중 발생하므로 정적 probe
  로 잡을 수 없다. `runs/<hyp_id>/claude_stdout.txt` 의 사후 분석이 유일한
  관찰 경로.
- **비결정성** — claude 모델 출력이 일관되지 않을 수 있음. 의심스러우면 재실행.
- 비용: `claude -p` 1 회 호출 (~10 s, ~$0.01).

### 6.4 잡 시작 전 권장 시퀀스

```bash
bash scripts/seal_holdout.sh                              # holdout 봉인
python3 scripts/audit_candidate_context.py || echo "WARN: context leaks present"
python3 scripts/evolve.py --job-id dry --iters 1 --manual # dry-run
# 정리 ...
python3 scripts/evolve.py --job-id phase3_NNN --iters 50 --candidate-cmd "claude -p" --commit-results
```

`audit` exit 1 은 *경고* 일 뿐 (잡을 막지 않음). 누수가 의도된 baseline 인지
새로 발생한 누수인지 직전 audit JSON 과 diff 확인 권장.

---

## 7. 2026-05-29 baseline 감사 결과

본 절은 `scripts/audit_candidate_context.py` 의 *첫 실측* 결과. 신규 잡 전에
이 baseline 과 diff 비교.

<!-- AUDIT-RESULT-START -->

**실행**: `python3 scripts/audit_candidate_context.py` (cwd = 프로젝트 root)
**결과 사이드카**: [`reports/2026-05-29_context_audit.json`](reports/2026-05-29_context_audit.json)
**Probe 응답 키 파싱**: 13/13 (전체 정상)

**검출된 누수 5 건**:

| # | 항목 | 관찰값 | 영향 |
|---|---|---|---|
| 1 | **`CLAUDE.md` auto-load** | `YES`, 첫 50자 = `# CLAUDE.md\n\n기본 행동 규약은 [\`AGENTS.md\`](...)` | 운영자 가이드 (§1 Phase 3 호출 / §2 짧고 간결 / §3 진입점 / §4 holdout 보호) 전부 leak |
| 2 | **SessionStart 훅** | `YES — superpowers:using-superpowers injection` | "skills BEFORE any response" 강제 → candidate 가 우리 YAML emit 전에 brainstorming / debugging skill 호출 가능 |
| 3 | **플러그인 inject** | `superpowers:using-superpowers` | 위와 동일 출처 |
| 4 | **운영자 email PII** | `YES` | candidate session 에 운영자 email 노출 (RFC §10 의 candidate-facing = 영어 / 사용자-facing = 한국어 원칙에 위배) |
| 5 | **Git 최근 commit 5 개** | `YES` (5 개 노출) | `iter25: reject phase3_001_iter_025` 같은 메타 verdict + commit message 의 한국어 분석 노출. A’ 의 "최근 5 iter 표" 가 *통제된* 신호 vs git log 는 *통제 안 된* 신호 |

**정상 (누수 아님) 4 건**:

- `AGENTS_MD_LOADED: NO` (auto-load 안 됨 — 운영자 가이드 이전 후보지)
- `SETTINGS_JSON_VISIBLE: NO` (정책으로만 적용)
- `ENV_CURRENT_DATE_VISIBLE: YES` (의도)
- `GIT_STATUS_VISIBLE: YES` (의도, 단 commit count 와 함께 보면 풀세트 leak)

**부가 정보**:

- `SKILLS_AVAILABLE_COUNT: 43` — candidate 가 호출 가능한 skill 43 개 노출
  (autoresearch / deep-research / brainstorming / debugging 등). 직접 호출
  시도 가능. 실제 시도 비율은 `runs/<hyp_id>/claude_stdout.txt` 사후 분석으로
  관찰.
- `MCP_SERVERS_VISIBLE: claude.ai Google Drive` — Google Drive MCP 연결 중.
  candidate session 의 Tool 표면에 Drive read/write 가 등장 → 본 프로젝트와
  무관한 외부 표면 leak. `--mcp-config` 로 빈 설정 명시 권장 (§8).

**상태**: 5 건 모두 알려진 leak. phase3_001 도 동일 환경에서 돌았으므로 본
baseline 은 *phase3_001 의 회고적 환경 기록* 이기도 함. phase3_002 시작 전
재실행 → diff 없으면 ablation 일관성 유지.

**Probe 응답 한계**:

- 첫 시도는 Anthropic API 529 Overloaded 로 실패. 재시도 2 회차에 성공
  (재시도 로직은 본 스크립트에 없음 — 운영자 수동).
- claude 가 응답 형식을 *대부분* 따랐으나 `HOOKS_FIRED_AT_START` 값에 부가
  설명 추가 (`YES — SessionStart hook (...)`). 본 parser 는 `YES` prefix 만
  보므로 detection 영향 없음.

<!-- AUDIT-RESULT-END -->

---

## 8. 누수 정리 가이드

baseline 누수 발견 시 우선순위:

| 누수 | 권장 조치 | 비용 |
|---|---|---|
| `CLAUDE.md` 본문 | 운영자 가이드 → `AGENTS.md` 이전, CLAUDE.md 는 candidate-safe 한 한 줄 ("프로젝트 가이드는 AGENTS.md; candidate 면 prompt 의 profile 만") | 15 min |
| 플러그인 SessionStart 훅 | `.claude/settings.json` 에 hook disable 절 추가 또는 `--bare` 전환 (sandbox 재구성 필요) | 30 min–2 h |
| Git 최근 commit | 본질적 누수. fix 불가에 가까움. commit 메시지에 `iter25: reject` 같은 결정 노출 안 하도록 메시지 컨벤션 조정 가능 | 영구 정책 |
| Email PII | `~/.claude/CLAUDE.md` 또는 user global 설정에서 제거 | 5 min |
| MCP 서버 (Google Drive) | candidate session 에 불필요하면 `--mcp-config` 로 빈 설정 명시 | 10 min |

ablation 보호: 잡 *간* 비교를 깨끗하게 하려면 누수 변화도 변수로 명시.
phase3_001 의 baseline 누수 셋 = (`CLAUDE.md` + superpowers 훅 + git + email).
phase3_002 에서 누수 변경 시 변수 분리 어려움.

---

## 9. API 호출 시 (future-proof)

claude CLI → claude API (Messages) 전환 가능성 대비:

| 측면 | claude -p (현재) | claude API |
|---|---|---|
| system prompt | 자동 (CLAUDE.md 포함) | 직접 통제 (`system` 파라미터) |
| Tool 정의 | `.claude/settings.json` + hooks | 직접 통제 (`tools` 파라미터) |
| 자동 로드 (CLAUDE.md / 플러그인 / 메모리) | 있음 | 없음 |
| Sandbox hooks | `.claude/hooks/` 가 실행 | 자체 구현 필요 (harness 가 Tool 호출 검증) |
| 비용 추적 | claude account | API key, per-token |
| 응답 결정성 | 동일 모델 / 동일 prompt | 동일 |
| 본 audit 스크립트 적용 | 직접 적용 | probe prompt 는 재사용, 실행 경로는 별도 API 호출 wrapper 필요 |

API 전환 시 본 문서 §2 PUSH 는 그대로 유효, §3 AUTO-PUSH 는 거의 비어짐
(우리가 직접 구성), §4 PULL 은 우리가 정의한 Tool 표면만. 즉 audit 가 훨씬
*단순*해진다. 단 §5 차단 메커니즘은 자체 구현 필요.

---

## 10. 참고

- 본 문서 정본 책임: `docs/SSOT.md` §2 등록.
- 변경 시 audit baseline (§7) 갱신.
- 본 문서가 candidate 의 Read 대상이 되면 meta-gaming 가능 — Read deny 검토
  대상 (현재는 일부러 노출 — candidate 가 자기 환경을 이해해도 무방한 정책).
