# Proposal — 내부 Skill/Agent + Candidate Profile A/B (prompt-eval)

- 상태: **Draft (skeleton)** — 본문 핵심은 phase3_002 결과 후 채움
- 작성: 2026-05-29
- 선행: [`2026-05-29-agent-design.md`](2026-05-29-agent-design.md) (A' candidate runtime)
- 영향: `.claude/skills/` (신설), `harness/`, 운영자 워크플로우, 외부 의존
- 트리거: 선행 RFC §11.5 후속 슬롯

---

## 1. 배경

선행 RFC §11 에서 발견된 2 가지 공백:

1. **외부 skill 의존** — `claude -p` (candidate) 와 운영자 interactive 양쪽에서
   superpowers 등 외부 플러그인이 SessionStart 훅으로 강제 주입. project-scope
   disable 로 candidate 쪽은 정리됐으나, *운영자 워크플로우 표준화* 가 없어
   매 잡마다 ad-hoc 명령 조합 — 재현성 ↓.
2. **prompt A/B 측정 부재** — 선행 RFC §4 는 *결과 메트릭* (lane entropy 등)
   만 정의. *candidate profile 자체의 변경 효과* 를 측정할 메커니즘 없음.
   profile rewrite 의사결정이 *감* 으로 진행될 수밖에 없음.

본 proposal 은 두 공백을 한 RFC 로 묶는다 — 둘 다 *운영자 layer* 작업이며,
하나는 다른 하나의 측정 도구가 될 수 있어 결합 시 시너지 큼.

---

## 2. 결정 (잠정)

### 2.1 내부 skill — `.claude/skills/aig/*`

운영자 interactive 세션에서 invoke. candidate 세션 노출은 **이미 분리됨**
(이 RFC 작성 후 별도 hot-fix 로 진행 — [`CANDIDATE-CONTEXT.md`](../CANDIDATE-CONTEXT.md)
§7.6, proposal-1 §11.6):

- ✅ step A 완료: runner 가 candidate-cmd 에 `--disable-slash-commands` +
  `--strict-mcp-config` 자동 부착 (`harness/runner.py::_harden_candidate_cmd`).
  audit 검증: skills 29 → 0, MCP NONE
- step B (남음): `.claude/skills/aig/*` 만들면 위 flag 영향 받음 (skill
  카탈로그가 전부 죽음). 내부 skill 을 *살리려면* hardening 제외 로직 또는
  `--allowed-skill` 같은 화이트리스트 필요 → 본 RFC 가 다룰 사항
- 검증: audit 의 `SKILLS_AVAILABLE_COUNT` / `MCP_SERVERS_VISIBLE` 두 키가
  *우리가 만든 것만* 남는지

→ 본 RFC 는 step B (내부 skill 빌드 + hardening 화이트리스트) 에 집중.
step A 는 이미 갈피 잡힘.

| 스킬 | 무엇 | 대체 대상 |
|---|---|---|
| `aig:run-job` | phase3 잡 시작 (verify → smoke → dry → audit → 본 잡) | README 의 수동 시퀀스 |
| `aig:audit-context` | `scripts/audit_candidate_context.py` wrapper | 수동 호출 |
| `aig:analyze-run` | 잡 종료 후 REPORT 생성 + 판정 표 매칭 | `scripts/analyze_run.py` 수동 |
| `aig:propose-change` | RFC 템플릿 + 작성 가이드 | 매번 hand-craft |
| `aig:prompt-eval` | candidate profile A/B (§3 참조) | — (새로움) |

원칙:
- 스킬 본문은 *얇은 wrapper* — 핵심 로직은 `scripts/` 또는 `harness/` 에.
- 외부 의존 0 — 우리 코드만 호출.
- TODO (phase3_002 후): 5 개 중 가장 ROI 높은 1 개만 먼저 prototype.

### 2.2 prompt-eval — candidate profile A/B 측정

목적: candidate profile 변경이 *실제로* 결과를 바꾸는지 측정.

설계 초안 (phase3_002 결과 보고 확정):

```
aig:prompt-eval --profile-a harness/prompts/candidate.md \
                --profile-b harness/prompts/candidate.v2.md \
                --iters 20 \
                --seed 42
→ runs/_promptcmp/<date>/
  ├── profile_a/  (20 iter, profile_a 로)
  ├── profile_b/  (20 iter, profile_b 로)
  └── compare.json (best_cer, lane entropy, format_reject_pct 비교)
```

도전 과제:
- **비결정성** — LLM 응답 변동 → N=20 으론 부족할 수 있음. paired bootstrap?
- **비용** — 2× 잡 시간. profile 변경 1 번에 ~8h.
- **공정 비교** — seed 고정 가능? `claude -p` 는 seed 제어 X. → 변동성 인정,
  *큰 효과만 detect* 하는 도구로 positioning.

### 2.3 외부 의존 제거 단계

| 단계 | 무엇 | 시점 |
|---|---|---|
| 1 | superpowers candidate 쪽 disable | ✅ 완료 (commit `5419915`) |
| 2 | 운영자 interactive 에서도 superpowers off | 본 RFC 채택 시 |
| 3 | `~/.claude/settings.json` user-scope 정리 | step 2 와 함께 |

---

## 3. 작업 계획 (TBD — phase3_002 후 확정)

skeleton — 실 작업 항목은 phase3_002 결과 봐서 ROI 높은 순서로 reorder.

- [ ] phase3_002 결과 분석 → 본 proposal 본문 채우기
- [ ] §2.1 의 5 스킬 중 우선순위 결정
- [ ] §2.2 prompt-eval 비결정성 처리 방식 확정 (paired bootstrap / N / 통계)
- [ ] prototype 1 개 구현 → smoke
- [ ] 외부 의존 step 2, 3 진행

---

## 4. 영향 받는 파일 (예상)

### 신규
- `.claude/skills/aig/run-job.md` 외 4 개 (선택된 것)
- `harness/prompts/candidate.v2.md` (prompt-eval 비교용 변형)
- `scripts/prompt_eval.py` (또는 `harness/prompt_eval.py`)

### 수정
- `.claude/settings.json` (운영자 superpowers off 시)
- `docs/SSOT.md` (skill 디렉토리 등록)
- `docs/PHASE3-PLAN.md` (운영자 워크플로우 단순화)
- `README.md` (수동 시퀀스 → `aig:run-job` 단일 호출)

---

## 5. 가드레일 검토 (예상)

| 대상 | 변경 |
|---|---|
| candidate sandbox | 없음 — 본 proposal 은 운영자 layer 한정 |
| `.claude/settings.json` enabledPlugins | superpowers off 확대 (step 2) |
| 운영자 세션 | 외부 skill 의존 제거, 내부 skill 표준화 |
| holdout / baseline / frozen / judge | 영향 없음 |

---

## 6. 미결 사항

- prompt-eval 의 N / 통계 방법 — phase3_002 의 iter 간 변동성 봐서 결정
- 스킬 5 개 중 어느 것부터 — phase3_002 잡 중 *어디서 가장 ad-hoc 명령이
  많이 들어갔나* 관찰하면 답이 나옴
- subagent (operator → Agent tool) 활용 여부 — 현재 RFC scope 외, 본 RFC
  채택 후 별도 평가
