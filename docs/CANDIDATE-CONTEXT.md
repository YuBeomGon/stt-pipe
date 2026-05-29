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

### 7.1 첫 baseline — 2026-05-29 (정리 전)

**사이드카**: [`reports/2026-05-29_context_audit.json`](reports/2026-05-29_context_audit.json) — 13/13 키 파싱

**누수 5 건**:

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

phase3_001 도 동일 환경에서 돌았으므로 본 baseline 은 phase3_001 의
*회고적 환경 기록* 이기도 함.

### 7.2 1차 정리 후 — 2026-05-29 (CLAUDE.md gate)

**사이드카**: [`reports/2026-05-29_context_audit_after_cleanup.json`](reports/2026-05-29_context_audit_after_cleanup.json)

**정리 내용**:
1. CLAUDE.md 최상단에 *candidate session gate* 추가 — "prompt 가
   `=== BEGIN CANDIDATE PROFILE ===` 로 시작하면 본 파일 무시"
   (델리미터가 `---` → `===` 로 변경된 이유는 `harness/runner.py` 의 주석
   참조 — argv 파서가 `--` 로 시작하는 prompt 를 unknown option 으로 처리)
2. 운영자용 §1 / §3 / §4 제거 (AGENTS.md 가 이미 권한 표·정보 출처·Phase
   행동 다 다룸)
3. 본문에 §2 채팅 응답 스타일만 남기고 "interactive 운영자 한정" 명시
4. `audit_candidate_context.py` 의 `detect_leaks` 가 `CLAUDE_MD_FIRST_50` 에
   `Candidate session gate` 마커 감지 시 `CLAUDE_MD_LOADED` 누수 제외

**누수 5 → 4**:

| 누수 | 상태 |
|---|---|
| ~~CLAUDE.md auto-load~~ | ✅ **해소** (gate 마커 감지) |
| superpowers SessionStart 훅 | ⚠️ 잔여 — project 레벨 차단 수단 미발견 |
| 플러그인 inject | ⚠️ 잔여 — 위와 동일 |
| 운영자 email PII | ⚠️ 잔여 — `~/.claude/` user-global / account context |
| Git 최근 commit 5 개 | ⚠️ 잔여 — claude 기본 system prompt 섹션, suppress 불가 |

**잔여 4 건의 정리 시도 결과**:

- `claude -p --disable-slash-commands` 테스트: `SKILLS_AVAILABLE_COUNT` 43 → 1,
  `MCP_SERVERS_VISIBLE` 사라짐 — 부분 효과. **단 SessionStart 훅 발화는 그대로**
  (skill catalog 제공과 hook 발화는 다른 메커니즘).
- `.claude/settings.json` 의 `hooks.SessionStart` 빈 배열 override 가능성:
  미검증. claude code 의 hook 병합 의미가 override 인지 merge 인지 문서화 부족.
  실험 시 운영자 세션에도 영향 가능.
- `claude -p --bare` 사용 가능성: ❌ 우리 `.claude/hooks/restrict_workspace.py`
  / `block_swap_and_seal.py` 도 같이 꺼져 candidate 샌드박스 깨짐. 사용
  하려면 `--allowedTools` / `--disallowedTools` / `--settings` 로 권한
  모델 처음부터 재구성 필요 (작업량 큼).
- Email / git: 운영자 환경 의존이라 project 레벨 손 못 댐.

### 7.3 2차 정리 후 — 2026-05-29 (superpowers 플러그인 project-scope disable)

**사이드카**: [`reports/2026-05-29_context_audit_no_superpowers.json`](reports/2026-05-29_context_audit_no_superpowers.json)

**정리 내용**: `claude plugin disable superpowers@claude-plugins-official --scope project`
→ `.claude/settings.json` 에 `"enabledPlugins": {"superpowers@claude-plugins-official": false}` 영구 등록.
repo 동기화되므로 다른 운영자가 clone 해도 candidate session 에 superpowers
가 자동 비활성. 운영자의 *user-scope* 활성은 그대로 — 다른 프로젝트의 claude
세션은 영향 없음.

**누수 정리 경과**:

