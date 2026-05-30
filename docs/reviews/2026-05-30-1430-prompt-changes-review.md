# Review — 프롬프트 변경 (진단주입 + explore/exploit + synthesis)

| | |
|---|---|
| 작성 | 2026-05-30 14:30 KST |
| 방식 | subagent 3개 병렬 (doc-code 정합 / 기능·버그 / claude -p 최소 컨텍스트) + 메인 종합 |
| 대상 커밋 | `3a2efbf` (진단주입), `5baff8f` (explore/synthesize 스케줄+synthesis), `cc3ea3c` (exploit 리네임+SSOT 정합) |
| 대상 파일 | `harness/runner.py`, `harness/prompts/candidate.md`, `docs/{PHASE3-PLAN,PHASE3-STATUS,SSOT}.md` |
| 테스트 | `pytest tests/` → 139 passed (py11) |

본 리뷰의 3개 축은 운영자 요청: (1) 현재 문서↔코드 정합성, (2) 방금 수정한
프롬프트 기능 동작·버그, (3) `claude -p` 후보 세션에 최소 컨텍스트만 들어가는지.

---

## 1. 기능·버그 검토 (subagent 2)

`_explore_ratio` / `_is_explore_iter` / `_iteration_mode` / `_promising_rejects`
/ `_dominant_axis` / `_format_error_profile` / `_format_recent_table` 실측 검증.

### 🔴 F1 — `_format_error_profile` 가 `best_cer is None` 에서 crash (수정 완료)
- **증거**: 가드가 `if not state.best_hyp_id:` 뿐. `best_hyp_id` 있고 `best_cer
  None` 이면 `f"… {state.best_cer:.4f}"` 에서 `TypeError` → `build_candidate_prompt`
  전체 중단. 형제 함수(`_promising_rejects`, `_dominant_axis`, `_iteration_mode`)는
  모두 `best_cer is None` 을 가드하는데 이 함수만 누락.
- **도달경로**: `record_best()` 는 두 필드를 항상 같이 set 하므로 정상 API 로는
  발생 X. 단 `HarnessState.load()` 가 cross-field 검증 없이 `state.json` 을
  로드하므로, 손상/마이그레이션된 state(best_hyp_id 있고 best_cer null)로 **resume
  시** crash.
- **처리**: 가드를 `if not state.best_hyp_id or state.best_cer is None:` 로 수정 +
  회귀 테스트 `test_format_error_profile_best_hyp_without_cer_does_not_crash` 추가.

### 🟢 정상 확인 (버그 없음)
- **explore/exploit 스케줄 밀도**가 목표 비율과 일치 (iters 1–10 실측 0.70 vs 목표
  0.75; 190–210 실측 0.19 vs floor 0.20). **결정적** — 같은 state → byte-identical
  프롬프트, 모든 `glob` 은 `sorted()` 래핑, RNG/Date 없음 → resume-safe.
- **`_promising_rejects`** 실데이터(best=iter_018) 검증: best_gain desc 정렬
  (iter_045 +0.10 / iter_014 +0.04 / iter_008 +0.03), best dir 제외, cer-blowup
  필터(×1.25) 정상, `coverage=min(lr,1.0)` 과생성 클램프, **hal 은 best_gain 계산
  *후* gains 에 추가되어 랭킹 못 건드림** (의도대로), ✓/✗/· 부호 정확, 결측 필드
  무크래시.
- **`_dominant_axis`** 분기(over-generation → coverage/deletion → substitution)
  비중첩·None-safe.
- **explore 격리**: 진짜 explore 모드에서 `synthesis_block` 빈 값, reject diff 누수
  없음. (explore 프롬프트에 보이는 "Promising prior attempts to SYNTHESIZE" 문자열은
  inline 된 정적 `candidate.md` 프로필 설명이지 reject diff 아님 — benign.)

