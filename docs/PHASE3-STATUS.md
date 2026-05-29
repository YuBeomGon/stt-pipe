# Phase 3 Status — DoD 체크

> 목적: Phase 3 현재 상태를 체크박스로만 관리한다. 절차와 정책 본문은
> [`PHASE3-PLAN.md`](PHASE3-PLAN.md)를 참조하고 여기에는 복사하지 않는다.

---

## 1. 문서 정렬

- [x] `docs/SSOT.md` 생성 — 문서별 정본 책임 선언
- [x] `docs/PHASE3-PLAN.md`에서 진행 체크박스 제거
- [x] `docs/PHASE3-PLAN.md`를 자체 harness 기준으로 재작성
- [x] `docs/PHASE3-STATUS.md` 생성 — DoD 체크 전용
- [x] `docs/status/`는 세션 종료 인계 메모 용도로 유지
- [x] `docs/DESIGN.md`의 autoresearch 중심 표현 정리
- [x] `README.md`, `AGENTS.md`, `CLAUDE.md`의 Phase 3 요약 정리
- [x] `docs/AUTORESEARCH.md`를 historical/deprecated 문서로 격하 표시

## 2. Harness 코드 경계

- [x] `harness/` 패키지 생성 — PLAN §2
- [x] `scripts/verify_check.py` 정책 로직을 `harness/guards.py`로 이동 — PLAN §5
- [x] `scripts/append_history.sh` 역할을 `harness/history.py`로 이동 — PLAN §7
- [x] `harness/policy.py` 구현 — PLAN §6
- [x] `harness/state.py` 구현 — PLAN §4, §6
- [x] `harness/verify.py` 구현 — PLAN §4, §5
- [x] `harness/runner.py` 구현 — PLAN §4
- [x] `scripts/evolve.py` thin CLI 추가 — PLAN §2, §4

## 3. 기존 스크립트 정리

- [x] `scripts/verify_check.py`를 compatibility wrapper로 축소
- [x] `scripts/append_history.sh`를 compatibility wrapper로 축소
- [ ] `scripts/verify.sh` / `verify.sh.alt` swap 구조 폐기 여부 결정
- [ ] `scripts/swap_verify.sh` 폐기 또는 archive 여부 결정
- [ ] `scripts/swap_claude.sh` 폐기 또는 archive 여부 결정
- [ ] `.claude.alt/` 운영 필요성 재검토

## 4. 검증

- [x] `pytest tests/test_harness_runner.py tests/test_harness_policy.py tests/test_verify_check.py`
- [x] 전체 pytest
- [x] review C1: candidate `runs/_summary/` 수정 scope 위반/rollback 검증 — PLAN §2, §7
- [x] review C2/I3: NaN/Inf 및 score schema 누락 hard-fail 검증 — PLAN §5, §6
- [x] review C3: backend static 우회 패턴 검사 강화 — PLAN §5
- [x] review I1: state 저장 원자 replace 적용 — PLAN §7
- [x] review I2/I5: HISTORY 기록 git 인자 방어와 stderr 원문 미복사 — PLAN §7
- [ ] 정상 verify 1회
- [ ] 의도적 위반 smoke 1회
- [ ] 자체 harness dry run 1회

## 5. Phase 3 실행

### 5.1 phase3_001 (baseline, harness v1 — 자체 harness 첫 잡)
- [x] holdout 봉인 확인 — PLAN §3, §8
- [x] 25 iter 실행 (target 미도달, best 0.2545 @ iter 9) — PLAN §4, §6
- [x] `runs/_summary/HISTORY.md` 누적 확인 — PLAN §7
- [x] `scripts/analyze_run.py`로 REPORT 생성 → `docs/reports/phase3_001_REPORT_2026-05-29.md`
- [ ] `scripts/evaluate_holdout.py --unseal` 1회 평가 (보류 — A' 후 phase3_002 끝나면 한 번에)

## 6. A' — candidate runtime infra (proposal 2026-05-29-agent-design)

- [x] RFC 작성 — `docs/proposals/2026-05-29-agent-design.md`
- [x] candidate profile 신설 — `harness/prompts/candidate.md`
- [x] runner profile inline + YAML parse + format reject + 권고 lane round-robin + abort 가드 — PLAN §4
- [x] `analyze_run.py` D 축 확장 — lane entropy / fingerprint Jaccard / max streak / format reject 비율
- [x] 테스트 — `parse_candidate_metadata`, format reject path, abort 가드, `LANES ↔ profile` 일관성
- [x] 문서 정본 갱신 — PHASE3-PLAN §2 / §4 / §5 / §7, SSOT
- [ ] phase3_002 50 iter 실행 (proposal §10)
- [ ] phase3_002 REPORT 의 D 축으로 A' 효과 판정 — proposal §4 판정 표
- [ ] 후속 결정 — C-lite 진행 / SPEC 보강 / profile 재작성

> 주의: phase3_002 는 §7 컨텍스트 정리도 같이 들어간 환경에서 돌므로
> "A' 단독 효과" 분리 불가 — proposal §11.4 참고. phase3_002 결과를 새 baseline 으로.

## 7. Candidate 컨텍스트 정리 (proposal 2026-05-29-agent-design §11 Addendum)

선행 RFC scope 외 추가 작업. phase3_002 진입 전 정리.

- [x] `docs/CANDIDATE-CONTEXT.md` 작성 — PUSH / AUTO-PUSH / PULL 분류 + 감사 결과
- [x] `scripts/audit_candidate_context.py` — probe + leak 규칙
- [x] CLAUDE.md candidate session gate (22 줄로 축소, 본문 자기-면역)
- [x] superpowers 플러그인 project-scope disable — `.claude/settings.json`
- [x] PHASE3-PLAN §3.7 에 audit 절차 등록 + README workflow 에 추가
- [x] 누수 5 → 2 (잔여: email PII, git recent commits — claude CLI 기본 동작, 운영 합의로 허용)

## 8. 후속 RFC

- [ ] [`proposals/2026-05-29-skills-and-prompt-eval.md`](proposals/2026-05-29-skills-and-prompt-eval.md) — 내부 skill/agent + prompt-eval. 본문은 phase3_002 결과 후 채움