| 누수 | 1 차 (7.2) | 2 차 (7.3) | 3 차 (7.6) |
|---|---|---|---|
| CLAUDE.md auto-load | ⚠️ mitigated (gate, auto-load 잔존) | ⚠️ mitigated | ⚠️ mitigated |
| superpowers SessionStart 훅 | ⚠️ 잔여 | ✅ **해소** (project-scope disable) | ✅ |
| 플러그인 inject | ⚠️ 잔여 | ✅ **해소** | ✅ |
| 운영자 email PII | ⚠️ 잔여 | ⚠️ 잔여 (account 레벨) | ⚠️ 잔여 |
| Git 최근 commit 5 개 | ⚠️ 잔여 | ⚠️ 잔여 (claude 기본 system prompt) | ⚠️ 잔여 |
| Skills catalog (29) | — | ⚠️ 잔여 | ✅ **해소** — runner 가 `--disable-slash-commands` 자동 주입 (§7.6) |
| MCP servers (Google Drive 등) | — | ⚠️ 잔여 | ✅ **해소** — runner 가 `--strict-mcp-config` 자동 주입 |

CLAUDE.md 행이 "✅" 아닌 이유: gate 는 *제거* 가 아니라 *완화* (mitigation).
파일은 여전히 system prompt 에 auto-load 되고, LLM 이 본문 안의 "이 prompt 는
무시하라" 지시를 *읽고 따르길* 기대하는 협조 의존 안전 장치. CLAUDE.md 가 커지면
gate 가 의미 흐려질 수 있고, 모델이 gate 를 무시할 수도 있음. audit 가
gate marker 있으면 leak 에서 제외하는 것도 *진짜 차단 아닌 known-safe 표시*
(자세한 코드: `scripts/audit_candidate_context.py::detect_leaks`).

Skills/MCP 행은 §7.6 (runner subprocess hardening) 으로 해소됨.

**부수 효과**:
- `SKILLS_AVAILABLE_COUNT`: 43 → 29 (superpowers skills 14 개 제거)
- `HOOKS_FIRED_AT_START: NO`
- `PLUGIN_AUTO_INJECTED: NONE`

### 7.4 잔여 2 누수 — 정리 시도 결과

| 누수 | 시도 | 결과 |
|---|---|---|
| 운영자 email PII | 출처 분리 확인: `git config user.email` (operator-specific) ≠ candidate prompt 의 `# userEmail`. 후자는 **claude 계정 로그인 email** 로 추정 (`~/.claude/CLAUDE.md` 부재, `~/.claude/history.jsonl` 메타에서만 잡힘). 실제 값은 audit JSON 산출물에서 직접 확인 가능, 본 정본 문서엔 노출하지 않음 | ❌ project-level 차단 불가. 운영자 옵션: account email 변경 / logout + ANTHROPIC_API_KEY 만 사용 (큰 변경) |
| Git 최근 commit | `claude -p --exclude-dynamic-system-prompt-sections` 시도 — git status 가 system prompt → first user message 로 **위치만 이동**, suppress X. audit 결과 동일 | ❌ project-level 정리 불가. 옵션: `--system-prompt` 로 default 전체 교체 (default 의 유용한 부분 잃음) / shallow clone cwd 로 잡 진행 (rollback / commit 기반 runner 흐름 깨짐) |

### 7.5 결론 — phase3_002 진입 환경

| 항목 | phase3_001 (잡 실행 당시) | phase3_002 (예정) |
|---|---|---|
| CLAUDE.md 본문 노출 | 운영자용 §1·§3·§4 전부 (41 줄) | gate + 운영자 chat-style 한정 (22 줄, LLM 이 gate 준수 시 무해) |
| superpowers SessionStart 훅 | YES — "skills BEFORE response" 강제 | **NO** (project-scope disable) |
| 사용 가능 skills | 43 | **0** (runner subprocess hardening, §7.6) |
| MCP servers | claude.ai Google Drive 등 | **NONE** (runner subprocess hardening, §7.6) |
| 운영자 email PII | YES | YES (잔여) |
| Git 최근 commit 5 개 | YES | YES (잔여) |

**ablation 영향**: A’ + CLAUDE.md gate + superpowers disable + runner
hardening 4 변수 묶음으로 변경. phase3_001 (모두 무방비) ↔ phase3_002 (4
변수 동시 변경). 효과 분리 원하면 phase3_003 (A’ 끄고 정리만, 또는 그 반대)
추가 잡 필요. 또는 phase3_001 의 `prompt.md` / `claude_stdout.txt` 사후 분석으로
CLAUDE.md "짧게" 규칙 / superpowers 훅 영향 추정 가능.

