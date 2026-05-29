# Proposal — A’ Candidate Agent Runtime 설계

- 상태: **Accepted** (2026-05-29 — §10 결정 사항 확정)
- 작성: 2026-05-29
- 영향: Phase 3 harness, candidate 호출 경로, analyze_run D 축
- 후속 정본: 채택 시 [`PHASE3-PLAN.md`](../PHASE3-PLAN.md) §2/§4/§5/§7 갱신,
  [`SELF-EVOLVE-HARNESS-SPEC.md`](../SELF-EVOLVE-HARNESS-SPEC.md) §13 일반화는
  phase3_002 실측 후 (B 단계) 별도 진행

---

## 1. 배경

### 1.1 관찰 — 단조성

| 잡 | 환경 | iter | best | 채택 lane 분포 |
|---|---|---|---|---|
| phase3_001 (현 머신) | 자체 harness v1 | 25 | 0.2545 (iter 9) | chunking 83%, prompt 17%, decode 0%, post 0% |
| origin/phase3 (집 머신) | autoresearch | 50 | 0.1548 | chunking 위주, 후반 VAD/decode 일부 |

두 잡 모두 *같은 방향* 으로 쏠림. analyze_run.py D 축 산출 결과 phase3_001
은 **chunking 83% — 다양성 부족 (80% 임계 초과)** 경고 발동.

### 1.2 원인 분석 — harness 가 *반만 됨*

`harness/` (controller) 는 구축됐지만 **candidate side 인프라가 없음**:

| 영역 | 상태 |
|---|---|
| sandbox (deny + hooks) | ✓ `.claude/settings.json` + `.claude/hooks/` |
| Loop / guards / policy / state / history | ✓ `harness/runner.py` 외 5 모듈 |
| **candidate role / strategy / self-check** | ✗ 없음 |
| **candidate context management** | ✗ 없음 (HISTORY tail prose 그대로 주입) |
| **diversity enforcement** | ✗ 없음 |

현 candidate 호출 (`harness/runner.py::build_candidate_prompt`) 은 매 iter
LLM 을 **cold-start** 로 호출 — 시스템 프롬프트 / 역할 / 5 가지 접근법 안내 /
재시도 정책 / 자기검증 0. 매번 "처음 본 문제" 모드로 들어가서 가장 흔한 lever
(beam, chunking) 만 반복하는 것이 자연스러움.

### 1.3 진단

"한 방향" 의 원인은 후술할 세 층 모두에 분산되어 있을 가능성 큼:

| 층 | 가설 | 비중 (추정) |
|---|---|---|
| **L1. candidate 사고 부재** | 역할·접근법 안내 없음 → 평균적/안전한 lever 만 선택 | 大 |
| **L2. 컨텍스트 오염** | HISTORY tail prose 가 직전 lever 의 사고 anchoring | 中 |
| **L3. 다양성 강제 없음** | 같은 fingerprint 반복해도 harness 가 막지 않음 | 中~小 |

본 proposal 은 **L1 만 단독 해결 (A’) → 측정 → L2/L3 필요시 후속 (C-lite)**
ablation 순서로 간다.

---

## 2. 결정 — A’

### 2.1 무엇

5 가지 변경:

| # | 변경 | 위치 |
|---|---|---|
| 1 | **candidate profile 신설** — 역할 / 5 lane 정의 / ASR keyword set / YAML response format / 자기검증 체크리스트 | `harness/prompts/candidate.md` (신규) |
| 2 | **profile inline 주입** — runner 가 profile 읽어 prompt body 에 붙임 (Claude CLI flag 비종속) | `harness/runner.py::build_candidate_prompt` |
| 3 | **YAML response 파싱** — `yaml.safe_load` 로 lane / fingerprint / why_different 추출 | `harness/runner.py::run_candidate_command` |
| 4 | **format 누락 reject before verify** — `lane`/`diff_fingerprint`/`why_different_from_last_5` 키 누락 시 verify 호출 전 reject. fingerprint 중복 자체는 reject 안 함 (기록만) | `harness/runner.py` |
| 5 | **권고 lane round-robin** — `iter % 5` 로 5 lane 중 하나 제안. "may override with justification" — 강제 아님 | `harness/runner.py` |

