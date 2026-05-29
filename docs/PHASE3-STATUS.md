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
- [ ] 정상 verify 1회
- [ ] 의도적 위반 smoke 1회
- [ ] 자체 harness dry run 1회

## 5. Phase 3 실행

- [ ] holdout 봉인 확인 — PLAN §3, §8
- [ ] 25 iter 또는 target 도달까지 실행 — PLAN §4, §6
- [ ] `runs/_summary/HISTORY.md` 누적 확인 — PLAN §7
- [ ] `scripts/analyze_run.py`로 REPORT 생성 — PLAN §7
- [ ] `scripts/evaluate_holdout.py --unseal` 1회 평가 — PLAN §8