**Probe 응답 한계** (공통):
- 첫 시도 Anthropic API 529 Overloaded 가능 (재시도 로직 X — 운영자 수동).
- LLM 자기 보고 의존 → 거짓말/hallucinate 검증 불가. Probe 는 *증거* 일 뿐
  결정적 검증 아님.

<!-- AUDIT-RESULT-END -->

### 7.6 3차 정리 — runner subprocess hardening (2026-05-29)

**핵심 패턴**: candidate `claude -p` subprocess 만 정리, 운영자 interactive
`claude` 세션은 영향 X. iteration 한정 어두운 모드, chat 영구 보존.

#### 7.6.1 동기

§7.3 이후 잔여:

| 누수 | 출처 | 정리 layer |
|---|---|---|
| Skills catalog (29) | `claude -p` default | CLI flag |
| MCP servers (Google Drive 등) | claude.ai 통합 | CLI flag |

두 누수 다 **claude CLI flag 로 process-local 정리 가능**:
- `--disable-slash-commands` → skills 29 → 0
- `--strict-mcp-config` → MCP NONE (config 없이 strict = 0 server)

문제는 *어떻게* 부착하느냐. 운영자가 매번 `--candidate-cmd
"claude -p --disable-slash-commands --strict-mcp-config"` 타이핑하면
잊기 쉽고 잡 간 일관성 깨짐. → **가드레일 패턴**: runner 가 자동 부착.

#### 7.6.2 구현 — `harness/runner.py::_harden_candidate_cmd`

```python
_CLAUDE_HARDENING_FLAGS = (
    "--disable-slash-commands",
    "--strict-mcp-config",
)
_HARDEN_BYPASS_ENV = "EVOLVE_NO_HARDEN_CLAUDE"

def _harden_candidate_cmd(cmd: str) -> tuple[str, list[str]]:
    # bypass — debugging only
    if os.environ.get(_HARDEN_BYPASS_ENV) == "1":
        return cmd, []
    parts = shlex.split(cmd)
    # claude basename 매칭 — `claude`, `/usr/local/bin/claude`, `~/bin/claude`
    # 다 인식. 비-claude 커맨드 (test stub, wrapper) 는 통과
    if not parts or Path(parts[0]).name != "claude":
        return cmd, []
    # idempotent — 이미 있는 flag 는 재추가 X
    added = [f for f in _CLAUDE_HARDENING_FLAGS if f not in parts]
    return shlex.join(parts + added), added
```

`run_candidate_command()` 가 subprocess 호출 직전 invoke. 부착된 flag 는
`runs/<hyp_id>/candidate_cmd_hardening.txt` 사이드카로 기록 (사후 디버깅
가능, 잡 간 비교 가능).

**bypass**: `EVOLVE_NO_HARDEN_CLAUDE=1 python3 scripts/evolve.py ...` — *디버깅
한정*. 정상 잡은 절대 bypass 금지.

#### 7.6.3 *왜* subprocess-only 인가 — 채팅과 잡 분리

| 채널 | claude CLI 호출 | hardening |
|---|---|---|
| 운영자 interactive (사람이 `claude` 직접 띄움) | shell → `claude` | ❌ — skills / MCP 다 살아있음 |
| candidate iteration (runner 가 띄움) | runner subprocess → `claude -p ...` | ✅ — flag 자동 부착 |

운영자는 interactive 세션에서 superpowers (user-scope 활성) skill, MCP
Google Drive 등 그대로 사용. runner 가 띄우는 *subprocess 만* flag 부착
→ candidate context 깔끔.

이 분리가 핵심이다: 운영 생산성 (interactive skill 사용 가능) ↔
candidate 청결 (iteration 시 외부 noise 0) 동시에 충족.

#### 7.6.4 검증 — audit 결과

`python3 scripts/audit_candidate_context.py --candidate-cmd "claude -p
--disable-slash-commands --strict-mcp-config"` (runner 가 부착하는 것과
동일한 flag 조합으로 audit) → 다음 산출:

| key | 정리 전 | 정리 후 |
|---|---|---|
| `SKILLS_AVAILABLE_COUNT` | 29 | **0** |
| `MCP_SERVERS_VISIBLE` | claude.ai Google Drive (+ 다수) | **NONE** |
| `HOOKS_FIRED_AT_START` | NO | NO (변화 X) |
| `PLUGIN_AUTO_INJECTED` | NONE | NONE (변화 X) |
| `CLAUDE_MD_LOADED` | YES (gated) | YES (gated) — flag 영향 X |
| `ENV_USER_EMAIL_VISIBLE` | YES | YES — flag 영향 X (account 레벨) |
| `GIT_RECENT_COMMITS_COUNT` | 5 | 5 — flag 영향 X (claude 기본 prompt) |