추가로:

- **abort 가드**: 첫 5 iter 중 4 회 format reject 시 잡 중단 → profile 재작성
  신호. 잘못 쓴 profile 로 잡 전체 낭비 방지.
- **runtime data 주입**: 최근 5 iter 의 `(lane, fingerprint)` 를 prompt 에
  표시 — candidate 가 *반복 회피* 를 자기 책임으로 처리하게.

### 2.2 명시적으로 *안* 하는 것

| 항목 | 이유 |
|---|---|
| fingerprint 중복 reject | A’ ablation 보호. L2/L3 효과가 A’ 에 섞이면 단일 변수 분리 불가. fingerprint 는 기록만 → 다음 잡 분석에서 *얼마나 반복했나* 데이터로 활용 |
| HISTORY 압축 (직전 합의 M1) | C-lite 로 미룸. A’ 효과 측정 후 단조성 잔존 시 도입 |
| cold-restart wildcard (M3) | C-lite 이후 |
| tournament / population | Stage 2/3 — Roadmap 후반 |
| `.claude/agents/candidate.md` autodiscovery | Claude CLI flag 존재 확인됐으나 file autodiscovery 보장 X. prompt.md 사이드카에 실 내용 안 남아 디버깅 / 이식성 ↓. inline 정답 |
| AST 기반 fingerprint | diff 만으로 안정적 AST 어려움. heuristic keyword set 부터, AST 는 차후 보조 |
| lane 강제 rotation | candidate 사고력 측정 불가, forcing 효과만 봄. 권고 형태로 시작 |

---

## 3. 핵심 인터페이스

### 3.1 Candidate response 형식 (강제)

candidate 의 응답 마지막에 다음 fenced block 이 반드시 포함되어야 함:

````
```yaml
lane: decoding
diff_fingerprint: [beam, length_penalty, patience]
why_different_from_last_5: iter 9 이후 patience 미시도, 같은 lane 이나 새 hyperparam
```
````

- `lane` ∈ {`segmentation`, `decoding`, `prompt`, `postprocess`, `telemetry`}
- `diff_fingerprint`: keyword token list (profile 안 allowlist 기반, 자유 추가
  허용)
- `why_different_from_last_5`: 한 문장. lane 이 동일하면 "왜 새로운 시도인지",
  override 시 "왜 권고 lane 안 따랐는지"

### 3.2 5 lane 정의 (도메인 종속, ASR)

profile 안에 정본:

| lane | 무엇 | 예시 keyword |
|---|---|---|
| segmentation | 오디오 분할·VAD·chunk 경계 | chunk, overlap, vad, silence, boundary |
| decoding | beam / temperature / penalty / fallback | beam, temperature, length_penalty, patience, fallback |
| prompt | initial prompt / language / suppress token | prompt, language, suppress, hotword |
| postprocess | dedup / merge / regex / number 정규화 | dedup, merge, regex, normalize |
| telemetry | diagnostic / metric 노출 (개선 X, 분석 신호 추가) | logprob, attention, debug |

다른 도메인 이식 시 keyword set 갱신 — `SELF-EVOLVE-HARNESS-SPEC` 에는 "도메인별
keyword set 필요" 원리만, 구체 set 은 프로젝트 spec.

### 3.3 Runner 흐름 (변경 후)

```
build_candidate_prompt():
  prompt = (
    profile_body  # harness/prompts/candidate.md
    + goal/constraints (기존)
    + state (best_cer, iter, ...)
    + recent_5_iters_table  # (lane, fingerprint, cer, status)
    + suggested_lane = lane[iter % 5]
    + history_tail (기존, C-lite 에서 압축 예정)
    + diagnosis_summary (기존)
    + required_output_format_spec
  )

run_candidate_command():
  claude -p <prompt>
  diff = git diff workspace/transcribe.py
  meta = parse_yaml_fenced_block(stdout)
  if missing(meta, ['lane','diff_fingerprint','why_different_from_last_5']):
    return REJECT_FORMAT  # verify 호출 X
  if meta.lane not in LANES:
    return REJECT_FORMAT
  return OK, diff, meta

run_job():
  format_reject_count = 0
  for iter in 1..N:
    result = run_iteration()
    if result == REJECT_FORMAT:
      format_reject_count += 1
      if iter <= 5 and format_reject_count >= 4:
        abort('profile 재작성 필요')
```