### 🟡 경미 (선택)
- 유망 reject diff 가 `_PROMISING_DIFF_MAX_CHARS`(4000)에서 줄 중간 절단 →
  ```diff fence 가 깨진 형태. 후보는 verbatim apply 가 아니라 *조합* 지시라 무해.
  권고: cap 이하 마지막 newline 에서 자르고 `… (truncated)` 표기.
- `_is_explore_iter` 가 매 호출 iter 1 부터 재계산(O(n)) — is_explore(5000)≈0.0012s,
  무시 가능.

**판정**: explore/exploit + synthesis 기계는 기능상 정확·결정적. F1 (resume crash)
하나만 실질 버그였고 수정됨.

---

## 2. 문서 ↔ 코드 정합성 (subagent 1)

핵심(prompt-build, format-gate 스키마, 양 abort 가드)은 정본 문서와 코드가
일치 확인. 잔존 stale (대부분 수정 완료):

| 항목 | 상태 |
|---|---|
| PHASE3-PLAN §4 step1 prompt-build (진단주입/error profile/explore-exploit/synthesis) ↔ `build_candidate_prompt` | ✅ 일치 |
| §5 format gate 스키마 ↔ `parse_candidate_metadata` (`capability_investigated`/`what_i_learned`/`hypothesis`/`fingerprint`) | ✅ 일치 |
| 양 abort 가드 (format 4/5, command-fail 연속 3, status 문자열) ↔ 코드 | ✅ 일치 |
| candidate.md EXPLORE/EXPLOIT ↔ `_EXPLORE_DIRECTIVE`/`_EXPLOIT_DIRECTIVE` | ✅ 일치 |
| **PHASE3-PLAN §6 fallback "0.01"** — 코드는 `0.002` (F2 banking) | 🟠→ **수정** |
| **§7 메타 스키마 "lane / fingerprint / why"** — 옛 A' (`why` 필드 없음) | 🟠→ **수정** |
| §7 "lane 선택" 문구 / abort status 누락 | 🟡→ **수정** (mechanism 선택, command-failure 추가) |
| **SSOT §5 agent-design 행** — A' lane 스키마가 현행처럼 보임 | 🟠→ **수정** (대체 표기) |
| `scripts/analyze_run.py` 5-lane D축 entropy 잔존 (lane 미선언 시 None) | 🟡 코드(문서 아님). lane 은 optional 로 합법 생존 — dormant. 후속 |

**판정**: 정본 문서는 코드와 일치. 위 사실오류(0.01, `why` 필드, SSOT 행)는 본
리뷰에서 정정 완료. analyze_run 의 휴면 5-lane 기계만 후속 정리 대상.

---

## 3. `claude -p` 최소 컨텍스트 (subagent 3)

운영자 질문 — "claude -p 시 필수 프롬프트만 들어가고 CLAUDE.md 등은 안 들어가나?"

**답: 주입 프롬프트는 깨끗·최소. 단 운영 상태 1건 주의.**

- ✅ **주입 프롬프트 자체 무누수**: `runs/phase3_004_iter_050/prompt.md` 에
  CLAUDE.md/AGENTS.md/email/운영자 규칙 텍스트 **0건** (grep 검증). 내용은 후보
  프로필 + workspace inline + frozen surface + goal/state/error-profile/recent/
  ledger/mode/history-suppressed + diagnosis 뿐.
- ✅ **하드닝 플래그**(`_harden_candidate_cmd`): `--disable-slash-commands`
  (skills 29→0) + `--strict-mcp-config` (MCP→NONE) + `--disallowedTools=Bash,
  WebFetch,WebSearch,Task` (Bash 차단 = `cat judge/…` 류 cheat 봉쇄). 실제 run
  의 `candidate_cmd_hardening.txt` 로 4개 모두 적용 확인.
- ✅ **AGENTS.md 미로드**, **CLAUDE.md 게이트** 작동(마커 기반 면제).
- 🟡 **게이트 문자열 불일치(robustness)**: 프롬프트는 `=== BEGIN CANDIDATE PROFILE
  (harness/prompts/candidate.md) ===` 로 시작, CLAUDE.md 게이트는 `=== BEGIN
  CANDIDATE PROFILE ===`. "로 시작하면" 규칙이라 **작동은 하지만** 정확한 prefix
  일치는 아님. 정확 prefix 를 강제하는 테스트도 없음 → 한쪽 편집 시 silent desync
  위험. 권고: CLAUDE.md 게이트를 "`=== BEGIN CANDIDATE PROFILE` 로 시작" 으로
  loosen 하거나, 정확 prefix 단언 테스트 추가.
- ⚪ **잔여(설계상 허용)**: 운영자 email + git commit 제목은 claude CLI 계정/기본
  동작이라 후보 system prompt 에 들어감 — 프로젝트로 제거 불가, 문서화됨.
- 🔴→⚪ **감사 4-leak 은 우리 swap 상태**: 감사 스크립트가 superpowers hook +
  plugin 재주입 + Read-deny 미적용(=4 leak)을 보고했는데, 이는 **운영자가
  하드닝을 OFF (`.claude`↔`.claude.alt` swap) 해둔 현재 상태** 때문 (문서 작업용).
  버그 아님. **잡 실행 전 `bash scripts/swap_claude.sh` 로 ON 필수** — ON 이면
  Read-deny + 샌드박스 hook + superpowers-disable 복구되어 2-leak(email/git) 로 회귀.

**판정**: 주입 프롬프트는 최소·무누수, 하드닝 정상. 게이트 문자열 정합(🟡)만 후속
권고. 잡 실행 전 하드닝 ON 은 필수 운영 절차.

---

## 4. 종합 + 후속

- **수정 완료**: F1 resume crash, 문서 사실오류 4건(0.01→0.002, 메타 스키마,
  abort status, SSOT §5 행).
- **후속 권고(비차단)**: (a) 게이트 문자열 정확 prefix 일치 + 테스트, (b) reject
  diff 절단을 newline 경계로, (c) `analyze_run.py` 휴면 5-lane 기계 정리.
- **운영 필수**: 잡 실행 전 하드닝 ON swap (현재 OFF).

전체적으로 진단주입 + explore/exploit + synthesis 변경은 기능 정확·문서 정합.
차단 이슈 없음.