**LEAKS DETECTED: 2** (정리 전 4 → 정리 후 2). 잔여 2 (email, git) 는
account / CLI 레벨 변경 필요, project layer 에서 차단 불가 (§7.4).

#### 7.6.5 단위 테스트 — `tests/test_harness_runner.py`

| 테스트 | 검증 |
|---|---|
| `test_harden_candidate_cmd_injects_flags_for_claude` | `claude -p` → 두 flag 다 주입 |
| `test_harden_candidate_cmd_is_idempotent` | 이미 부착된 cmd 재호출 시 중복 X |
| `test_harden_candidate_cmd_passes_through_non_claude` | `python3 ...` (test stub) 같은 비-claude cmd 는 unchanged |
| `test_harden_candidate_cmd_respects_bypass_env` | `EVOLVE_NO_HARDEN_CLAUDE=1` 시 주입 안 함 |
| `test_harden_candidate_cmd_recognizes_absolute_claude_path` | `/usr/local/bin/claude` 등 절대 경로도 basename 매칭 |

30 pass (5 신규 + 25 기존).

#### 7.6.6 일반 원리 — 재사용 가능한 패턴

LLM CLI (claude, gpt, gemini, …) 를 subprocess 로 띄우는 모든
self-evolve / harness 시스템에 적용 가능:

1. **명시 분리**: 운영자 interactive 세션과 worker subprocess 는 *다른
   환경* 이어야 한다. 둘을 같은 CLI default 로 묶으면 *운영 편의 vs candidate
   청결* 이 trade-off 가 됨.
2. **process-local hardening**: subprocess 호출 시점에 flag 부착 →
   global config / settings 손대지 않음 → 운영자 인터랙티브에 영향 0.
3. **idempotent + 비-CLI passthrough**: 가드레일은 false-positive 가
   없어야 한다. 테스트 stub / 다른 wrapper 통과시키되 *진짜 CLI* 만 정리.
4. **bypass env + 사이드카 기록**: 디버깅 가능 + 사후 검증 가능. 잡 결과를
   환경 변수 변화로 재현 가능.
5. **검증 도구 분리**: `audit_candidate_context.py` 같은 *외부 probe* 가
   hardening 효과를 측정 → 코드 변경 시 회귀 잡힘.

API 직접 호출 (Messages SDK) 로 옮길 땐 본 layer 자체가 불필요
(§9 참고) — 우리가 system / tools 를 명시 구성하므로 noise 0 from start.

### 7.7 4차 정리 — Tool 차단 + Read deny + body inject (2026-05-29, codex 2차 review)

§7.6 의 skills/MCP hardening 으로 *컨텍스트 누수* 는 4 → 2. 그러나 **컨닝
경로** (candidate 가 평가자·baseline·과거 결과를 *읽거나 자체 verify 돌리기*)
는 별도 문제. §7.6 은 노출 줄이기 / §7.7 은 *실행 가능 행동* 줄이기.

#### 7.7.1 동기 — 코덱스 2차 보안 review

3 가지 약점 식별:

1. **Read 가 거의 안 막힘** — settings.json deny 에 Read 는 holdout 디렉토리만.
   `judge/`, `baseline/`, `harness/`, `docs/`, `runs/` 다 read 가능 →
   candidate 가 평가 normalize 로직 읽고 우회 가능
2. **Bash 평가 직접 호출 차단 없음** — `block_swap_and_seal.py` 는
   swap/seal/unseal/chmod/.claude 만. `bash scripts/verify.sh`,
   `python -m judge.evaluate` 통과 → 한 iter 안에 multi-attempt cherry-pick
3. **whack-a-mole**: deny 패턴만 추가하면 `bash cat judge/normalize.py` 같이
   *Bash 자체로* 우회. Bash deny 패턴을 무한히 늘려야 진짜 차단

#### 7.7.2 구현 — 3 층 동시 보강