---

## 4. 측정 — A’ 효과 판정 기준

`scripts/analyze_run.py` D 축 확장 (~30 LOC):

| 메트릭 | phase3_001 baseline | A’ 후 기대 |
|---|---|---|
| lane 분포 entropy (max 1.61 = ln 5) | 0.45 (chunking 83%) | > 1.2 |
| 채택 fingerprint Jaccard 평균 거리 | — (미측정) | > 0.5 |
| 최장 동일-fingerprint streak | 5 (iter 10~14) | ≤ 2 |
| format reject 비율 | n/a | < 0.20 (첫 잡 calibration) |

**판정 표**:

| 결과 | 해석 | 다음 |
|---|---|---|
| lane entropy > 1.2 AND streak ≤ 2 | A’ 단독으로 단조성 깨짐 | C-lite 보류, B (spec 일반화) 로 진행 |
| 1.2 > entropy > 0.8 | 부분 효과 | C-lite (fingerprint 중복 reject + HISTORY 압축) 진행 |
| entropy ≤ 0.8 | A’ 효과 미미 | C-lite 필수, profile 재검토 |
| format reject 비율 > 0.40 | profile 자체가 LLM 에 안 맞음 | profile rewrite, 잡 재실행 |

---

## 5. 대안 / 기각 사유

| 대안 | 기각 사유 |
|---|---|
| (a) A’ + C-lite 동시 도입 | ablation 불가 — 어느 효과인지 분리 못 함. SOTA 빠를 수 있으나 우리는 *harness 연구* 단계라 학습 가치 우선 |
| (b) HARNESS-SPEC 신설 먼저 | 구현 안 한 일반화는 떠다님. SELF-EVOLVE-HARNESS-SPEC §13 보강이 B 단계로 충분 |
| (c) `.claude/agents/candidate.md` + Claude CLI flag | file autodiscovery 보장 X, prompt.md 사이드카에 실 내용 안 남음, CLI 버전 의존, 이식성 ↓ |
| (d) lane 강제 rotation | candidate 사고력 측정 불가. 권고로 시작해 *얼마나 따르나* 자체를 데이터로 |
| (e) AST 기반 fingerprint | diff 만으론 안정적 AST 어려움. heuristic keyword set 으로 시작, 정확도 부족 시 보강 |
| (f) LLM 압축 (HISTORY summary) | 비결정성 추가 + 비용. C-lite 단계에도 rule-based 만 |
| (g) candidate self-critique 2 차 호출 | 비용 2x. 응답 포맷 강제로 1 차 호출 안에서 강제 |

---

## 6. 작업 계획

| # | 단계 | 산출 | 추정 LOC | Task |
|---|---|---|---|---|
| 1 | RFC 작성 | `docs/proposals/2026-05-29-agent-design.md` | — | #15 |
| 2 | candidate profile 작성 | `harness/prompts/candidate.md` | ~100 (markdown) | #16 |
| 3 | runner 수정 | `harness/runner.py` (profile inline, YAML parse, lane round-robin, abort 가드) | ~150 | #17 |
| 4 | analyze D 축 확장 | `scripts/analyze_run.py` + `docs/templates/REPORT.md` | ~50 | #18 |
| 5 | 테스트 | `tests/test_harness_runner.py`, `tests/test_analyze_smoke.py` | ~100 | #19 |
| 6 | 문서 정본 갱신 | `PHASE3-PLAN.md` §2/§4/§5/§7, `SSOT.md`, `PHASE3-STATUS.md` | ~30 (markdown) | #20 |
| 7 | 커밋 | step 별 분리: (a) RFC, (b) profile + runner, (c) analyze + 템플릿, (d) 테스트, (e) 문서 — 5 커밋 | — | — |
| 8 | phase3_002 25 iter 실행 | `python3 scripts/evolve.py --job-id phase3_002 ...` (사용자 명령) | — | — |
| 9 | REPORT 분석 → 판정 | `docs/reports/phase3_002_REPORT_*.md` 의 D 축 vs §4 판정 표 | — | — |
| 10 | 후속 결정 | C-lite 진행 / SPEC 보강 / profile 재작성 중 하나 | — | — |

**총 LOC 추정: ~430** (코드 300 + 마크다운 130). 1 ~ 1.5 일 작업.

---

## 7. 영향 받는 파일

### 신규
- `docs/proposals/2026-05-29-agent-design.md` (이 문서)
- `harness/prompts/candidate.md`
- `harness/prompts/__init__.py` (빈 파일, 패키지화 안 함 — 단순 데이터 디렉토리)

### 수정
- `harness/runner.py`
- `scripts/analyze_run.py`
- `docs/templates/REPORT.md` (D 축 새 메트릭 행)
- `docs/PHASE3-PLAN.md` (§2 코드 경계, §4 iteration flow, §5 Guard 정책, §7 기록 정책)
- `docs/SSOT.md` (§2 주제별 정본 — `harness/prompts/` 등록, §3 코드 책임 지도)
- `docs/PHASE3-STATUS.md` (§N A’ DoD 체크박스 신설)
- `tests/test_harness_runner.py`
- `tests/test_analyze_smoke.py`
- `requirements.txt` (PyYAML 추가 — `yaml.safe_load` 용. 이미 의존성에 있을 가능성 있음, 확인 필요)

### 변경 없음
- `README.md` — 사용자 명령 흐름 동일 (`scripts/evolve.py` 인자 동일)
- `AGENTS.md`, `CLAUDE.md` — 운영자용
- `STT-PIPELINE-SPEC.md`, `DESIGN.md` — 도메인 / 구조 정본
- `.claude/settings.json`, `.claude/hooks/` — candidate sandbox 변경 없음
  (profile inline 이라 candidate 가 직접 파일 read 안 함)
- `frozen/`, `judge/`, `baseline/`, `assets/` — 봉인 유지

---

## 8. 가드레일 검토

| 대상 | 변경 |
|---|---|
| 운영자 세션 | 없음 — 본 작업이 이미 가능 (지금까지 scripts/, docs/, harness/, tests/ 모두 자유 편집해온 게 증거) |
| candidate sandbox (`.claude/`) | 없음 — profile inline 주입이라 candidate 가 파일 직접 read 안 함 → allowlist 추가 / 완화 둘 다 불필요 |
| Phase 3 보호 영역 (judge/frozen/baseline/assets) | 없음 |
| holdout chmod 000 | 없음 |

---

## 9. 채택 / 거부 후 처리

### 채택 시
1. 본 proposal 상단 상태를 **Accepted (2026-MM-DD)** 로 갱신
2. `PHASE3-PLAN` §2/§4/§5/§7 에 A’ 흡수 (정본 반영)
3. `SSOT.md` 갱신
4. phase3_002 실행
5. phase3_002 결과 본 후 `SELF-EVOLVE-HARNESS-SPEC.md` §13 일반화 (B 단계)
6. 본 proposal 은 historical 로 유지 — 결정 추적용

### 거부 시
1. 상단 상태를 **Rejected (2026-MM-DD)** + 거부 사유 한 문단 추가
2. 새 proposal 작성 또는 phase3_002 를 다른 변수로 진행

---

## 10. 결정 사항 (2026-05-29)

- [x] **PyYAML 추가** — requirements.txt 에 미존재 확인됨. 본 작업에서 추가
- [x] **언어 정책: candidate-facing = 영어 / 사용자-facing = 한국어**
      → `harness/prompts/candidate.md` 본문 영어, runner 의 한국어 주석 / 사용자
      문서는 한국어 유지. 일반 원칙: LLM 이 읽는 모든 것 (prompt body, profile,
      response format spec) 은 영어
- [x] **phase3_002 = 50 iter** — 집 머신 origin/phase3 (50 iter, best 0.1548)
      와 직접 비교 가능. 잡 시간 ~4 h (phase3_001 2 h × 2)
- [x] **abort 임계 4/5 보수적** — LLM 이 거의 모든 응답에서 format 못 맞출 때만
      잡 중단. profile 의 명확성 결함 신호로만 활용