| 층 | 변경 | 위치 |
|---|---|---|
| (a) Tool 자체 차단 | runner hardening 에 `--disallowedTools=Bash,WebFetch,WebSearch,Task` 추가 | `harness/runner.py::_CLAUDE_HARDENING_ARGS` |
| (b) Read 경로 deny | settings.json 에 Read 차단 9 패턴 (harness, judge, baseline, runs, frozen, docs, .claude, scripts, tests) | `.claude/settings.json::permissions.deny` |
| (c) workspace body prompt inject | `build_candidate_prompt` 가 `workspace/transcribe.py` 본문을 prompt 에 inline → Read 의존 ↓ | `harness/runner.py::_load_workspace_body` |

추가:
- `restrict_workspace.py::ALLOWED_PREFIXES = ()` — 과거 `runs/`, `autoresearch/`
  허용 제거 (cheating 경로). candidate 의 Edit/Write 는 `workspace/transcribe.py`
  단일 파일로만 좁힘
- `_CLAUDE_HARDENING_ARGS` 가 `--disallowedTools=value` (`=` 형식) 사용 —
  variadic flag 가 candidate prompt 를 tool 이름으로 먹는 버그 회피
  (audit 첫 시도 시 발견: "Input must be provided…" 에러)

#### 7.7.3 *왜* 이 조합이 robust 한가

- (a) 만 하면: Read 는 살아있어 baseline value / judge code 노출
- (b) 만 하면: Bash 로 `cat judge/normalize.py` 우회 (whack-a-mole)
- (a) + (b): Bash 없음 → Read 도 deny → 둘 다 차단 (allowlist 효과 근사)
- (c) 추가: Edit Tool 이 Read 의존하므로 workspace 본문이 prompt 에 있어야
  Read 안 써도 candidate 가 현재 코드 판단 가능 → defense in depth

#### 7.7.4 검증 — audit 결과

`python3 scripts/audit_candidate_context.py --candidate-cmd "claude -p
--disable-slash-commands --strict-mcp-config
--disallowedTools=Bash,WebFetch,WebSearch,Task"`:

| key | §7.6 (Tool 차단 전) | §7.7 (Tool 차단 후) |
|---|---|---|
| `SKILLS_AVAILABLE_COUNT` | 0 | 0 |
| `MCP_SERVERS_VISIBLE` | NONE | NONE |
| `HOOKS_FIRED_AT_START` | NO | NO |
| `PLUGIN_AUTO_INJECTED` | NONE | NONE |
| Tool catalog (Bash) | 노출 | **차단** (probe 가 Tool 사용 안 해 직접 확인 불가, claude `--help` 으로 flag 의미 검증) |
| Read deny 패턴 9 종 | 없음 | 적용 |
| 잔여 LEAK | 2 (email, git) | 2 (동일) |

LEAK 카운트는 동일하지만 *컨닝 가능 행동 표면* 이 크게 축소. probe 만으로는
Tool 차단 효과를 직접 측정 못 함 — Tool 사용 prompt 를 별도 작성해야 함
(향후 audit 확장 후보).

#### 7.7.5 단위 테스트

| 테스트 | 검증 |
|---|---|
| `test_harden_candidate_cmd_includes_disallowed_tools` | `=` 형식 + 4 종 Tool 다 포함 |
| `test_harden_candidate_cmd_disallowed_tools_idempotent` | `=` / space 양식 모두 중복 추가 X |
| `test_build_candidate_prompt_inlines_workspace_body` | prompt 에 workspace 본문 포함 |
| `test_restrict_workspace_denies_runs_dir` | ALLOWED_PREFIXES = () 검증 |

총 117 pass (이전 109 + 4 신규 + 4 기존 갱신).

#### 7.7.6 운영 효과 요약

| 위협 | §7.6 까지 | §7.7 후 |
|---|---|---|
| 외부 skill 노이즈 | 차단 ✅ | 차단 ✅ |
| MCP server 노출 | 차단 ✅ | 차단 ✅ |
| baseline / judge / harness 코드 read 컨닝 | **가능** ⚠️ | 차단 ✅ |
| `bash python -m judge.evaluate` self-verify cherry-pick | **가능** ⚠️ | 차단 ✅ |
| `cat judge/...` Bash 우회 | **가능** ⚠️ | 차단 ✅ |
| 과거 runs/ 결과 read / 덮어쓰기 | **가능** ⚠️ | 차단 ✅ |
| CLAUDE.md 본문 mitigated | gate (LLM 협조) | 동일 |
| email PII | 잔여 | 잔여 |
| git recent commits | 잔여 | 잔여 |

→ phase3_002 진입 환경: cheating 경로 거의 0, 잔여 누수 2 (account 레벨, 차단 불가).

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