---

## 11. Addendum — RFC 외 추가 작업 (2026-05-29, phase3_002 진입 전)

본 RFC §6 작업 계획 (step 1~7) 완료 후, **§8 (phase3_002 실행) 진입 직전**
candidate 컨텍스트 자동 주입 문제가 별도 발견되어 다음을 추가로 진행했다.
본 RFC 의 결정 사항은 변경 없음 — 회고 기록 only.

### 11.1 발견

`claude -p` 가 cwd 의 `CLAUDE.md`, `.claude/settings.json` 의 enabled
플러그인, SessionStart 훅을 *자동* 주입함을 확인. phase3_001 컨텍스트에
운영자용 가이드 + superpowers 의 "skills BEFORE response" 강제 + 운영자
email + git recent commits 가 *우리가 모르게* 같이 들어가 있었음.

본 RFC §1.3 의 **L2 (컨텍스트 오염)** 가설이 *예상보다 훨씬 크고 다른
경로* 임이 드러남 — HISTORY tail 뿐 아니라 CLAUDE.md / 플러그인.

### 11.2 추가 산출 (RFC scope 외)

| 산출 | 위치 |
|---|---|
| 컨텍스트 정본 문서 (PUSH / AUTO-PUSH / PULL 분류) | [`docs/CANDIDATE-CONTEXT.md`](../CANDIDATE-CONTEXT.md) |
| 자동 감사 스크립트 (probe + 누수 규칙 기반) | [`scripts/audit_candidate_context.py`](../../scripts/audit_candidate_context.py) |
| 정기 결과 (`<YYYY-MM-DD>_context_audit.json`) | [`docs/reports/`](../reports/) |
| CLAUDE.md candidate session gate (자기 면역) | `CLAUDE.md` 상단 (22 줄로 축소) |
| superpowers 플러그인 project-scope disable | `.claude/settings.json` |

### 11.3 누수 정리 경과

5 누수 (CLAUDE.md / SessionStart 훅 / 플러그인 / email / git commits)
→ **2 누수** (email PII, git recent commits — 둘 다 claude CLI 기본 동작이라
project 레벨 정리 불가). 자세한 baseline 비교는 CANDIDATE-CONTEXT.md §7.

### 11.4 phase3_002 환경 변화

- ✅ phase3_002 는 phase3_001 대비 *추가 변수* (CLAUDE.md / 플러그인 제거) 가
  들어간 환경. 따라서 ablation 의 *해석 시* "A' 효과 = candidate profile +
  컨텍스트 정리 합산" 으로 봐야 하며, A' 단독 효과 분리는 불가능.
- 운영적으론 *더 깨끗한 환경* 이라 phase3_002 결과를 baseline 으로 잡고
  이후 RFC 는 그 위에서 ablation.

### 11.5 후속 RFC 슬롯

본 RFC 가 다루지 못한 다음 두 주제는 별도 proposal 로 분리:

- 내부 skill / agent 설계 (외부 superpowers 의존 제거 + 운영자 워크플로우
  표준화) → [`docs/proposals/2026-05-29-skills-and-prompt-eval.md`](2026-05-29-skills-and-prompt-eval.md)
- candidate profile A/B 측정 메커니즘 (prompt-eval) → 동일 proposal §3

### 11.6 추가 hot-fix — runner subprocess hardening

§11 작업 후 audit 재검토에서 잔여 누수가 *5 → 4* 임이 드러남
(skills 29, MCP 추가). `--disable-slash-commands` + `--strict-mcp-config`
조합으로 process-local 해소 가능 확인. `harness/runner.py::_harden_candidate_cmd`
가 candidate-cmd argv[0] basename = `claude` 일 때 두 flag 자동 부착.

핵심: **subprocess-only**. 운영자 interactive `claude` 세션은 영향 0 (skill /
MCP 다 살아있음) — iteration 호출 시점에만 정리. 결과: 4 → **2** (email,
git commits — claude account/CLI 레벨, project 차단 불가).

정본 + 검증 결과 + 5 단위 테스트:
[`docs/CANDIDATE-CONTEXT.md`](../CANDIDATE-CONTEXT.md) §7.6.
